"""Per-page entity extraction Lambda (invoked directly by IndexDocument)."""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

from invoice_recon.cli import extract_entities_for_page

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """
    Extract entities for a single page using Bedrock.

    Invoked directly by IndexDocument Lambda (not Step Functions).

    Input:  {doc_id, budget_item, page_number, text, text_source, word_boxes, tables}
    Output: {entities: {...}}
    """
    doc_id = event["doc_id"]
    page_number = event["page_number"]

    logger.info(f"ExtractEntities: {doc_id} page {page_number}")

    # Deserialize tables if they were serialized
    tables = event.get("tables")

    result = extract_entities_for_page(
        doc_id=doc_id,
        budget_item=event["budget_item"],
        page_number=page_number,
        text=event["text"],
        text_source=event["text_source"],
        word_boxes=event.get("word_boxes", []),
        tables=tables,
    )

    logger.info(f"ExtractEntities: {doc_id} page {page_number} done")

    return {
        "entities": result["entities"],
    }
