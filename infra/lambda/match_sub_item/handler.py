"""Match sub-items: auto-extract from table rows or match a specific sub-item."""

import hashlib
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

from shared.s3_utils import download_json, list_keys, upload_json

from invoice_recon.budget_items import get_budget_item_slug
from invoice_recon.matching import (
    find_word_boxes_for_terms,
    generate_candidates_for_line_item,
    select_default_evidence,
)
from invoice_recon.models import (
    CandidateEvidenceSet,
    LineItem,
    PageRecord,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

MIN_CANDIDATE_SCORE = float(os.environ.get("MIN_CANDIDATE_SCORE", "0.1"))
S3_DOWNLOAD_CONCURRENCY = 20

# Global cache: persists across invocations in the same Lambda execution environment.
# Key: (job_id, budget_slug), Value: list of PageRecord
_pages_cache: dict = {}


def lambda_handler(event, context):
    """
    Handle sub-item operations.

    Modes:
      - auto_extract: Propose sub-items from table row data on a specific page.
      - match: Run matching synchronously. If result is cached in S3, returns immediately.
      - poll: Check if an async match result is ready.

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
    elif mode == "poll":
        return handle_poll(job_id, body)
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

    # Group amounts by table_row_index to merge balance/allocation from same row
    from collections import defaultdict
    row_groups = defaultdict(list)
    for amount_obj in matching_amounts:
        tri = amount_obj.get("table_row_index")
        if tri is not None:
            row_groups[tri].append(amount_obj)
        else:
            # No table_row_index — treat as standalone proposal
            row_groups[f"_solo_{id(amount_obj)}"].append(amount_obj)

    proposed = []
    for group_key, group_amounts in row_groups.items():
        values = [a["value"] for a in group_amounts if a.get("value") is not None]
        if not values:
            continue

        tri = group_amounts[0].get("table_row_index")
        table_row_texts = _extract_row_words(
            page_data.get("tables"), tri, budget_item, budget_slug,
        )

        if len(group_amounts) == 1:
            # Single amount in this row — standard proposal
            amount_obj = group_amounts[0]
            context = amount_obj.get("context", "")
            keywords = _extract_keywords_from_context(context, budget_item)
            proposed.append({
                "label": context or f"${values[0]:.2f}",
                "amount": values[0],
                "amounts": values,
                "keywords": keywords,
                "table_row_index": tri,
                "raw_amount": amount_obj.get("raw", ""),
                "row_texts": list(keywords),
                "table_row_texts": table_row_texts,
            })
        else:
            # Multiple amounts on same row — merge into one proposal
            all_contexts = [a.get("context", "") for a in group_amounts]
            combined_context = " / ".join(c for c in all_contexts if c)
            all_keywords = set()
            for a in group_amounts:
                all_keywords.update(
                    _extract_keywords_from_context(a.get("context", ""), budget_item)
                )
            keywords = list(all_keywords)[:10]
            label = combined_context or " / ".join(f"${v:.2f}" for v in values)
            proposed.append({
                "label": label,
                "amount": values[0],
                "amounts": values,
                "keywords": keywords,
                "table_row_index": tri,
                "raw_amount": " / ".join(a.get("raw", "") for a in group_amounts),
                "row_texts": list(keywords),
                "table_row_texts": table_row_texts,
            })

    source_type = "table_row" if table_row_amounts else "all"
    logger.info(
        f"AutoExtract: {len(matching_amounts)} amounts matching {budget_item} "
        f"(source={source_type}) out of {len(amounts)} total on page {source_page}"
    )
    for p in proposed:
        logger.info(
            f"  Proposal: amount={p['amount']} row_texts={p['row_texts']}"
        )

    return _success({"proposed_sub_items": proposed})


def handle_match(job_id, body):
    """
    Run matching for a user-defined sub-item.

    Checks S3 for a cached result first. If not cached, runs matching and
    caches the result. The Lambda timeout (90s) exceeds the API Gateway
    timeout (29s), so even if the client gets a 504, the Lambda finishes
    and caches the result for subsequent poll requests.
    """
    doc_id = body.get("doc_id")
    budget_item = body.get("budget_item")
    sub_item_id = body.get("sub_item_id")
    keywords = body.get("keywords", [])
    amount = body.get("amount")
    source_page = body.get("source_page")
    row_texts = body.get("row_texts", [])
    table_row_texts = body.get("table_row_texts", [])
    amounts_list = body.get("amounts", [])
    if not amounts_list and amount is not None:
        amounts_list = [amount]

    if not all([doc_id, budget_item]):
        return _error(400, "doc_id and budget_item are required")

    # Generate a deterministic request ID from the match parameters.
    request_id = _make_request_id(job_id, sub_item_id, doc_id, amount,
                                   row_texts, table_row_texts, amounts=amounts_list)

    # Check S3 for a cached result (from a previous invocation that may have
    # completed after the API Gateway timeout).
    cached = _get_cached_result(job_id, request_id)
    if cached:
        logger.info(f"Cache hit for request_id={request_id}")
        return _success(cached)

    # Check if another invocation is already processing this request.
    # Without this check, every retry starts full processing from scratch,
    # each timing out at 29s (API Gateway limit) and spawning yet another.
    if _is_processing(job_id, request_id):
        logger.info(f"Another invocation is processing request_id={request_id}, returning 'processing'")
        return _success({"status": "processing", "request_id": request_id})

    # Write a "processing" marker so retry requests know work is in progress.
    _write_processing_marker(job_id, request_id)

    t0 = time.time()

    # Load all pages for this budget item's document
    pages = _load_all_pages_for_budget_item(job_id, doc_id)

    t1 = time.time()
    logger.info(f"TIMING: page load = {t1 - t0:.2f}s ({len(pages) if pages else 0} pages)")

    if not pages:
        return _error(404, f"No page data found for doc_id={doc_id}")

    # Exclude the source page (the GL summary page) and any near-duplicate
    # copies.  The same GL page may appear in multiple physical PDFs that get
    # combined into one virtual document, so filtering by page number alone
    # is insufficient — we also check text similarity (Jaccard on word tokens).
    if source_page:
        # Find the source page text from the already-loaded pages (avoids
        # an extra S3 round-trip vs calling _load_single_page).
        source_text = ""
        for p in pages:
            if p.page_number == source_page:
                source_text = p.text
                break
        similar = _find_gl_duplicates(source_text, pages) if source_text else set()
        similar.add(source_page)
        if len(similar) > 1:
            logger.info(
                f"GL duplicate detection: excluding {len(similar)} pages "
                f"(source={source_page}, duplicates={similar - {source_page}})"
            )
        pages = [p for p in pages if p.page_number not in similar]

    # Run matching for each amount (supports merged balance/allocation sub-items)
    explanation = " ".join(keywords) if keywords else ""
    all_candidates = []
    for match_amount in amounts_list:
        synthetic_item = LineItem(
            row_id=sub_item_id or "sub_item",
            row_index=0,
            budget_item=budget_item,
            amount=Decimal(str(match_amount)) if match_amount is not None else None,
            explanation=explanation,
        )
        all_candidates.extend(generate_candidates_for_line_item(synthetic_item, pages))

    # Deduplicate by page set, keeping highest-scoring candidate per unique page combination
    seen_pages = {}
    for c in sorted(all_candidates, key=lambda x: x.score, reverse=True):
        key = tuple(sorted(c.page_numbers))
        if key not in seen_pages:
            seen_pages[key] = c
    candidates = list(seen_pages.values())

    t2 = time.time()
    logger.info(f"TIMING: matching = {t2 - t1:.2f}s ({len(candidates)} candidates)")

    # Filter by minimum score
    filtered = [c for c in candidates if c.score >= MIN_CANDIDATE_SCORE]

    # Filter by row text context (discard candidates with no contextual overlap).
    # Uses keywords as primary tokens; falls back to table cell words when all
    # keywords are generic for this document.
    if row_texts or table_row_texts:
        pre_filter_count = len(filtered)
        filtered = _filter_candidates_by_row_texts(
            filtered, pages, row_texts, table_row_texts
        )
        logger.info(
            f"Row text filter: {pre_filter_count} -> {len(filtered)} candidates "
            f"using row_texts={row_texts} table_row_texts={table_row_texts}"
        )

    # Combined amount+keyword recovery: the standard matching engine picks the
    # FIRST page with the matching amount (may be wrong). Scan all pages for
    # ones containing BOTH the target amount AND a distinctive token (what the
    # user does manually with "granite AND 2.68"). If found, inject or boost
    # those candidates.
    if amounts_list and (row_texts or table_row_texts):
        for match_amount in amounts_list:
            filtered = _recover_combined_matches(
                filtered, pages, match_amount, row_texts, table_row_texts, doc_id
            )

    t3 = time.time()
    logger.info(f"TIMING: filtering = {t3 - t2:.2f}s, total = {t3 - t0:.2f}s")

    # Select default evidence
    selected = select_default_evidence(filtered)

    logger.info(
        f"MatchSubItem: {len(filtered)} candidates (from {len(candidates)}) "
        f"for sub_item_id={sub_item_id}"
    )

    result_data = {
        "sub_item_id": sub_item_id,
        "candidates": [c.model_dump(mode="json") for c in filtered],
        "selected_evidence": selected.model_dump(mode="json") if selected else None,
    }

    # Cache the result in S3 so poll requests can retrieve it.
    _cache_match_result(job_id, request_id, result_data)

    return _success(result_data)


def handle_poll(job_id, body):
    """Check if an async match result is ready."""
    request_id = body.get("request_id")
    if not request_id:
        return _error(400, "request_id is required")

    cached = _get_cached_result(job_id, request_id)
    if cached:
        return _success(cached)

    # Result not ready yet — check if processing is in progress
    return _success({"status": "processing", "request_id": request_id})


# ---------------------------------------------------------------------------
# Request ID & S3 result cache
# ---------------------------------------------------------------------------


def _make_request_id(job_id, sub_item_id, doc_id, amount, row_texts,
                      table_row_texts, amounts=None):
    """Generate a deterministic request ID from match parameters."""
    key_parts = json.dumps([
        job_id, sub_item_id, doc_id, str(amount),
        sorted(row_texts), sorted(table_row_texts or []),
        sorted([str(a) for a in (amounts or [])]),
    ], sort_keys=True)
    return hashlib.sha256(key_parts.encode()).hexdigest()[:16]


def _match_result_key(job_id, request_id):
    return f"jobs/{job_id}/match_results/{request_id}.json"


def _processing_marker_key(job_id, request_id):
    return f"jobs/{job_id}/match_results/{request_id}.processing"


def _get_cached_result(job_id, request_id):
    """Check S3 for a cached match result. Returns the data dict or None."""
    try:
        return download_json(_match_result_key(job_id, request_id))
    except Exception:
        return None


def _cache_match_result(job_id, request_id, result_data):
    """Write match result to S3 for retrieval by poll requests."""
    try:
        # Include the request_id in the result so the frontend can identify it.
        data = {**result_data, "request_id": request_id}
        upload_json(data, _match_result_key(job_id, request_id))
        logger.info(f"Cached match result for request_id={request_id}")
    except Exception as e:
        logger.warning(f"Failed to cache match result: {e}")


def _is_processing(job_id, request_id):
    """Check if another invocation is already processing this request.

    Markers older than 120s are considered stale (Lambda crashed or timed out)
    and are ignored so the request can be retried.
    """
    try:
        marker = download_json(_processing_marker_key(job_id, request_id))
        started_at = marker.get("started_at", 0)
        if time.time() - started_at > 120:
            logger.info(f"Stale processing marker for request_id={request_id}, ignoring")
            return False
        return True
    except Exception:
        return False


def _write_processing_marker(job_id, request_id):
    """Write a processing marker to S3 so poll requests know work is in progress."""
    try:
        upload_json(
            {"status": "processing", "started_at": time.time()},
            _processing_marker_key(job_id, request_id),
        )
    except Exception as e:
        logger.warning(f"Failed to write processing marker: {e}")


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
    all_doc_ids, keys_by_doc = _find_keys_for_budget(job_id, budget_slug)

    offset = 0
    for phys_doc_id in sorted(all_doc_ids):
        page_count = len(keys_by_doc.get(phys_doc_id, []))

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
    """Load all PageRecords for a budget item, assembling virtual pages from physical docs.

    Uses a global cache to avoid re-loading across invocations in the same
    Lambda execution environment, and parallel S3 reads for the initial load.
    """
    budget_slug = get_budget_item_slug(doc_id) if doc_id else doc_id
    cache_key = (job_id, budget_slug)

    if cache_key in _pages_cache:
        logger.info(f"Page cache hit for {cache_key} ({len(_pages_cache[cache_key])} pages)")
        return _pages_cache[cache_key]

    # Single S3 list call to discover all keys and doc_ids at once.
    all_doc_ids, keys_by_doc = _find_keys_for_budget(job_id, budget_slug)

    # Flatten into a single list for parallel download
    keys_to_load = []  # [(s3_key, phys_doc_id), ...]
    for phys_doc_id in sorted(all_doc_ids):
        for page_key in sorted(keys_by_doc.get(phys_doc_id, [])):
            keys_to_load.append((page_key, phys_doc_id))

    logger.info(f"Loading {len(keys_to_load)} pages for {budget_slug} (parallel, {S3_DOWNLOAD_CONCURRENCY} threads)")

    # Parallel S3 download
    loaded = {}  # {s3_key: (page_data, phys_doc_id)}
    with ThreadPoolExecutor(max_workers=S3_DOWNLOAD_CONCURRENCY) as pool:
        future_to_key = {}
        for s3_key, phys_doc_id in keys_to_load:
            future = pool.submit(download_json, s3_key)
            future_to_key[future] = (s3_key, phys_doc_id)

        for future in as_completed(future_to_key):
            s3_key, phys_doc_id = future_to_key[future]
            try:
                page_data = future.result()
                loaded[s3_key] = (page_data, phys_doc_id)
            except Exception as e:
                logger.warning(f"Failed to load {s3_key}: {e}")

    # Assemble virtual pages in sorted order (same logic as before)
    all_pages = []
    offset = 0

    for phys_doc_id in sorted(all_doc_ids):
        # Gather pages for this doc_id from loaded results, sorted by key
        doc_pages = []
        for s3_key, did in keys_to_load:
            if did == phys_doc_id and s3_key in loaded:
                doc_pages.append(loaded[s3_key][0])

        doc_pages.sort(key=lambda p: p["page_number"])

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

    # Cache for subsequent invocations
    _pages_cache[cache_key] = all_pages
    logger.info(f"Cached {len(all_pages)} pages for {cache_key}")

    return all_pages


def _find_keys_for_budget(job_id, budget_slug):
    """Find all physical doc_ids AND their S3 keys for a budget item in one list call.

    Returns (doc_ids, keys_by_doc) where keys_by_doc maps doc_id → [s3_key, ...].
    This avoids multiple sequential list_keys calls.
    """
    prefix = f"jobs/{job_id}/page_data/"
    all_keys = list_keys(prefix, suffix=".json")

    # Group keys by doc_id, filtering to this budget item
    keys_by_doc = {}  # {doc_id: [s3_key, ...]}
    for key in all_keys:
        parts = key[len(prefix):].split("/")
        if len(parts) < 2:
            continue
        d = parts[0]
        if d == budget_slug or d.startswith(f"{budget_slug}__"):
            keys_by_doc.setdefault(d, []).append(key)

    doc_ids = sorted(keys_by_doc.keys())
    return doc_ids, keys_by_doc


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



def _is_budget_item_text(text, budget_lower, budget_slug_lower):
    """Check if text is too generic because it matches the budget item name."""
    t = text.strip().lower()
    if not t:
        return True
    if t == budget_lower or t == budget_slug_lower:
        return True
    if t in budget_lower:
        return True
    if budget_lower in t:
        return True
    return False


def _extract_row_words(tables, table_row_index, budget_item="", budget_slug=""):
    """Extract individual words from a table row for candidate filtering fallback.

    Returns lowercase word tokens. Used as secondary filter when context
    keywords are all too generic.
    """
    if not tables or table_row_index is None:
        return []

    budget_lower = budget_item.lower() if budget_item else ""
    budget_slug_lower = budget_slug.lower() if budget_slug else ""

    words = set()
    for table in tables:
        cells = table.get("cells", []) if isinstance(table, dict) else []
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            if cell.get("row_index") != table_row_index:
                continue
            cell_text = cell.get("text", "").strip()
            if not cell_text:
                continue
            stripped = re.sub(r'[$,.\s]', '', cell_text)
            if stripped.isdigit():
                continue
            cleaned = re.sub(r'[&\-]', '', cell_text)
            for w in re.findall(r"[a-zA-Z][a-zA-Z0-9]+", cleaned):
                wl = w.lower()
                if _is_budget_item_text(wl, budget_lower, budget_slug_lower):
                    continue
                words.add(wl)

    return list(words)


def _tokenize(texts):
    """Convert a list of text strings into a set of lowercase alphanumeric tokens."""
    tokens = set()
    for rt in texts:
        rt_clean = rt.strip().lower()
        if not rt_clean:
            continue
        cleaned = re.sub(r'[&\-]', '', rt_clean)
        words = re.findall(r"[a-zA-Z][a-zA-Z0-9]+", cleaned)
        tokens.update(words)
    return tokens


def _find_distinctive_tokens(tokens, page_text_map):
    """Return tokens that appear on <50% of pages (distinctive for this document)."""
    total_pages = len(page_text_map)
    if total_pages <= 2:
        return tokens

    all_page_texts = list(page_text_map.values())
    distinctive = set()
    for token in tokens:
        count = sum(1 for pt in all_page_texts if _token_in_page(token, pt))
        if count / total_pages < 0.5:
            distinctive.add(token)
        else:
            logger.info(
                f"Row text filter: '{token}' on {count}/{total_pages} pages, generic"
            )
    return distinctive


def _token_in_page(token, page_text):
    """Check if a token appears in page text, with tolerance for spaces in
    alphanumeric identifiers (e.g. 'pp21' matches 'PP 21' in text).
    """
    if token in page_text:
        return True
    # For mixed letter-digit tokens (pp21, q3, etc.), allow optional whitespace
    # at letter-digit and digit-letter boundaries.
    has_letter = any(c.isalpha() for c in token)
    has_digit = any(c.isdigit() for c in token)
    if has_letter and has_digit:
        # Build regex: insert \s* at each letter↔digit boundary
        parts = []
        for i, ch in enumerate(token):
            parts.append(re.escape(ch))
            if i < len(token) - 1:
                curr_is_digit = ch.isdigit()
                next_is_digit = token[i + 1].isdigit()
                if curr_is_digit != next_is_digit:
                    parts.append(r"\s*")
        pattern = "".join(parts)
        if re.search(pattern, page_text):
            return True
    return False


def _recover_combined_matches(candidates, pages, amount, row_texts,
                               table_row_texts, doc_id):
    """Scan for pages with BOTH the target amount AND a distinctive token.

    The standard matching engine only returns the SINGLE best page for amount
    matching — if it picks the wrong page, the correct page ends up with only
    a low keyword score. This function directly checks every page for the
    combination of amount + keyword, like a user's boolean search, and injects
    high-confidence candidates that the standard engine missed.
    """
    page_text_map = {p.page_number: p.text.lower() for p in pages}

    # Union distinctive tokens from both keyword context and table cell words
    # (same logic as _filter_candidates_by_row_texts).
    tokens = set()
    keyword_tokens = _tokenize(row_texts or [])
    if keyword_tokens:
        tokens.update(_find_distinctive_tokens(keyword_tokens, page_text_map))
    if table_row_texts:
        table_tokens = _tokenize(table_row_texts)
        tokens.update(_find_distinctive_tokens(table_tokens, page_text_map))
    if not tokens:
        return candidates

    # Prefer specific tokens (<10% of pages) for recovery too — avoids
    # injecting false positives from generic tokens like "telephone".
    total_pages = len(page_text_map)
    all_page_texts = list(page_text_map.values())
    specific = set()
    if total_pages > 2:
        for token in tokens:
            count = sum(1 for pt in all_page_texts if _token_in_page(token, pt))
            if count / total_pages < 0.10:
                specific.add(token)
    search_tokens = specific if specific else tokens

    target = float(amount)
    # Tight tolerance for recovery — this is a fallback, so we only want
    # amounts that genuinely match (not near-misses like $149.19 ≈ $149.20).
    tolerance = 0.005

    # Pages already covered by high-score candidates
    existing_pages = set()
    for c in candidates:
        if c.score >= 0.80:
            existing_pages.update(c.page_numbers)

    # Scan all pages for combined amount+keyword match
    new_candidates = []
    for page in pages:
        if page.page_number in existing_pages:
            continue

        # Check if page has the target amount — first from Bedrock-extracted
        # entities, then by scanning the raw page text. The text fallback
        # catches amounts that Bedrock missed (e.g., "$ 2.68" with a space,
        # or amounts at the very bottom of the page).
        page_amounts = page.entities.get("amounts", [])
        has_amount = False
        for a in page_amounts:
            try:
                v = float(a.get("value", 0))
                if abs(v - target) < tolerance:
                    has_amount = True
                    break
            except (ValueError, TypeError):
                continue
        if not has_amount:
            # Text fallback: search for the amount string in the page text.
            # Uses regex with word boundaries to avoid false positives
            # (e.g., "72" matching page numbers).  Handles OCR variations:
            #   "$72.00", "$ 72.00", "$72", "$ 72", "72.00"
            pt = page_text_map.get(page.page_number, "")
            amount_str = f"{target:.2f}"
            # Build regex patterns for amount with word boundaries
            patterns = [re.escape(amount_str)]  # "72.00"
            int_amount = int(target)
            if target == int_amount:
                # Whole dollar: match "$ 72" or "$72" but not bare "72"
                patterns.append(r"\$\s*" + str(int_amount) + r"(?![.\d])")
            for pat in patterns:
                if re.search(pat, pt):
                    has_amount = True
                    break
        if not has_amount:
            continue

        # Check if page has a specific/distinctive token
        pt = page_text_map.get(page.page_number, "")
        has_token = any(_token_in_page(tok, pt) for tok in search_tokens)
        if not has_token:
            continue

        # Found a combined match — create a high-confidence candidate
        matching_tokens = [tok for tok in search_tokens if _token_in_page(tok, pt)]
        amount_str = f"{target:.2f}"
        search_terms = [amount_str, f"${amount_str}"]
        highlights = {
            page.page_number: find_word_boxes_for_terms(page, search_terms)
        }

        new_candidates.append(CandidateEvidenceSet(
            doc_id=doc_id,
            page_numbers=[page.page_number],
            score=0.95,
            rationale=[
                f"Combined match: amount ${amount_str} + keywords {matching_tokens}",
                "Amount and vendor/context both found on this page",
            ],
            evidence_snippets=[],
            highlights=highlights,
        ))
        logger.info(
            f"Combined recovery: page {page.page_number} has ${amount_str} + "
            f"{matching_tokens} → score 0.95"
        )

    if not new_candidates:
        return candidates

    # Merge: combined recovery candidates first (they have BOTH amount + keyword),
    # then existing candidates (deduplicated).  Combined candidates are stronger
    # evidence than amount-only matches because they verify two independent signals.
    new_pages = {pn for c in new_candidates for pn in c.page_numbers}
    merged = list(new_candidates)
    for c in candidates:
        # Keep existing candidates that don't overlap with new ones
        if not any(pn in new_pages for pn in c.page_numbers):
            merged.append(c)
    merged.sort(key=lambda c: c.score, reverse=True)
    return merged


def _find_gl_duplicates(source_text, pages, threshold=0.85):
    """Find pages that are near-duplicates of the GL source page.

    Uses Jaccard similarity on word token sets.  The same GL summary page
    often appears in multiple physical PDFs that get merged into one virtual
    document — this detects those copies even though they have different
    virtual page numbers.

    Args:
        source_text: Full text of the GL source page.
        pages: List of PageRecord candidates.
        threshold: Jaccard similarity threshold (0-1).  0.85 is conservative
            enough to avoid false positives (genuine evidence pages rarely
            share 85%+ of their vocabulary with a GL summary).

    Returns:
        Set of virtual page numbers that are near-duplicates.
    """
    source_tokens = set(re.findall(r'\w+', source_text.lower()))
    if not source_tokens:
        return set()

    duplicates = set()
    for page in pages:
        page_tokens = set(re.findall(r'\w+', page.text.lower()))
        if not page_tokens:
            continue
        intersection = len(source_tokens & page_tokens)
        union = len(source_tokens | page_tokens)
        jaccard = intersection / union
        if jaccard >= threshold:
            duplicates.add(page.page_number)
            logger.debug(
                f"GL duplicate: page {page.page_number} jaccard={jaccard:.3f}"
            )

    return duplicates


def _filter_candidates_by_row_texts(candidates, pages, row_texts,
                                     table_row_texts=None):
    """Discard candidates whose pages don't contain distinctive context words.

    Strategy:
    1. Union distinctive tokens (<50% of pages) from both keyword context
       (row_texts) and table cell words (table_row_texts).
    2. Among those, identify "specific" tokens (<10% of pages) — these are
       vendor names like "dialpad" or "granite" that uniquely identify the
       correct pages. Generic-but-distinctive tokens like "telephone" (~20-40%
       of telecom pages) cause false positives.
    3. If specific tokens exist, use ONLY those for filtering.
       Otherwise, fall back to all distinctive tokens.
    4. If everything is generic (>50%), skip filtering.

    For multi-page candidates (>3 pages), requires that at least 2 pages
    individually contain a filter token.
    """
    if not row_texts and not table_row_texts:
        return candidates

    page_text_map = {p.page_number: p.text.lower() for p in pages}

    # Union distinctive tokens from both keyword context and table cell words.
    tokens = set()
    keyword_tokens = _tokenize(row_texts or [])
    if keyword_tokens:
        tokens.update(_find_distinctive_tokens(keyword_tokens, page_text_map))
    if table_row_texts:
        table_tokens = _tokenize(table_row_texts)
        table_distinctive = _find_distinctive_tokens(table_tokens, page_text_map)
        if table_distinctive - tokens:
            logger.info(f"Row text filter: adding table tokens={table_distinctive - tokens}")
        tokens.update(table_distinctive)

    if not tokens:
        logger.info("Row text filter: no distinctive tokens, skipping filter")
        return candidates

    # Among distinctive tokens, find "specific" ones (<10% of pages).
    # These are vendor/product names that strongly identify the correct pages.
    # Tokens like "telephone" may be distinctive (<50%) but still appear on
    # many telecom pages, causing false positives.
    total_pages = len(page_text_map)
    all_page_texts = list(page_text_map.values())
    specific = set()
    if total_pages > 2:
        for token in tokens:
            count = sum(1 for pt in all_page_texts if _token_in_page(token, pt))
            if count / total_pages < 0.10:
                specific.add(token)

    # Prefer specific tokens when available; fall back to all distinctive
    filter_tokens = specific if specific else tokens
    logger.info(
        f"Row text filter: distinctive={tokens}, specific={specific}, "
        f"filtering with={filter_tokens}"
    )

    kept = []
    for candidate in candidates:
        pns = candidate.page_numbers

        # Never discard single-page exact amount matches — the dollar amount
        # is a stronger signal than keyword context.  This prevents the filter
        # from removing the correct page when its text uses a slightly different
        # format for the vendor name (e.g., "DialPad Inc" vs "dialpad").
        has_exact_amount = (
            candidate.score >= 0.90
            and len(pns) == 1
            and any("Exact amount match" in r for r in candidate.rationale)
        )
        if has_exact_amount:
            kept.append(candidate)
            continue

        # Count how many pages individually contain a filter token
        matching_pages = sum(
            1 for pn in pns
            if any(_token_in_page(token, page_text_map.get(pn, "")) for token in filter_tokens)
        )

        # Single/small candidates: at least 1 page must match
        # Multi-page candidates (>3): at least 2 pages must match
        min_required = 2 if len(pns) > 3 else 1

        if matching_pages >= min_required:
            kept.append(candidate)
        else:
            logger.info(
                f"Row text filter: discarded candidate pages={pns} "
                f"score={candidate.score:.2f} "
                f"(matching_pages={matching_pages}, required={min_required})"
            )
    return kept


def _extract_keywords_from_context(context, budget_item=""):
    """Extract meaningful keywords from an amount's context string.

    Handles compound names by collapsing hyphens/ampersands:
        T-Mobile → tmobile, AT&T → att, PR & Tax PP21 → [pr, tax, pp21]
    Allows 2-char tokens for abbreviations (PR, Q3) since short codes
    are often highly distinctive identifiers.
    Filters out words that match the budget item name (e.g., "telecommunications"
    from a Telecommunications context) since they provide no discriminating value.
    """
    if not context:
        return []
    stop_words = {
        "the", "and", "for", "from", "with", "this", "that", "was", "are",
        "has", "had", "have", "been", "will", "would", "could", "should",
        "not", "but", "all", "any", "can", "her", "his", "its", "our",
        "per", "than", "too", "very",
    }
    # Build budget item word set for filtering
    budget_words = set()
    if budget_item:
        budget_cleaned = re.sub(r'[&\-/]', ' ', budget_item.lower())
        budget_words = {w for w in budget_cleaned.split() if len(w) >= 2}

    # Collapse hyphens and ampersands to join compound names
    cleaned = re.sub(r'[&\-]', '', context)
    # Find alphanumeric tokens starting with a letter, 2+ chars
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9]+", cleaned)
    keywords = [
        w.lower() for w in words
        if w.lower() not in stop_words and w.lower() not in budget_words
    ]
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
