import json
import os
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import boto3
import fitz  # PyMuPDF

# Import CSV parsing utilities
from csv_parser import parse_and_normalize_csv, group_by_category
from models import InvoiceLineItem

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
    "\n\nIMPORTANT: Return ONLY valid JSON with no explanations or commentary. "
    "Ensure proper JSON syntax: no trailing commas, all property names in double quotes.\n"
    "\n\nOutput must be a single JSON object: {page_number: <int>, entities: <array of objects>}. "
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
    "\n\nIMPORTANT: Return ONLY the JSON object with no explanations, no introductory text, and no commentary.\n"
    "Do not add any text before or after the JSON.\n"
    "\n\nOutput must be JSON: "
    "{entities: [{name: <string>, pages: <array of page numbers>, objects: <array of entity objects from Stage 1>}]}. "
    "Each group must include the canonical name, ALL page numbers where that entity appears, and ALL the Stage 1 entity objects."
)


def _normalize_name_for_matching(name: str) -> str:
    """
    Normalize a name for matching by:
    - Converting to lowercase
    - Removing punctuation (periods, commas)
    - Removing extra whitespace
    - Sorting words alphabetically (to handle "First Last" vs "Last, First")

    Args:
        name: Name string to normalize

    Returns:
        Normalized name string for comparison
    """
    if not name:
        return ""

    # Convert to lowercase and remove punctuation
    normalized = name.lower()
    for char in ".,;:()":
        normalized = normalized.replace(char, "")

    # Split into words and sort alphabetically
    words = normalized.split()
    words = [w for w in words if w]  # Remove empty strings
    words.sort()

    return " ".join(words)


def _names_match(name1: str, name2: str) -> bool:
    """
    Check if two names match, accounting for:
    - Name order variations (First Last vs Last, First)
    - Punctuation differences
    - Case differences

    Args:
        name1: First name to compare
        name2: Second name to compare

    Returns:
        True if names match, False otherwise
    """
    return _normalize_name_for_matching(name1) == _normalize_name_for_matching(name2)


def _reconstruct_entity_objects(stage2_groups: List[Dict], stage1_outputs: List[Dict]) -> List[Dict]:
    """
    Reconstruct full entity objects from Stage 1 data after Stage 2 grouping.
    Stage 2 only returns {name, pages}, but we need to attach the full objects.

    IMPORTANT: Filters objects to only include those matching the group name.
    This prevents matching all employees when looking for a specific employee.
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

        # Collect entity objects from the pages that match this group's name
        all_objects = []
        for page_num in group_pages:
            page_entities = page_to_entities.get(page_num, [])
            for entity in page_entities:
                if not isinstance(entity, dict):
                    continue

                # FILTER: Only include entities whose name matches this group
                entity_name = entity.get("name", "")
                if entity_name and _names_match(entity_name, group_name):
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


def download_from_s3(uri: str) -> bytes:
    """
    Download a file from S3 and return its bytes.

    Args:
        uri: S3 URI (s3://bucket/key or https://bucket.s3.amazonaws.com/key)

    Returns:
        File contents as bytes
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
        if total_size > 0 and downloaded % (5 * 1024 * 1024) == 0:  # Log every 5MB
            print(f"Downloaded {downloaded}/{total_size} bytes ({downloaded/total_size*100:.1f}%)")

    print(f"Download complete ({len(content)} bytes)")
    return content


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

    # Try to find the matching closing brace by counting nested braces
    # This handles cases where there's explanatory text after the JSON
    brace_count = 0
    in_string = False
    escape_next = False
    end = -1

    for i in range(start, len(text)):
        char = text[i]

        # Handle string escaping
        if escape_next:
            escape_next = False
            continue
        if char == '\\':
            escape_next = True
            continue

        # Track if we're inside a string
        if char == '"':
            in_string = not in_string
            continue

        # Only count braces outside of strings
        if not in_string:
            if char == '{' or char == '[':
                brace_count += 1
            elif char == '}' or char == ']':
                brace_count -= 1
                if brace_count == 0:
                    end = i
                    break

    if end == -1 or end <= start:
        # Fallback to old method
        end = max(text.rfind("}"), text.rfind("]"))
        if end == -1 or end <= start:
            return None

    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        # Debug: print what we tried to parse
        print(f"JSON parse error: {e}")
        print(f"Attempted to parse from position {start} to {end}")

        # Show context around the error position if available
        if hasattr(e, 'pos'):
            error_pos = e.pos
            context_start = max(0, start + error_pos - 100)
            context_end = min(len(text), start + error_pos + 100)
            print(f"Error at character {error_pos} in extracted JSON")
            print(f"Context around error:")
            print(text[context_start:context_end])
            print(" " * (start + error_pos - context_start) + "^")
        else:
            print(f"Substring preview: {text[start:min(start+200, end+1)]}...")
            print(f"Substring end: ...{text[max(start, end-200):end+1]}")

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


def _extract_pages_from_pdf(pdf_bytes: bytes) -> Tuple[List[Dict], List[Dict]]:
    """
    Extract pages from PDF bytes using PyMuPDF.

    Returns:
        Tuple of (pages, text_coords_cache)
        - pages: List of dicts with 'text' and 'image_bytes' for LLM processing
        - text_coords_cache: List of PyMuPDF text+coords dicts for coordinate lookup
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

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

        # Extract text with coordinates for later lookup
        text_dict = page.get_text("dict")
        text_dict["rotation"] = page.rotation
        text_coords_cache.append(text_dict)

    doc.close()
    return pages, text_coords_cache


def _run_stage1_on_pages(
    pages: List[Dict],
    model_id: str,
    max_tokens: int,
    temperature: float,
    user_request: str,
    few_shot_examples: List[Dict],
    stage1_system_prompt: str
) -> List[Dict]:
    """
    Run Stage 1 entity extraction on all pages.

    Returns:
        List of stage1 outputs (one per page)
    """
    def process_page(page_data):
        i, page = page_data
        print(f"  Processing page {i+1}/{len(pages)}...")
        page_text = page["text"]

        # If page text is empty, use Textract fallback
        if not page_text or not page_text.strip():
            if page.get("image_bytes"):
                print(f"  Page {i+1} has no extractable text, using Textract")
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
                    return i, _normalize_stage1_output(i + 1, output_text)
                except Exception as textract_error:
                    print(f"  Textract failed for page {i+1}: {textract_error}")
                    return i, {"page_number": i + 1, "page_type": "unknown", "entities": []}
            else:
                return i, {"page_number": i + 1, "page_type": "unknown", "entities": []}

        # Build Stage 1 prompt
        stage1_system, stage1_messages = build_messages(
            page_text,
            f"{user_request} (Page {i+1} of {len(pages)})",
            few_shot_examples,
            stage1_system_prompt,
        )

        # Invoke Bedrock
        response = invoke_bedrock(
            model_id=model_id,
            system_prompt=stage1_system,
            messages=stage1_messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        output_text = response["content"][0]["text"]

        # If LLM failed to detect entities, use Textract fallback
        if _llm_failed_to_detect_entities(output_text) and page.get("image_bytes"):
            try:
                print(f"  Falling back to Textract for page {i+1}")
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
                print(f"  Textract fallback failed for page {i+1}: {textract_error}")

        return i, _normalize_stage1_output(i + 1, output_text)

    # Process pages concurrently
    all_stage1_outputs = [None] * len(pages)
    with ThreadPoolExecutor(max_workers=min(len(pages), 3)) as executor:
        futures = {
            executor.submit(process_page, (i, page)): i for i, page in enumerate(pages)
        }
        for future in as_completed(futures):
            i, output = future.result()
            all_stage1_outputs[i] = output

    return all_stage1_outputs


def _run_stage2_grouping(
    stage1_outputs: List[Dict],
    model_id: str,
    max_tokens: int,
    temperature: float,
    stage2_system_prompt: str
) -> List[Dict]:
    """
    Run Stage 2 entity grouping on Stage 1 outputs.

    Returns:
        List of entity groups with {name, pages, objects}
    """
    stage2_payload = json.dumps(_prepare_stage2_payload(stage1_outputs))
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

    # Stage 2 needs more tokens for large PDFs
    stage2_max_tokens = max(max_tokens * 5, 64000)

    stage2_response = invoke_bedrock(
        model_id=model_id,
        system_prompt=stage2_system,
        messages=stage2_messages,
        max_tokens=stage2_max_tokens,
        temperature=temperature,
    )

    stage2_output = stage2_response["content"][0]["text"]

    # Debug: Print first 500 chars of Stage 2 output
    print(f"Stage 2 output preview (first 500 chars):")
    print(stage2_output[:500])
    print(f"Stage 2 output length: {len(stage2_output)} chars")
    print(f"Stage 2 stop_reason: {stage2_response.get('stop_reason', 'unknown')}")

    # Parse Stage 2 JSON
    stage2_json = _extract_json_candidate(stage2_output)

    if stage2_json and isinstance(stage2_json, dict):
        entities = stage2_json.get("entities", [])
        if isinstance(entities, list):
            print(f"Successfully parsed Stage 2: found {len(entities)} entity groups")
            # Reconstruct full entity objects from Stage 1 data
            return _reconstruct_entity_objects(entities, stage1_outputs)
        else:
            print(f"WARNING: Stage 2 'entities' field is not a list: {type(entities)}")
    else:
        print(f"WARNING: Stage 2 output could not be parsed as JSON dict, got type: {type(stage2_json)}")
        print(f"Stage 2 output last 500 chars:")
        print(stage2_output[-500:])

    print("WARNING: Stage 2 output could not be parsed, returning empty list")
    return []


def _parse_date(date_str: str) -> Optional[datetime]:
    """Parse various date formats."""
    if not date_str:
        return None

    from datetime import datetime
    date_str = str(date_str).strip()

    for fmt in ["%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y", "%Y/%m/%d"]:
        try:
            return datetime.strptime(date_str, fmt)
        except:
            pass
    return None


def _extract_pay_records(obj: Dict) -> List[Dict]:
    """
    Extract pay records from entity with date, amount, hours, rate.
    Returns list of {date, date_str, amount, hours, rate, source_page, source_type}
    """
    records = []

    if not isinstance(obj, dict):
        return records

    page_num = obj.get("page_number", "?")

    # Type 1: Paystub with pay_date and gross_pay
    if "pay_date" in obj and "gross_pay" in obj:
        pay_date = _parse_date(obj["pay_date"])
        if pay_date:
            records.append({
                "date": pay_date,
                "date_str": obj["pay_date"],
                "amount": float(obj["gross_pay"]),
                "hours": obj.get("hours_worked"),
                "rate": obj.get("hourly_rate"),
                "source_page": page_num,
                "source_type": "paystub"
            })

    # Type 2: Summary with dates array and earnings array
    if "dates" in obj and "earnings" in obj:
        dates = obj["dates"]
        earnings = obj["earnings"]
        hours_list = obj.get("hours", [])
        rate = obj.get("rate")

        if isinstance(dates, list) and isinstance(earnings, list):
            for i, date_str in enumerate(dates):
                pay_date = _parse_date(date_str)
                if pay_date and i < len(earnings):
                    records.append({
                        "date": pay_date,
                        "date_str": date_str,
                        "amount": float(earnings[i]),
                        "hours": hours_list[i] if i < len(hours_list) else None,
                        "rate": rate,
                        "source_page": page_num,
                        "source_type": "summary"
                    })

    # Type 3: Pay periods array
    if "pay_periods" in obj and isinstance(obj["pay_periods"], list):
        for period in obj["pay_periods"]:
            if isinstance(period, dict):
                date_str = period.get("date") or period.get("pay_date")
                pay_date = _parse_date(date_str)

                if pay_date:
                    amount = period.get("total_pay") or period.get("gross_pay")
                    if amount:
                        records.append({
                            "date": pay_date,
                            "date_str": date_str,
                            "amount": float(amount),
                            "hours": period.get("total_hours") or period.get("hours_worked"),
                            "rate": period.get("rate") or period.get("hourly_rate"),
                            "source_page": page_num,
                            "source_type": "pay_period"
                        })

    return records


def _generate_reconciliation_report(
    csv_line_items: List[InvoiceLineItem],
    document_analysis: Dict
) -> Dict:
    """
    Generate a reconciliation report comparing CSV invoice amounts to PDF supporting docs.

    For SALARY items: validates that pay records match CSV amounts by:
    - Filtering pay records to reporting period
    - Deduplicating by pay date
    - Validating hours × rate = amount
    - Flagging inconsistencies between different PDF representations

    For non-SALARY items: checks if supporting documentation exists

    Args:
        csv_line_items: List of CSV line items
        document_analysis: DocumentAnalysis output from to_document_analysis

    Returns:
        Dict with reconciliation results:
        {
            "summary": {counts of matched/mismatched/missing},
            "line_items": [detailed status for each line item]
        }
    """
    from datetime import datetime
    from collections import defaultdict

    groups = document_analysis.get("groups", [])

    # Build lookup: csv_line_number -> group
    line_to_group = {}
    for group in groups:
        meta = group.get("meta", {})
        csv_line = meta.get("csv_line_number")
        if csv_line:
            line_to_group[csv_line] = group

    # Analyze each CSV line item
    line_item_results = []

    perfect_matches = 0
    amount_mismatches = 0
    missing_data = 0
    has_issues = 0

    for csv_item in csv_line_items:
        line_num = csv_item.line_number
        category = csv_item.budget_item
        csv_amount = csv_item.amount

        group = line_to_group.get(line_num)

        result = {
            "csv_line_number": line_num,
            "entity_name": csv_item.entity_name,
            "category": category,
            "csv_amount": csv_amount,
            "reporting_period": csv_item.raw_row.get("Reporting Period"),
            "status": "unknown",
            "pdf_amount": 0,
            "difference": 0,
            "issues": []
        }

        if not group:
            result["status"] = "no_match"
            result["issues"].append("No matching PDF group found")
            missing_data += 1
            line_item_results.append(result)
            continue

        # For SALARY category: detailed pay record validation
        if category == "SALARY":
            meta = group.get("meta", {})
            matched_objects = meta.get("matched_entity_objects", [])
            reporting_period_str = csv_item.raw_row.get("Reporting Period", "")
            reporting_period_date = _parse_date(reporting_period_str)

            # Extract all pay records
            all_records = []
            for obj in matched_objects:
                records = _extract_pay_records(obj)
                all_records.extend(records)

            # Filter to reporting period
            period_records = [r for r in all_records
                              if reporting_period_date and r["date"].month == reporting_period_date.month
                              and r["date"].year == reporting_period_date.year]

            if not period_records:
                result["status"] = "missing_data"
                result["issues"].append("No pay records found for reporting period")
                missing_data += 1
            else:
                # Group by date and check for inconsistencies
                by_date = defaultdict(list)
                for record in period_records:
                    by_date[record["date_str"]].append(record)

                # Deduplicate and validate
                validated_amounts = []
                date_details = []

                for date_str in sorted(by_date.keys()):
                    records = by_date[date_str]
                    amounts = set(r["amount"] for r in records)

                    date_detail = {
                        "date": date_str,
                        "representations": []
                    }

                    for record in records:
                        rep = {
                            "source_type": record["source_type"],
                            "source_page": record["source_page"],
                            "amount": record["amount"]
                        }

                        # Validate hours × rate
                        if record["hours"] and record["rate"]:
                            expected = float(record["hours"]) * float(record["rate"])
                            diff = abs(expected - record["amount"])
                            rep["hours"] = record["hours"]
                            rep["rate"] = record["rate"]
                            rep["expected_amount"] = expected

                            if diff >= 0.02:
                                rep["has_calc_error"] = True
                                result["issues"].append(f"Date {date_str} {record['source_type']}: calc error (${diff:.2f} difference)")

                        date_detail["representations"].append(rep)

                    # Check for inconsistent amounts
                    if len(amounts) > 1:
                        date_detail["inconsistent"] = True
                        result["issues"].append(f"Date {date_str}: inconsistent amounts across representations: {sorted(amounts)}")

                        # Use paystub if available, else first amount
                        paystub_amounts = [r["amount"] for r in records if r["source_type"] == "paystub"]
                        validated_amounts.append(paystub_amounts[0] if paystub_amounts else min(amounts))
                    else:
                        validated_amounts.append(list(amounts)[0])

                    date_details.append(date_detail)

                # Calculate total
                total_pdf = sum(validated_amounts)
                diff = abs(csv_amount - total_pdf)

                result["pdf_amount"] = total_pdf
                result["difference"] = diff
                result["date_details"] = date_details

                if diff < 0.02 and not result["issues"]:
                    result["status"] = "match"
                    perfect_matches += 1
                elif diff < 0.02:
                    result["status"] = "match_with_issues"
                    has_issues += 1
                else:
                    result["status"] = "amount_mismatch"
                    result["issues"].insert(0, f"Amount mismatch: CSV ${csv_amount:.2f} vs PDF ${total_pdf:.2f}")
                    amount_mismatches += 1

        else:
            # For non-SALARY items: just check if supporting pages exist
            supporting_pages = group.get("supportingPages", [])

            result["supporting_pages"] = supporting_pages
            result["supporting_page_count"] = len(supporting_pages)

            if not supporting_pages:
                result["status"] = "missing_data"
                result["issues"].append("No supporting pages found")
                missing_data += 1
            else:
                result["status"] = "has_support"
                result["pdf_amount"] = None  # Can't extract amounts from non-salary items

        line_item_results.append(result)

    return {
        "summary": {
            "total_line_items": len(csv_line_items),
            "perfect_matches": perfect_matches,
            "amount_mismatches": amount_mismatches,
            "missing_data": missing_data,
            "has_issues": has_issues
        },
        "line_items": line_item_results
    }


def _run_stage3_csv_matching(
    category: str,
    csv_items: List[InvoiceLineItem],
    entity_groups: List[Dict],
    stage1_outputs: List[Dict],
    model_id: str,
    max_tokens: int,
    temperature: float
) -> Dict:
    """
    Run Stage 3: Match CSV line items to entity groups for a specific category.

    Returns:
        Dict with {category, csv_to_group_matches, unmatched_csv_lines, unmatched_pdf_groups}
    """
    # Create page summaries for this PDF
    page_summaries = []
    for page_output in stage1_outputs:
        if not isinstance(page_output, dict):
            continue
        page_num = page_output.get("page_number")
        entities = page_output.get("entities", [])
        entity_names = []
        for e in entities:
            if isinstance(e, dict):
                name = e.get("name") or e.get("type", "unknown")
                entity_names.append(name)
        page_summaries.append({
            "page_number": page_num,
            "entities_found": entity_names,
            "summary": page_output.get("summary", "")[:200]  # Truncate long summaries
        })

    # Build Stage 3 prompt
    STAGE3_SYSTEM_PROMPT = f"""You are a precise entity matching assistant for the {category} budget category.

Your task is to match CSV invoice line items to entity groups detected in the PDF supporting documentation.

Output JSON format:
{{
  "category": "{category}",
  "csv_to_group_matches": [
    {{
      "csv_line_number": <int>,
      "csv_entity_name": "<name or null>",
      "matched_group_names": ["<PDF entity group name>"],
      "matched_group_pages": [1, 2, 3],
      "match_confidence": "high|medium|low|none",
      "match_reasoning": "<brief explanation>"
    }}
  ],
  "unmatched_csv_lines": [<line numbers>],
  "unmatched_pdf_groups": ["<group names>"]
}}

IMPORTANT:
- Only use entities and page numbers that exist in the provided data
- Do not invent or hallucinate matches
- If no match found, set matched_group_names to [] and match_confidence to "none"
"""

    stage3_prompt = f"""CSV Line Items for {category} category (to be verified):
{json.dumps([item.to_dict() for item in csv_items], indent=2)}

Entity Groups Found in {category} PDF:
{json.dumps(entity_groups, indent=2)}

PDF Page Summaries ({category} PDF):
{json.dumps(page_summaries, indent=2)}

MATCHING RULES FOR {category}:

FOR SALARY CATEGORY (employee line items):
1. Match by entity name to detected entity groups, considering name variations:
   - Name order variations (FirstName LastName vs LastName, FirstName)
   - Punctuation differences (periods, commas, hyphens)
   - Middle initials (with or without)
2. matched_group_names should contain the PDF entity group names
3. Each CSV line item should match to exactly one person (or set of name variations)

FOR NON-SALARY CATEGORIES (FRINGE, EQUIPMENT, OTHER, etc.):
1. These line items typically do NOT have entity names (no person names)
2. Match by identifying which PDF pages contain supporting documentation:
   - Look for pages with expense descriptions matching the CSV description
   - Look for pages with itemized breakdowns or totals
   - Look for pages with relevant category headers
3. matched_group_names should typically be [] (empty - no person entities for non-salary items)
4. matched_group_pages should list ALL pages where supporting documentation appears

Match each CSV line item to the appropriate entity groups or pages."""

    stage3_messages = [{
        "role": "user",
        "content": stage3_prompt
    }]

    # Invoke Bedrock
    stage3_response = invoke_bedrock(
        model_id=model_id,
        system_prompt=STAGE3_SYSTEM_PROMPT,
        messages=stage3_messages,
        max_tokens=max_tokens,
        temperature=temperature
    )

    stage3_output = stage3_response["content"][0]["text"]

    # Parse Stage 3 JSON
    csv_matches = _extract_json_candidate(stage3_output)

    if csv_matches and isinstance(csv_matches, dict):
        return csv_matches

    # Fallback if parsing fails
    print(f"WARNING: Stage 3 output could not be parsed for category {category}")
    return {
        "category": category,
        "csv_to_group_matches": [],
        "unmatched_csv_lines": [item.line_number for item in csv_items],
        "unmatched_pdf_groups": [eg.get("name") for eg in entity_groups if isinstance(eg, dict)]
    }


def _csv_to_document_analysis_multi_pdf(
    csv_line_items: List[InvoiceLineItem],
    pdf_results: Dict[str, Dict],
    all_csv_matches: Dict[str, Dict]
) -> Dict:
    """
    Transform CSV line items and multi-PDF results into DocumentAnalysis format.

    With multi-PDF approach, each CSV line item maps to pages in a specific category PDF.
    Page numbers need to be offset to avoid conflicts when merging PDFs.

    Args:
        csv_line_items: All CSV line items
        pdf_results: Dict mapping category -> {stage1_outputs, entity_groups, pages_metadata, pdf_bytes, page_count}
        all_csv_matches: Dict mapping category -> Stage 3 match results

    Returns:
        DocumentAnalysis dict with merged pages and CSV-based groups
    """
    # Merge all pages from all PDFs into a single document with page offsets
    all_pages_metadata = []
    page_offset_by_category = {}
    current_page_offset = 0

    # First pass: collect all pages and calculate offsets
    for category in sorted(pdf_results.keys()):  # Sort for consistent ordering
        page_offset_by_category[category] = current_page_offset
        category_pages = pdf_results[category]["pages_metadata"]

        # Adjust page numbers with offset
        for page_meta in category_pages:
            adjusted_page = page_meta.copy()
            adjusted_page["pageNumber"] = page_meta["pageNumber"] + current_page_offset
            adjusted_page["category"] = category  # Add category metadata
            all_pages_metadata.append(adjusted_page)

        current_page_offset += len(category_pages)

    # Create analysis structure
    analysis = {
        "schemaVersion": "1.0",
        "documentId": "csv-reconciliation-multi-pdf",
        "pageCount": len(all_pages_metadata),
        "pages": all_pages_metadata,
        "groups": [],
        "meta": {
            "categories": list(pdf_results.keys()),
            "page_offset_by_category": page_offset_by_category
        }
    }

    # Process matches for each category
    for category, csv_matches in all_csv_matches.items():
        # Skip if no PDF for this category
        if category not in pdf_results:
            # Create groups for unmatched CSV items (no PDF provided)
            for match in csv_matches.get("csv_to_group_matches", []):
                csv_line_num = match.get("csv_line_number")
                if csv_line_num:
                    # Find the CSV item with this line number
                    csv_item = next((item for item in csv_line_items if item.line_number == csv_line_num), None)
                    if not csv_item:
                        continue
                    group_id = f"csv_{category}_{csv_line_num}"
                    label = csv_item.entity_name or f"{csv_item.budget_item}"
                    if not csv_item.entity_name and csv_item.description:
                        label += f": {csv_item.description[:50]}"

                    group = {
                        "groupId": group_id,
                        "label": label,
                        "kind": "csv_line_item",
                        "summaryPages": [],
                        "supportingPages": [],  # No PDF provided
                        "occurrences": [],
                        "meta": {
                            "csv_line_number": csv_line_num,
                            "csv_entity_name": csv_item.entity_name,
                            "csv_budget_item": csv_item.budget_item,
                            "csv_category": category,
                            "csv_amount": csv_item.amount,
                            "csv_unit": csv_item.unit,
                            "csv_description": csv_item.description,
                            "match_confidence": "none",
                            "warning": f"No PDF provided for category {category}"
                        }
                    }
                    analysis["groups"].append(group)
            continue

        # Get PDF results and page offset for this category
        pdf_result = pdf_results[category]
        entity_groups = pdf_result["entity_groups"]
        stage1_outputs = pdf_result["stage1_outputs"]
        pdf_bytes = pdf_result["pdf_bytes"]
        page_offset = page_offset_by_category[category]

        # Build entity group lookup for this category
        entity_group_lookup = {eg.get("name"): eg for eg in entity_groups if isinstance(eg, dict)}

        # Process each CSV line item match for this category
        for match in csv_matches.get("csv_to_group_matches", []):
            csv_line_num = match.get("csv_line_number")
            if not csv_line_num:
                continue

            # Find the CSV item with this line number (can't use as array index because we skip rows)
            csv_item = next((item for item in csv_line_items if item.line_number == csv_line_num), None)
            if not csv_item:
                print(f"WARNING: CSV line {csv_line_num} not found in parsed items")
                continue

            # Get matched entity groups and their pages
            matched_groups = match.get("matched_group_names", [])
            category_pages = []  # Pages within this category's PDF (1-indexed)
            matched_entity_objects = []

            if category == "SALARY" and matched_groups:
                # SALARY: Pull pages from matched entity groups
                for group_name in matched_groups:
                    entity_group = entity_group_lookup.get(group_name)
                    if entity_group:
                        pages = entity_group.get("pages", [])
                        category_pages.extend(pages)
                        objects = entity_group.get("objects", [])
                        matched_entity_objects.extend(objects)
            else:
                # Non-SALARY: Use pages directly from Stage 3 match
                category_pages = match.get("matched_group_pages", [])

            # Apply page offset to translate to global page numbers
            global_pages = [p + page_offset for p in category_pages]
            global_pages = sorted(set(global_pages))  # Remove duplicates and sort

            # Create group from CSV line item
            group_id = f"csv_{category}_{csv_line_num}"

            # Label: entity name for SALARY, budget item + description for others
            if category == "SALARY":
                label = csv_item.entity_name or f"Unknown Employee (Line {csv_line_num})"
            else:
                label = f"{csv_item.budget_item}"
                if csv_item.description:
                    label += f": {csv_item.description[:50]}"

            group = {
                "groupId": group_id,
                "label": label,
                "kind": "csv_line_item",
                "summaryPages": [],  # No summary pages - CSV is the summary
                "supportingPages": global_pages,  # Global page numbers
                "occurrences": [],  # Will be populated by coordinate attachment
                "meta": {
                    "csv_line_number": csv_line_num,
                    "csv_entity_name": csv_item.entity_name,
                    "csv_budget_item": csv_item.budget_item,
                    "csv_category": category,
                    "csv_amount": csv_item.amount,
                    "csv_unit": csv_item.unit,
                    "csv_description": csv_item.description,
                    "csv_raw_row": csv_item.raw_row,
                    "match_confidence": match.get("match_confidence"),
                    "matched_group_names": matched_groups,
                    "matched_entity_objects": matched_entity_objects,
                    "match_reasoning": match.get("match_reasoning"),
                    "category_pages": category_pages,  # Original pages within category PDF
                    "page_offset": page_offset  # For debugging/reference
                }
            }

            analysis["groups"].append(group)

    print(f"Created {len(analysis['groups'])} groups from {len(csv_line_items)} CSV line items")
    return analysis


def _handle_multi_pdf_csv_mode(event, context):
    """
    Handle multi-PDF CSV reconciliation mode.

    Process:
    1. Download and parse CSV master invoice
    2. Group CSV line items by budget category
    3. For each PDF upload:
       - Run Stage 1 (entity extraction) independently
       - Run Stage 2 (entity grouping) independently
    4. For each category with both CSV items and PDF:
       - Run Stage 3 (CSV-to-entity matching)
    5. Merge all PDFs into single virtual document with page offsets
    6. Transform to DocumentAnalysis format
    """
    print("=== Multi-PDF CSV Reconciliation Mode ===")

    # Extract parameters
    csv_s3_uri = event.get("csv_s3_uri")
    csv_content = event.get("csv_content")
    pdf_uploads = event.get("pdf_uploads")  # List of {category, s3_uri} or {category, local_path}

    model_id = event.get("model_id", DEFAULT_MODEL_ID)
    max_tokens = int(event.get("max_tokens", 16000))
    temperature = float(event.get("temperature", 0.0))
    user_request = event.get("question", "Apply the stage 1 instructions to produce the entity JSON.")
    few_shot_examples = event.get("few_shot_examples") or []

    # Step 1: Download and parse CSV
    print("\n=== Step 1: Parsing CSV ===")
    csv_line_items = []

    if csv_s3_uri:
        csv_bytes = download_from_s3(csv_s3_uri)
        csv_line_items = parse_and_normalize_csv(csv_bytes)
    elif csv_content:
        csv_bytes = base64.b64decode(csv_content)
        csv_line_items = parse_and_normalize_csv(csv_bytes)
    else:
        raise ValueError("csv_s3_uri or csv_content required for CSV mode")

    print(f"\nParsed {len(csv_line_items)} CSV line items")

    # Group CSV line items by budget category
    csv_by_category = group_by_category(csv_line_items)
    print(f"CSV line items by category:")
    for category, items in csv_by_category.items():
        print(f"  {category}: {len(items)} items")

    # Step 2: Process each PDF independently (Stage 1 & 2)
    print("\n=== Step 2: Processing PDFs (Stage 1 & 2) ===")
    pdf_results = {}  # category -> {stage1_outputs, entity_groups, pages_metadata, pdf_bytes, page_count}

    for pdf_upload in pdf_uploads:
        category = pdf_upload.get("category")
        s3_uri = pdf_upload.get("s3_uri")
        local_path = pdf_upload.get("local_path")

        if not category:
            print("WARNING: PDF upload missing category field, skipping")
            continue

        print(f"\n--- Processing PDF for category: {category} ---")

        # Download or load PDF
        if local_path:
            print(f"Loading local PDF: {local_path}")
            with open(local_path, "rb") as f:
                pdf_bytes = f.read()
        elif s3_uri:
            pdf_bytes = download_from_s3(s3_uri)
        else:
            print(f"WARNING: PDF upload for {category} missing s3_uri or local_path, skipping")
            continue

        # Extract pages using PyMuPDF
        pages, text_coords_cache = _extract_pages_from_pdf(pdf_bytes)
        print(f"Extracted {len(pages)} pages")

        # Run Stage 1: Entity extraction (per page)
        stage1_outputs = _run_stage1_on_pages(
            pages,
            model_id=model_id,
            max_tokens=max_tokens,
            temperature=temperature,
            user_request=user_request,
            few_shot_examples=few_shot_examples,
            stage1_system_prompt=DEFAULT_STAGE1_SYSTEM_PROMPT
        )

        # Run Stage 2: Entity grouping
        entity_groups = _run_stage2_grouping(
            stage1_outputs,
            model_id=model_id,
            max_tokens=max_tokens,
            temperature=temperature,
            stage2_system_prompt=DEFAULT_STAGE2_SYSTEM_PROMPT
        )

        # Create pages metadata
        pages_metadata = []
        for i in range(len(pages)):
            pages_metadata.append({
                "pageNumber": i + 1,
                "category": category
            })

        # Store results for this category
        pdf_results[category] = {
            "stage1_outputs": stage1_outputs,
            "entity_groups": entity_groups,
            "pages_metadata": pages_metadata,
            "pdf_bytes": pdf_bytes,
            "page_count": len(pages)
        }

        print(f"Category {category}: Found {len(entity_groups)} entity groups across {len(pages)} pages")

    # Step 3: Run Stage 3 matching (CSV to entity groups) per category
    print("\n=== Step 3: Matching CSV to Entity Groups ===")
    all_csv_matches = {}  # category -> Stage 3 match results

    for category, csv_items in csv_by_category.items():
        # Check if we have a PDF for this category
        if category not in pdf_results:
            print(f"WARNING: No PDF provided for category {category} with {len(csv_items)} CSV line items")
            # Store empty match results
            all_csv_matches[category] = {
                "category": category,
                "csv_to_group_matches": [],
                "unmatched_csv_lines": [item.line_number for item in csv_items],
                "unmatched_pdf_groups": []
            }
            continue

        print(f"\n--- Matching CSV items for category: {category} ---")

        pdf_result = pdf_results[category]
        entity_groups = pdf_result["entity_groups"]
        stage1_outputs = pdf_result["stage1_outputs"]

        # Run Stage 3 matching
        csv_matches = _run_stage3_csv_matching(
            category=category,
            csv_items=csv_items,
            entity_groups=entity_groups,
            stage1_outputs=stage1_outputs,
            model_id=model_id,
            max_tokens=max_tokens * 2,  # Stage 3 needs more tokens
            temperature=temperature
        )

        all_csv_matches[category] = csv_matches
        print(f"Category {category}: Matched {len(csv_matches.get('csv_to_group_matches', []))} CSV line items")

    # Step 4: Transform to DocumentAnalysis with page offsets
    print("\n=== Step 4: Building DocumentAnalysis ===")
    document_analysis = _csv_to_document_analysis_multi_pdf(
        csv_line_items=csv_line_items,
        pdf_results=pdf_results,
        all_csv_matches=all_csv_matches
    )

    document_analysis_json = json.dumps(document_analysis)

    # Step 5: Generate reconciliation report
    print("\n=== Step 5: Generating Reconciliation Report ===")
    reconciliation_report = _generate_reconciliation_report(
        csv_line_items=csv_line_items,
        document_analysis=document_analysis
    )

    print(f"Reconciliation Summary:")
    print(f"  Perfect matches: {reconciliation_report['summary']['perfect_matches']}")
    print(f"  Amount mismatches: {reconciliation_report['summary']['amount_mismatches']}")
    print(f"  Missing data: {reconciliation_report['summary']['missing_data']}")
    print(f"  Has issues: {reconciliation_report['summary']['has_issues']}")

    # Return response
    return {
        "statusCode": 200,
        "body": {
            "answer": document_analysis_json,
            "reconciliation_report": reconciliation_report,
            "csv_metadata": {
                "total_line_items": len(csv_line_items),
                "csv_line_items": [item.to_dict() for item in csv_line_items],
                "categories": list(csv_by_category.keys())
            },
            "csv_matches_by_category": all_csv_matches,
            "pdf_categories_processed": list(pdf_results.keys()),
            "model_id": model_id,
        }
    }


def lambda_handler(event, context):
    """
    Multi-PDF CSV-based reconciliation handler.

    Supports two modes:
    1. NEW: Multi-PDF CSV reconciliation mode
       - csv_s3_uri or csv_content: Master invoice CSV
       - pdf_uploads: List of {category: str, s3_uri: str} or {category: str, local_path: str}

    2. LEGACY: Single PDF entity extraction mode (backward compatible)
       - s3_uri or local_pdf_path: Single PDF document

    Common parameters:
      - question: optional request text for stage 1
      - few_shot_examples: optional list of examples
      - max_tokens: optional override for response length
      - temperature: optional override for sampling temperature
      - model_id: optional model override
    """
    # Check for multi-PDF CSV mode
    csv_s3_uri = event.get("csv_s3_uri")
    csv_content = event.get("csv_content")  # Base64 encoded CSV content
    pdf_uploads = event.get("pdf_uploads")  # List of {category, s3_uri} or {category, local_path}

    # NEW: Multi-PDF CSV reconciliation mode
    if (csv_s3_uri or csv_content) and pdf_uploads:
        return _handle_multi_pdf_csv_mode(event, context)

    # LEGACY: Single PDF entity extraction mode (backward compatible)
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
