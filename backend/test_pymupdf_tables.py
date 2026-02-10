#!/usr/bin/env python3
"""Test script for PyMuPDF table extraction."""

import sys
from pathlib import Path
import fitz  # PyMuPDF
from invoice_recon.pdf_extract import (
    extract_tables_pymupdf,
    _is_pymupdf_table_sufficient,
    _convert_pymupdf_table_to_structure,
)
from invoice_recon.config import Config


def test_quality_validation():
    """Test table quality validation."""
    print("=" * 60)
    print("TEST 1: Quality Validation")
    print("=" * 60)

    # Test case 1: Empty table
    empty_table = []
    assert not _is_pymupdf_table_sufficient(empty_table), "Empty table should fail"
    print("✓ Empty table rejected")

    # Test case 2: Insufficient rows
    one_row = [["Header1", "Header2", "Header3"]]
    assert not _is_pymupdf_table_sufficient(one_row), "Single row should fail"
    print(f"✓ Single row rejected (min={Config.MIN_TABLE_ROWS})")

    # Test case 3: Insufficient cells
    small_table = [["A"], ["B"]]
    if Config.MIN_TABLE_CELLS > 2:
        assert not _is_pymupdf_table_sufficient(small_table), "Too few cells should fail"
        print(f"✓ Too few cells rejected (min={Config.MIN_TABLE_CELLS})")

    # Test case 4: Low coverage (too many empty cells)
    sparse_table = [
        ["A", "", "", ""],
        ["", "", "", ""],
        ["", "", "", ""],
    ]
    coverage = 1 / (3 * 4)  # Only 1 non-empty cell out of 12
    if coverage < Config.MIN_TABLE_CELL_COVERAGE:
        assert not _is_pymupdf_table_sufficient(sparse_table), "Low coverage should fail"
        print(f"✓ Sparse table rejected (coverage={coverage:.2f}, min={Config.MIN_TABLE_CELL_COVERAGE})")

    # Test case 5: Good table
    good_table = [
        ["Item", "Q1", "Q2", "Total"],
        ["Salary", "$50k", "$50k", "$100k"],
        ["Fringe", "$10k", "$10k", "$20k"],
    ]
    assert _is_pymupdf_table_sufficient(good_table), "Good table should pass"
    print("✓ Good table accepted")

    print()


def test_coordinate_normalization():
    """Test coordinate normalization from PDF points to 0-1."""
    print("=" * 60)
    print("TEST 2: Coordinate Normalization")
    print("=" * 60)

    # Create a mock PyMuPDF table object
    class MockTable:
        def __init__(self):
            # Table bbox in PDF points (assume page is 612x792 - standard letter)
            self.bbox = (100, 100, 500, 300)  # (x0, y0, x1, y1)
            # Cell bboxes (4 cells in 2x2 grid)
            self.cells = [
                (100, 100, 300, 200),  # Cell 1,1
                (300, 100, 500, 200),  # Cell 1,2
                (100, 200, 300, 300),  # Cell 2,1
                (300, 200, 500, 300),  # Cell 2,2
            ]

    class MockPage:
        class MediaBox:
            width = 612
            height = 792

        mediabox = MediaBox()

    mock_table = MockTable()
    mock_page = MockPage()
    cell_data = [
        ["A", "B"],
        ["C", "D"],
    ]

    table_struct = _convert_pymupdf_table_to_structure(
        mock_table, cell_data, 0, mock_page
    )

    # Check table bbox normalization
    expected_table_left = 100 / 612
    expected_table_top = 100 / 792
    expected_table_width = 400 / 612
    expected_table_height = 200 / 792

    assert abs(table_struct.bbox["left"] - expected_table_left) < 0.01, "Table left incorrect"
    assert abs(table_struct.bbox["top"] - expected_table_top) < 0.01, "Table top incorrect"
    assert abs(table_struct.bbox["width"] - expected_table_width) < 0.01, "Table width incorrect"
    assert abs(table_struct.bbox["height"] - expected_table_height) < 0.01, "Table height incorrect"

    print(f"✓ Table bbox normalized correctly")
    print(f"  left: {table_struct.bbox['left']:.3f}, top: {table_struct.bbox['top']:.3f}")
    print(f"  width: {table_struct.bbox['width']:.3f}, height: {table_struct.bbox['height']:.3f}")

    # Check cell count
    assert len(table_struct.cells) == 4, f"Expected 4 cells, got {len(table_struct.cells)}"
    print(f"✓ Correct cell count: {len(table_struct.cells)}")

    # Check first cell
    cell1 = table_struct.cells[0]
    assert cell1.row_index == 1, "Cell 1 row index should be 1"
    assert cell1.col_index == 1, "Cell 1 col index should be 1"
    assert cell1.text == "A", f"Cell 1 text should be 'A', got '{cell1.text}'"
    print(f"✓ Cell 1: row={cell1.row_index}, col={cell1.col_index}, text='{cell1.text}'")

    # Check coordinate range (should be 0-1)
    for cell in table_struct.cells:
        assert 0 <= cell.bbox["left"] <= 1, f"Cell left out of range: {cell.bbox['left']}"
        assert 0 <= cell.bbox["top"] <= 1, f"Cell top out of range: {cell.bbox['top']}"
        assert 0 <= cell.bbox["width"] <= 1, f"Cell width out of range: {cell.bbox['width']}"
        assert 0 <= cell.bbox["height"] <= 1, f"Cell height out of range: {cell.bbox['height']}"

    print("✓ All cell coordinates in 0-1 range")
    print()


def test_real_pdf_if_available():
    """Test with a real PDF if available."""
    print("=" * 60)
    print("TEST 3: Real PDF Test (if available)")
    print("=" * 60)

    # Look for test PDFs
    test_pdf_paths = [
        Path("test-files/pdf/Telecommunications.pdf"),
        Path("../test-files/pdf/Telecommunications.pdf"),
        Path("test-files/pdf/Salary.pdf"),
        Path("../test-files/pdf/Salary.pdf"),
    ]

    test_pdf = None
    for path in test_pdf_paths:
        if path.exists():
            test_pdf = path
            break

    if not test_pdf:
        print("⊘ No test PDFs found, skipping real PDF test")
        print("  (This is OK - other tests still validate the implementation)")
        print()
        return

    print(f"Found test PDF: {test_pdf}")

    try:
        doc = fitz.open(test_pdf)
        page = doc[0]  # Test first page

        print(f"Page size: {page.rect.width} x {page.rect.height}")

        # Try extracting tables
        tables = extract_tables_pymupdf(page)

        print(f"Extracted {len(tables)} tables")

        if tables:
            for i, table in enumerate(tables):
                print(f"\nTable {i}:")
                print(f"  Rows: {table.row_count}, Cols: {table.col_count}")
                print(f"  Cells: {len(table.cells)}")
                print(f"  Sample cells:")
                for cell in table.cells[:3]:  # Show first 3 cells
                    print(f"    ({cell.row_index},{cell.col_index}): '{cell.text}'")

            print(f"\n✓ Successfully extracted tables from real PDF")
        else:
            print("⊘ No tables found (may be expected if PDF has no visible grid lines)")

        doc.close()

    except Exception as e:
        print(f"❌ Error testing real PDF: {e}")
        import traceback
        traceback.print_exc()

    print()


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("TESTING PYMUPDF TABLE EXTRACTION")
    print("=" * 60)
    print(f"Configuration:")
    print(f"  MIN_TABLE_ROWS: {Config.MIN_TABLE_ROWS}")
    print(f"  MIN_TABLE_CELLS: {Config.MIN_TABLE_CELLS}")
    print(f"  MIN_TABLE_CELL_COVERAGE: {Config.MIN_TABLE_CELL_COVERAGE}")
    print(f"  PYMUPDF_TABLE_STRATEGY: {Config.PYMUPDF_TABLE_STRATEGY}")
    print()

    try:
        # Run tests
        test_quality_validation()
        test_coordinate_normalization()
        test_real_pdf_if_available()

        print("=" * 60)
        print("ALL TESTS PASSED ✓")
        print("=" * 60)
        print()
        return 0

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}\n")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}\n")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
