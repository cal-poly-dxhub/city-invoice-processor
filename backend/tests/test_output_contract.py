"""Tests for output contract and JSON generation."""

import json
import tempfile
from decimal import Decimal
from pathlib import Path
import pytest
from invoice_recon.models import (
    CandidateEvidenceSet,
    DocumentRef,
    LineItem,
    NavigationGroup,
    SelectedEvidence,
)
from invoice_recon.output_contract import (
    build_normalized_fields,
    write_reconciliation_output,
)
from invoice_recon.config import Config


def test_build_normalized_fields():
    """Test building normalized fields from line item."""
    line_item = LineItem(
        row_id="row_0",
        row_index=0,
        budget_item="Salary",
        amount=Decimal("5000.00"),
        hourly_rate=Decimal("50.00"),
        employee_first_name="John",
        employee_last_name="Doe",
        employee_title="Engineer",
        reporting_period="2024-01",
        agency_invoice_date="2024-01-31",
        explanation="January salary",
        raw={"original": "data"},
    )

    normalized = build_normalized_fields(line_item)

    assert normalized["budget_item"] == "Salary"
    assert normalized["amount"] == 5000.00
    assert normalized["hourly_rate"] == 50.00
    assert normalized["employee_first_name"] == "John"
    assert normalized["employee_last_name"] == "Doe"
    assert normalized["employee_title"] == "Engineer"
    assert normalized["reporting_period"] == "2024-01"
    assert normalized["agency_invoice_date"] == "2024-01-31"
    assert normalized["explanation"] == "January salary"


def test_build_normalized_fields_with_nulls():
    """Test normalized fields with missing values."""
    line_item = LineItem(
        row_id="row_0",
        row_index=0,
        budget_item="Equipment",
        raw={},
    )

    normalized = build_normalized_fields(line_item)

    assert normalized["budget_item"] == "Equipment"
    assert normalized["amount"] is None
    assert normalized["hourly_rate"] is None
    assert normalized["employee_first_name"] is None


def test_write_reconciliation_output():
    """Test writing reconciliation output to JSON."""
    with tempfile.TemporaryDirectory() as tmpdir:
        job_id = "test_job_001"
        csv_path = Path(tmpdir) / "test.csv"
        pdf_dir = Path(tmpdir) / "pdfs"
        pdf_dir.mkdir()

        # Create dummy CSV
        csv_path.write_text("Budget Item,Amount\nSalary,5000.00\n")

        # Mock data
        documents = [
            DocumentRef(
                doc_id="salary",
                budget_item="Salary",
                path=str(pdf_dir / "Salary.pdf"),
                file_sha256="abc123",
                page_count=10,
            )
        ]

        navigation_groups = [
            NavigationGroup(
                group_id="bi:salary",
                label="Salary",
                budget_item="Salary",
                employee_key=None,
                line_item_ids=["row_0"],
            )
        ]

        line_items = [
            LineItem(
                row_id="row_0",
                row_index=0,
                budget_item="Salary",
                amount=Decimal("5000.00"),
                employee_first_name="John",
                employee_last_name="Doe",
                raw={"budget_item": "Salary", "amount": "5000.00"},
            )
        ]

        candidates_map = {
            "row_0": [
                CandidateEvidenceSet(
                    doc_id="salary",
                    page_numbers=[1, 2, 3],
                    score=0.95,
                    rationale=["Strong match"],
                    evidence_snippets=[],
                )
            ]
        }

        selected_evidence_map = {
            "row_0": SelectedEvidence(
                doc_id="salary",
                page_numbers=[1, 2, 3],
                selection_source="auto",
            )
        }

        pdf_mappings = [{"budget_item": "Salary", "path": str(pdf_dir / "Salary.pdf")}]

        # Set output dir to tmpdir
        original_output_dir = Config.OUTPUT_DIR
        try:
            Config.OUTPUT_DIR = tmpdir

            output_path = write_reconciliation_output(
                job_id=job_id,
                csv_path=csv_path,
                pdf_dir=pdf_dir,
                documents=documents,
                navigation_groups=navigation_groups,
                line_items=line_items,
                candidates_map=candidates_map,
                selected_evidence_map=selected_evidence_map,
                pdf_mappings=pdf_mappings,
            )

            # Check output file exists
            assert output_path.exists()

            # Load and validate JSON
            with open(output_path) as f:
                data = json.load(f)

            # Validate structure
            assert "job" in data
            assert data["job"]["job_id"] == job_id
            assert data["job"]["aws_region"] == "us-west-2"

            assert "inputs" in data
            assert data["inputs"]["csv_path"] == str(csv_path)

            assert "documents" in data
            assert len(data["documents"]) == 1
            assert data["documents"][0]["doc_id"] == "salary"

            assert "navigation_groups" in data
            assert len(data["navigation_groups"]) == 1

            assert "line_items" in data
            assert len(data["line_items"]) == 1

            line_item_output = data["line_items"][0]
            assert line_item_output["row_id"] == "row_0"
            assert line_item_output["budget_item"] == "Salary"

            # Check candidates
            assert len(line_item_output["candidates"]) == 1
            assert line_item_output["candidates"][0]["page_numbers"] == [1, 2, 3]

            # Check selected evidence
            selected = line_item_output["selected_evidence"]
            assert selected["doc_id"] == "salary"
            assert selected["page_numbers"] == [1, 2, 3]
            assert selected["selection_source"] == "auto"

            # Validate page numbers are 1-based
            assert all(p >= 1 for p in selected["page_numbers"])

        finally:
            Config.OUTPUT_DIR = original_output_dir
