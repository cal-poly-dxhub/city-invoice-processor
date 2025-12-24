"""
Phase 2: Financial Reconciliation Lambda
Verifies mathematical consistency of numbers within each group.
"""

import base64
import json
import os
from typing import Dict, List, Optional

import boto3
import fitz  # PyMuPDF

# Clients for connection reuse inside Lambda
s3_client = boto3.client(
    "s3", config=boto3.session.Config(retries={"max_attempts": 3}, read_timeout=300)
)
bedrock_runtime = boto3.client(
    "bedrock-runtime",
    config=boto3.session.Config(retries={"max_attempts": 3}, read_timeout=600),
)

DEFAULT_MODEL_ID = os.environ.get(
    "MODEL_ID", "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
)

RECONCILIATION_SYSTEM_PROMPT = """You are a financial reconciliation assistant. Your task is to extract all financial numbers from the provided document images and verify mathematical consistency.

CRITICAL RULES:
- ONLY extract numbers that appear in the ACTUAL DOCUMENTS shown in the images
- DO NOT invent, guess, or hallucinate any numbers
- DO NOT perform calculations on your own - only verify calculations that are explicitly shown in the document
- If you cannot find any financial numbers, return empty arrays
- Use the visual layout to understand relationships between numbers (tables, indentation, formatting)
- Pay attention to bold text, larger fonts, and visual separators - these indicate totals/subtotals

For the document images provided, perform these tasks:

1. EXTRACT all financial numbers you find:
   - Totals, subtotals, grand totals
   - Line items, individual amounts
   - Dates associated with amounts
   - Labels/descriptions for each number

2. VERIFY mathematical consistency:
   - Check if line items sum to subtotals/totals (where explicitly stated)
   - Check if multiple invoices sum to a summary total (where explicitly stated)
   - Check if amounts are referenced consistently across pages
   - Flag any discrepancies you find

   TIMESHEET VERIFICATION:
   - If multiple timesheet pages exist for a pay period, sum ALL hours across all timesheet pages for that date range
   - Compare the total timesheet hours to the hours shown on the corresponding pay stub or summary page
   - Report any differences between the timesheet total and pay stub hours

3. OUTPUT as JSON (IMPORTANT: valid JSON only, no trailing commas):
{
  "extracted_numbers": [
    {"label": "...", "amount": 123.45, "unit": "hours|USD|percent|count|etc", "context": "...", "page": N}
  ],
  "verifications": [
    {"type": "sum_check", "description": "...", "expected": 100.00, "actual": 100.00, "unit": "hours|USD|etc", "passes": true, "pages": [1, 2]}
  ],
  "discrepancies": [
    {"description": "...", "expected": 100.00, "actual": 99.00, "unit": "hours|USD|etc", "pages": [1, 2]}
  ],
  "confidence": 85
}

CRITICAL: "expected" and "actual" MUST be numbers, NOT expressions:
- CORRECT: "actual": 1537.50
- WRONG: "actual": 61.50 * 25.00
- Put calculations in "description", not in "expected" or "actual" fields

CRITICAL JSON FORMATTING RULES:
- Return ONLY valid JSON, nothing else
- No trailing commas in arrays or objects
- All string values must be properly quoted
- Numbers should be unquoted
- Do NOT truncate the JSON - complete all arrays and objects
- Keep descriptions concise to fit within token limits

Where:
- extracted_numbers: Key financial numbers (focus on totals/subtotals, not every line item)
  - unit: Specify the appropriate unit based on context (hours, USD, percent, count, items, etc.)
- verifications: Mathematical checks you performed (summarize)
  - unit: Use the same unit as the numbers being verified
- discrepancies: Any mismatches found
  - unit: Use the appropriate unit for the values being compared
- confidence: 0-100 score

IMPORTANT: Always include the "unit" field and set it appropriately:
- For hours worked: "hours"
- For dollar amounts: "USD"
- For percentages: "percent"
- For counts/quantities: "count" or "items"
- Match the unit to what's actually being measured
"""


def invoke_bedrock(
    model_id: str,
    system_prompt: str,
    messages: List[Dict],
    max_tokens: int = 4096,
    temperature: float = 0.0,
) -> Dict:
    """Call AWS Bedrock with retry logic."""
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
                delay = base_delay * (2 ** attempt)
                print(f"Rate limited. Retrying in {delay} seconds... (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                raise

    raise Exception(f"Failed after {max_retries} retries")


def extract_json_from_response(response_text: str) -> Optional[Dict]:
    """Extract JSON from LLM response, handling markdown code blocks and trailing commas."""
    if not response_text:
        return None

    text = response_text.strip()

    # Strip markdown code blocks if present
    if text.startswith("```"):
        first_newline = text.find("\n")
        if first_newline != -1:
            text = text[first_newline + 1:]
        if text.endswith("```"):
            text = text[:-3].rstrip()

    # Find JSON object
    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1 or end <= start:
        return None

    json_str = text[start : end + 1]

    # Try parsing as-is first
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        # Try removing trailing commas (common LLM formatting issue)
        # Replace ", ]" with " ]" and ", }" with " }"
        import re
        cleaned = re.sub(r',\s*([}\]])', r'\1', json_str)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as e2:
            print(f"JSON decode error even after cleaning: {e2}")
            print(f"Original error: {e}")
            print(f"Attempted to parse: {json_str[:500]}...")
            return None


def render_pages_as_images(doc, page_numbers: List[int], dpi: int = 150, max_dimension: int = 2000) -> List[Dict]:
    """
    Render PDF pages as images for vision API.

    Args:
        doc: PyMuPDF document object
        page_numbers: List of page numbers to render
        dpi: Resolution for rendering (default 150 for good quality/reasonable size)
        max_dimension: Maximum pixels per dimension for multi-image requests (default 2000 for Bedrock)

    Returns:
        List of dicts with page_number and base64_image
    """
    from PIL import Image
    import io

    images = []
    for page_num in sorted(page_numbers):
        page_index = page_num - 1  # Convert to 0-based
        if 0 <= page_index < doc.page_count:
            page = doc[page_index]

            # Render page as image
            pixmap = page.get_pixmap(dpi=dpi)

            # Check if we need to resize
            width, height = pixmap.width, pixmap.height
            if width > max_dimension or height > max_dimension:
                # Calculate scaling factor to fit within max_dimension
                scale = min(max_dimension / width, max_dimension / height)
                new_width = int(width * scale)
                new_height = int(height * scale)

                # Convert to PIL Image for resizing
                img_bytes = pixmap.tobytes("png")
                img = Image.open(io.BytesIO(img_bytes))
                img = img.resize((new_width, new_height), Image.LANCZOS)

                # Convert back to bytes
                img_buffer = io.BytesIO()
                img.save(img_buffer, format='PNG')
                image_bytes = img_buffer.getvalue()

                print(f"  Page {page_num}: Resized from {width}x{height} to {new_width}x{new_height}")
            else:
                image_bytes = pixmap.tobytes("png")

            # Convert to base64 for Bedrock API
            base64_image = base64.b64encode(image_bytes).decode('utf-8')

            images.append({
                "page_number": page_num,
                "base64_image": base64_image
            })

    return images


def verify_group_batch(
    doc,
    group_label: str,
    page_numbers: List[int],
    page_types: Dict[int, str],
    batch_label: str,
    model_id: str = DEFAULT_MODEL_ID,
    dpi: int = 150,
) -> Dict:
    """
    Verify a batch of pages for a group using vision (single-pass extraction + verification).

    Args:
        doc: PyMuPDF document object
        group_label: Name of the group (e.g., "PG&E")
        page_numbers: List of page numbers in this batch
        page_types: Dict mapping page number to type ('summary' or 'supporting')
        batch_label: Label for this batch (e.g., "batch 1/4")
        model_id: Bedrock model ID to use
        dpi: DPI for rendering images

    Returns:
        Dict with extracted_numbers, verifications, discrepancies, and confidence
    """
    # Render pages as images for vision
    page_images = render_pages_as_images(doc, page_numbers, dpi=dpi)

    if not page_images:
        return {
            "extracted_numbers": [],
            "verifications": [],
            "discrepancies": [],
            "confidence": 0,
            "error": "Failed to render pages as images"
        }

    # Build message content with images
    content = []

    # Add all page images
    for img in page_images:
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": img["base64_image"]
            }
        })

    # Identify which pages are summary vs supporting
    summary_pages = [p for p in page_numbers if page_types.get(p) == 'summary']
    supporting_pages = [p for p in page_numbers if page_types.get(p) == 'supporting']

    # Build context-aware prompt
    prompt_text = f"Verify financial consistency for group '{group_label}' ({batch_label}, pages {page_numbers}).\n\n"

    if summary_pages and supporting_pages:
        prompt_text += f"DOCUMENT STRUCTURE:\n"
        prompt_text += f"- Summary pages (showing totals): {summary_pages}\n"
        prompt_text += f"- Supporting documentation (showing details): {supporting_pages}\n\n"
        prompt_text += f"VERIFICATION TASK:\n"
        prompt_text += f"Verify that the numbers in the supporting documentation sum to match the totals shown on the summary pages. "
        prompt_text += f"Check if detailed line items from supporting pages add up to the summary totals."
    elif summary_pages:
        prompt_text += f"All pages are SUMMARY pages showing totals: {summary_pages}\n"
        prompt_text += f"Verify internal consistency within these summary pages."
    elif supporting_pages:
        prompt_text += f"All pages are SUPPORTING DOCUMENTATION: {supporting_pages}\n"
        prompt_text += f"Verify that these detail pages are internally consistent."
    else:
        prompt_text += f"Analyze these pages for mathematical accuracy and consistency."

    content.append({
        "type": "text",
        "text": prompt_text
    })

    # Call LLM for verification
    messages = [
        {
            "role": "user",
            "content": content
        }
    ]

    # Use higher max_tokens for vision responses
    max_tokens = min(8192, 1000 + (len(page_numbers) * 1000))
    print(f"  Using max_tokens: {max_tokens}")

    response = invoke_bedrock(
        model_id=model_id,
        system_prompt=RECONCILIATION_SYSTEM_PROMPT,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.0,
    )

    # Extract content from response
    content_blocks = response.get("content", [])
    if not content_blocks:
        raise ValueError("No content in LLM response")

    response_text = content_blocks[0].get("text", "")

    # Parse JSON from response
    result = extract_json_from_response(response_text)

    if not result:
        # Log full response for debugging
        print(f"FULL RESPONSE TEXT:\n{response_text}\n")
        raise ValueError(f"Failed to extract JSON from response. Check logs for full text.")

    return result


def verify_group(
    doc,
    group_label: str,
    page_numbers: List[int],
    page_types: Dict[int, str],
    model_id: str = DEFAULT_MODEL_ID,
) -> Dict:
    """
    Verify financial consistency for a single group using vision.
    Intelligently batches based on page types (summary vs supporting).
    Uses single-pass extraction+verification to maintain visual context.

    Args:
        doc: PyMuPDF document object
        group_label: Name of the group (e.g., "PG&E")
        page_numbers: List of page numbers in this group
        page_types: Dict mapping page number to type ('summary' or 'supporting')
        model_id: Bedrock model ID to use

    Returns:
        Dict with verification results
    """
    print(f"\n{'='*60}")
    print(f"Verifying group: {group_label}")
    print(f"Pages: {page_numbers}")

    # Show page types
    summary_pages = [p for p in page_numbers if page_types.get(p) == 'summary']
    supporting_pages = [p for p in page_numbers if page_types.get(p) == 'supporting']
    print(f"Summary pages: {summary_pages}")
    print(f"Supporting pages: {supporting_pages}")
    print(f"{'='*60}")

    page_count = len(page_numbers)

    # Configuration for batching
    MAX_PAGES_PER_BATCH = 10  # Maximum pages per batch
    DPI = 150  # Always use high quality for accuracy

    try:
        # Check if we can process everything in one batch
        if page_count <= MAX_PAGES_PER_BATCH:
            # Small group - process in single call
            print(f"Processing all {page_count} pages in single batch at {DPI} DPI...")
            result = verify_group_batch(doc, group_label, page_numbers, page_types, "all pages", model_id, dpi=DPI)
            result["group_label"] = group_label
            result["pages"] = page_numbers
            return result

        else:
            # Large group - intelligent batching based on page types
            print(f"Large group ({page_count} pages) - using intelligent batching")

            # Strategy: Try to keep each summary page with its nearby supporting docs
            # Create batches that include summary pages + supporting docs
            batches = []
            current_batch = []

            # First, add all summary pages to ensure they're all in at least one batch
            for page_num in page_numbers:
                if page_types.get(page_num) == 'summary':
                    current_batch.append(page_num)

            # If summary pages alone exceed max, process them separately
            if len(current_batch) > MAX_PAGES_PER_BATCH:
                # Too many summary pages, split them
                for i in range(0, len(current_batch), MAX_PAGES_PER_BATCH):
                    batch = current_batch[i:i + MAX_PAGES_PER_BATCH]
                    batches.append(batch)
                current_batch = []

            # Now add supporting pages, keeping batches under the limit
            for page_num in page_numbers:
                if page_types.get(page_num) == 'supporting':
                    if len(current_batch) < MAX_PAGES_PER_BATCH:
                        current_batch.append(page_num)
                    else:
                        # Current batch is full, start a new one
                        if current_batch:
                            batches.append(sorted(current_batch))
                        current_batch = [page_num]

            # Add remaining pages
            if current_batch:
                batches.append(sorted(current_batch))

            print(f"Created {len(batches)} intelligent batches:")
            for i, batch in enumerate(batches, 1):
                batch_summary = [p for p in batch if page_types.get(p) == 'summary']
                batch_supporting = [p for p in batch if page_types.get(p) == 'supporting']
                print(f"  Batch {i}: {len(batch)} pages ({len(batch_summary)} summary, {len(batch_supporting)} supporting)")

            # Process each batch
            all_extracted = []
            all_verifications = []
            all_discrepancies = []
            confidence_scores = []

            for i, batch_pages in enumerate(batches, 1):
                batch_label = f"batch {i}/{len(batches)}"
                print(f"\nProcessing {batch_label}: {len(batch_pages)} pages...")
                print(f"Rendering at {DPI} DPI...")

                batch_result = verify_group_batch(doc, group_label, batch_pages, page_types, batch_label, model_id, dpi=DPI)

                # Aggregate results
                all_extracted.extend(batch_result.get("extracted_numbers", []))
                all_verifications.extend(batch_result.get("verifications", []))
                all_discrepancies.extend(batch_result.get("discrepancies", []))
                confidence_scores.append(batch_result.get("confidence", 0))

            # Calculate average confidence
            avg_confidence = sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0

            print(f"\nBatch processing complete:")
            print(f"  Total extracted numbers: {len(all_extracted)}")
            print(f"  Total verifications: {len(all_verifications)}")
            print(f"  Total discrepancies: {len(all_discrepancies)}")
            print(f"  Average confidence: {avg_confidence:.1f}%")

            return {
                "group_label": group_label,
                "pages": page_numbers,
                "extracted_numbers": all_extracted,
                "verifications": all_verifications,
                "discrepancies": all_discrepancies,
                "confidence": int(avg_confidence),
                "notes": f"Large group processed in {len(batches)} intelligent batches. Summary pages grouped with supporting documentation."
            }

    except Exception as e:
        print(f"ERROR verifying group '{group_label}': {e}")
        import traceback
        traceback.print_exc()
        return {
            "group_label": group_label,
            "pages": page_numbers,
            "error": str(e),
            "extracted_numbers": [],
            "verifications": [],
            "discrepancies": [],
            "confidence": 0
        }


def assess_documentation_quality(group_result: Dict, all_results: List[Dict]) -> Dict:
    """
    Assess documentation quality using deterministic heuristics.

    Args:
        group_result: Single group verification result
        all_results: All group results (to check for shared pages)

    Returns:
        Dict with verification_level, supporting_docs_found, requires_manual_review, notes
    """
    pages = group_result.get('pages', [])
    verifications = group_result.get('verifications', [])
    extracted = group_result.get('extracted_numbers', [])

    # Check 1: Does group have timesheet or detailed documentation verifications?
    has_timesheet_verification = any(
        'timesheet' in v.get('description', '').lower() or
        'timesheet' in v.get('type', '').lower()
        for v in verifications
    )

    # Check 2: Are there detailed document references in extracted numbers?
    has_detailed_docs = any(
        'timesheet' in e.get('context', '').lower() or
        'detailed' in e.get('context', '').lower() or
        'invoice' in e.get('context', '').lower()
        for e in extracted
    )

    # Check 3: Is this a single-page entry on a highly shared page?
    is_likely_summary_only = False
    if len(pages) == 1:
        # Count how many other groups share this single page
        shared_page = pages[0]
        shared_count = sum(1 for r in all_results if shared_page in r.get('pages', []))
        if shared_count >= 3:  # Page is shared by 3+ groups (likely a summary page)
            is_likely_summary_only = True

    # Check 4: Very few pages and no detailed docs
    has_minimal_pages = len(pages) <= 2

    # Check if there's already a batch processing note
    existing_note = group_result.get("notes", "")

    # Determine verification level
    if has_timesheet_verification or has_detailed_docs:
        verification_level = "fully_verified"
        supporting_docs_found = True
        requires_manual_review = False
        notes = "Supporting documentation found (timesheets, invoices, or detailed records)"
    elif is_likely_summary_only:
        verification_level = "summary_only"
        supporting_docs_found = False
        requires_manual_review = True
        notes = f"Only appears on page {pages[0]} which is shared by multiple entities (likely a summary page). No supporting documentation found."
    elif has_minimal_pages and not has_detailed_docs:
        verification_level = "minimal_documentation"
        supporting_docs_found = False
        requires_manual_review = True
        notes = f"Limited documentation ({len(pages)} page(s)). No detailed timesheets or supporting records found."
    else:
        verification_level = "partially_verified"
        supporting_docs_found = True
        requires_manual_review = False
        notes = "Some documentation present but verification depth is limited"

    # Append existing batch processing note if present
    if existing_note:
        notes = f"{notes}. {existing_note}"

    return {
        "verification_level": verification_level,
        "supporting_docs_found": supporting_docs_found,
        "requires_manual_review": requires_manual_review,
        "notes": notes
    }


def reconcile_document(pdf_path: str, reconciled_json_path: str, model_id: str = DEFAULT_MODEL_ID) -> Dict:
    """
    Main reconciliation function.

    Args:
        pdf_path: Path to PDF file
        reconciled_json_path: Path to reconciled groupings JSON
        model_id: Bedrock model ID to use

    Returns:
        Dict with reconciliation results for all groups
    """
    print(f"Loading PDF: {pdf_path}")
    print(f"Loading reconciled JSON: {reconciled_json_path}")

    # Load reconciled groupings
    with open(reconciled_json_path, 'r') as f:
        reconciled_data = json.load(f)

    pdf_name = reconciled_data.get("pdfName", "unknown")
    groups = reconciled_data.get("groups", [])

    print(f"\nPDF: {pdf_name}")
    print(f"Groups to verify: {len(groups)}")

    # Load PDF
    doc = fitz.open(pdf_path)
    print(f"PDF loaded: {doc.page_count} pages")

    # Verify each group
    results = []
    for i, group in enumerate(groups, 1):
        label = group.get("label", f"Group {i}")
        pages_data = group.get("pages", [])

        if not pages_data:
            print(f"WARNING: Group '{label}' has no pages, skipping")
            continue

        # Parse page numbers and types from new format
        # Format: [{"page": 1, "type": "summary"}, {"page": 2, "type": "supporting"}, ...]
        page_numbers = []
        page_types = {}
        for page_item in pages_data:
            page_num = page_item.get("page")
            page_type = page_item.get("type", "supporting")
            if page_num:
                page_numbers.append(page_num)
                page_types[page_num] = page_type

        if not page_numbers:
            print(f"WARNING: Group '{label}' has no valid pages, skipping")
            continue

        result = verify_group(doc, label, page_numbers, page_types, model_id)
        results.append(result)

    doc.close()

    # Post-process: Assess documentation quality for each group
    for result in results:
        quality_assessment = assess_documentation_quality(result, results)
        result.update(quality_assessment)

    # Compile summary
    summary = {
        "pdf_name": pdf_name,
        "total_groups": len(groups),
        "verified_groups": len(results),
        "groups": results,
        "summary": {
            "total_discrepancies": sum(len(r.get("discrepancies", [])) for r in results),
            "average_confidence": sum(r.get("confidence", 0) for r in results) / len(results) if results else 0
        }
    }

    return summary


def lambda_handler(event, context):
    """AWS Lambda handler for Phase 2 reconciliation."""
    # TODO: In production, get PDF and JSON from S3
    # For now, this is designed for local testing

    pdf_path = event.get("pdf_path")
    reconciled_json_path = event.get("reconciled_json_path")
    model_id = event.get("model_id", DEFAULT_MODEL_ID)

    if not pdf_path or not reconciled_json_path:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing pdf_path or reconciled_json_path"})
        }

    try:
        results = reconcile_document(pdf_path, reconciled_json_path, model_id)

        return {
            "statusCode": 200,
            "body": json.dumps(results, indent=2)
        }

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)})
        }
