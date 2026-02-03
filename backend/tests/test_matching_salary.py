"""Tests for Salary matching logic."""

from decimal import Decimal
import pytest
from invoice_recon.models import LineItem, PageRecord
from invoice_recon.matching import (
    normalize_name,
    score_page_for_employee,
    generate_candidates_for_line_item,
    calculate_proximity_score,
    find_name_pairs_with_proximity,
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

    # Should get: +0.35 (last name) + 0.30 (first name) + 0.10 (doc_type) + 0.10 (full name fuzzy) = 0.85
    # No proximity bonus because page has no word geometry
    assert score >= 0.80, f"Expected score >= 0.80, got {score}"
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

    # Should get: +0.35 (last name) + 0.15 (first initial) + 0.10 (doc_type) = 0.60
    # No proximity bonus because page has no word geometry
    assert score >= 0.55, f"Expected score >= 0.55, got {score}"
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


# =====================================================================
# Proximity-based matching tests
# =====================================================================


def test_calculate_proximity_score_same_line():
    """Test proximity calculation for names on same line."""
    # Names very close together (same line, adjacent horizontally)
    first_box = {"left": 0.1, "top": 0.5, "width": 0.05, "height": 0.02}
    last_box = {"left": 0.2, "top": 0.5, "width": 0.05, "height": 0.02}

    score = calculate_proximity_score(first_box, last_box)

    # Should get high score (very close)
    assert score >= 0.9, f"Expected score >= 0.9 for close names, got {score}"


def test_calculate_proximity_score_distant():
    """Test proximity calculation for names far apart."""
    # Names far apart (different sections of page)
    first_box = {"left": 0.1, "top": 0.2, "width": 0.05, "height": 0.02}
    last_box = {"left": 0.7, "top": 0.8, "width": 0.05, "height": 0.02}

    score = calculate_proximity_score(first_box, last_box)

    # Should get very low score (distant)
    assert score < 0.2, f"Expected score < 0.2 for distant names, got {score}"


def test_find_name_pairs_multiple_occurrences():
    """Test finding name pairs when names appear multiple times."""
    page = PageRecord(
        doc_id="test",
        page_number=1,
        text_source="pymupdf",
        text="John Smith at top, Jane Smith at bottom",
        entities={"people": []},
        words=[
            # John Smith at top (close together)
            {"text": "John", "left": 0.1, "top": 0.1, "width": 0.05, "height": 0.02},
            {"text": "Smith", "left": 0.2, "top": 0.1, "width": 0.05, "height": 0.02},
            # Jane Smith at bottom (close together)
            {"text": "Jane", "left": 0.1, "top": 0.8, "width": 0.05, "height": 0.02},
            {"text": "Smith", "left": 0.2, "top": 0.8, "width": 0.05, "height": 0.02},
        ],
    )

    # Search for John + Smith (should find 2 pairs: John-Smith@top and John-Smith@bottom)
    pairs = find_name_pairs_with_proximity(page, "john", "smith")

    # Should find 2 pairs (John with each Smith occurrence)
    assert len(pairs) == 2, f"Expected 2 pairs, got {len(pairs)}"

    # Best pair should be John-Smith@top (close together)
    first_box, last_box, proximity_score = pairs[0]
    assert proximity_score > 0.9, f"Expected best pair to have high proximity, got {proximity_score}"

    # Both boxes should be at top of page
    assert first_box["top"] < 0.3 and last_box["top"] < 0.3


def test_score_page_for_employee_with_proximity_bonus():
    """Test that close names get proximity bonus."""
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
        text="John Doe timecard",
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
        words=[
            # Names very close together (same line, adjacent)
            {"text": "John", "left": 0.1, "top": 0.5, "width": 0.05, "height": 0.02},
            {"text": "Doe", "left": 0.2, "top": 0.5, "width": 0.05, "height": 0.02},
        ],
    )

    score, rationale = score_page_for_employee(line_item, page)

    # Should get: 0.35 (last) + 0.30 (first) + 0.25 (proximity bonus) + 0.10 (doc_type) = 1.00 (capped)
    assert score >= 0.95, f"Expected score >= 0.95 with proximity bonus, got {score}"

    # Check that proximity is mentioned in rationale
    proximity_mentioned = any("proximity" in r.lower() for r in rationale)
    assert proximity_mentioned, f"Expected proximity in rationale, got: {rationale}"

    # Check for bonus (not penalty)
    bonus_mentioned = any("bonus" in r.lower() or "close" in r.lower() for r in rationale)
    assert bonus_mentioned, f"Expected bonus/close in rationale, got: {rationale}"


def test_score_page_for_employee_with_proximity_penalty():
    """Test that distant names get proximity penalty."""
    line_item = LineItem(
        row_id="row_0",
        row_index=0,
        budget_item="Salary",
        employee_first_name="John",
        employee_last_name="Smith",
        raw={},
    )

    page = PageRecord(
        doc_id="salary",
        page_number=1,
        text_source="pymupdf",
        text="John at top, Jane Smith at bottom",
        entities={
            "doc_type": "timecard",
            "people": [
                {
                    "full_name": "John Smith",
                    "first_name": "John",
                    "last_name": "Smith",
                },
                {
                    "full_name": "Jane Smith",
                    "first_name": "Jane",
                    "last_name": "Smith",
                },
            ],
        },
        words=[
            # John at top, distant from Smith
            {"text": "John", "left": 0.1, "top": 0.1, "width": 0.05, "height": 0.02},
            # Jane Smith at bottom (close together)
            {"text": "Jane", "left": 0.1, "top": 0.8, "width": 0.05, "height": 0.02},
            {"text": "Smith", "left": 0.2, "top": 0.8, "width": 0.05, "height": 0.02},
        ],
    )

    score, rationale = score_page_for_employee(line_item, page)

    # Should get: 0.35 (last) + 0.30 (first) - 0.20 (proximity penalty) + 0.10 (doc_type) = 0.55
    # The penalty applies because John and Smith are far apart (best pair is still John-Smith@bottom)
    assert score < 0.70, f"Expected score < 0.70 with proximity penalty, got {score}"

    # Check that proximity penalty is mentioned in rationale
    proximity_mentioned = any("proximity" in r.lower() for r in rationale)
    assert proximity_mentioned, f"Expected proximity in rationale, got: {rationale}"


def test_disambiguate_same_last_name():
    """Test that proximity helps disambiguate employees with same last name."""
    john_smith = LineItem(
        row_id="row_0",
        row_index=0,
        budget_item="Salary",
        employee_first_name="John",
        employee_last_name="Smith",
        amount=Decimal("5000.00"),
        raw={},
    )

    jane_smith = LineItem(
        row_id="row_1",
        row_index=1,
        budget_item="Salary",
        employee_first_name="Jane",
        employee_last_name="Smith",
        amount=Decimal("6000.00"),
        raw={},
    )

    # Page with John Smith's timecard (names close together)
    john_page = PageRecord(
        doc_id="salary",
        page_number=1,
        text_source="pymupdf",
        text="Timecard for John Smith - $5000",
        entities={
            "doc_type": "timecard",
            "people": [
                {
                    "full_name": "John Smith",
                    "first_name": "John",
                    "last_name": "Smith",
                }
            ],
            "amounts": [{"value": 5000.0, "raw": "5000"}],
        },
        words=[
            # John and Smith close together
            {"text": "John", "left": 0.1, "top": 0.5, "width": 0.05, "height": 0.02},
            {"text": "Smith", "left": 0.2, "top": 0.5, "width": 0.05, "height": 0.02},
        ],
    )

    # Score both line items against John's page
    john_score, john_rationale = score_page_for_employee(john_smith, john_page)
    jane_score, jane_rationale = score_page_for_employee(jane_smith, john_page)

    # John should score MUCH higher due to:
    # - First name match (+0.30)
    # - Proximity bonus (+0.25)
    # - Amount match (+0.40)
    # Total for John: 0.35 + 0.30 + 0.25 + 0.40 + 0.10 = 1.40 (capped to 1.0)
    # Total for Jane: 0.35 + 0.00 (no first name match) = 0.35

    assert john_score > jane_score + 0.30, \
        f"John should score significantly higher than Jane. John={john_score}, Jane={jane_score}"

    # John's score should be very high
    assert john_score >= 0.90, f"John should have high score, got {john_score}"

    # Jane's score should be much lower (only last name match + doc type = 0.45)
    # Allow some tolerance for fuzzy matching variations
    assert jane_score < 0.65, f"Jane should have low score without first name, got {jane_score}"
