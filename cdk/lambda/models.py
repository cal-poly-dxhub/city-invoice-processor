"""
Data models for CSV-based invoice reconciliation.
"""

from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any


@dataclass
class InvoiceLineItem:
    """Represents a single line item from the CSV master invoice."""
    line_number: int
    entity_name: Optional[str]  # Present for SALARY rows, None for non-employee items
    budget_item: str  # SALARY, FRINGE, INDIRECT_COSTS, OTHER, EQUIPMENT, etc.
    amount: float
    unit: str
    description: Optional[str] = None
    date: Optional[str] = None
    is_employee_item: bool = False  # True for SALARY rows with names
    raw_row: Optional[Dict[str, Any]] = None

    def to_dict(self):
        return asdict(self)


@dataclass
class PDFAmount:
    """Represents an amount extracted from a PDF page."""
    page: int
    amount: float
    label: str
    unit: str

    def to_dict(self):
        return asdict(self)


@dataclass
class LineItemVerification:
    """Verification result for a single CSV line item."""
    csv_line_number: int
    csv_entity: Optional[str]
    csv_budget_item: str
    csv_amount: float
    csv_unit: str
    pdf_pages_reviewed: List[int]
    pdf_amounts_found: List[PDFAmount]
    pdf_total: float
    verification_passes: bool
    discrepancy: Optional[Dict[str, float]]
    confidence: int

    def to_dict(self):
        result = asdict(self)
        result['pdf_amounts_found'] = [
            amt.to_dict() if hasattr(amt, 'to_dict') else amt
            for amt in self.pdf_amounts_found
        ]
        return result
