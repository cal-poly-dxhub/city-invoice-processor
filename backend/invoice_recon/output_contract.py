"""Output reconciliation results to JSON."""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from invoice_recon.config import Config
from invoice_recon.models import (
    CandidateEvidenceSet,
    DocumentRef,
    InputsInfo,
    JobInfo,
    LineItem,
    LineItemOutput,
    NavigationGroup,
    PageRecord,
    PageWordData,
    ReconciliationOutput,
    SelectedEvidence,
    UserEdits,
)

logger = logging.getLogger(__name__)


def build_normalized_fields(line_item: LineItem) -> Dict[str, Any]:
    """Build normalized fields dict from a LineItem."""
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
    return normalized


def write_reconciliation_output(
    job_id: str,
    csv_path: Path,
    pdf_dir: Path,
    documents: List[DocumentRef],
    navigation_groups: List[NavigationGroup],
    line_items: List[LineItem],
    candidates_map: Dict[str, List[CandidateEvidenceSet]],
    selected_evidence_map: Dict[str, SelectedEvidence],
    pdf_mappings: List[Dict[str, str]],
    pages_by_doc: Optional[Dict[str, List[PageRecord]]] = None,
) -> Path:
    """
    Write reconciliation output to JSON.

    Args:
        job_id: Job ID
        csv_path: Path to CSV file
        pdf_dir: Path to PDF directory
        documents: List of DocumentRef (virtual combined documents)
        navigation_groups: List of NavigationGroup
        line_items: List of LineItem
        candidates_map: Map of row_id -> candidates
        selected_evidence_map: Map of row_id -> selected evidence
        pdf_mappings: List of PDF mappings from discovery
        pages_by_doc: Map of doc_id -> virtual PageRecords (with renumbered pages)

    Returns:
        Path to written reconciliation.json
    """
    artifacts_dir = Config.get_artifacts_dir(job_id)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    output_path = artifacts_dir / "reconciliation.json"

    # Build job info
    job_info = JobInfo(
        job_id=job_id,
        created_at=datetime.utcnow().isoformat() + "Z",
        aws_region=Config.AWS_REGION,
        bedrock_model_id=Config.BEDROCK_MODEL_ID,
        textract_mode=Config.TEXTRACT_MODE,
    )

    # Build inputs info
    inputs_info = InputsInfo(
        csv_path=str(csv_path),
        pdf_dir=str(pdf_dir),
        documents=pdf_mappings,
    )

    # Populate pages_data for each document (for search highlighting)
    # Use pre-built virtual pages if available (supports multi-file virtual documents)
    for doc_ref in documents:
        if pages_by_doc and doc_ref.doc_id in pages_by_doc:
            virtual_pages = pages_by_doc[doc_ref.doc_id]
            for page in virtual_pages:
                doc_ref.pages_data[page.page_number] = PageWordData(
                    words=page.words,
                    text=page.text,
                )
            logger.info(f"Exported {len(virtual_pages)} virtual pages of word data for {doc_ref.doc_id}")
        else:
            # Fallback: load from SQLite (for single-file compat)
            from invoice_recon.index_store import IndexStore
            index_store = IndexStore(artifacts_dir / "index.sqlite")
            pages = index_store.get_all_pages_for_document(doc_ref.doc_id)
            for page in pages:
                doc_ref.pages_data[page.page_number] = PageWordData(
                    words=page.words,
                    text=page.text,
                )
            logger.info(f"Exported {len(pages)} pages of word data for {doc_ref.doc_id}")

    # Build line item outputs
    line_item_outputs = []
    for line_item in line_items:
        candidates = candidates_map.get(line_item.row_id, [])
        selected = selected_evidence_map.get(
            line_item.row_id,
            SelectedEvidence(doc_id=None, page_numbers=[], selection_source="auto"),
        )

        line_item_output = LineItemOutput(
            row_id=line_item.row_id,
            row_index=line_item.row_index,
            budget_item=line_item.budget_item,
            raw=line_item.raw,
            normalized=build_normalized_fields(line_item),
            candidates=candidates,
            selected_evidence=selected,
        )

        line_item_outputs.append(line_item_output)

    # Build reconciliation output
    reconciliation = ReconciliationOutput(
        job=job_info,
        inputs=inputs_info,
        documents=documents,
        navigation_groups=navigation_groups,
        line_items=line_item_outputs,
    )

    # Write to file
    with open(output_path, "w") as f:
        json.dump(
            reconciliation.model_dump(mode="json"),
            f,
            indent=2,
            ensure_ascii=False,
        )

    logger.info(f"Wrote reconciliation output to {output_path}")
    return output_path


def load_user_edits(job_id: str) -> Optional[UserEdits]:
    """
    Load user edits overlay if present.

    Args:
        job_id: Job ID

    Returns:
        UserEdits object or None if not present
    """
    artifacts_dir = Config.get_artifacts_dir(job_id)
    user_edits_path = artifacts_dir / "user_edits.json"

    if not user_edits_path.exists():
        return None

    try:
        with open(user_edits_path) as f:
            data = json.load(f)
        return UserEdits(**data)
    except Exception as e:
        logger.error(f"Failed to load user edits: {e}")
        return None


def apply_user_edits(
    selected_evidence_map: Dict[str, SelectedEvidence],
    user_edits: UserEdits,
) -> None:
    """
    Apply user edits to selected evidence map (in-place).

    Args:
        selected_evidence_map: Map of row_id -> SelectedEvidence
        user_edits: UserEdits object
    """
    for override in user_edits.overrides:
        selected_evidence_map[override.row_id] = SelectedEvidence(
            doc_id=override.doc_id,
            page_numbers=override.page_numbers,
            selection_source="user",
        )
        logger.info(
            f"Applied user edit for {override.row_id}: "
            f"{override.doc_id} pages {override.page_numbers}"
        )
