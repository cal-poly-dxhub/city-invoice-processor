"""
CSV parser for MOHCD Invoice Details format.
Handles parsing, column detection, and normalization to InvoiceLineItem objects.
"""

import csv
import io
from typing import List, Dict, Any
from models import InvoiceLineItem


# Column indices for MOHCD Invoice Details format (0-indexed)
MOHCD_COLUMNS = {
    "employee_first_name": 12,  # Column 13 (1-indexed)
    "employee_last_name": 13,   # Column 14
    "employee_title": 14,       # Column 15
    "hourly_rate": 15,          # Column 16
    "amount": 16,               # Column 17
    "budget_item": 8,           # Column 9
    "explanation": 9,           # Column 10 (description)
}


def parse_csv(csv_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Parse CSV bytes into list of dictionaries.
    Auto-detects delimiter and handles quoted fields.

    For MOHCD Invoice Details format:
    - Skips first 4 rows (MOHCD header, "INVOICE DETAILS", blank, run date/time)
    - Row 5 contains column headers
    - Data starts from row 6

    Args:
        csv_bytes: Raw CSV file bytes

    Returns:
        List of dictionaries, one per row (excluding header)
    """
    # Decode bytes to string
    csv_text = csv_bytes.decode('utf-8-sig')  # Handle BOM if present

    # Split into lines and skip first 4 header rows
    lines = csv_text.split('\n')

    # Check if this looks like MOHCD format (first line starts with "MOHCD")
    if lines and 'MOHCD' in lines[0]:
        print("Detected MOHCD Invoice Details format - skipping 4 header rows")
        # Skip first 4 rows (MOHCD, INVOICE DETAILS, blank, run date/time)
        # Row 5 (index 4) has column headers, row 6+ has data
        csv_text = '\n'.join(lines[4:])

    # Auto-detect delimiter
    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(csv_text[:1024])
        delimiter = dialect.delimiter
    except:
        # Default to comma if detection fails
        delimiter = ','

    # Parse CSV
    reader = csv.DictReader(io.StringIO(csv_text), delimiter=delimiter)
    rows = []

    for row in reader:
        rows.append(row)

    return rows


def normalize_csv_schema(rows: List[Dict[str, Any]]) -> List[InvoiceLineItem]:
    """
    Normalize CSV rows to InvoiceLineItem objects.
    Handles MOHCD Invoice Details format.

    Filtering rules:
    - Only include rows where Amount > 0
    - Skip SALARY_TOTAL rows (aggregates)

    Args:
        rows: List of CSV row dictionaries

    Returns:
        List of InvoiceLineItem objects
    """
    line_items = []

    # Get column names from first row (if rows exist)
    if not rows:
        return line_items

    col_names = list(rows[0].keys())

    for idx, row in enumerate(rows, start=1):
        # Extract fields by column index or name
        try:
            # Try to get by column name first (more robust)
            budget_item = row.get("Budget Item", "").strip()
            amount_str = row.get("Amount", "0").strip()
            first_name = row.get("Employee First Name", "").strip()
            last_name = row.get("Employee Last Name", "").strip()
            explanation = row.get("Explanation", "").strip()

            # Parse amount
            try:
                amount = float(amount_str) if amount_str else 0.0
            except ValueError:
                amount = 0.0

            # Filter: Skip rows with amount <= 0
            if amount <= 0:
                continue

            # Filter: Skip SALARY_TOTAL rows
            if budget_item == "SALARY_TOTAL":
                continue

            # Determine if this is an employee item
            is_employee_item = (
                budget_item == "SALARY" and
                first_name and
                last_name
            )

            # Construct entity name
            if is_employee_item:
                entity_name = f"{first_name} {last_name}".strip()
            else:
                entity_name = None

            # Create line item
            line_item = InvoiceLineItem(
                line_number=idx,
                entity_name=entity_name,
                budget_item=budget_item,
                amount=amount,
                unit="USD",  # Default to USD
                description=explanation if explanation else None,
                date=None,  # Could extract from Reporting Period if needed
                is_employee_item=is_employee_item,
                raw_row=row
            )

            line_items.append(line_item)

        except Exception as e:
            print(f"Warning: Failed to parse row {idx}: {e}")
            continue

    return line_items


def parse_and_normalize_csv(csv_bytes: bytes) -> List[InvoiceLineItem]:
    """
    Convenience function to parse and normalize CSV in one call.

    Args:
        csv_bytes: Raw CSV file bytes

    Returns:
        List of InvoiceLineItem objects
    """
    rows = parse_csv(csv_bytes)
    line_items = normalize_csv_schema(rows)

    print(f"Parsed {len(rows)} CSV rows, normalized to {len(line_items)} line items")

    # Print summary by budget category
    categories = {}
    for item in line_items:
        cat = item.budget_item
        if cat not in categories:
            categories[cat] = {"count": 0, "total_amount": 0.0}
        categories[cat]["count"] += 1
        categories[cat]["total_amount"] += item.amount

    print("Line items by category:")
    for cat, stats in sorted(categories.items()):
        print(f"  {cat}: {stats['count']} items, ${stats['total_amount']:,.2f}")

    return line_items


def group_by_category(line_items: List[InvoiceLineItem]) -> Dict[str, List[InvoiceLineItem]]:
    """
    Group line items by budget category.

    Args:
        line_items: List of InvoiceLineItem objects

    Returns:
        Dictionary mapping category -> list of line items
    """
    grouped = {}

    for item in line_items:
        category = item.budget_item
        if category not in grouped:
            grouped[category] = []
        grouped[category].append(item)

    return grouped
