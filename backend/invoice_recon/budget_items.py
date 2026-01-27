"""Budget item management and filename mapping."""

import re
from pathlib import Path
from typing import Dict, List, Optional

# Fixed list of canonical budget items
BUDGET_ITEMS = [
    "Salary",
    "Fringe",
    "Contractual Service",
    "Equipment",
    "Insurance",
    "Travel and Conferences",
    "Space Rental/Occupancy Costs",
    "Telecommunications",
    "Utilities",
    "Supplies",
    "Other",
    "Indirect Costs",
]

# Mapping from CSV values to canonical budget items
# CSV files may use different naming conventions (e.g., SALARY_TOTAL, SPACE_RENTAL)
CSV_TO_CANONICAL = {
    # Direct mappings (various capitalizations)
    "SALARY": "Salary",
    "SALARY_TOTAL": "Salary",
    "FRINGE": "Fringe",
    "FRINGE_TOTAL": "Fringe",
    "CONTRACTUAL_SERVICE": "Contractual Service",
    "CONTRACTUAL_SERVICES": "Contractual Service",
    "EQUIPMENT": "Equipment",
    "INSURANCE": "Insurance",
    "TRAVEL_AND_CONFERENCES": "Travel and Conferences",
    "TRAVEL": "Travel and Conferences",
    "SPACE_RENTAL": "Space Rental/Occupancy Costs",
    "SPACE_RENTAL_OCCUPANCY_COSTS": "Space Rental/Occupancy Costs",
    "TELECOMMUNICATIONS": "Telecommunications",
    "TELECOM": "Telecommunications",
    "UTILITIES": "Utilities",
    "SUPPLIES": "Supplies",
    "OTHER": "Other",
    "INDIRECT_COSTS": "Indirect Costs",
    "INDIRECT": "Indirect Costs",
    # Also support lowercase and title case
    "salary": "Salary",
    "salary_total": "Salary",
    "fringe": "Fringe",
    "contractual_service": "Contractual Service",
    "contractual_services": "Contractual Service",
    "equipment": "Equipment",
    "insurance": "Insurance",
    "travel_and_conferences": "Travel and Conferences",
    "travel": "Travel and Conferences",
    "space_rental": "Space Rental/Occupancy Costs",
    "space_rental_occupancy_costs": "Space Rental/Occupancy Costs",
    "telecommunications": "Telecommunications",
    "telecom": "Telecommunications",
    "utilities": "Utilities",
    "supplies": "Supplies",
    "other": "Other",
    "indirect_costs": "Indirect Costs",
    "indirect": "Indirect Costs",
}


def slugify(text: str) -> str:
    """
    Convert text to slug format for matching.

    Rules:
    - Convert to lowercase
    - Replace non-alphanumeric characters with underscore
    - Collapse multiple underscores to single underscore
    - Strip leading/trailing underscores

    Examples:
        "Salary" -> "salary"
        "Space Rental/Occupancy Costs" -> "space_rental_occupancy_costs"
        "Travel and Conferences" -> "travel_and_conferences"
    """
    # Convert to lowercase
    text = text.lower()

    # Replace non-alphanumeric with underscore
    text = re.sub(r"[^a-z0-9]+", "_", text)

    # Collapse multiple underscores
    text = re.sub(r"_+", "_", text)

    # Strip leading/trailing underscores
    text = text.strip("_")

    return text


def build_slug_to_budget_item_map() -> Dict[str, str]:
    """Build a mapping from slug to canonical budget item name."""
    mapping = {}
    for item in BUDGET_ITEMS:
        slug = slugify(item)
        mapping[slug] = item
    return mapping


def match_filename_to_budget_item(filename: str) -> Optional[str]:
    """
    Match a PDF filename to a budget item.

    Removes extension, slugifies, and looks up in the budget items map.

    Examples:
        "Salary.pdf" -> "Salary"
        "Space_Rental_Occupancy_Costs.pdf" -> "Space Rental/Occupancy Costs"
        "travel_and_conferences.pdf" -> "Travel and Conferences"
        "unknown.pdf" -> None
    """
    # Remove extension
    stem = Path(filename).stem

    # Slugify
    slug = slugify(stem)

    # Look up in map
    slug_map = build_slug_to_budget_item_map()
    return slug_map.get(slug)


def discover_pdfs_in_dir(pdf_dir: Path) -> List[Dict[str, str]]:
    """
    Discover PDFs in a directory and map them to budget items.

    Returns:
        List of dicts with keys: "path", "budget_item"
    """
    if not pdf_dir.exists():
        raise FileNotFoundError(f"PDF directory not found: {pdf_dir}")

    results = []
    for pdf_path in sorted(pdf_dir.glob("*.pdf")):
        budget_item = match_filename_to_budget_item(pdf_path.name)
        if budget_item:
            results.append({
                "path": str(pdf_path),
                "budget_item": budget_item,
            })

    return results


def get_budget_item_slug(budget_item: str) -> str:
    """Get the slug for a budget item (for use as doc_id, etc.)."""
    return slugify(budget_item)


def is_employee_budget_item(budget_item: str) -> bool:
    """Check if a budget item is employee-related (Salary or Fringe)."""
    return budget_item in ("Salary", "Fringe")


def normalize_csv_budget_item(csv_value: str) -> str:
    """
    Normalize a CSV budget item value to canonical form.

    Handles various CSV formats like SALARY_TOTAL, SPACE_RENTAL, etc.
    If the value is already canonical or in the mapping, returns canonical.
    If unknown, returns the value as-is (will likely cause matching issues).

    Examples:
        "SALARY_TOTAL" -> "Salary"
        "SPACE_RENTAL" -> "Space Rental/Occupancy Costs"
        "Salary" -> "Salary" (already canonical)
        "equipment" -> "Equipment"
    """
    # Strip whitespace
    csv_value = csv_value.strip()

    # Check direct mapping
    if csv_value in CSV_TO_CANONICAL:
        return CSV_TO_CANONICAL[csv_value]

    # Check if already canonical
    if csv_value in BUDGET_ITEMS:
        return csv_value

    # Try case-insensitive slug-based matching as fallback
    slug = slugify(csv_value)
    slug_map = build_slug_to_budget_item_map()
    if slug in slug_map:
        return slug_map[slug]

    # Return as-is if unknown (will cause issues downstream)
    return csv_value
