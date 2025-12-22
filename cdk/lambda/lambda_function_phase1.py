import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import boto3
import fitz  # PyMuPDF

# Clients are created outside the handler for connection reuse inside Lambda
s3_client = boto3.client(
    "s3", config=boto3.session.Config(retries={"max_attempts": 3}, read_timeout=300)
)
bedrock_runtime = boto3.client(
    "bedrock-runtime",
    config=boto3.session.Config(retries={"max_attempts": 3}, read_timeout=600),
)
textract_client = boto3.client(
    "textract",
    config=boto3.session.Config(retries={"max_attempts": 3}, read_timeout=60),
)

TEXTRACT_MAX_LINES = 200


DEFAULT_MODEL_ID = os.environ.get(
    "MODEL_ID", "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
)
DEFAULT_STAGE1_SYSTEM_PROMPT = (
    "You are an entity extractor for financial documents. Extract entities (people, companies) "
    "that have associated financial data on this page. "
    "Output must be a single JSON object: {page_number: <int>, entities: <array of objects>}. "
    "Each entity object MUST have a 'name' field containing the entity name. "
    "\n\nCRITICAL RULES:\n"
    "- ONLY extract entities that appear in the ACTUAL TEXT on this page\n"
    "- DO NOT invent, guess, or use placeholder names like 'JOHN DOE', 'ABC COMPANY', etc.\n"
    "- DO NOT create example or template data - every field must come from the actual document\n"
    "- Each entity must have associated data (amounts, dates, hours, etc.) - not just a name\n"
    "- If a name appears multiple times with different transactions, create separate entity objects\n"
    "- Ignore page headers, footers, column labels, and form field labels\n"
    "- If you cannot find ANY entities with financial data on this page, return {page_number: N, entities: []}\n"
    "- NEVER return entities with fabricated addresses, account numbers, or other made-up data\n"
    "\n\nENTITY NAMING PRIORITY (VENDOR vs CUSTOMER):\n"
    "- For invoices/bills: use the VENDOR/BILLER name (who is sending the bill), NOT the customer/account holder\n"
    "- Look for vendor in: company logo, letterhead, 'From' field, 'Billed By', header area\n"
    "- Do NOT use 'Account Name', 'Customer Name', or 'Account Holder' as the entity name\n"
    "- For service receipts: use the service provider/vendor name, not the customer receiving the service\n"
    "- When a person's name appears with a company name, prioritize the company name unless it's payroll\n"
    "- For payroll/paystubs: use the employee name as the entity (employee is receiving payment)\n"
    "- General rule: the entity is who is providing the service/goods, not who is paying for it\n"
    "- Include all relevant fields: name, dates, amounts, service details, customer info as metadata"
)
DEFAULT_STAGE2_SYSTEM_PROMPT = (
    "You are an entity grouper. Your job is to group the same entities together across multiple pages. "
    "You will receive Stage 1 output with entities extracted from each page. "
    "\n\nCRITICAL RULES:\n"
    "- ONLY use entities that were extracted in Stage 1 - DO NOT create new entities\n"
    "- DO NOT invent or hallucinate entity names or page numbers\n"
    "- Group entities that represent the same person/company across different pages\n"
    "- Match entities by name variations:\n"
    "  * Punctuation differences (e.g., middle initial with/without period)\n"
    "  * Name order differences (first-last vs last-first vs last, first)\n"
    "  * Treat all orderings of the same name components as the same entity\n"
    "- Each page belongs to exactly ONE entity. Do NOT assign the same page number to multiple entities.\n"
    "- Every entity from Stage 1 must appear in exactly one group in your output.\n"
    "- If Stage 1 returned no entities, return an empty entities array.\n"
    "\n\nOutput must be JSON: "
    "{entities: [{name: <string>, pages: <array of page numbers>, objects: <array of entity objects from Stage 1>}]}. "
    "Each group must include the canonical name, ALL page numbers where that entity appears, and ALL the Stage 1 entity objects."
)


def _reconstruct_entity_objects(stage2_groups: List[Dict], stage1_outputs: List[Dict]) -> List[Dict]:
    """
    Reconstruct full entity objects from Stage 1 data after Stage 2 grouping.
    Stage 2 only returns {name, pages}, but we need to attach the full objects.
    """
    # Build a lookup: page_number -> list of entities on that page
    page_to_entities = {}
    for page in stage1_outputs:
        if not isinstance(page, dict):
            continue
        page_num = page.get("page_number")
        entities = page.get("entities", [])
        if isinstance(entities, list):
            page_to_entities[page_num] = entities

    # Reconstruct each group with full objects
    reconstructed = []
    for group in stage2_groups:
        if not isinstance(group, dict):
            continue

        group_name = group.get("name")
        group_pages = group.get("pages", [])

        # Collect all entity objects from the pages in this group
        all_objects = []
        for page_num in group_pages:
            page_entities = page_to_entities.get(page_num, [])
            for entity in page_entities:
                if not isinstance(entity, dict):
                    continue
                # Include this entity's full data
                entity_with_page = entity.copy()
                entity_with_page["page_number"] = page_num
                all_objects.append(entity_with_page)

        reconstructed.append({
            "name": group_name,
            "pages": group_pages,
            "objects": all_objects
        })

    return reconstructed


def parse_s3_uri(uri: str) -> Tuple[str, str]:
    """
    Accepts s3:// URIs or virtual-hosted–style HTTPS links and returns (bucket, key).
    """
    parsed = urlparse(uri)

    if parsed.scheme == "s3":
        return parsed.netloc, parsed.path.lstrip("/")

    if parsed.scheme in ("http", "https") and ".s3" in parsed.netloc:
        bucket = parsed.netloc.split(".")[0]
        return bucket, parsed.path.lstrip("/")

    raise ValueError(f"Unsupported S3 URI: {uri}")


def fetch_s3_pages(uri: str) -> Tuple[List[Dict[str, Optional[str]]], bytes, List[Dict]]:
    """
    Fetches document from S3 and extracts pages.
    Returns: (pages, raw_pdf_bytes, text_coords_cache)
    - pages: List of dicts with 'text' and 'image_bytes' for LLM processing
    - raw_pdf_bytes: Original PDF bytes
    - text_coords_cache: List of PyMuPDF text+coords dicts for coordinate lookup (avoids reopening PDF)
    """
    bucket, key = parse_s3_uri(uri)
    print(f"Downloading from S3: {bucket}/{key}")

    obj = s3_client.get_object(Bucket=bucket, Key=key)
    content = b""
    total_size = obj.get("ContentLength", 0)
    downloaded = 0

    for chunk in obj["Body"].iter_chunks(chunk_size=1024 * 1024):
        content += chunk
        downloaded += len(chunk)
        if total_size > 0:
            print(
                f"Downloaded {downloaded}/{total_size} bytes ({downloaded/total_size*100:.1f}%)"
            )

    print("Download complete, processing PDF...")

    if key.lower().endswith(".pdf"):
        doc = fitz.open(stream=content, filetype="pdf")

        if doc.needs_pass:
            doc.authenticate("")

        pages = []
        text_coords_cache = []

        for page_num in range(doc.page_count):
            page = doc[page_num]
            pixmap = page.get_pixmap(dpi=200)
            image_bytes = pixmap.tobytes("png")

            # Extract plain text for LLM
            pages.append({
                "text": page.get_text(),
                "image_bytes": image_bytes,
            })

            # Extract text with coordinates for later lookup (avoid reopening PDF)
            text_dict = page.get_text("dict")
            text_dict["rotation"] = page.rotation  # Add rotation for coordinate normalization
            text_coords_cache.append(text_dict)

        doc.close()
        return pages, content, text_coords_cache

    return [{"text": content.decode("utf-8"), "image_bytes": None}], content, []


def _extract_json_candidate(output_text: str) -> Optional[object]:
    if not output_text:
        return None

    # Strip markdown code blocks if present
    text = output_text.strip()
    if text.startswith("```"):
        # Find the end of the opening fence (```json or ```)
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        # Remove closing fence if present
        if text.endswith("```"):
            text = text[:-3].rstrip()

    # Find JSON object or array
    start = min(
        [pos for pos in (text.find("{"), text.find("[")) if pos != -1],
        default=-1,
    )
    if start == -1:
        return None
    end = max(text.rfind("}"), text.rfind("]"))
    if end == -1 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None


def _llm_failed_to_detect_entities(output_text: str) -> bool:
    if not output_text:
        return True
    stripped = output_text.strip()
    if not stripped:
        return True
    lowered = stripped.lower()
    failure_phrases = (
        "unable to",
        "cannot",
        "can't",
        "could not",
        "no entities",
        "no relevant",
        "no data",
        "not legible",
        "unreadable",
    )
    if any(phrase in lowered for phrase in failure_phrases):
        return True
    candidate = _extract_json_candidate(stripped)
    if candidate is None:
        return False
    if isinstance(candidate, list) and not candidate:
        return True
    if isinstance(candidate, dict):
        entities = candidate.get("entities")
        if entities == []:
            return True
        if not candidate:
            return True
    return False


def _extract_textract_geometry(image_bytes: bytes) -> List[Dict]:
    """
    Extract text geometry from Textract for coordinate highlighting.
    Returns list of {text, bbox} dicts with normalized coordinates.
    """
    try:
        response = textract_client.analyze_document(
            Document={"Bytes": image_bytes},
            FeatureTypes=["TABLES", "FORMS"],
        )

        textract_geometry = []
        for block in response.get("Blocks", []):
            if block.get("BlockType") == "LINE" and block.get("Text"):
                if "Geometry" in block and "BoundingBox" in block["Geometry"]:
                    bbox = block["Geometry"]["BoundingBox"]
                    textract_geometry.append({
                        "text": block["Text"],
                        "bbox": {
                            "x": bbox.get("Left", 0),
                            "y": bbox.get("Top", 0),
                            "width": bbox.get("Width", 0),
                            "height": bbox.get("Height", 0)
                        }
                    })

        return textract_geometry
    except Exception as e:
        print(f"Failed to extract Textract geometry: {e}")
        return []


def _textract_fallback(page_number: int, image_bytes: bytes, model_id: str = None, max_tokens: int = 16000,
                      temperature: float = 0.0, system_prompt: str = None, user_request: str = None,
                      few_shot_examples: list = None) -> str:
    """
    Use Textract to extract text, then pass it through the LLM to structure entities.
    Returns JSON with structured entity data.
    """
    response = textract_client.analyze_document(
        Document={"Bytes": image_bytes},
        FeatureTypes=["TABLES", "FORMS"],
    )

    # Extract lines with their text and geometry
    lines = []
    textract_geometry = []

    for block in response.get("Blocks", []):
        if block.get("BlockType") == "LINE" and block.get("Text"):
            lines.append(block["Text"])

            # Store geometry for this line
            if "Geometry" in block and "BoundingBox" in block["Geometry"]:
                bbox = block["Geometry"]["BoundingBox"]
                textract_geometry.append({
                    "text": block["Text"],
                    "bbox": {
                        "x": bbox.get("Left", 0),
                        "y": bbox.get("Top", 0),
                        "width": bbox.get("Width", 0),
                        "height": bbox.get("Height", 0)
                    }
                })

            if len(lines) >= TEXTRACT_MAX_LINES:
                break

    textract_text = "\n".join(lines)
    print(f"Textract extracted {len(lines)} lines from page {page_number}")

    # Pass Textract text through LLM to extract structured entities
    if model_id and system_prompt and user_request is not None:
        stage1_system, stage1_messages = build_messages(
            textract_text,
            f"{user_request} (Page {page_number})",
            few_shot_examples or [],
            system_prompt,
        )
        llm_response = invoke_bedrock(
            model_id=model_id,
            system_prompt=stage1_system,
            messages=stage1_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        llm_output = llm_response["content"][0]["text"]

        # Parse LLM output and inject textract_geometry for coordinate lookup
        try:
            parsed = json.loads(llm_output)
            if isinstance(parsed, dict):
                parsed["textract_fallback"] = True
                parsed["textract_geometry"] = textract_geometry
                return json.dumps(parsed)
        except:
            pass  # If parsing fails, return as-is

        return llm_output

    # Fallback: return basic JSON structure
    fallback_payload = {
        "page_number": page_number,
        "page_type": "supporting_documentation",
        "entities": [{"type": "textract_text", "text": textract_text}],
        "textract_fallback": True,
        "textract_geometry": textract_geometry,
    }
    return json.dumps(fallback_payload)


def _normalize_stage1_output(page_number: int, output_text: str) -> Dict:
    candidate = _extract_json_candidate(output_text or "")
    if isinstance(candidate, dict):
        # ALWAYS use PyMuPDF's page number, not LLM's potentially incorrect one
        candidate["page_number"] = page_number
        candidate.setdefault("page_type", "unknown")
        candidate.setdefault("entities", [])
        if not isinstance(candidate["entities"], list):
            candidate["entities"] = []
        else:
            # Filter to dict entities and normalize field names
            normalized_entities = []
            for entity in candidate["entities"]:
                if isinstance(entity, dict):
                    # Normalize entity name field: use 'name' if present, otherwise try 'entity_name'
                    if "name" not in entity or entity.get("name") == "N/A":
                        if "entity_name" in entity and entity["entity_name"] != "N/A":
                            entity["name"] = entity["entity_name"]
                    normalized_entities.append(entity)
            candidate["entities"] = normalized_entities
        if "raw_output" in candidate:
            candidate["raw_output"] = candidate["raw_output"][:2000]
        return candidate
    return {
        "page_number": page_number,
        "page_type": "unknown",
        "entities": [],
        "raw_output": (output_text or "")[:2000],
    }


def _prepare_stage2_payload(stage1_outputs: List[Dict]) -> List[Dict]:
    """
    Prepare Stage 2 payload from Stage 1 outputs.
    Passes through all entity data - no stripping (more reliable, accepts slower performance).
    """
    sanitized = []
    for page in stage1_outputs:
        if not isinstance(page, dict):
            continue
        page_number = page.get("page_number")
        entities = page.get("entities") if isinstance(page.get("entities"), list) else []
        entities = [entity for entity in entities if isinstance(entity, dict)]

        sanitized.append({
            "page_number": page_number,
            "entities": entities,
        })
    return sanitized


def build_messages(
    document_text: str,
    user_request: str,
    few_shot_examples: List[Dict[str, str]],
    system_prompt: str,
) -> Tuple[str, List[Dict]]:
    """
    Builds a Bedrock Messages API payload with optional few-shot examples.
    Returns (system_prompt, messages) for Anthropic Claude format.
    few_shot_examples: list of {"user": "...", "assistant": "..."}.
    """
    messages: List[Dict] = []

    for example in few_shot_examples:
        messages.append(
            {"role": "user", "content": [{"type": "text", "text": example["user"]}]}
        )
        messages.append(
            {
                "role": "assistant",
                "content": [{"type": "text", "text": example["assistant"]}],
            }
        )

    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": f"Source document:\n{document_text}\n\nRequest:\n{user_request}",
                }
            ],
        }
    )

    return system_prompt, messages


def invoke_bedrock(
    model_id: str,
    system_prompt: str,
    messages: List[Dict],
    max_tokens: int = 1000,
    temperature: float = 0.0,
) -> Dict:
    import time
    from botocore.exceptions import ClientError

    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "system": system_prompt,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
    )

    # Retry with exponential backoff for rate limiting
    max_retries = 5
    base_delay = 2  # seconds

    for attempt in range(max_retries):
        try:
            response = bedrock_runtime.invoke_model(
                modelId=model_id,
                contentType="application/json",
                accept="application/json",
                body=body,
            )
            return json.loads(response["body"].read())

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")

            if error_code == "ThrottlingException" and attempt < max_retries - 1:
                # Calculate exponential backoff delay
                delay = base_delay * (2 ** attempt)
                print(f"Rate limited. Retrying in {delay} seconds... (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                # Re-raise if not throttling or out of retries
                raise

    raise Exception(f"Failed after {max_retries} retries")


def _compute_normalized_coords(rect, page_rect) -> Dict[str, float]:
    """
    Compute normalized bounding box coordinates.
    Returns coords as floats between 0 and 1 relative to page dimensions.

    IMPORTANT: page_rect should be the MediaBox (original dimensions), not page.rect,
    to avoid issues with rotated pages.

    Args:
        rect: PyMuPDF Rect object from search_for
        page_rect: PyMuPDF Rect object representing page bounds (use MediaBox)

    Returns:
        Dict with keys: x, y, width, height (all normalized 0-1)
    """
    # Compute normalized coordinates
    x = (rect.x0 - page_rect.x0) / page_rect.width
    y = (rect.y0 - page_rect.y0) / page_rect.height
    width = (rect.x1 - rect.x0) / page_rect.width
    height = (rect.y1 - rect.y0) / page_rect.height

    # Log if coordinates are out of bounds (indicates a problem)
    if x < 0 or x > 1 or y < 0 or y > 1 or width < 0 or width > 1 or height < 0 or height > 1:
        print(f"WARNING: Coordinates out of bounds before clamping: x={x:.4f}, y={y:.4f}, w={width:.4f}, h={height:.4f}")
        print(f"  rect: ({rect.x0:.1f}, {rect.y0:.1f}, {rect.x1:.1f}, {rect.y1:.1f})")
        print(f"  page_rect: ({page_rect.x0:.1f}, {page_rect.y0:.1f}, {page_rect.x1:.1f}, {page_rect.y1:.1f}), width={page_rect.width:.1f}, height={page_rect.height:.1f}")

    # Clamp to [0, 1] range and validate
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    width = max(0.0, min(1.0, width))
    height = max(0.0, min(1.0, height))

    # Ensure x + width <= 1.0 and y + height <= 1.0
    if x + width > 1.0:
        width = 1.0 - x
    if y + height > 1.0:
        height = 1.0 - y

    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }


def _search_textract_geometry(textract_geometry: List[Dict], search_text: str) -> List[Dict[str, float]]:
    """
    Search through Textract geometry data to find matching text.
    Uses intelligent matching: requires the search text to be a distinct word/phrase,
    not just a substring of a longer word.

    Textract coordinates are already normalized (0-1).

    Args:
        textract_geometry: List of {text, bbox} dicts from Textract
        search_text: Text string to search for

    Returns:
        List of coordinate dicts matching our format
    """
    if not textract_geometry or not search_text:
        return []

    coords = []
    search_text_lower = search_text.lower().strip()
    search_words = search_text_lower.split()

    for item in textract_geometry:
        item_text = item.get("text", "").lower().strip()
        item_words = item_text.split()

        # Check if all search words appear in this line as complete words
        # This prevents "ACC" from matching "account"
        all_words_found = True
        for search_word in search_words:
            # Check if this search word appears as a complete word (not substring)
            if search_word not in item_words:
                # Also check if the search word appears with punctuation
                # e.g., "Inc" should match "Inc." or "Inc,"
                found_with_punct = False
                for item_word in item_words:
                    # Remove common punctuation and compare
                    clean_item_word = item_word.strip('.,;:!?()')
                    if search_word == clean_item_word:
                        found_with_punct = True
                        break

                if not found_with_punct:
                    all_words_found = False
                    break

        if all_words_found:
            bbox = item.get("bbox", {})
            if bbox and bbox.get("width", 0) > 0 and bbox.get("height", 0) > 0:
                coords.append({
                    "x": bbox["x"],
                    "y": bbox["y"],
                    "width": bbox["width"],
                    "height": bbox["height"]
                })

    return coords


def _find_text_coords_on_page(
    doc, page_number: int, search_text: str, stage1_outputs: List[Dict] = None
) -> List[Dict[str, float]]:
    """
    Find all occurrences of search_text on the given page and return normalized coords.
    First tries PyMuPDF search, then falls back to Textract geometry if available.
    If exact match fails, tries searching for individual words and combining their bounds.

    Args:
        doc: PyMuPDF document object
        page_number: 1-based page number
        search_text: Text string to search for
        stage1_outputs: Optional list of stage1 outputs containing textract_geometry

    Returns:
        List of coordinate dicts, each with x, y, width, height (normalized 0-1)
    """
    if not search_text or not search_text.strip():
        return []

    try:
        # Try Textract geometry first (most accurate for scanned/complex pages)
        if stage1_outputs:
            for page_data in stage1_outputs:
                if page_data.get("page_number") == page_number:
                    textract_geometry = page_data.get("textract_geometry")
                    if textract_geometry:
                        coords = _search_textract_geometry(textract_geometry, search_text)
                        if coords:
                            return coords
                    break

        # Fall back to PyMuPDF simple substring search
        # Keep it simple - just search for the text as-is
        page_index = page_number - 1
        if page_index >= 0 and page_index < doc.page_count:
            page = doc[page_index]

            # Get page dimensions accounting for rotation
            # PyMuPDF's search_for() returns coords in the rotated coordinate space
            page_rect = page.rect
            rotation = page.rotation

            # For 90° or 270° rotation, dimensions are swapped in the coordinate space
            if rotation in (90, 270):
                page_width = page_rect.height
                page_height = page_rect.width
            else:
                page_width = page_rect.width
                page_height = page_rect.height

            # Try exact phrase search
            rects = page.search_for(search_text.strip())
            if rects:
                coords = []
                for i, rect in enumerate(rects):
                    # Debug logging for coordinate issues (should be rare now with rotation fix)
                    if rect.x0 > page_width or rect.y0 > page_height:
                        print(f"  WARNING: Match {i+1} coordinates still exceed page dimensions after rotation fix!")
                        print(f"    rect: ({rect.x0:.1f}, {rect.y0:.1f}, {rect.x1:.1f}, {rect.y1:.1f})")
                        print(f"    page: width={page_width:.1f}, height={page_height:.1f}, rotation={rotation}°")

                    # Normalize using displayed page dimensions (accounts for rotation)
                    coord = {
                        "x": (rect.x0 - page_rect.x0) / page_width,
                        "y": (rect.y0 - page_rect.y0) / page_height,
                        "width": (rect.x1 - rect.x0) / page_width,
                        "height": (rect.y1 - rect.y0) / page_height,
                    }

                    # Clamp to valid range
                    coord["x"] = max(0.0, min(1.0, coord["x"]))
                    coord["y"] = max(0.0, min(1.0, coord["y"]))
                    coord["width"] = max(0.0, min(1.0, coord["width"]))
                    coord["height"] = max(0.0, min(1.0, coord["height"]))

                    # Ensure bounds
                    if coord["x"] + coord["width"] > 1.0:
                        coord["width"] = 1.0 - coord["x"]
                    if coord["y"] + coord["height"] > 1.0:
                        coord["height"] = 1.0 - coord["y"]

                    coords.append(coord)
                print(f"PyMuPDF found '{search_text}' on page {page_number}: {len(coords)} matches")
                return coords

            # If no exact match, try without punctuation
            # E.g., "Godspeed Courier Services, Inc" -> "Godspeed Courier Services Inc"
            search_no_punct = search_text.replace(",", "").replace(".", "").strip()
            if search_no_punct != search_text.strip():
                rects = page.search_for(search_no_punct)
                if rects:
                    coords = []
                    for rect in rects:
                        # Normalize using displayed page dimensions (accounts for rotation)
                        coord = {
                            "x": (rect.x0 - page_rect.x0) / page_width,
                            "y": (rect.y0 - page_rect.y0) / page_height,
                            "width": (rect.x1 - rect.x0) / page_width,
                            "height": (rect.y1 - rect.y0) / page_height,
                        }

                        # Clamp to valid range
                        coord["x"] = max(0.0, min(1.0, coord["x"]))
                        coord["y"] = max(0.0, min(1.0, coord["y"]))
                        coord["width"] = max(0.0, min(1.0, coord["width"]))
                        coord["height"] = max(0.0, min(1.0, coord["height"]))

                        # Ensure bounds
                        if coord["x"] + coord["width"] > 1.0:
                            coord["width"] = 1.0 - coord["x"]
                        if coord["y"] + coord["height"] > 1.0:
                            coord["height"] = 1.0 - coord["y"]

                        coords.append(coord)
                    print(f"PyMuPDF found '{search_no_punct}' on page {page_number}: {len(coords)} matches")
                    return coords

            # Last resort: try uppercase version (for logo text like "ACC BUSINESS")
            search_upper = search_text.upper().strip()
            if search_upper != search_text.strip():
                rects = page.search_for(search_upper)
                if rects:
                    coords = []
                    for rect in rects:
                        coord = {
                            "x": (rect.x0 - page_rect.x0) / page_width,
                            "y": (rect.y0 - page_rect.y0) / page_height,
                            "width": (rect.x1 - rect.x0) / page_width,
                            "height": (rect.y1 - rect.y0) / page_height,
                        }
                        coord["x"] = max(0.0, min(1.0, coord["x"]))
                        coord["y"] = max(0.0, min(1.0, coord["y"]))
                        coord["width"] = max(0.0, min(1.0, coord["width"]))
                        coord["height"] = max(0.0, min(1.0, coord["height"]))
                        if coord["x"] + coord["width"] > 1.0:
                            coord["width"] = 1.0 - coord["x"]
                        if coord["y"] + coord["height"] > 1.0:
                            coord["height"] = 1.0 - coord["y"]
                        coords.append(coord)
                    print(f"PyMuPDF found uppercase '{search_upper}' on page {page_number}: {len(coords)} matches")
                    return coords

            print(f"PyMuPDF could not find '{search_text}' on page {page_number}")

        return []
    except Exception as e:
        print(f"Error finding coords for '{search_text}' on page {page_number}: {e}")
        return []


def _group_nearby_rects(rects: List, expected_count: int):
    """
    Group rectangles that are close to each other (likely same entity occurrence).

    Args:
        rects: List of fitz.Rect objects
        expected_count: Number of words in the entity name

    Returns:
        List of lists, where each inner list contains rects for one occurrence
    """
    if not rects:
        return []

    import fitz

    # Sort rects by position (top-to-bottom, left-to-right)
    sorted_rects = sorted(rects, key=lambda r: (r.y0, r.x0))

    # Group rects that are within reasonable distance
    # Use adaptive threshold based on typical rect sizes
    avg_height = sum(r.height for r in sorted_rects) / len(sorted_rects)
    avg_width = sum(r.width for r in sorted_rects) / len(sorted_rects)
    distance_threshold = max(avg_height * 2, avg_width * 2, 50)  # pixels

    groups = []
    current_group = [sorted_rects[0]]

    for rect in sorted_rects[1:]:
        # Check if this rect is close to any rect in current group
        is_nearby = False
        for group_rect in current_group:
            dx = min(abs(rect.x0 - group_rect.x1), abs(rect.x1 - group_rect.x0),
                    abs(rect.x0 - group_rect.x0), abs(rect.x1 - group_rect.x1))
            dy = min(abs(rect.y0 - group_rect.y1), abs(rect.y1 - group_rect.y0),
                    abs(rect.y0 - group_rect.y0), abs(rect.y1 - group_rect.y1))
            distance = (dx**2 + dy**2)**0.5

            if distance < distance_threshold:
                is_nearby = True
                break

        if is_nearby and len(current_group) < expected_count:
            current_group.append(rect)
        else:
            # Start new group if current group has reasonable size
            if len(current_group) >= expected_count * 0.5:  # At least half the expected words
                groups.append(current_group)
            current_group = [rect]

    # Don't forget the last group
    if len(current_group) >= expected_count * 0.5:
        groups.append(current_group)

    return groups


def _combine_rects(rects: List) -> 'fitz.Rect':
    """
    Combine multiple rectangles into a single bounding box.

    Args:
        rects: List of fitz.Rect objects

    Returns:
        fitz.Rect that encompasses all input rects
    """
    import fitz

    if not rects:
        return fitz.Rect(0, 0, 0, 0)

    x0 = min(r.x0 for r in rects)
    y0 = min(r.y0 for r in rects)
    x1 = max(r.x1 for r in rects)
    y1 = max(r.y1 for r in rects)

    return fitz.Rect(x0, y0, x1, y1)


def to_document_analysis(
    document_id: str,
    page_count: Optional[int],
    matched_entities_root: Dict,
    stage1_outputs: Optional[List[Dict]] = None,
    pages_metadata: Optional[List[Dict]] = None,
) -> Dict:
    """
    Transform the matched_entities structure into a frontend-friendly DocumentAnalysis format.

    DocumentAnalysis provides:
    - A flat list of groups (one per matched entity)
    - A flat list of occurrences (highlighting locations) within each group
    - Page-level metadata
    - Normalized structure with unique IDs for easy frontend consumption

    Args:
        document_id: Unique identifier for this document (can be "anonymous-document" if unknown)
        page_count: Total number of pages in the document (from PyMuPDF doc.page_count)
        matched_entities_root: Dict containing "matched_entities" key with the grouped entities
        stage1_outputs: Optional list of stage1 outputs
        pages_metadata: Optional list of page metadata from fetch_s3_pages

    Returns:
        DocumentAnalysis dict with structure:
        {
            "schemaVersion": "1.0",
            "documentId": str,
            "pageCount": int or None,
            "pages": [{"pageNumber": int}, ...],
            "groups": [Group, ...]
        }

    Each Group contains:
        - groupId: Unique ID like "g_0", "g_1"
        - label: Display name (usually the entity name)
        - summaryPages: Pages where summaries appear
        - supportingPages: Pages where supporting docs appear
        - occurrences: List of highlight locations with coords

    Each Occurrence contains:
        - occurrenceId: Unique ID like "g0_s0_e0"
        - groupId: Back-reference to parent group
        - pageNumber: 1-based page number
        - role: "summary" or "supporting"
        - coords: List of normalized bounding boxes (x, y, width, height in 0-1 range)
        - snippet: Short text preview for UI
        - rawSource: Original entity object for detailed inspection
    """
    # Build pages array
    pages = []
    if page_count:
        for page_num in range(1, page_count + 1):
            pages.append({
                "pageNumber": page_num
            })

    analysis = {
        "schemaVersion": "1.0",
        "documentId": document_id,
        "pageCount": page_count,
        "pages": pages,
        "groups": []
    }

    entities_list = matched_entities_root.get("entities", [])
    if not isinstance(entities_list, list):
        return analysis

    for group_idx, entity in enumerate(entities_list):
        if not isinstance(entity, dict):
            continue

        # Generate unique group ID
        group_id = f"g_{group_idx}"

        # Extract group metadata
        label = entity.get("name", f"Group {group_idx}")
        entity_pages = entity.get("pages", []) or []

        # For backward compatibility with frontend, we'll put all pages in supportingPages
        # Frontend currently expects summaryPages and supportingPages
        summary_pages = []
        supporting_pages = entity_pages

        occurrences = []

        # Process coords attached directly to entity
        entity_coords = entity.get("coords", [])
        if isinstance(entity_coords, list):
            for coord_idx, coord in enumerate(entity_coords):
                if not isinstance(coord, dict):
                    continue

                page_number = coord.get("page_number")
                role = coord.get("role", "supporting")

                if not page_number:
                    continue

                # Generate unique occurrence ID
                occurrence_id = f"{group_id}_p{page_number}_c{coord_idx}"

                # Extract snippet from entity name
                snippet = label

                # Create a single-element coords array (the bounding box without page_number/role)
                coord_box = {k: v for k, v in coord.items() if k not in ["page_number", "role"]}

                occurrence = {
                    "occurrenceId": occurrence_id,
                    "groupId": group_id,
                    "pageNumber": page_number,
                    "role": role,
                    "coords": [coord_box],  # Wrap in array as frontend expects list of boxes
                    "snippet": snippet,
                }
                occurrences.append(occurrence)

        # Build the group
        group = {
            "groupId": group_id,
            "label": label,
            "kind": None,  # Reserved for future use (e.g., "employee", "vendor", etc.)
            "summaryPages": summary_pages,
            "supportingPages": supporting_pages,
            "occurrences": occurrences,
            "meta": {
                "rawObjects": entity.get("objects", [])
            }
        }

        analysis["groups"].append(group)

    return analysis


def _search_cached_text(text_dict: Dict, search_text: str) -> List[Dict[str, float]]:
    """
    Search for text in cached PyMuPDF text dict and return normalized coordinates.

    Args:
        text_dict: PyMuPDF page.get_text("dict") output
        search_text: Text to search for

    Returns:
        List of coordinate dicts with x, y, width, height (normalized 0-1)
    """
    if not search_text or not text_dict:
        return []

    search_lower = search_text.lower().strip()
    coords = []

    # Get page dimensions and handle rotation
    base_width = text_dict.get("width", 1)
    base_height = text_dict.get("height", 1)
    rotation = text_dict.get("rotation", 0)

    # For 90° or 270° rotation, dimensions are swapped in coordinate space
    # (Same logic as the old code had with page.search_for())
    if rotation in (90, 270):
        page_width = base_height
        page_height = base_width
    else:
        page_width = base_width
        page_height = base_height

    # Iterate through blocks, lines, and spans to find matching text
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # Skip non-text blocks (images, etc.)
            continue

        for line in block.get("lines", []):
            # Reconstruct line text from spans
            line_text = "".join(span.get("text", "") for span in line.get("spans", []))

            if search_lower in line_text.lower():
                # Found match - use the line's bounding box
                bbox = line.get("bbox")
                if bbox and len(bbox) == 4:
                    x0, y0, x1, y1 = bbox
                    normalized = {
                        "x": x0 / page_width,
                        "y": y0 / page_height,
                        "width": (x1 - x0) / page_width,
                        "height": (y1 - y0) / page_height
                    }
                    # Debug logging
                    if len(coords) == 0:  # Only log first match per search
                        print(f"  Match '{line_text[:30]}...' at bbox={bbox}")
                        print(f"  Page dims: w={page_width:.1f}, h={page_height:.1f}, rotation={rotation}")
                        print(f"  Normalized: {normalized}")
                    coords.append(normalized)

    return coords


def attach_coords_to_matched_entities(
    matched_entities: List[Dict], pdf_bytes: bytes, stage1_outputs: List[Dict] = None
) -> None:
    """
    Augment matched_entities structure with coordinate information for entity names.
    Modifies the matched_entities list in-place by adding 'coords' fields.
    Reopens PDF to search for text coordinates using PyMuPDF's search_for().

    Only entities with a "name" field will have coordinates attached. Other entities
    (like payment objects, dates, amounts) will not have coordinates.

    Args:
        matched_entities: List of matched entity dicts from stage 2
        pdf_bytes: Raw PDF file bytes
        stage1_outputs: Optional list of stage1 outputs containing textract_geometry
    """
    if not matched_entities or not pdf_bytes:
        return

    try:

        # Process each entity group
        for entity_group in matched_entities:
            entity_name = entity_group.get("name", "Unknown")
            entity_pages = entity_group.get("pages", [])

            # Generate name variations for better matching
            name_variations = [entity_name]

            # If name is in "Last, First" format, try "First Last" too
            if "," in entity_name:
                parts = [p.strip() for p in entity_name.split(",")]
                if len(parts) == 2:
                    name_variations.append(f"{parts[1]} {parts[0]}")

            # Extract names from objects for additional variations
            all_objects = entity_group.get("objects", [])
            if isinstance(all_objects, list):
                for obj in all_objects:
                    if isinstance(obj, dict):
                        # Check various name fields (both string and nested dict forms)
                        possible_names = []

                        # Direct string fields
                        for key in ["name", "company_name", "vendor_name", "service_provider"]:
                            val = obj.get(key)
                            if val and isinstance(val, str):
                                possible_names.append(val)

                        # Nested dict fields (e.g., vendor.name, company.name, client.name)
                        for key in ["vendor", "company", "client"]:
                            val = obj.get(key)
                            if val and isinstance(val, dict) and "name" in val:
                                nested_name = val["name"]
                                if isinstance(nested_name, str):
                                    possible_names.append(nested_name)

                        for obj_name in possible_names:
                            if obj_name not in name_variations:
                                name_variations.append(obj_name)

            # Attach coordinates to entity name on each page
            # NOTE: We tried caching text+coords but get_text("dict") returns unrotated coordinates
            # while search_for() returns rotated coordinates. Reopening PDF is more reliable.
            entity_group["coords"] = []
            print(f"Searching for entity '{entity_name}' with {len(name_variations)} name variations")

            # We need to reopen the PDF for accurate coordinate search
            # The cached approach had coordinate system mismatches
            try:
                import fitz
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                if doc.needs_pass:
                    doc.authenticate("")

                for page_num in entity_pages:
                    # Try all name variations until we find a match
                    for name_var in name_variations:
                        coords = _find_text_coords_on_page(doc, page_num, name_var, stage1_outputs)
                        if coords:
                            for coord in coords:
                                coord["page_number"] = page_num
                            entity_group["coords"].extend(coords)
                            break  # Found match, no need to try other variations

                doc.close()
            except Exception as e:
                print(f"Error searching for coordinates: {e}")

        print(f"Successfully attached coordinates to {len(matched_entities)} entity groups")

    except Exception as e:
        print(f"Error attaching coordinates: {e}")
        # Don't raise - allow Lambda to return response without coords


def lambda_handler(event, context):
    """
    event:
      s3_uri: S3 link to the document (s3://bucket/key or https://bucket.s3.amazonaws.com/key)
      question: optional request text for stage 1 (defaults to stage 1 instructions)
      few_shot_examples: optional list of {"user": "...", "assistant": "..."} examples for both stages
      max_tokens: optional override for response length
      temperature: optional override for sampling temperature
    """
    # Support local PDF path for testing
    local_pdf_path = event.get("local_pdf_path")
    s3_uri = event.get("s3_uri")

    if local_pdf_path:
        print(f"Using local PDF file: {local_pdf_path}")
        with open(local_pdf_path, "rb") as f:
            pdf_bytes = f.read()

        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.needs_pass:
            doc.authenticate("")

        pages = []
        text_coords_cache = []
        for page_num in range(doc.page_count):
            page = doc[page_num]
            pixmap = page.get_pixmap(dpi=200)
            image_bytes = pixmap.tobytes("png")
            pages.append({
                "text": page.get_text(),
                "image_bytes": image_bytes,
            })
            # Extract text with coordinates for later lookup
            text_dict = page.get_text("dict")
            text_dict["rotation"] = page.rotation  # Add rotation for coordinate normalization
            text_coords_cache.append(text_dict)
        doc.close()
    elif s3_uri:
        pages, pdf_bytes, text_coords_cache = fetch_s3_pages(s3_uri)
    else:
        raise ValueError("Missing required field: s3_uri or local_pdf_path")

    user_request = event.get(
        "question",
        "Apply the stage 1 instructions to produce the entity JSON.",
    )
    stage1_system_prompt = DEFAULT_STAGE1_SYSTEM_PROMPT
    stage2_system_prompt = DEFAULT_STAGE2_SYSTEM_PROMPT
    few_shot_examples = event.get("few_shot_examples") or []

    model_id = event.get("model_id", DEFAULT_MODEL_ID)
    max_tokens = int(event.get("max_tokens", 1000))
    temperature = float(event.get("temperature", 0.0))

    def process_page(page_data):
        i, page = page_data
        print(f"Processing page {i+1} of {len(pages)}...")
        page_text = page["text"]

        # If page text is empty or whitespace, skip LLM and use Textract directly
        # This prevents hallucinations when given empty input
        if not page_text or not page_text.strip():
            if page.get("image_bytes"):
                print(f"Page {i+1} has no extractable text, using Textract")
                try:
                    output_text = _textract_fallback(
                        i + 1,
                        page["image_bytes"],
                        model_id=model_id,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system_prompt=stage1_system_prompt,
                        user_request=user_request,
                        few_shot_examples=few_shot_examples
                    )
                    print(f"Completed page {i+1} (Textract)")
                    return i, _normalize_stage1_output(i + 1, output_text)
                except Exception as textract_error:
                    print(f"Textract failed for page {i+1}: {textract_error}")
                    # Return empty entities for this page
                    return i, {"page_number": i + 1, "page_type": "unknown", "entities": []}
            else:
                print(f"Page {i+1} has no text and no image bytes, skipping")
                return i, {"page_number": i + 1, "page_type": "unknown", "entities": []}

        stage1_system, stage1_messages = build_messages(
            page_text,
            f"{user_request} (Page {i+1} of {len(pages)})",
            few_shot_examples,
            stage1_system_prompt,
        )
        response = invoke_bedrock(
            model_id=model_id,
            system_prompt=stage1_system,
            messages=stage1_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        output_text = response["content"][0]["text"]

        # If LLM failed to detect entities, use full Textract fallback
        if _llm_failed_to_detect_entities(output_text) and page.get("image_bytes"):
            try:
                print(f"Falling back to Textract for page {i+1}")
                output_text = _textract_fallback(
                    i + 1,
                    page["image_bytes"],
                    model_id=model_id,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system_prompt=stage1_system_prompt,
                    user_request=user_request,
                    few_shot_examples=few_shot_examples
                )
            except Exception as textract_error:
                print(f"Textract fallback failed for page {i+1}: {textract_error}")
                # Continue with the LLM output even if Textract fails

        print(f"Completed page {i+1}")
        return i, _normalize_stage1_output(i + 1, output_text)

    all_stage1_outputs = [None] * len(pages)
    # Concurrency set to 3 for local testing to avoid rate limits
    # When deploying to AWS Lambda, you can increase this (e.g., 10) for better performance
    # AWS Lambda has higher concurrency limits and better rate limit handling
    with ThreadPoolExecutor(max_workers=min(len(pages), 3)) as executor:
        futures = {
            executor.submit(process_page, (i, page)): i for i, page in enumerate(pages)
        }
        for future in as_completed(futures):
            i, output = future.result()
            all_stage1_outputs[i] = output

    stage1_output = json.dumps(all_stage1_outputs)
    stage2_payload = json.dumps(_prepare_stage2_payload(all_stage1_outputs))
    stage2_request = (
        "Use the extracted entities to perform comparison and matching. "
        "Return matched_entities and orphaned_entities with associated page numbers and extracted JSON "
        "objects per the system prompt. "
        "For page references, only use page_number values present in the extracted entity JSON. "
        "Do not infer or invent entities, fields, or values that are not present in the extracted "
        "JSON. Only use the JSON objects provided.\n\n"
        f"Extracted entity JSON:\n{stage2_payload}"
    )
    stage2_system, stage2_messages = build_messages(
        "",
        stage2_request,
        [],
        stage2_system_prompt,
    )

    # Stage 2 needs more tokens to return all matched entities across all pages
    # For large PDFs (100+ pages), this can require substantial output
    stage2_max_tokens = max(max_tokens * 5, 64000)  # At least 5x Stage 1 tokens, up to 64k

    stage2_response = invoke_bedrock(
        model_id=model_id,
        system_prompt=stage2_system,
        messages=stage2_messages,
        max_tokens=stage2_max_tokens,
        temperature=temperature,
    )

    stage2_output = stage2_response["content"][0]["text"]

    # Augment with coordinate information and transform to DocumentAnalysis
    stage2_output_augmented = stage2_output
    document_analysis_json = None

    try:
        # Parse the stage2 JSON output to extract entities
        stage2_json = _extract_json_candidate(stage2_output)

        if stage2_json and isinstance(stage2_json, dict):
            entities = stage2_json.get("entities", [])

            if isinstance(entities, list) and entities:
                print(f"Attaching coordinates to {len(entities)} entity groups")
                # Attach coords by reopening PDF (most reliable for rotated pages)
                attach_coords_to_matched_entities(entities, pdf_bytes, all_stage1_outputs)

            # Transform to DocumentAnalysis format
            # Extract document_id from event if available, otherwise use placeholder
            document_id = event.get("document_id", "anonymous-document")
            page_count = len(pages)

            print(f"Transforming to DocumentAnalysis format (documentId: {document_id}, pageCount: {page_count})")
            document_analysis = to_document_analysis(
                document_id,
                page_count,
                stage2_json,
                stage1_outputs=all_stage1_outputs,
                pages_metadata=pages
            )
            document_analysis_json = json.dumps(document_analysis)

            # Keep the raw matched_entities for backwards compatibility (optional)
            stage2_output_augmented = json.dumps(stage2_json)
        else:
            print("WARNING: Could not parse stage2 output as JSON (possibly truncated), returning without coords")
    except Exception as e:
        print(f"ERROR: Error augmenting coordinates or transforming to DocumentAnalysis: {e}")
        import traceback
        traceback.print_exc()
        # Fall back to original output without coords

    return {
        "statusCode": 200,
        "body": {
            "answer": document_analysis_json if document_analysis_json else stage2_output_augmented,
            "stage1_answer": stage1_output,
            "stage2_answer": stage2_output_augmented,
            "stage1_usage": {"total_pages": len(pages)},
            "stage2_usage": stage2_response.get("usage", {}),
            "stage1_stop_reason": "completed_all_pages",
            "stage2_stop_reason": stage2_response.get("stop_reason"),
            "model_id": model_id,
        },
    }
