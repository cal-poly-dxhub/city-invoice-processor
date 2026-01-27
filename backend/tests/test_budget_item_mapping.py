"""Tests for budget item mapping and slugification."""

import pytest
from invoice_recon.budget_items import (
    slugify,
    match_filename_to_budget_item,
    get_budget_item_slug,
    is_employee_budget_item,
)


def test_slugify():
    """Test slugification logic."""
    assert slugify("Salary") == "salary"
    assert slugify("Space Rental/Occupancy Costs") == "space_rental_occupancy_costs"
    assert slugify("Travel and Conferences") == "travel_and_conferences"
    assert slugify("Equipment") == "equipment"
    assert slugify("Fringe") == "fringe"


def test_slugify_edge_cases():
    """Test slugify with various edge cases."""
    assert slugify("Multiple   Spaces") == "multiple_spaces"
    assert slugify("Special@#$Chars") == "special_chars"
    assert slugify("  Leading/Trailing  ") == "leading_trailing"
    assert slugify("UPPERCASE") == "uppercase"


def test_match_filename_to_budget_item():
    """Test filename to budget item matching."""
    assert match_filename_to_budget_item("Salary.pdf") == "Salary"
    assert match_filename_to_budget_item("salary.pdf") == "Salary"
    assert match_filename_to_budget_item("SALARY.pdf") == "Salary"

    assert match_filename_to_budget_item("Fringe.pdf") == "Fringe"

    assert (
        match_filename_to_budget_item("Space_Rental_Occupancy_Costs.pdf")
        == "Space Rental/Occupancy Costs"
    )

    assert (
        match_filename_to_budget_item("space_rental_occupancy_costs.pdf")
        == "Space Rental/Occupancy Costs"
    )

    assert (
        match_filename_to_budget_item("Travel_and_Conferences.pdf")
        == "Travel and Conferences"
    )

    assert (
        match_filename_to_budget_item("travel_and_conferences.pdf")
        == "Travel and Conferences"
    )


def test_match_filename_unknown():
    """Test that unknown filenames return None."""
    assert match_filename_to_budget_item("unknown.pdf") is None
    assert match_filename_to_budget_item("random_file.pdf") is None
    assert match_filename_to_budget_item("not_a_budget_item.pdf") is None


def test_get_budget_item_slug():
    """Test getting slug for budget item."""
    assert get_budget_item_slug("Salary") == "salary"
    assert get_budget_item_slug("Fringe") == "fringe"
    assert get_budget_item_slug("Space Rental/Occupancy Costs") == "space_rental_occupancy_costs"


def test_is_employee_budget_item():
    """Test employee budget item detection."""
    assert is_employee_budget_item("Salary") is True
    assert is_employee_budget_item("Fringe") is True
    assert is_employee_budget_item("Equipment") is False
    assert is_employee_budget_item("Supplies") is False
    assert is_employee_budget_item("Travel and Conferences") is False
