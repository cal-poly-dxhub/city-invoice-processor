# Reconciliation Report Modal — Design Document

**Date:** 2026-03-05
**Branch:** feature/payment-invoice-checkboxes
**Status:** Approved

## Problem

The "Finish Reconciliation" modal currently only shows incomplete items as a navigation aid. Users need a comprehensive report listing all line items with their payment/invoice verification status and associated evidence pages, with un-reconciled items surfaced first.

## Design

### Approach: Extracted ReconciliationReport Component

A new `ReconciliationReport.jsx` component renders the report body inside the existing modal overlay. A shared `resolveVirtualPage()` utility is extracted from PDFViewer so both components can map virtual page numbers to physical files.

### New Files

- `frontend/src/components/ReconciliationReport.jsx` — report body + CSV export
- `frontend/src/components/ReconciliationReport.css` — styles
- `frontend/src/utils/virtualPages.js` — extracted `resolveVirtualPage()` + `getSourceFiles()` helpers

### Modified Files

- `frontend/src/pages/ReviewPage.jsx` — replace inline modal body with `<ReconciliationReport />`
- `frontend/src/components/PDFViewer.jsx` — import from shared `virtualPages.js` util

### Data Flow

ReviewPage passes to ReconciliationReport:
- `lineItems` — all line items (with confidence filter applied)
- `subItems` — map of row_id → sub-item arrays
- `documents` — document list (for virtual→physical resolution)
- `completionStatus` — full completion_status state
- `getLineItemCompletionStatus(rowId)` — rollup status (returns `{payment: bool, invoice: bool}`)
- `getCompletionPages(rowId)` — page evidence arrays (returns `{payment: PageRef[], invoice: PageRef[]}`)
- `onNavigateToItem(item, pageNum)` — callback to close modal and navigate to item/page
- `onClose()` — close modal

### Report Layout

Two sections, each with items in numerical order by `row_index`:

1. **"Needs Attention"** — items where `isItemCompleted()` returns false
2. **"Completed"** — items where both payment and invoice are verified

Each item row shows:
- Row number, budget item, amount
- Employee name (for Salary/Fringe)
- Payment status with page references (or unchecked)
- Invoice status with page references (or unchecked)
- Expandable sub-item section (if sub-items exist)

### Sub-item Display

Line items with sub-items show:
- Rollup status at the line item level: "Payment (2/3 sub-items)"
- Collapsible sub-item list, each showing its own payment/invoice status + evidence pages

### Page Reference Format

`filename.pdf p.N (vp M)` where:
- `filename.pdf` = physical PDF filename from `source_files`
- `N` = local page number within that physical file
- `M` = virtual page number in the combined document

Page references are clickable — clicking calls `onNavigateToItem(item, virtualPageNum)`.

### CSV Export

Client-side CSV generation (no library). Columns:

```
Row, Budget Item, Label, Amount, Employee, Payment Status, Payment Evidence, Invoice Status, Invoice Evidence
```

- Line items get integer row numbers (1, 2, 3...)
- Sub-items get dotted numbers (1.1, 1.2, 2.1...)
- Evidence columns: semicolon-separated page references (`"att_bill.pdf p.3 (vp 3); att_bill.pdf p.7 (vp 7)"`)
- Download triggered via Blob URL

### Virtual Page Resolution

Extracted to `frontend/src/utils/virtualPages.js`:

```js
resolveVirtualPage(virtualPageNum, sourceFiles)
// Returns: { sourceFile, localPage } or null

getSourceFiles(doc)
// Returns: source_files array with backward-compat fallback
```

PDFViewer imports these instead of defining them inline. ReconciliationReport uses them for page reference formatting.

## Future Work

- PDF export (separate follow-up)
- Print-friendly CSS
