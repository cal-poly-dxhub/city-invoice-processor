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
) -> Path:
    """
    Write reconciliation output to JSON.

    Args:
        job_id: Job ID
        csv_path: Path to CSV file
        pdf_dir: Path to PDF directory
        documents: List of DocumentRef
        navigation_groups: List of NavigationGroup
        line_items: List of LineItem
        candidates_map: Map of row_id -> candidates
        selected_evidence_map: Map of row_id -> selected evidence
        pdf_mappings: List of PDF mappings from discovery

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
