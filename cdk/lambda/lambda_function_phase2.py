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


def render_pages_as_images(doc, page_numbers: List[int], dpi: int = 150) -> List[Dict]:
    """
    Render PDF pages as images for vision API.

    Args:
        doc: PyMuPDF document object
        page_numbers: List of page numbers to render
        dpi: Resolution for rendering (default 150 for good quality/reasonable size)

    Returns:
        List of dicts with page_number and base64_image
    """
    images = []
    for page_num in sorted(page_numbers):
        page_index = page_num - 1  # Convert to 0-based
        if 0 <= page_index < doc.page_count:
            page = doc[page_index]

            # Render page as image
            pixmap = page.get_pixmap(dpi=dpi)
            image_bytes = pixmap.tobytes("png")

            # Convert to base64 for Bedrock API
            base64_image = base64.b64encode(image_bytes).decode('utf-8')

            images.append({
                "page_number": page_num,
                "base64_image": base64_image
            })

    return images


def verify_group(
    doc,
    group_label: str,
    page_numbers: List[int],
    model_id: str = DEFAULT_MODEL_ID,
) -> Dict:
    """
    Verify financial consistency for a single group using vision.

    Args:
        doc: PyMuPDF document object
        group_label: Name of the group (e.g., "PG&E")
        page_numbers: List of page numbers in this group
        model_id: Bedrock model ID to use

    Returns:
        Dict with verification results
    """
    print(f"\n{'='*60}")
    print(f"Verifying group: {group_label}")
    print(f"Pages: {page_numbers}")
    print(f"{'='*60}")

    # Render pages as images for vision
    print(f"Rendering {len(page_numbers)} pages as images...")
    page_images = render_pages_as_images(doc, page_numbers)

    if not page_images:
        print(f"WARNING: No images rendered for group '{group_label}'")
        return {
            "group_label": group_label,
            "pages": page_numbers,
            "error": "Failed to render pages as images",
            "extracted_numbers": [],
            "verifications": [],
            "discrepancies": [],
            "confidence": 0
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

    # Add text prompt
    content.append({
        "type": "text",
        "text": f"Verify financial consistency for group '{group_label}' (pages {page_numbers}). The images above show all pages in this group. Analyze them for mathematical accuracy and consistency."
    })

    # Call LLM for verification
    messages = [
        {
            "role": "user",
            "content": content
        }
    ]

    try:
        # Use higher max_tokens for vision responses (more verbose)
        # Scale with number of pages: ~1000 tokens per page baseline
        max_tokens = min(8192, 1000 + (len(page_numbers) * 1000))
        print(f"Using max_tokens: {max_tokens}")

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

        # Add metadata
        result["group_label"] = group_label
        result["pages"] = page_numbers

        return result

    except Exception as e:
        print(f"ERROR verifying group '{group_label}': {e}")
        return {
            "group_label": group_label,
            "pages": page_numbers,
            "error": str(e),
            "extracted_numbers": [],
            "verifications": [],
            "discrepancies": [],
            "confidence": 0
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
        pages = group.get("pages", [])

        if not pages:
            print(f"WARNING: Group '{label}' has no pages, skipping")
            continue

        result = verify_group(doc, label, pages, model_id)
        results.append(result)

    doc.close()

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
