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


class PageWordData(BaseModel):
    """Word-level data for a single page (for search highlighting)."""

    words: List[Dict[str, Any]] = Field(default_factory=list)  # Word bounding boxes with text, left, top, width, height
    text: str = ""  # Full page text for fallback search


class SourceFile(BaseModel):
    """A physical PDF file that is part of a virtual combined document."""

    doc_id: str  # Physical doc_id (e.g., "salary__payroll_jan")
    pdf_path: str  # Relative path from pdf_dir (e.g., "salary/payroll_jan.pdf")
    filename: str  # Display name (e.g., "payroll_jan.pdf")
    page_count: int  # Pages in this physical file
    page_offset: int  # Starting virtual page minus 1 (e.g., 0, 5, 8...)


class DocumentRef(BaseModel):
    """Reference to a PDF document (possibly a virtual combined document)."""

    doc_id: str
    budget_item: str
    path: str
    file_sha256: str
    page_count: int
    pages_data: Dict[int, PageWordData] = Field(default_factory=dict)  # Page number -> word data for search
    source_files: List["SourceFile"] = Field(default_factory=list)  # Physical PDF files composing this document
    pdf_path: str = ""  # Relative path from pdf_dir (for single-file compat)
    filename: str = ""  # Original filename


class PageRecord(BaseModel):
    """Represents extracted data for a single page."""

    doc_id: str
    page_number: int  # 1-based
    text_source: str  # "pymupdf" or "textract"
    text: str
    entities: Dict[str, Any] = Field(default_factory=dict)
    words: List[Dict[str, Any]] = Field(default_factory=list)  # Word bounding boxes from Textract


class CandidateEvidenceSet(BaseModel):
    """A candidate set of pages as evidence for a line item."""

    doc_id: str
    page_numbers: List[int]
    score: float
    rationale: List[str]
    evidence_snippets: List[str] = Field(default_factory=list)
    highlights: Dict[int, List[Dict[str, float]]] = Field(default_factory=dict)  # page_number -> list of {left, top, width, height}


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


class SubItem(BaseModel):
    """A user-created sub-item for drilling into summary totals."""

    sub_item_id: str  # e.g., "row_51_a"
    parent_row_id: str  # e.g., "row_51"
    label: str  # User-provided name
    doc_id: str  # Budget item doc_id
    keywords: List[str] = Field(default_factory=list)
    amount: Optional[float] = None
    amounts: List[float] = Field(default_factory=list)  # Multiple amounts (merged balance/allocation rows)
    source_page: Optional[int] = None  # The GL page this was created from
    candidates: List[CandidateEvidenceSet] = Field(default_factory=list)
    selected_evidence: Optional[SelectedEvidence] = None


class CompletionStatus(BaseModel):
    """Payment/Invoice verification status for a line item or sub-item."""

    payment: bool = False
    invoice: bool = False


class UserEdits(BaseModel):
    """Collection of user edit overrides."""

    overrides: List[UserEditOverride] = Field(default_factory=list)
    sub_items: List[SubItem] = Field(default_factory=list)
    completion_status: Dict[str, CompletionStatus] = Field(default_factory=dict)
