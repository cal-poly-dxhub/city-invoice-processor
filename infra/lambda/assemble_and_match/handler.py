"""Step 4: Assemble virtual documents and run matching."""

import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

from shared import cache
from shared.s3_utils import download_json, list_keys, upload_json

from invoice_recon.budget_items import get_budget_item_slug
from invoice_recon.matching import (
    generate_candidates_for_line_item,
    select_default_evidence,
)
from invoice_recon.models import (
    CandidateEvidenceSet,
    DocumentRef,
    LineItem,
    PageRecord,
    PageWordData,
    SelectedEvidence,
    SourceFile,
)
from invoice_recon.navigation_groups import build_navigation_groups

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Minimum candidate score (same as Config.MIN_CANDIDATE_SCORE default)
MIN_CANDIDATE_SCORE = float(os.environ.get("MIN_CANDIDATE_SCORE", "0.1"))


def lambda_handler(event, context):
    """
    Assemble virtual documents, run matching, write reconciliation.json to S3.

    Input: {job_id, bucket, line_items_key, pdf_mappings: [...]}
    Output: {job_id, output_key}
    """
    job_id = event["job_id"]
    pdf_mappings = event["pdf_mappings"]

    # Load line items from S3 (written by ParseCSV to avoid 256KB payload limit)
    line_items_key = event["line_items_key"]
    line_items_data = download_json(line_items_key)
    logger.info(f"AssembleAndMatch: loaded {len(line_items_data)} line items from {line_items_key}")

    logger.info(f"AssembleAndMatch: job_id={job_id}")
    logger.info(f"  {len(line_items_data)} line items, {len(pdf_mappings)} PDF mappings")

    # Deserialize line items
    line_items = [LineItem(**item) for item in line_items_data]

    # --- Load page data from S3 ---
    # Group pdf_mappings by budget item for virtual document assembly
    physical_docs = {}  # doc_id -> {budget_item, pages: [...], pdf_path, filename}

    for mapping in pdf_mappings:
        doc_id = mapping["doc_id"]
        prefix = f"jobs/{job_id}/page_data/{doc_id}/"
        page_keys = list_keys(prefix, suffix=".json")

        pages = []
        for page_key in sorted(page_keys):
            page_data = download_json(page_key)
            pages.append(page_data)

        physical_docs[doc_id] = {
            "budget_item": mapping["budget_item"],
            "pages": sorted(pages, key=lambda p: p["page_number"]),
            "pdf_path": mapping.get("pdf_path", ""),
            "filename": mapping.get("filename", ""),
            "page_count": len(pages),
        }

        logger.info(f"  Loaded {len(pages)} pages for {doc_id}")

    # --- Assemble virtual documents (same logic as cli.py Steps 3b) ---
    physical_by_budget = defaultdict(list)
    for doc_id, doc_info in physical_docs.items():
        physical_by_budget[doc_info["budget_item"]].append((doc_id, doc_info))

    documents = []
    pages_by_doc = {}

    for budget_item, phys_docs_list in physical_by_budget.items():
        # Sort by pdf_path for stable ordering
        phys_docs_list.sort(key=lambda x: x[1].get("pdf_path", ""))

        source_files = []
        combined_pages = []
        offset = 0
        budget_slug = get_budget_item_slug(budget_item)

        for doc_id, doc_info in phys_docs_list:
            source_files.append(SourceFile(
                doc_id=doc_id,
                pdf_path=doc_info["pdf_path"],
                filename=doc_info["filename"],
                page_count=doc_info["page_count"],
                page_offset=offset,
            ))

            for page_data in doc_info["pages"]:
                virtual_page = PageRecord(
                    doc_id=budget_slug,
                    page_number=page_data["page_number"] + offset,
                    text_source=page_data["text_source"],
                    text=page_data["text"],
                    entities=page_data.get("entities", {}),
                    words=page_data.get("word_boxes", []),
                )
                combined_pages.append(virtual_page)

            offset += doc_info["page_count"]

        # Use first physical doc for file_sha256 (same convention as CLI)
        first_doc_id, first_doc_info = phys_docs_list[0]
        first_doc_cache = cache.get_document(first_doc_id)
        file_sha256 = first_doc_cache.get("file_sha256", "") if first_doc_cache else ""

        combined_doc = DocumentRef(
            doc_id=budget_slug,
            budget_item=budget_item,
            path="",  # S3-based, no local path
            file_sha256=file_sha256,
            page_count=offset,
            source_files=source_files,
            pdf_path=first_doc_info["pdf_path"],
            filename=first_doc_info["filename"],
        )
        documents.append(combined_doc)
        pages_by_doc[budget_slug] = combined_pages

        if len(phys_docs_list) > 1:
            logger.info(
                f"Combined {len(phys_docs_list)} files for {budget_item}: "
                f"{offset} total virtual pages"
            )

    logger.info(f"Assembled {len(documents)} virtual documents")

    # --- Build navigation groups ---
    navigation_groups = build_navigation_groups(line_items)
    logger.info(f"Created {len(navigation_groups)} navigation groups")

    # --- Match line items to candidates ---
    candidates_map = {}
    selected_evidence_map = {}

    for line_item in line_items:
        budget_item_slug = get_budget_item_slug(line_item.budget_item)
        pages = pages_by_doc.get(budget_item_slug, [])

        if not pages:
            logger.warning(f"No PDF found for budget item: {line_item.budget_item}")
            candidates_map[line_item.row_id] = []
            selected_evidence_map[line_item.row_id] = SelectedEvidence(
                doc_id=None, page_numbers=[], selection_source="auto"
            )
            continue

        candidates = generate_candidates_for_line_item(line_item, pages)

        filtered_candidates = [
            c for c in candidates if c.score >= MIN_CANDIDATE_SCORE
        ]

        candidates_map[line_item.row_id] = filtered_candidates

        selected = select_default_evidence(filtered_candidates)
        if not filtered_candidates and pages:
            selected = SelectedEvidence(
                doc_id=budget_item_slug,
                page_numbers=[],
                selection_source="auto",
            )
        selected_evidence_map[line_item.row_id] = selected

    logger.info(f"Generated candidates for {len(line_items)} line items")

    # --- Populate pages_data for documents ---
    for doc_ref in documents:
        virtual_pages = pages_by_doc.get(doc_ref.doc_id, [])
        for page in virtual_pages:
            doc_ref.pages_data[page.page_number] = PageWordData(
                words=page.words,
                text=page.text,
            )

    # --- Build reconciliation output ---
    line_item_outputs = []
    for line_item in line_items:
        candidates = candidates_map.get(line_item.row_id, [])
        selected = selected_evidence_map.get(
            line_item.row_id,
            SelectedEvidence(doc_id=None, page_numbers=[], selection_source="auto"),
        )

        normalized = {
            "budget_item": line_item.budget_item,
            "amount": float(line_item.amount) if line_item.amount else None,
            "hourly_rate": float(line_item.hourly_rate) if line_item.hourly_rate else None,
            "employee_first_name": line_item.employee_first_name,
            "employee_last_name": line_item.employee_last_name,
            "employee_title": line_item.employee_title,
            "reporting_period": line_item.reporting_period,
            "agency_invoice_date": line_item.agency_invoice_date,
            "explanation": line_item.explanation,
        }

        line_item_outputs.append({
            "row_id": line_item.row_id,
            "row_index": line_item.row_index,
            "budget_item": line_item.budget_item,
            "raw": line_item.raw,
            "normalized": normalized,
            "candidates": [c.model_dump(mode="json") for c in candidates],
            "selected_evidence": selected.model_dump(mode="json"),
        })

    reconciliation = {
        "job": {
            "job_id": job_id,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "aws_region": os.environ.get("AWS_REGION", "us-west-2"),
            "bedrock_model_id": os.environ.get(
                "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
            ),
            "textract_mode": os.environ.get("TEXTRACT_MODE", "auto"),
        },
        "inputs": {
            "csv_path": f"uploads/{job_id}/invoice.csv",
            "pdf_dir": f"uploads/{job_id}/pdf/",
            "documents": pdf_mappings,
        },
        "documents": [doc.model_dump(mode="json") for doc in documents],
        "navigation_groups": [ng.model_dump(mode="json") for ng in navigation_groups],
        "line_items": line_item_outputs,
    }

    # Write to S3
    output_key = f"jobs/{job_id}/reconciliation.json"
    upload_json(reconciliation, output_key)

    logger.info(f"AssembleAndMatch: wrote {output_key}")

    return {
        "job_id": job_id,
        "output_key": output_key,
    }
