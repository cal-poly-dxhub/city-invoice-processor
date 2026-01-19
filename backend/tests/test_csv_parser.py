"""Tests for CSV parser."""

from decimal import Decimal
from pathlib import Path
import tempfile
import pytest
from invoice_recon.csv_parser import (
    normalize_column_name,
    safe_decimal,
    safe_str,
    parse_csv,
)


def test_normalize_column_name():
    """Test column name normalization."""
    assert normalize_column_name("Budget Item") == "budget_item"
    assert normalize_column_name("Employee First Name") == "employee_first_name"
    assert normalize_column_name("Amount") == "amount"
    assert normalize_column_name("Hourly Rate") == "hourly_rate"


def test_safe_decimal():
    """Test safe decimal conversion."""
    assert safe_decimal("100.50") == Decimal("100.50")
    assert safe_decimal("$1,234.56") == Decimal("1234.56")
    assert safe_decimal("") is None
    assert safe_decimal(None) is None
    assert safe_decimal("invalid") is None


def test_safe_str():
    """Test safe string conversion."""
    assert safe_str("hello") == "hello"
    assert safe_str("  hello  ") == "hello"
    assert safe_str("") is None
    assert safe_str(None) is None


def test_parse_csv_basic():
    """Test basic CSV parsing."""
    csv_content = """Budget Item,Amount,Employee First Name,Employee Last Name
Salary,5000.00,John,Doe
Fringe,1000.00,Jane,Smith"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        csv_path = Path(f.name)

    try:
        line_items = parse_csv(csv_path)

        assert len(line_items) == 2

        # First item
        assert line_items[0].budget_item == "Salary"
        assert line_items[0].amount == Decimal("5000.00")
        assert line_items[0].employee_first_name == "John"
        assert line_items[0].employee_last_name == "Doe"
        assert line_items[0].row_index == 0

        # Second item
        assert line_items[1].budget_item == "Fringe"
        assert line_items[1].amount == Decimal("1000.00")
        assert line_items[1].employee_first_name == "Jane"
        assert line_items[1].employee_last_name == "Smith"
        assert line_items[1].row_index == 1

    finally:
        csv_path.unlink()


def test_parse_csv_missing_fields():
    """Test CSV parsing with missing fields."""
    csv_content = """Budget Item,Amount
Equipment,2500.00
Supplies,"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        csv_path = Path(f.name)

    try:
        line_items = parse_csv(csv_path)

        assert len(line_items) == 2

        # First item
        assert line_items[0].budget_item == "Equipment"
        assert line_items[0].amount == Decimal("2500.00")
        assert line_items[0].employee_first_name is None
        assert line_items[0].employee_last_name is None

        # Second item (missing amount)
        assert line_items[1].budget_item == "Supplies"
        assert line_items[1].amount is None

    finally:
        csv_path.unlink()


def test_parse_csv_stable_row_ids():
    """Test that row IDs are stable across identical data."""
    csv_content = """Budget Item,Amount,Employee First Name,Employee Last Name
Salary,5000.00,John,Doe"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        csv_path = Path(f.name)

    try:
        # Parse twice
        line_items_1 = parse_csv(csv_path)
        line_items_2 = parse_csv(csv_path)

        # Row IDs should be identical
        assert line_items_1[0].row_id == line_items_2[0].row_id

    finally:
        csv_path.unlink()


def test_parse_csv_file_not_found():
    """Test that missing CSV raises error."""
    with pytest.raises(FileNotFoundError):
        parse_csv(Path("/nonexistent/file.csv"))
