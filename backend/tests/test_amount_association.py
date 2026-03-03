"""Tests for amount-to-table-row association in bedrock_entities.py.

Covers the fix for multi-value cells where PyMuPDF stacks allocation
amounts (newline-separated) in one cell, causing all amounts to map
to the first row.
"""

import pytest
from invoice_recon.table_parser import TableCell, TableStructure
from invoice_recon.bedrock_entities import _associate_amounts_with_budget_items


def _cell(row, col, text, row_span=1):
    """Helper to create a TableCell with minimal bbox."""
    return TableCell(
        row_index=row, col_index=col, text=text,
        row_span=row_span, col_span=1,
        bbox={"left": 0, "top": 0, "width": 0.1, "height": 0.05},
    )


def _make_table(table_id, cells, row_count=10, col_count=5):
    return TableStructure(
        table_id=table_id, cells=cells,
        row_count=row_count, col_count=col_count,
        bbox={"left": 0, "top": 0, "width": 1, "height": 1},
    )


def _amount(raw, context=""):
    return {"raw": raw, "value": float(raw.replace("%", "")), "context": context}


class TestAmountAssociationExactMatch:
    """Amounts that appear as the sole content of a cell."""

    def test_exact_match_assigns_correct_row(self):
        cells = [
            _cell(7, 1, "Telecommunications"),
            _cell(7, 2, "19.16"),
            _cell(8, 1, "Telecommunications"),
            _cell(8, 2, "513.95"),
        ]
        table = _make_table("t0", cells)
        entities = {"amounts": [_amount("19.16"), _amount("513.95")]}

        _associate_amounts_with_budget_items(entities, [table], "telecommunications")

        assert entities["amounts"][0]["table_row_index"] == 7
        assert entities["amounts"][1]["table_row_index"] == 8


class TestAmountAssociationPyMuPDFMultiLine:
    """PyMuPDF multi-value cells with newline-separated amounts."""

    def test_multiline_cell_uses_line_offset(self):
        """Amounts in a stacked allocation column should map to their actual rows."""
        cells = [
            # Row 7: Granite — Balance in col 2, allocation blob in col 3
            _cell(7, 1, "Telecommunications"),
            _cell(7, 2, "19.16"),
            _cell(7, 3, "2.68\n72.00\n20.60"),
            # Row 8: DialPad — Balance in col 2
            _cell(8, 1, "Telecommunications"),
            _cell(8, 2, "513.95"),
            # Row 9: Comcast — Balance in col 2
            _cell(9, 1, "Telecommunications"),
            _cell(9, 2, "149.20"),
        ]
        table = _make_table("t0", cells)
        entities = {
            "amounts": [
                _amount("19.16", "Granite Balance"),
                _amount("2.68", "Granite Allocation"),
                _amount("513.95", "DialPad Balance"),
                _amount("72.00", "DialPad Allocation"),
                _amount("149.20", "Comcast Balance"),
                _amount("20.60", "Comcast Allocation"),
            ]
        }

        _associate_amounts_with_budget_items(entities, [table], "telecommunications")

        results = {a["raw"]: a["table_row_index"] for a in entities["amounts"]}
        # Exact-match amounts: found in their own cells
        assert results["19.16"] == 7, "Granite balance should be row 7"
        assert results["513.95"] == 8, "DialPad balance should be row 8"
        assert results["149.20"] == 9, "Comcast balance should be row 9"
        # Line-offset amounts: from multi-value cell at row 7
        assert results["2.68"] == 7, "Granite alloc (line 0) should be row 7"
        assert results["72.00"] == 8, "DialPad alloc (line 1) should be row 8"
        assert results["20.60"] == 9, "Comcast alloc (line 2) should be row 9"

    def test_exact_match_preferred_over_multiline(self):
        """If an amount appears in both an exact cell and a multi-value cell,
        prefer the exact match."""
        cells = [
            _cell(7, 3, "2.68\n72.00"),  # multi-value
            _cell(8, 2, "72.00"),  # exact match in row 8
        ]
        table = _make_table("t0", cells)
        entities = {"amounts": [_amount("72.00")]}

        _associate_amounts_with_budget_items(entities, [table], "telecommunications")

        assert entities["amounts"][0]["table_row_index"] == 8

    def test_amount_not_in_any_cell_gets_page_default(self):
        """Amounts that don't appear in any table cell get page default."""
        cells = [_cell(7, 1, "Telecommunications"), _cell(7, 2, "19.16")]
        table = _make_table("t0", cells)
        entities = {"amounts": [_amount("999.99")]}

        _associate_amounts_with_budget_items(entities, [table], "telecommunications")

        assert entities["amounts"][0]["source"] == "page_default"
        assert entities["amounts"][0]["budget_item"] == "telecommunications"


class TestAmountAssociationTextractMergedCell:
    """Textract cells with row_span > 1 (proper merged cell detection)."""

    def test_textract_rowspan_cell_used_as_fallback(self):
        """A Textract merged cell (row_span > 1) should be used as fallback,
        not preferred over exact matches."""
        cells = [
            # Merged cell spanning rows 7-9 with multiple amounts
            _cell(7, 3, "2.68 72.00 20.60", row_span=3),
            # Exact match cells in individual rows
            _cell(8, 2, "72.00"),
        ]
        table = _make_table("t0", cells)
        entities = {"amounts": [_amount("72.00")]}

        _associate_amounts_with_budget_items(entities, [table], "telecommunications")

        # Should prefer the exact match (row 8) over the merged cell (row 7)
        assert entities["amounts"][0]["table_row_index"] == 8

    def test_textract_rowspan_fallback_when_no_exact(self):
        """When an amount only appears in a merged Textract cell, use
        the cell's starting row as fallback."""
        cells = [
            _cell(7, 3, "2.68 72.00 20.60", row_span=3),
        ]
        table = _make_table("t0", cells)
        entities = {"amounts": [_amount("72.00")]}

        _associate_amounts_with_budget_items(entities, [table], "telecommunications")

        # Falls back to row 7 (cell's starting row) since we can't
        # determine exact position from space-separated Textract text
        assert entities["amounts"][0]["table_row_index"] == 7
        assert entities["amounts"][0]["source"] == "table_row"


class TestAmountAssociationNoTables:
    """Edge case: no tables on the page."""

    def test_no_tables_uses_page_default(self):
        entities = {"amounts": [_amount("42.50")]}

        _associate_amounts_with_budget_items(entities, None, "telecommunications")

        assert entities["amounts"][0]["source"] == "page_default"
        assert entities["amounts"][0]["budget_item"] == "telecommunications"

    def test_empty_tables_list_uses_page_default(self):
        entities = {"amounts": [_amount("42.50")]}

        _associate_amounts_with_budget_items(entities, [], "telecommunications")

        assert entities["amounts"][0]["source"] == "page_default"
