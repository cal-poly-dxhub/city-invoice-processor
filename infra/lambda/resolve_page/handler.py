"""Per-page text extraction Lambda (invoked directly by IndexDocument)."""

import base64
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

from invoice_recon.pdf_extract import resolve_page_extraction

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """
    Resolve text extraction for a single page.

    Invoked directly by IndexDocument Lambda (not Step Functions).
    PNG bytes are passed inline as base64 (fits within 6MB Lambda payload).

    Input:  {page_data: {..., png_bytes_b64: "<base64>"}}
    Output: {text, text_source, word_boxes, tables}
    """
    page_data = event["page_data"]

    # Decode PNG bytes from base64 if present
    if "png_bytes_b64" in page_data:
        page_data["png_bytes"] = base64.b64decode(page_data["png_bytes_b64"])
        del page_data["png_bytes_b64"]

    page_number = page_data.get("page_number", "?")
    logger.info(f"ResolvePage: page {page_number}, needs_api={page_data.get('needs_api')}")

    # Call the stateless resolve function from pdf_extract
    text, text_source, word_boxes, tables = resolve_page_extraction(page_data)

    # Serialize tables
    tables_data = None
    if tables:
        tables_data = [
            t.dict() if hasattr(t, "dict") else t for t in tables
        ]

    logger.info(f"ResolvePage: page {page_number} -> {text_source} ({len(text)} chars)")

    return {
        "text": text,
        "text_source": text_source,
        "word_boxes": word_boxes,
        "tables": tables_data,
    }
