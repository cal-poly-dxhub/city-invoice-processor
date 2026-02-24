"""Step 3: Index a single PDF document (container Lambda).

Orchestrates per-PDF processing:
1. Download PDF from S3
2. Phase 1: Sequential PyMuPDF prep (prepare_page_data for each page)
3. Phase 2: Parallel text extraction via direct Lambda.invoke (ResolvePage)
4. Phase 3: Parallel entity extraction via direct Lambda.invoke (ExtractEntities)
5. Write page data to S3, update DynamoDB cache
"""

import base64
import hashlib
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

from shared import cache
from shared.s3_utils import download_file, upload_json

from invoice_recon.pdf_extract import (
    compute_file_sha256,
    get_pdf_page_count,
    prepare_page_data,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)

lambda_client = None


def get_lambda_client():
    global lambda_client
    if lambda_client is None:
        # Increase read_timeout from default 60s to 300s — ExtractEntities
        # may need multiple Bedrock retries (50+ seconds each).
        lambda_client = boto3.client(
            "lambda",
            config=BotoConfig(read_timeout=300, retries={"max_attempts": 0}),
        )
    return lambda_client


def invoke_resolve_page(fn_arn: str, page_data: dict) -> dict:
    """Invoke ResolvePage Lambda synchronously with page data inline."""
    # Encode PNG bytes as base64 for JSON transport
    payload = {"page_data": dict(page_data)}
    if "png_bytes" in payload["page_data"] and payload["page_data"]["png_bytes"]:
        payload["page_data"]["png_bytes_b64"] = base64.b64encode(
            payload["page_data"]["png_bytes"]
        ).decode("ascii")
        del payload["page_data"]["png_bytes"]

    # Serialize Pydantic TableStructure objects to dicts for JSON transport
    tables = payload["page_data"].get("pymupdf_tables")
    if tables:
        payload["page_data"]["pymupdf_tables"] = [
            t.model_dump() if hasattr(t, "model_dump") else t for t in tables
        ]

    resp = get_lambda_client().invoke(
        FunctionName=fn_arn,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    result = json.loads(resp["Payload"].read())

    if "errorMessage" in result:
        raise RuntimeError(f"ResolvePage failed: {result['errorMessage']}")

    return result


def invoke_extract_entities(fn_arn: str, payload: dict) -> dict:
    """Invoke ExtractEntities Lambda synchronously."""
    resp = get_lambda_client().invoke(
        FunctionName=fn_arn,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    result = json.loads(resp["Payload"].read())

    if "errorMessage" in result:
        raise RuntimeError(f"ExtractEntities failed: {result['errorMessage']}")

    return result


def lambda_handler(event, context):
    """
    Index a single PDF document.

    Input: {
        job_id, bucket, doc_id, budget_item, s3_key,
        resolve_page_fn_arn, extract_entities_fn_arn
    }
    Output: {doc_id, page_count, file_sha256}
    """
    job_id = event["job_id"]
    doc_id = event["doc_id"]
    budget_item = event["budget_item"]
    s3_key = event["s3_key"]
    resolve_page_fn_arn = event.get("resolve_page_fn_arn") or os.environ["RESOLVE_PAGE_FN_ARN"]
    extract_entities_fn_arn = event.get("extract_entities_fn_arn") or os.environ["EXTRACT_ENTITIES_FN_ARN"]

    logger.info(f"IndexDocument: {doc_id} ({budget_item}) from {s3_key}")

    # Download PDF to /tmp
    local_pdf = f"/tmp/{job_id}/{doc_id}.pdf"
    download_file(s3_key, local_pdf)
    pdf_path = Path(local_pdf)

    # Compute file hash for cache check
    file_sha256 = compute_file_sha256(pdf_path)

    # Check DynamoDB cache — skip extraction if unchanged, but still
    # write page data to S3 for this job_id so AssembleAndMatch can read it.
    if not cache.should_reextract_document(doc_id, file_sha256):
        logger.info(f"IndexDocument: {doc_id} unchanged, using cache")
        cached_doc = cache.get_document(doc_id)
        cached_page_count = int(cached_doc["page_count"])

        for pg_num in range(1, cached_page_count + 1):
            cached_page = cache.get_page(doc_id, pg_num)
            if cached_page:
                page_data_s3_key = f"jobs/{job_id}/page_data/{doc_id}/{pg_num}.json"
                upload_json(
                    {
                        "doc_id": doc_id,
                        "page_number": pg_num,
                        "text_source": cached_page.get("text_source", "pymupdf"),
                        "text": cached_page.get("text", ""),
                        "entities": cached_page.get("entities", {}),
                        "word_boxes": cached_page.get("words", []),
                        "tables": cached_page.get("tables"),
                    },
                    page_data_s3_key,
                )

        logger.info(f"IndexDocument: {doc_id} wrote {cached_page_count} cached pages to S3")
        return {
            "doc_id": doc_id,
            "page_count": cached_page_count,
            "file_sha256": file_sha256,
        }

    page_count = get_pdf_page_count(pdf_path)
    logger.info(f"IndexDocument: {doc_id} has {page_count} pages")

    # --- Phase 1: Sequential PyMuPDF prep ---
    # fitz is NOT thread-safe, so all PyMuPDF work happens here sequentially
    import fitz

    doc = fitz.open(str(pdf_path))
    prepared_pages = []

    for page_num in range(1, page_count + 1):
        page = doc[page_num - 1]
        page_data = prepare_page_data(page, page_num)
        prepared_pages.append(page_data)

    doc.close()
    logger.info(
        f"IndexDocument: Phase 1 done. "
        f"{sum(1 for p in prepared_pages if p['needs_api'])} pages need API calls"
    )

    # --- Phase 2: Parallel text extraction (ResolvePage Lambda) ---
    max_workers = int(os.environ.get("MAX_WORKERS", "5"))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for page_data in prepared_pages:
            if page_data["needs_api"]:
                future = pool.submit(
                    invoke_resolve_page, resolve_page_fn_arn, page_data
                )
                futures[future] = page_data["page_number"]

        for future in as_completed(futures):
            pg_num = futures[future]
            result = future.result()
            # Update the prepared page data with API results
            for pd in prepared_pages:
                if pd["page_number"] == pg_num:
                    pd["text"] = result["text"]
                    pd["text_source"] = result["text_source"]
                    pd["word_boxes"] = result["word_boxes"]
                    pd["tables"] = result.get("tables")
                    pd["needs_api"] = False
                    break

    logger.info("IndexDocument: Phase 2 (text extraction) done")

    # Resolve pages that didn't need API (use PyMuPDF results directly)
    for pd in prepared_pages:
        if "text" not in pd or pd.get("needs_api"):
            pd["text"] = pd.get("pymupdf_text", "")
            pd["text_source"] = "pymupdf"
            pd["word_boxes"] = pd.get("pymupdf_word_boxes", [])
            pd["tables"] = pd.get("pymupdf_tables")

    # --- Phase 3: Parallel entity extraction (ExtractEntities Lambda) ---
    # Check cache per page, only extract for pages with changed text
    pages_needing_entities = []
    cached_entities = {}

    for pd in prepared_pages:
        pg_num = pd["page_number"]
        text_sha256 = hashlib.sha256(pd["text"].encode("utf-8")).hexdigest()

        if not cache.should_reextract_entities(doc_id, pg_num, text_sha256):
            cached_page = cache.get_page(doc_id, pg_num)
            if cached_page:
                cached_entities[pg_num] = cached_page.get("entities", {})
                continue

        pages_needing_entities.append(pd)

    logger.info(
        f"IndexDocument: {len(cached_entities)} pages cached, "
        f"{len(pages_needing_entities)} need entity extraction"
    )

    extracted_entities = {}

    if pages_needing_entities:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            for pd in pages_needing_entities:
                tables_raw = pd.get("tables")
                if tables_raw:
                    tables_serialized = [
                        t.model_dump() if hasattr(t, "model_dump") else t
                        for t in tables_raw
                    ]
                else:
                    tables_serialized = tables_raw
                payload = {
                    "doc_id": doc_id,
                    "budget_item": budget_item,
                    "page_number": pd["page_number"],
                    "text": pd["text"],
                    "text_source": pd["text_source"],
                    "word_boxes": pd.get("word_boxes", []),
                    "tables": tables_serialized,
                }
                future = pool.submit(
                    invoke_extract_entities, extract_entities_fn_arn, payload
                )
                futures[future] = pd["page_number"]

            for future in as_completed(futures):
                pg_num = futures[future]
                result = future.result()
                extracted_entities[pg_num] = result["entities"]

    logger.info("IndexDocument: Phase 3 (entity extraction) done")

    # --- Phase 4: Persist to DynamoDB + write page data to S3 ---
    cache.upsert_document(doc_id, budget_item, file_sha256, page_count)

    for pd in prepared_pages:
        pg_num = pd["page_number"]
        entities = extracted_entities.get(pg_num, cached_entities.get(pg_num, {}))

        # Update DynamoDB cache
        cache.upsert_page(
            doc_id=doc_id,
            page_number=pg_num,
            text_source=pd["text_source"],
            text=pd["text"],
            entities=entities,
            words=pd.get("word_boxes"),
            tables=pd.get("tables"),
        )

        # Write page data JSON to S3
        page_data_s3_key = f"jobs/{job_id}/page_data/{doc_id}/{pg_num}.json"
        upload_json(
            {
                "doc_id": doc_id,
                "page_number": pg_num,
                "text_source": pd["text_source"],
                "text": pd["text"],
                "entities": entities,
                "word_boxes": pd.get("word_boxes", []),
                "tables": pd.get("tables"),
            },
            page_data_s3_key,
        )

    logger.info(f"IndexDocument: {doc_id} complete ({page_count} pages)")

    return {
        "doc_id": doc_id,
        "page_count": page_count,
        "file_sha256": file_sha256,
    }
