"""Tests for component amount highlighting."""

import pytest
from decimal import Decimal
from invoice_recon.matching import score_page_by_amount, generate_amount_based_candidates
from invoice_recon.models import LineItem, PageRecord


def test_score_page_by_amount_exact_match():
    """Test that exact amount match returns None for matched_amounts (use target)."""
    line_item = LineItem(
        row_id="test-1",
        row_index=1,
        budget_item="Supplies",
        amount=Decimal("100.00"),
        explanation="Test supplies",
    )

    page = PageRecord(
        doc_id="test.pdf",
        page_number=1,
        text="Invoice for supplies. Total: $100.00",
        text_source="pymupdf",
        entities={
            "amounts": [
                {"value": 100.00, "raw": "$100.00", "context": "Total: $100.00"}
            ]
        },
        words=[],
    )

    score, rationale, matched_amounts = score_page_by_amount(line_item, page)

    assert score == 0.95
    assert "Exact amount match" in rationale[0]
    assert matched_amounts is None  # Should use target amount for highlighting


def test_score_page_by_amount_component_match():
    """Test that component match returns the component amounts for highlighting."""
    line_item = LineItem(
        row_id="test-1",
        row_index=1,
        budget_item="Supplies",
        amount=Decimal("150.00"),
        explanation="Test supplies",
    )

    page = PageRecord(
        doc_id="test.pdf",
        page_number=1,
        text="Invoice for supplies. Items: $50.00, $75.00, $25.00",
        text_source="pymupdf",
        entities={
            "amounts": [
                {"value": 50.00, "raw": "$50.00", "context": "Items: $50.00"},
                {"value": 75.00, "raw": "$75.00", "context": "Items: $75.00"},
                {"value": 25.00, "raw": "$25.00", "context": "Items: $25.00"},
            ]
        },
        words=[
            {"text": "$50.00", "left": 0.1, "top": 0.2, "width": 0.05, "height": 0.02},
            {"text": "$75.00", "left": 0.2, "top": 0.2, "width": 0.05, "height": 0.02},
            {"text": "$25.00", "left": 0.3, "top": 0.2, "width": 0.05, "height": 0.02},
        ],
    )

    score, rationale, matched_amounts = score_page_by_amount(line_item, page)

    assert score == 0.85
    assert "Component match" in rationale[0]
    assert matched_amounts is not None
    assert len(matched_amounts) == 3  # All 3 components sum to 150 ($50 + $75 + $25)

    # Verify the sum
    component_sum = sum(amt["value"] for amt in matched_amounts)
    assert abs(component_sum - 150.00) < 0.01


def test_generate_amount_based_candidates_with_components():
    """Test that generate_amount_based_candidates highlights component amounts."""
    line_item = LineItem(
        row_id="test-88",
        row_index=88,
        budget_item="Supplies",
        amount=Decimal("1605.16"),
        explanation="Test supplies",
    )

    page = PageRecord(
        doc_id="test.pdf",
        page_number=9,
        text="Invoice components: $88.25, $30.00, $1486.91",
        text_source="pymupdf",
        entities={
            "amounts": [
                {"value": 88.25, "raw": "$88.25", "context": "Component 1"},
                {"value": 30.00, "raw": "$30.00", "context": "Component 2"},
                {"value": 1486.91, "raw": "$1486.91", "context": "Component 3"},
            ]
        },
        words=[
            {"text": "$88.25", "left": 0.1, "top": 0.2, "width": 0.05, "height": 0.02},
            {"text": "$30.00", "left": 0.2, "top": 0.2, "width": 0.05, "height": 0.02},
            {"text": "$1486.91", "left": 0.3, "top": 0.2, "width": 0.05, "height": 0.02},
        ],
    )

    candidates = generate_amount_based_candidates(line_item, [page])

    assert len(candidates) == 1
    candidate = candidates[0]

    assert candidate.score == 0.85
    assert "Component match" in candidate.rationale[1]

    # Verify highlights exist for the page
    assert 9 in candidate.highlights
    highlights = candidate.highlights[9]

    # Should have 3 highlights (one for each component)
    assert len(highlights) == 3

    # Verify highlight positions match word positions
    highlight_tops = sorted([h["top"] for h in highlights])
    assert all(abs(top - 0.2) < 0.01 for top in highlight_tops)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
