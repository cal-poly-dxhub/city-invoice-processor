"""CSV parser for invoice line items."""

import hashlib
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd
from invoice_recon.budget_items import normalize_csv_budget_item
from invoice_recon.models import LineItem


def normalize_column_name(col: str) -> str:
    """
    Normalize column name to snake_case.

    Examples:
        "Budget Item" -> "budget_item"
        "Employee First Name" -> "employee_first_name"
        "Amount" -> "amount"
    """
    # Convert to lowercase and replace spaces/special chars with underscore
    col = col.lower().strip()
    col = col.replace(" ", "_").replace("-", "_").replace("/", "_")
    # Collapse multiple underscores
    while "__" in col:
        col = col.replace("__", "_")
    return col


def safe_decimal(value: Any) -> Optional[Decimal]:
    """Safely convert a value to Decimal, returning None on failure."""
    if pd.isna(value):
        return None

    try:
        # Remove currency symbols and commas
        if isinstance(value, str):
            value = value.replace("$", "").replace(",", "").strip()
            if not value:
                return None

        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def safe_str(value: Any) -> Optional[str]:
    """Safely convert a value to string, returning None if empty/NA."""
    if pd.isna(value):
        return None

    value_str = str(value).strip()
    return value_str if value_str else None


def build_row_id(row_index: int, row_data: Dict[str, Any]) -> str:
    """
    Build a stable row_id from row index and key fields.

    Uses row_index + hash of (budget_item, project_id, employee names,
    amount, reporting_period, explanation).
    """
    # Extract key fields for hashing
    key_fields = [
        str(row_data.get("budget_item", "")),
        str(row_data.get("project_id", "")),
        str(row_data.get("employee_first_name", "")),
        str(row_data.get("employee_last_name", "")),
        str(row_data.get("amount", "")),
        str(row_data.get("reporting_period", "")),
        str(row_data.get("explanation", "")),
    ]

    # Create hash
    hash_input = "|".join(key_fields).encode("utf-8")
    hash_hex = hashlib.sha256(hash_input).hexdigest()[:8]

    return f"row_{row_index}_{hash_hex}"


def parse_csv(csv_path: Path, skiprows: int = 0) -> List[LineItem]:
    """
    Parse invoice CSV file into LineItem objects.

    Args:
        csv_path: Path to CSV file
        skiprows: Number of rows to skip before header (default 0)

    Returns:
        List of LineItem objects
    """
    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    # Try reading with specified skiprows first
    df = pd.read_csv(csv_path, skiprows=skiprows, encoding='utf-8-sig')

    # Check if we got valid column names
    normalized_cols = [normalize_column_name(col) for col in df.columns]
    if 'budget_item' not in normalized_cols:
        # Auto-detect header row
        # Look for a row containing "Budget Item" or similar key columns
        header_row_index = None
        with open(csv_path, 'r', encoding='utf-8-sig') as f:
            for i, line in enumerate(f):
                if i > 20:  # Don't search beyond first 20 rows
                    break
                if 'Budget Item' in line:
                    header_row_index = i
                    break

        if header_row_index is not None and header_row_index > 0:
            # Skip all rows before the header (rows 0 to header_row_index-1)
            # Row header_row_index becomes the header
            df = pd.read_csv(
                csv_path,
                skiprows=range(header_row_index),
                encoding='utf-8-sig'
            )

    # Normalize column names
    df.columns = [normalize_column_name(col) for col in df.columns]

    # Build LineItems
    line_items = []
    for row_index, row in df.iterrows():
        # Convert row to dict
        raw_data = row.to_dict()

        # Extract and normalize fields
        raw_budget_item = safe_str(row.get("budget_item")) or ""
        canonical_budget_item = normalize_csv_budget_item(raw_budget_item) if raw_budget_item else ""

        normalized = {
            "budget_item": canonical_budget_item,
            "amount": safe_decimal(row.get("amount")),
            "hourly_rate": safe_decimal(row.get("hourly_rate")),
            "employee_first_name": safe_str(row.get("employee_first_name")),
            "employee_last_name": safe_str(row.get("employee_last_name")),
            "employee_title": safe_str(row.get("employee_title")),
            "reporting_period": safe_str(row.get("reporting_period")),
            "agency_invoice_date": safe_str(row.get("agency_invoice_date")),
            "explanation": safe_str(row.get("explanation")),
        }

        # Build row_id
        row_id = build_row_id(int(row_index), normalized)

        # Create LineItem
        line_item = LineItem(
            row_id=row_id,
            row_index=int(row_index),
            budget_item=normalized["budget_item"] or "",
            amount=normalized["amount"],
            hourly_rate=normalized["hourly_rate"],
            employee_first_name=normalized["employee_first_name"],
            employee_last_name=normalized["employee_last_name"],
            employee_title=normalized["employee_title"],
            reporting_period=normalized["reporting_period"],
            agency_invoice_date=normalized["agency_invoice_date"],
            explanation=normalized["explanation"],
            raw=raw_data,
        )

        line_items.append(line_item)

    return line_items
