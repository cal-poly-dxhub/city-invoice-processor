"""Tests for Salary matching logic."""

from decimal import Decimal
import pytest
from invoice_recon.models import LineItem, PageRecord
from invoice_recon.matching import (
    normalize_name,
    score_page_for_employee,
    generate_candidates_for_line_item,
)


def test_normalize_name():
    """Test name normalization."""
    assert normalize_name("John Doe") == "john doe"
    assert normalize_name("  Mary   Anne  ") == "mary anne"
    assert normalize_name("O'Brien") == "obrien"
    assert normalize_name("Smith-Jones") == "smithjones"
    assert normalize_name(None) == ""


def test_score_page_for_employee_last_name_match():
    """Test scoring when last name matches."""
    line_item = LineItem(
        row_id="row_0",
        row_index=0,
        budget_item="Salary",
        employee_first_name="John",
        employee_last_name="Doe",
        raw={},
    )

    page = PageRecord(
        doc_id="salary",
        page_number=1,
        text_source="pymupdf",
        text="Timecard for employee",
        entities={
            "doc_type": "timecard",
            "people": [
                {
                    "full_name": "John Doe",
                    "first_name": "John",
                    "last_name": "Doe",
                }
            ],
        },
    )

    score, rationale = score_page_for_employee(line_item, page)

    # Should get: +0.70 (last name) + 0.20 (first name) + 0.10 (doc_type)
    assert score >= 0.95  # Allow some fuzzy match bonus
    assert any("Last name match" in r for r in rationale)
    assert any("First name match" in r for r in rationale)
    assert any("Doc type: timecard" in r for r in rationale)


def test_score_page_for_employee_no_match():
    """Test scoring when employee doesn't match."""
    line_item = LineItem(
        row_id="row_0",
        row_index=0,
        budget_item="Salary",
        employee_first_name="John",
        employee_last_name="Doe",
        raw={},
    )

    page = PageRecord(
        doc_id="salary",
        page_number=1,
        text_source="pymupdf",
        text="Some other content",
        entities={
            "doc_type": "unknown",
            "people": [
                {
                    "full_name": "Jane Smith",
                    "first_name": "Jane",
                    "last_name": "Smith",
                }
            ],
        },
    )

    score, rationale = score_page_for_employee(line_item, page)

    assert score == 0.0
    assert any("No matching person" in r for r in rationale)


def test_score_page_for_employee_first_initial_match():
    """Test scoring with first initial match."""
    line_item = LineItem(
        row_id="row_0",
        row_index=0,
        budget_item="Salary",
        employee_first_name="John",
        employee_last_name="Doe",
        raw={},
    )

    page = PageRecord(
        doc_id="salary",
        page_number=1,
        text_source="pymupdf",
        text="Employee timecard",
        entities={
            "doc_type": "timecard",
            "people": [
                {
                    "full_name": "J. Doe",
                    "first_name": "J",
                    "last_name": "Doe",
                }
            ],
        },
    )

    score, rationale = score_page_for_employee(line_item, page)

    # Should get: +0.70 (last name) + 0.20 (first initial) + 0.10 (doc_type)
    assert score >= 0.95  # Allow some fuzzy match bonus
    assert any("Last name match" in r for r in rationale)
    assert any("First initial match" in r for r in rationale)


def test_generate_candidates_for_salary():
    """Test candidate generation for Salary line item."""
    line_item = LineItem(
        row_id="row_0",
        row_index=0,
        budget_item="Salary",
        amount=Decimal("5000.00"),
        employee_first_name="John",
        employee_last_name="Doe",
        raw={},
    )

    pages = [
        PageRecord(
            doc_id="salary",
            page_number=1,
            text_source="pymupdf",
            text="Timecard for John Doe",
            entities={
                "doc_type": "timecard",
                "people": [
                    {
                        "full_name": "John Doe",
                        "first_name": "John",
                        "last_name": "Doe",
                    }
                ],
            },
        ),
        PageRecord(
            doc_id="salary",
            page_number=2,
            text_source="pymupdf",
            text="Paystub for John Doe",
            entities={
                "doc_type": "paystub",
                "people": [
                    {
                        "full_name": "John Doe",
                        "first_name": "John",
                        "last_name": "Doe",
                    }
                ],
            },
        ),
        PageRecord(
            doc_id="salary",
            page_number=3,
            text_source="pymupdf",
            text="Other employee Jane Smith",
            entities={
                "doc_type": "timecard",
                "people": [
                    {
                        "full_name": "Jane Smith",
                        "first_name": "Jane",
                        "last_name": "Smith",
                    }
                ],
            },
        ),
    ]

    candidates = generate_candidates_for_line_item(line_item, pages)

    # Should have candidates
    assert len(candidates) > 0

    # Best candidate should include pages 1 and 2 (John Doe pages)
    best_candidate = candidates[0]
    assert best_candidate.doc_id == "salary"
    assert 1 in best_candidate.page_numbers
    assert 2 in best_candidate.page_numbers
    assert best_candidate.score > 0.7  # High score for strong matches


def test_generate_candidates_empty_pages():
    """Test candidate generation with no pages."""
    line_item = LineItem(
        row_id="row_0",
        row_index=0,
        budget_item="Salary",
        employee_first_name="John",
        employee_last_name="Doe",
        raw={},
    )

    candidates = generate_candidates_for_line_item(line_item, [])
    assert len(candidates) == 0
