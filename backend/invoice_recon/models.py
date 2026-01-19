"""Pydantic models for invoice reconciliation."""

from decimal import Decimal
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class LineItem(BaseModel):
    """Represents a single line item from the CSV."""

    row_id: str
    row_index: int
    budget_item: str
    amount: Optional[Decimal] = None
    hourly_rate: Optional[Decimal] = None
    employee_first_name: Optional[str] = None
    employee_last_name: Optional[str] = None
    employee_title: Optional[str] = None
    reporting_period: Optional[str] = None
    agency_invoice_date: Optional[str] = None
    explanation: Optional[str] = None
    raw: Dict[str, Any] = Field(default_factory=dict)


class DocumentRef(BaseModel):
    """Reference to a PDF document."""

    doc_id: str
    budget_item: str
    path: str
    file_sha256: str
    page_count: int


class PageRecord(BaseModel):
    """Represents extracted data for a single page."""

    doc_id: str
    page_number: int  # 1-based
    text_source: str  # "pymupdf" or "textract"
    text: str
    entities: Dict[str, Any] = Field(default_factory=dict)


class CandidateEvidenceSet(BaseModel):
    """A candidate set of pages as evidence for a line item."""

    doc_id: str
    page_numbers: List[int]
    score: float
    rationale: List[str]
    evidence_snippets: List[str] = Field(default_factory=list)


class SelectedEvidence(BaseModel):
    """The selected evidence for a line item."""

    doc_id: Optional[str] = None
    page_numbers: List[int] = Field(default_factory=list)
    selection_source: str = "auto"  # "auto" or "user"


class NavigationGroup(BaseModel):
    """A navigation group for UI browsing."""

    group_id: str
    label: str
    budget_item: str
    employee_key: Optional[str] = None
    line_item_ids: List[str] = Field(default_factory=list)


class JobInfo(BaseModel):
    """Job metadata."""

    job_id: str
    created_at: str
    aws_region: str
    bedrock_model_id: str
    textract_mode: str


class InputsInfo(BaseModel):
    """Input file information."""

    csv_path: str
    pdf_dir: str
    documents: List[Dict[str, str]] = Field(default_factory=list)


class LineItemOutput(BaseModel):
    """Output format for a line item with candidates and selected evidence."""

    row_id: str
    row_index: int
    budget_item: str
    raw: Dict[str, Any]
    normalized: Dict[str, Any] = Field(default_factory=dict)
    candidates: List[CandidateEvidenceSet] = Field(default_factory=list)
    selected_evidence: SelectedEvidence


class ReconciliationOutput(BaseModel):
    """Complete reconciliation output."""

    job: JobInfo
    inputs: InputsInfo
    documents: List[DocumentRef]
    navigation_groups: List[NavigationGroup]
    line_items: List[LineItemOutput]


class UserEditOverride(BaseModel):
    """User override for selected evidence."""

    row_id: str
    doc_id: str
    page_numbers: List[int]


class UserEdits(BaseModel):
    """Collection of user edit overrides."""

    overrides: List[UserEditOverride] = Field(default_factory=list)
