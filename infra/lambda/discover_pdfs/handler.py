"""Step 2: Discover PDFs Lambda handler."""

import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))

from shared.s3_utils import list_keys

from invoice_recon.budget_items import (
    match_filename_to_budget_item,
    get_budget_item_slug,
    slugify,
)

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    """
    Discover PDFs in S3 and map to budget items.

    Input:  {job_id, bucket, pdf_prefix}
    Output: {job_id, pdf_mappings: [{doc_id, budget_item, s3_key}, ...]}
    """
    job_id = event["job_id"]
    pdf_prefix = event["pdf_prefix"]  # e.g. "uploads/{job_id}/pdf/"

    logger.info(f"DiscoverPDFs: job_id={job_id}, prefix={pdf_prefix}")

    # List all PDFs under the prefix
    pdf_keys = list_keys(pdf_prefix, suffix=".pdf")
    logger.info(f"DiscoverPDFs: found {len(pdf_keys)} PDF files")

    # Classify into flat files vs subdirectory files
    # Flat:  uploads/{job_id}/pdf/Salary.pdf
    # Subdir: uploads/{job_id}/pdf/salary/payroll_jan.pdf
    results = []
    budget_items_with_subdirs = set()

    # Phase 1: Detect subdirectories
    subdir_files = {}  # budget_item -> list of keys
    flat_files = []

    for key in sorted(pdf_keys):
        # Strip prefix to get relative path
        rel_path = key[len(pdf_prefix):]
        parts = rel_path.split("/")

        if len(parts) >= 2:
            # Subdirectory: first part is directory name
            dir_name = parts[0]
            budget_item = match_filename_to_budget_item(dir_name + ".pdf")
            if budget_item:
                budget_items_with_subdirs.add(budget_item)
                if budget_item not in subdir_files:
                    subdir_files[budget_item] = []
                subdir_files[budget_item].append({
                    "s3_key": key,
                    "rel_path": rel_path,
                    "filename": parts[-1],
                })
        else:
            # Flat file
            flat_files.append({
                "s3_key": key,
                "rel_path": rel_path,
                "filename": parts[0],
            })

    # Phase 2: Build mappings from subdirectories
    for budget_item, files in sorted(subdir_files.items()):
        budget_slug = get_budget_item_slug(budget_item)
        for file_info in files:
            file_slug = slugify(file_info["filename"].rsplit(".", 1)[0])
            doc_id = f"{budget_slug}__{file_slug}"
            results.append({
                "doc_id": doc_id,
                "budget_item": budget_item,
                "s3_key": file_info["s3_key"],
                "pdf_path": file_info["rel_path"],
                "filename": file_info["filename"],
            })

    # Phase 3: Build mappings from flat files (skip if subdir exists)
    for file_info in flat_files:
        budget_item = match_filename_to_budget_item(file_info["filename"])
        if not budget_item:
            logger.warning(f"No budget item match for: {file_info['filename']}")
            continue
        if budget_item in budget_items_with_subdirs:
            logger.warning(
                f"Skipping flat file {file_info['filename']}: "
                f"subdirectory exists for {budget_item}"
            )
            continue

        doc_id = get_budget_item_slug(budget_item)
        results.append({
            "doc_id": doc_id,
            "budget_item": budget_item,
            "s3_key": file_info["s3_key"],
            "pdf_path": file_info["rel_path"],
            "filename": file_info["filename"],
        })

    logger.info(f"DiscoverPDFs: mapped {len(results)} PDFs to budget items")
    for r in results:
        logger.info(f"  {r['filename']} -> {r['budget_item']} (doc_id={r['doc_id']})")

    return {
        "job_id": job_id,
        "pdf_mappings": results,
    }
