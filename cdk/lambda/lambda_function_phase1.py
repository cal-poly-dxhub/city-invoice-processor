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
    config=boto3.session.Config(retries={"max_attempts": 3}, read_timeout=300),
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
    "You are an entity resolver. Parse each page, dynamically inferring categories and ignoring "
    "header information. Differentiate between summaries and supporting documentation. For each "
    "page, identify its page_type (for example: summary/statement, paystub, supporting "
    "documentation, or other) and include page_type in the JSON output for that page so downstream "
    "matching can detect missing context. Output must be a single JSON object with this shape: "
    "{page_number: <int>, page_type: <string>, entities: <array of objects>}. "
    "\n\nCRITICAL: Pages often contain 4-6+ separate paystubs or records. You MUST extract EVERY SINGLE ONE.\n"
    "- Read through the ENTIRE page text before responding\n"
    "- Count how many distinct employee names appear - that's your minimum number of entities\n"
    "- If you see 'Salazar, Tomas' twice with different dates, create TWO separate entity objects\n"
    "- If you see 'Mutul, Rolando' three times with different pay periods, create THREE separate entity objects\n"
    "- Do NOT stop after 2-3 entities - pages typically have 4-8 entities\n"
    "- Each distinct paystub, timesheet, or record MUST be a separate entity object\n"
    "- Do NOT combine or skip any entities\n"
    "Each entity object must include all the fields you can infer for that specific paystub/record."
)
DEFAULT_STAGE2_SYSTEM_PROMPT = (
    "For each entity in this JSON object, match the data on the summary page(s) against the data "
    "in supporting documentation. Aggregate supporting data when needed to align with the summary. "
    "Use page_type annotations to determine whether missing data is from summaries/statements "
    "or from supporting documentation (e.g., paystubs). "
    "\n\nCRITICAL RULES FOR PAGE NUMBERS:\n"
    "- summary_pages and supporting_pages arrays MUST ONLY contain page_number values that actually appear "
    "in the stage 1 extracted JSON for that entity.\n"
    "- Do NOT invent, infer, or hallucinate page numbers.\n"
    "- Do NOT include a page number unless that exact entity name appears on that page in the input.\n"
    "- Match entities by name variations (e.g., 'Lydia I Candila' and 'Lydia I. Candila' are the same person).\n"
    "- If an entity appears on page 4 in stage 1, it MUST be in your output for page 4.\n"
    "- If an entity does NOT appear on page 2 in stage 1, do NOT include page 2 in your output.\n"
    "\nWhile assessing match quality, if dealing with timesheet hours, verify both the pay period dates and not just the "
    "final amounts. "
    "\nReturn structured JSON with two arrays: "
    "1) matched_entities: each item should include entity name/identifier, summary_pages (list of ALL page numbers "
    "where this entity appears in summaries), supporting_pages (list of ALL page numbers where this entity appears "
    "in supporting docs), summary_objects (array of stage 1 entity objects from summary pages), and supporting_objects "
    "(array of stage 1 entity objects from supporting pages). Include ALL pages and ALL objects for each matched entity. "
    "2) orphaned_entities: entities that lack a corresponding match (either summary without "
    "supporting proof or supporting data with no summary). Include the pages where each orphaned "
    "entity appears and the extracted JSON objects for those pages. Do not include narrative "
    "details about why it is missing. Always include all unmatched entities here, even if no "
    "matches were found in either direction. Explicitly include summary-only entities that lack "
    "supporting documentation and supporting-only entities that lack summaries. "
    "\nEnsure that matched_entities and orphaned_entities together cover ALL entities present across "
    "ALL pages in the stage 1 extracted JSON. Do not drop, skip, or omit any entities. Process every single "
    "entity from every page. "
    "Do not write a narrative summary; only return the JSON object."
)


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


def fetch_s3_pages(uri: str) -> Tuple[List[Dict[str, Optional[str]]], bytes]:
    """
    Fetches document from S3 and extracts pages.
    Returns: (pages, raw_pdf_bytes)
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
        for page_num in range(doc.page_count):
            page = doc[page_num]
            pixmap = page.get_pixmap(dpi=200)
            image_bytes = pixmap.tobytes("png")

            pages.append({
                "text": page.get_text(),
                "image_bytes": image_bytes,
            })

        doc.close()
        return pages, content

    return [{"text": content.decode("utf-8"), "image_bytes": None}], content


def _extract_json_candidate(output_text: str) -> Optional[object]:
    start = min(
        [pos for pos in (output_text.find("{"), output_text.find("[")) if pos != -1],
        default=-1,
    )
    if start == -1:
        return None
    end = max(output_text.rfind("}"), output_text.rfind("]"))
    if end == -1 or end <= start:
        return None
    try:
        return json.loads(output_text[start : end + 1])
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
            candidate["entities"] = [
                entity for entity in candidate["entities"] if isinstance(entity, dict)
            ]
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
    sanitized = []
    for page in stage1_outputs:
        if not isinstance(page, dict):
            continue
        page_number = page.get("page_number")
        page_type = page.get("page_type", "unknown")
        entities = page.get("entities") if isinstance(page.get("entities"), list) else []
        entities = [entity for entity in entities if isinstance(entity, dict)]
        payload = {
            "page_number": page_number,
            "page_type": page_type,
            "entities": entities,
        }
        if page.get("textract_fallback") is True:
            payload["textract_fallback"] = True
        sanitized.append(payload)
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
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "system": system_prompt,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
    )

    response = bedrock_runtime.invoke_model(
        modelId=model_id,
        contentType="application/json",
        accept="application/json",
        body=body,
    )

    return json.loads(response["body"].read())


def _compute_normalized_coords(rect, page_rect) -> Dict[str, float]:
    """
    Compute normalized bounding box coordinates.
    Returns coords as floats between 0 and 1 relative to page dimensions.

    Args:
        rect: PyMuPDF Rect object from search_for
        page_rect: PyMuPDF Rect object representing page bounds

    Returns:
        Dict with keys: x, y, width, height (all normalized 0-1)
    """
    return {
        "x": (rect.x0 - page_rect.x0) / page_rect.width,
        "y": (rect.y0 - page_rect.y0) / page_rect.height,
        "width": (rect.x1 - rect.x0) / page_rect.width,
        "height": (rect.y1 - rect.y0) / page_rect.height,
    }


def _search_textract_geometry(textract_geometry: List[Dict], search_text: str) -> List[Dict[str, float]]:
    """
    Search through Textract geometry data to find matching text.
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

    for item in textract_geometry:
        item_text = item.get("text", "").lower().strip()

        # Check for exact match or if search text is contained in this line
        if search_text_lower in item_text:
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
        # Try PyMuPDF search first
        page_index = page_number - 1
        if page_index >= 0 and page_index < doc.page_count:
            page = doc[page_index]
            page_rect = page.rect
            rects = page.search_for(search_text.strip())

            if rects:
                coords = []
                for rect in rects:
                    coord = _compute_normalized_coords(rect, page_rect)
                    coords.append(coord)
                return coords

            # If exact match failed, try searching for individual words
            # This handles cases where names are split across lines or text blocks
            words = search_text.strip().split()
            # Filter out very short words (like "I", "II") that cause false positives
            significant_words = [w for w in words if len(w) >= 3]
            if len(significant_words) >= 2:
                all_word_rects = []
                for word in significant_words:
                    word_rects = page.search_for(word)
                    all_word_rects.extend(word_rects)

                if all_word_rects:
                    # Group nearby rectangles (likely part of the same entity occurrence)
                    grouped_rects = _group_nearby_rects(all_word_rects, len(significant_words))
                    if grouped_rects:
                        coords = []
                        for group in grouped_rects:
                            # Compute bounding box that encompasses all rects in this group
                            combined_rect = _combine_rects(group)
                            coord = _compute_normalized_coords(combined_rect, page_rect)
                            coords.append(coord)
                        return coords

        # If no results from PyMuPDF, try Textract geometry
        if stage1_outputs:
            for page_data in stage1_outputs:
                if page_data.get("page_number") == page_number:
                    textract_geometry = page_data.get("textract_geometry")
                    if textract_geometry:
                        coords = _search_textract_geometry(textract_geometry, search_text)
                        if coords:
                            return coords
                    break

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

    matched_entities_list = matched_entities_root.get("matched_entities", [])
    if not isinstance(matched_entities_list, list):
        return analysis

    for group_idx, matched_entity in enumerate(matched_entities_list):
        if not isinstance(matched_entity, dict):
            continue

        # Generate unique group ID
        group_id = f"g_{group_idx}"

        # Extract group metadata
        label = matched_entity.get("name", f"Group {group_idx}")
        summary_pages = matched_entity.get("summary_pages", []) or []
        supporting_pages = matched_entity.get("supporting_pages", []) or []

        occurrences = []

        # Process coords attached directly to matched_entity
        entity_coords = matched_entity.get("coords", [])
        if isinstance(entity_coords, list):
            for coord_idx, coord in enumerate(entity_coords):
                if not isinstance(coord, dict):
                    continue

                page_number = coord.get("page_number")
                role = coord.get("role", "summary")

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
                "rawSummaryObjects": matched_entity.get("summary_objects"),
                "rawSupportingObjects": matched_entity.get("supporting_objects")
            }
        }

        analysis["groups"].append(group)

    return analysis


def attach_coords_to_matched_entities(
    pdf_bytes: bytes, matched_entities: List[Dict], stage1_outputs: List[Dict] = None
) -> None:
    """
    Augment matched_entities structure with coordinate information for entity names.
    Modifies the matched_entities list in-place by adding 'coords' fields.
    Uses PyMuPDF for searchable text and Textract geometry for scanned pages.

    Only entities with a "name" field will have coordinates attached. Other entities
    (like payment objects, dates, amounts) will not have coordinates.

    Args:
        pdf_bytes: Raw PDF file bytes
        matched_entities: List of matched entity dicts from stage 2
        stage1_outputs: Optional list of stage1 outputs containing textract_geometry
    """
    if not pdf_bytes or not matched_entities:
        return

    try:
        # Open PDF once for all coordinate searches
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")

        if doc.needs_pass:
            doc.authenticate("")

        # Process each matched entity group
        for entity_group in matched_entities:
            entity_name = entity_group.get("name", "Unknown")
            summary_pages = entity_group.get("summary_pages", [])
            supporting_pages = entity_group.get("supporting_pages", [])

            # Generate name variations for better matching
            name_variations = [entity_name]

            # If name is in "Last, First" format, try "First Last" too
            if "," in entity_name:
                parts = [p.strip() for p in entity_name.split(",")]
                if len(parts) == 2:
                    name_variations.append(f"{parts[1]} {parts[0]}")

            # Extract names from supporting_objects for additional variations
            supporting_objects = entity_group.get("supporting_objects", [])
            if isinstance(supporting_objects, list):
                for obj in supporting_objects:
                    if isinstance(obj, dict) and "name" in obj:
                        obj_name = obj["name"]
                        if obj_name and obj_name not in name_variations:
                            name_variations.append(obj_name)

            # Attach coordinates to entity name on each summary page
            entity_group["coords"] = []
            for page_num in summary_pages:
                # Try all name variations until we find a match
                for name_var in name_variations:
                    coords = _find_text_coords_on_page(doc, page_num, name_var, stage1_outputs)
                    if coords:
                        for coord in coords:
                            coord["page_number"] = page_num
                            coord["role"] = "summary"
                        entity_group["coords"].extend(coords)
                        break  # Found match, no need to try other variations

            # Attach coordinates to entity name on each supporting page
            for page_num in supporting_pages:
                # Try all name variations until we find a match
                for name_var in name_variations:
                    coords = _find_text_coords_on_page(doc, page_num, name_var, stage1_outputs)
                    if coords:
                        for coord in coords:
                            coord["page_number"] = page_num
                            coord["role"] = "supporting"
                        entity_group["coords"].extend(coords)
                        break  # Found match, no need to try other variations

        doc.close()
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
        for page_num in range(doc.page_count):
            page = doc[page_num]
            pixmap = page.get_pixmap(dpi=200)
            image_bytes = pixmap.tobytes("png")
            pages.append({
                "text": page.get_text(),
                "image_bytes": image_bytes,
            })
        doc.close()
    elif s3_uri:
        pages, pdf_bytes = fetch_s3_pages(s3_uri)
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
    with ThreadPoolExecutor(max_workers=min(len(pages), 10)) as executor:
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
    stage2_max_tokens = max(max_tokens * 3, 32000)  # At least 3x Stage 1 tokens

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
        # Parse the stage2 JSON output to extract matched_entities
        stage2_json = _extract_json_candidate(stage2_output)

        if stage2_json and isinstance(stage2_json, dict):
            matched_entities = stage2_json.get("matched_entities", [])

            if isinstance(matched_entities, list) and matched_entities:
                print(f"Attaching coordinates to {len(matched_entities)} matched entities")
                # Attach coords in-place, passing stage1 outputs for Textract geometry
                attach_coords_to_matched_entities(pdf_bytes, matched_entities, all_stage1_outputs)

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
                print("WARNING: No matched_entities found in stage2 output")
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
