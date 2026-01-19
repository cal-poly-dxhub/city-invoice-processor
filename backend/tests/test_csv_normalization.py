"""Tests for CSV budget item normalization."""

from decimal import Decimal
from pathlib import Path
import tempfile
import pytest
from invoice_recon.csv_parser import parse_csv
from invoice_recon.budget_items import normalize_csv_budget_item


def test_normalize_csv_budget_item_uppercase():
    """Test normalization of uppercase CSV values."""
    assert normalize_csv_budget_item("SALARY_TOTAL") == "Salary"
    assert normalize_csv_budget_item("SALARY") == "Salary"
    assert normalize_csv_budget_item("FRINGE_TOTAL") == "Fringe"
    assert normalize_csv_budget_item("SPACE_RENTAL") == "Space Rental/Occupancy Costs"
    assert normalize_csv_budget_item("CONTRACTUAL_SERVICE") == "Contractual Service"
    assert normalize_csv_budget_item("TRAVEL_AND_CONFERENCES") == "Travel and Conferences"


def test_normalize_csv_budget_item_lowercase():
    """Test normalization of lowercase CSV values."""
    assert normalize_csv_budget_item("salary_total") == "Salary"
    assert normalize_csv_budget_item("salary") == "Salary"
    assert normalize_csv_budget_item("space_rental") == "Space Rental/Occupancy Costs"
    assert normalize_csv_budget_item("contractual_service") == "Contractual Service"


def test_normalize_csv_budget_item_canonical():
    """Test that canonical names pass through unchanged."""
    assert normalize_csv_budget_item("Salary") == "Salary"
    assert normalize_csv_budget_item("Fringe") == "Fringe"
    assert normalize_csv_budget_item("Space Rental/Occupancy Costs") == "Space Rental/Occupancy Costs"
    assert normalize_csv_budget_item("Travel and Conferences") == "Travel and Conferences"


def test_normalize_csv_budget_item_with_whitespace():
    """Test normalization strips whitespace."""
    assert normalize_csv_budget_item("  SALARY_TOTAL  ") == "Salary"
    assert normalize_csv_budget_item("\tSALARY\t") == "Salary"


def test_parse_csv_with_uppercase_budget_items():
    """Test CSV parsing normalizes uppercase budget item values."""
    csv_content = """Budget Item,Amount,Employee First Name,Employee Last Name
SALARY_TOTAL,5000.00,John,Doe
FRINGE_TOTAL,1000.00,Jane,Smith
SPACE_RENTAL,2500.00,,
CONTRACTUAL_SERVICE,3000.00,,"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        csv_path = Path(f.name)

    try:
        line_items = parse_csv(csv_path)

        assert len(line_items) == 4

        # Check normalization
        assert line_items[0].budget_item == "Salary"
        assert line_items[1].budget_item == "Fringe"
        assert line_items[2].budget_item == "Space Rental/Occupancy Costs"
        assert line_items[3].budget_item == "Contractual Service"

        # Check other fields still parse correctly
        assert line_items[0].amount == Decimal("5000.00")
        assert line_items[0].employee_first_name == "John"
        assert line_items[0].employee_last_name == "Doe"

    finally:
        csv_path.unlink()


def test_parse_csv_mixed_case_budget_items():
    """Test CSV parsing with mixed case budget items."""
    csv_content = """Budget Item,Amount
SALARY,1000.00
Salary,2000.00
salary,3000.00"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        f.write(csv_content)
        csv_path = Path(f.name)

    try:
        line_items = parse_csv(csv_path)

        # All should normalize to "Salary"
        assert all(item.budget_item == "Salary" for item in line_items)

    finally:
        csv_path.unlink()
