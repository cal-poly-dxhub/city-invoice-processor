"""Match sub-items: auto-extract from table rows or match a specific sub-item."""

import json
import logging
import os
import re
import sys
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

from shared.s3_utils import download_json, list_keys

from invoice_recon.budget_items import get_budget_item_slug
from invoice_recon.matching import (
    generate_candidates_for_line_item,
    select_default_evidence,
)
from invoice_recon.models import (
    LineItem,
    PageRecord,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MIN_CANDIDATE_SCORE = float(os.environ.get("MIN_CANDIDATE_SCORE", "0.1"))


def lambda_handler(event, context):
    """
    Handle sub-item operations.

    Modes:
      - auto_extract: Propose sub-items from table row data on a specific page.
      - match: Run matching for a user-defined sub-item across the budget item's pages.

    Input (from API Gateway):
      event["body"] contains the JSON payload; event["pathParameters"]["jobId"] has the job ID.
    """
    # Parse API Gateway request
    body = json.loads(event.get("body", "{}"))
    job_id = event.get("pathParameters", {}).get("jobId") or body.get("job_id")
    mode = body.get("mode", "match")

    if not job_id:
        return _error(400, "job_id is required")

    if mode == "auto_extract":
        return handle_auto_extract(job_id, body)
    elif mode == "match":
        return handle_match(job_id, body)
    else:
        return _error(400, f"Unknown mode: {mode}")


def handle_auto_extract(job_id, body):
    """
    Propose sub-items from amounts on a specific page.

    Reads the stored page data and filters amounts matching the budget item.
    Prefers amounts with source == "table_row" (explicit table structure), but
    falls back to all matching amounts when table detection was not enabled.
    """
    doc_id = body.get("doc_id")
    budget_item = body.get("budget_item")
    source_page = body.get("source_page")

    if not all([doc_id, budget_item, source_page]):
        return _error(400, "doc_id, budget_item, and source_page are required")

    # Load page data for the source page.
    # Try the budget slug directly first (flat file), then search for physical doc_ids.
    page_data = _load_single_page(job_id, doc_id, source_page)
    if not page_data:
        return _error(404, f"Page data not found for {doc_id} page {source_page}")

    entities = page_data.get("entities", {})
    amounts = entities.get("amounts", [])

    if not amounts:
        return _success({"proposed_sub_items": []})

    # Filter to amounts matching the parent's budget item.
    # Prefer table_row amounts (have explicit table structure), but fall back
    # to ALL amounts matching the budget item when no table structures were
    # extracted (e.g., TABLE_DETECTION_ENABLED=false, or PyMuPDF-only pages).
    budget_slug = get_budget_item_slug(budget_item)
    table_row_amounts = []
    all_budget_amounts = []
    for amount_obj in amounts:
        amount_budget = amount_obj.get("budget_item", "")
        amount_source = amount_obj.get("source", "")

        if _budget_items_match(amount_budget, budget_item, budget_slug):
            all_budget_amounts.append(amount_obj)
            if amount_source == "table_row":
                table_row_amounts.append(amount_obj)

    # Use table_row amounts if available, otherwise fall back to all matching
    matching_amounts = table_row_amounts if table_row_amounts else all_budget_amounts

    # Build proposed sub-items
    proposed = []
    for amount_obj in matching_amounts:
        value = amount_obj.get("value")
        if value is None:
            continue

        context = amount_obj.get("context", "")
        # Extract keywords from context (words 3+ chars, exclude common words)
        keywords = _extract_keywords_from_context(context)

        proposed.append({
            "label": context or f"${value:.2f}",
            "amount": value,
            "keywords": keywords,
            "table_row_index": amount_obj.get("table_row_index"),
            "raw_amount": amount_obj.get("raw", ""),
        })

    source_type = "table_row" if table_row_amounts else "all"
    logger.info(
        f"AutoExtract: {len(matching_amounts)} amounts matching {budget_item} "
        f"(source={source_type}) out of {len(amounts)} total on page {source_page}"
    )

    return _success({"proposed_sub_items": proposed})


def handle_match(job_id, body):
    """
    Run matching for a user-defined sub-item.

    Loads all pages for the budget item's document, constructs a synthetic LineItem,
    and runs generate_candidates_for_line_item.
    """
    doc_id = body.get("doc_id")
    budget_item = body.get("budget_item")
    sub_item_id = body.get("sub_item_id")
    keywords = body.get("keywords", [])
    amount = body.get("amount")
    source_page = body.get("source_page")

    if not all([doc_id, budget_item]):
        return _error(400, "doc_id and budget_item are required")

    # Load all pages for this budget item's document
    pages = _load_all_pages_for_budget_item(job_id, doc_id)

    if not pages:
        return _error(404, f"No page data found for doc_id={doc_id}")

    # Optionally exclude the source page (the GL summary page)
    if source_page:
        pages = [p for p in pages if p.page_number != source_page]

    # Construct a synthetic LineItem
    explanation = " ".join(keywords) if keywords else ""
    synthetic_item = LineItem(
        row_id=sub_item_id or "sub_item",
        row_index=0,
        budget_item=budget_item,
        amount=Decimal(str(amount)) if amount is not None else None,
        explanation=explanation,
    )

    # Run matching
    candidates = generate_candidates_for_line_item(synthetic_item, pages)

    # Filter by minimum score
    filtered = [c for c in candidates if c.score >= MIN_CANDIDATE_SCORE]

    # Select default evidence
    selected = select_default_evidence(filtered)

    logger.info(
        f"MatchSubItem: {len(filtered)} candidates (from {len(candidates)}) "
        f"for sub_item_id={sub_item_id}"
    )

    return _success({
        "sub_item_id": sub_item_id,
        "candidates": [c.model_dump(mode="json") for c in filtered],
        "selected_evidence": selected.model_dump(mode="json") if selected else None,
    })


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_single_page(job_id, doc_id, page_number):
    """Load page data JSON for a single virtual page, resolving across physical doc_ids.

    For multi-file documents, virtual page numbers differ from local page numbers.
    Physical docs are sorted alphabetically and concatenated with cumulative offsets.
    Virtual page N maps to local page (N - offset) within the correct physical doc.
    """
    budget_slug = get_budget_item_slug(doc_id) if doc_id else doc_id

    # Try direct path first (flat file case: doc_id is the budget slug)
    direct_key = f"jobs/{job_id}/page_data/{budget_slug}/{page_number}.json"
    try:
        return download_json(direct_key)
    except Exception:
        pass

    # For virtual documents (subdirectories), reconstruct virtual→local page mapping.
    # Page data is stored under physical doc_ids with local page numbers.
    # Virtual pages are assembled by concatenating physical docs in sorted order.
    all_doc_ids = _find_doc_ids_for_budget(job_id, budget_slug)

    offset = 0
    for phys_doc_id in sorted(all_doc_ids):
        prefix = f"jobs/{job_id}/page_data/{phys_doc_id}/"
        page_keys = list_keys(prefix, suffix=".json")
        page_count = len(page_keys)

        # Virtual pages for this doc: [offset+1, offset+page_count]
        if offset < page_number <= offset + page_count:
            local_page = page_number - offset
            try:
                return download_json(
                    f"jobs/{job_id}/page_data/{phys_doc_id}/{local_page}.json"
                )
            except Exception:
                pass

        offset += page_count

    return None


def _load_all_pages_for_budget_item(job_id, doc_id):
    """Load all PageRecords for a budget item, assembling virtual pages from physical docs."""
    budget_slug = get_budget_item_slug(doc_id) if doc_id else doc_id
    all_doc_ids = _find_doc_ids_for_budget(job_id, budget_slug)

    # Also include the budget_slug itself (flat file case)
    if budget_slug not in all_doc_ids:
        all_doc_ids.insert(0, budget_slug)

    all_pages = []
    offset = 0

    for phys_doc_id in sorted(all_doc_ids):
        prefix = f"jobs/{job_id}/page_data/{phys_doc_id}/"
        page_keys = list_keys(prefix, suffix=".json")

        doc_pages = []
        for page_key in sorted(page_keys):
            try:
                page_data = download_json(page_key)
                doc_pages.append(page_data)
            except Exception as e:
                logger.warning(f"Failed to load {page_key}: {e}")

        doc_pages.sort(key=lambda p: p["page_number"])

        # For virtual assembly, we need to re-number pages with offset
        # But if this is the budget_slug itself (flat file), pages already have correct numbers
        is_flat = phys_doc_id == budget_slug
        for page_data in doc_pages:
            pg_num = page_data["page_number"]
            virtual_num = pg_num if is_flat else pg_num + offset

            all_pages.append(PageRecord(
                doc_id=budget_slug,
                page_number=virtual_num,
                text_source=page_data.get("text_source", "pymupdf"),
                text=page_data.get("text", ""),
                entities=page_data.get("entities", {}),
                words=page_data.get("word_boxes", []),
            ))

        if not is_flat:
            offset += len(doc_pages)

    return all_pages


def _find_doc_ids_for_budget(job_id, budget_slug):
    """Find all physical doc_ids for a budget item by listing S3 page_data prefixes."""
    prefix = f"jobs/{job_id}/page_data/"
    all_keys = list_keys(prefix, suffix=".json")

    # Extract unique doc_ids from keys like jobs/{job_id}/page_data/{doc_id}/{page}.json
    doc_ids = set()
    for key in all_keys:
        parts = key[len(prefix):].split("/")
        if len(parts) >= 2:
            doc_ids.add(parts[0])

    # Filter to doc_ids matching this budget item
    # Flat: doc_id == budget_slug
    # Subdirectory: doc_id starts with budget_slug + "__"
    matching = [
        d for d in sorted(doc_ids)
        if d == budget_slug or d.startswith(f"{budget_slug}__")
    ]

    return matching


def _budget_items_match(amount_budget, parent_budget, parent_slug):
    """Check if an amount's budget_item matches the parent line item's budget item.

    Handles several formats for amount_budget:
    - Canonical name: "Telecommunications"
    - Budget slug: "telecommunications"
    - Physical doc_id (subdirectory): "telecommunications__phone_bill_q1"
    """
    if not amount_budget:
        return False
    # Exact name match (case-insensitive)
    if amount_budget.lower() == parent_budget.lower():
        return True
    # Slug match
    amount_slug = get_budget_item_slug(amount_budget)
    if amount_slug == parent_slug:
        return True
    # Physical doc_id match: check the raw string (lowercased), NOT the slug.
    # slugify() collapses "__" to "_", breaking the "budget_slug__filename" pattern.
    if amount_budget.lower().startswith(f"{parent_slug}__"):
        return True
    return False


def _extract_keywords_from_context(context):
    """Extract meaningful keywords from an amount's context string."""
    if not context:
        return []
    # Remove common filler words and short words
    stop_words = {
        "the", "and", "for", "from", "with", "this", "that", "was", "are",
        "has", "had", "have", "been", "will", "would", "could", "should",
        "not", "but", "all", "any", "can", "her", "his", "its", "our",
        "per", "than", "too", "very",
    }
    words = re.findall(r"[a-zA-Z]{3,}", context)
    keywords = [w.lower() for w in words if w.lower() not in stop_words]
    # Deduplicate while preserving order
    seen = set()
    result = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result[:10]  # Cap at 10 keywords


def _success(data):
    return {
        "statusCode": 200,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps(data, default=str),
    }


def _error(status_code, message):
    return {
        "statusCode": status_code,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*",
        },
        "body": json.dumps({"error": message}),
    }
