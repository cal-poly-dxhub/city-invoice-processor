#!/usr/bin/env python3
"""Test script for table-aware budget item association."""

import json
from invoice_recon.table_parser import (
    parse_textract_tables,
    identify_budget_items_in_table,
    TableStructure,
)
from invoice_recon.budget_items import BUDGET_ITEMS
from invoice_recon.bedrock_entities import _associate_amounts_with_budget_items


def create_mock_textract_response():
    """Create a mock Textract response with a table containing budget items."""
    return {
        "Blocks": [
            # TABLE block
            {
                "BlockType": "TABLE",
                "Id": "table-1",
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.1,
                        "Top": 0.1,
                        "Width": 0.8,
                        "Height": 0.6,
                    }
                },
                "Relationships": [
                    {
                        "Type": "CHILD",
                        "Ids": ["cell-1", "cell-2", "cell-3", "cell-4"],
                    }
                ],
            },
            # Row 1, Col 1: "Salary"
            {
                "BlockType": "CELL",
                "Id": "cell-1",
                "RowIndex": 1,
                "ColumnIndex": 1,
                "RowSpan": 1,
                "ColumnSpan": 1,
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.1,
                        "Top": 0.1,
                        "Width": 0.4,
                        "Height": 0.15,
                    }
                },
                "Relationships": [{"Type": "CHILD", "Ids": ["word-1"]}],
            },
            # Row 1, Col 2: "$50,000"
            {
                "BlockType": "CELL",
                "Id": "cell-2",
                "RowIndex": 1,
                "ColumnIndex": 2,
                "RowSpan": 1,
                "ColumnSpan": 1,
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.5,
                        "Top": 0.1,
                        "Width": 0.4,
                        "Height": 0.15,
                    }
                },
                "Relationships": [{"Type": "CHILD", "Ids": ["word-2"]}],
            },
            # Row 2, Col 1: "Fringe"
            {
                "BlockType": "CELL",
                "Id": "cell-3",
                "RowIndex": 2,
                "ColumnIndex": 1,
                "RowSpan": 1,
                "ColumnSpan": 1,
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.1,
                        "Top": 0.25,
                        "Width": 0.4,
                        "Height": 0.15,
                    }
                },
                "Relationships": [{"Type": "CHILD", "Ids": ["word-3"]}],
            },
            # Row 2, Col 2: "$10,000"
            {
                "BlockType": "CELL",
                "Id": "cell-4",
                "RowIndex": 2,
                "ColumnIndex": 2,
                "RowSpan": 1,
                "ColumnSpan": 1,
                "Geometry": {
                    "BoundingBox": {
                        "Left": 0.5,
                        "Top": 0.25,
                        "Width": 0.4,
                        "Height": 0.15,
                    }
                },
                "Relationships": [{"Type": "CHILD", "Ids": ["word-4"]}],
            },
            # WORD blocks
            {"BlockType": "WORD", "Id": "word-1", "Text": "Salary"},
            {"BlockType": "WORD", "Id": "word-2", "Text": "$50,000"},
            {"BlockType": "WORD", "Id": "word-3", "Text": "Fringe"},
            {"BlockType": "WORD", "Id": "word-4", "Text": "$10,000"},
        ]
    }


def test_table_parsing():
    """Test that tables are correctly parsed from Textract response."""
    print("=" * 60)
    print("TEST 1: Table Parsing")
    print("=" * 60)

    response = create_mock_textract_response()
    tables = parse_textract_tables(response["Blocks"])

    assert len(tables) == 1, f"Expected 1 table, got {len(tables)}"
    table = tables[0]

    print(f"✓ Parsed 1 table")
    print(f"  - Table ID: {table.table_id}")
    print(f"  - Rows: {table.row_count}, Cols: {table.col_count}")
    print(f"  - Cells: {len(table.cells)}")

    # Check cells
    assert len(table.cells) == 4, f"Expected 4 cells, got {len(table.cells)}"

    cell_texts = [cell.text for cell in table.cells]
    expected_texts = ["Salary", "$50,000", "Fringe", "$10,000"]

    for expected in expected_texts:
        assert expected in cell_texts, f"Expected cell text '{expected}' not found"
        print(f"  - Cell: '{expected}' ✓")

    print()
    return tables


def test_budget_item_identification(tables):
    """Test that budget items are identified in table rows."""
    print("=" * 60)
    print("TEST 2: Budget Item Identification")
    print("=" * 60)

    table = tables[0]
    row_budget_map = identify_budget_items_in_table(table, BUDGET_ITEMS)

    print(f"Row budget item mapping:")
    for row_idx, budget_item in sorted(row_budget_map.items()):
        print(f"  Row {row_idx}: {budget_item}")

    # Check expected mappings
    assert row_budget_map.get(1) == "Salary", f"Row 1 should be Salary, got {row_budget_map.get(1)}"
    assert row_budget_map.get(2) == "Fringe", f"Row 2 should be Fringe, got {row_budget_map.get(2)}"

    print(f"✓ Budget items correctly identified")
    print()
    return row_budget_map


def test_amount_association(tables):
    """Test that amounts are associated with correct budget items."""
    print("=" * 60)
    print("TEST 3: Amount Association")
    print("=" * 60)

    # Mock entities dict (as if returned by Bedrock)
    entities = {
        "page_number": 1,
        "doc_type": "invoice",
        "amounts": [
            {
                "raw": "$50,000",
                "value": 50000.0,
                "currency": "USD",
                "context": "Salary total",
            },
            {
                "raw": "$10,000",
                "value": 10000.0,
                "currency": "USD",
                "context": "Fringe benefits",
            },
        ],
    }

    # Associate amounts with budget items
    _associate_amounts_with_budget_items(
        entities, page_tables=tables, page_doc_id="telecommunications"
    )

    print("Amount associations:")
    for amt in entities["amounts"]:
        print(f"  {amt['raw']} -> {amt.get('budget_item')} ({amt.get('source')})")

    # Check associations
    amt1 = entities["amounts"][0]
    assert amt1["budget_item"] == "Salary", f"$50,000 should be Salary, got {amt1['budget_item']}"
    assert amt1["source"] == "table_row", f"$50,000 should be table_row, got {amt1['source']}"

    amt2 = entities["amounts"][1]
    assert amt2["budget_item"] == "Fringe", f"$10,000 should be Fringe, got {amt2['budget_item']}"
    assert amt2["source"] == "table_row", f"$10,000 should be table_row, got {amt2['source']}"

    print(f"✓ Amounts correctly associated with budget items")
    print()


def test_non_table_amount():
    """Test that amounts outside tables inherit page default."""
    print("=" * 60)
    print("TEST 4: Non-Table Amount (Page Default)")
    print("=" * 60)

    entities = {
        "page_number": 1,
        "doc_type": "invoice",
        "amounts": [
            {
                "raw": "$5,000",
                "value": 5000.0,
                "currency": "USD",
                "context": "Service charge",
            }
        ],
    }

    # No tables - should inherit page default
    _associate_amounts_with_budget_items(
        entities, page_tables=None, page_doc_id="telecommunications"
    )

    amt = entities["amounts"][0]
    print(f"Amount: {amt['raw']}")
    print(f"  Budget Item: {amt.get('budget_item')}")
    print(f"  Source: {amt.get('source')}")

    assert amt["budget_item"] == "telecommunications", \
        f"Should inherit page default, got {amt['budget_item']}"
    assert amt["source"] == "page_default", f"Should be page_default, got {amt['source']}"

    print(f"✓ Non-table amount correctly inherits page default")
    print()


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("TESTING TABLE-AWARE BUDGET ITEM ASSOCIATION")
    print("=" * 60 + "\n")

    try:
        # Test 1: Parse tables
        tables = test_table_parsing()

        # Test 2: Identify budget items
        test_budget_item_identification(tables)

        # Test 3: Associate amounts
        test_amount_association(tables)

        # Test 4: Non-table amounts
        test_non_table_amount()

        print("=" * 60)
        print("ALL TESTS PASSED ✓")
        print("=" * 60)
        print()

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}\n")
        raise
    except Exception as e:
        print(f"\n❌ ERROR: {e}\n")
        raise


if __name__ == "__main__":
    main()
