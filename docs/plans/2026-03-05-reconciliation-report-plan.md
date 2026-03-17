# Reconciliation Report Modal — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the basic "Finish Reconciliation" summary modal with a comprehensive reconciliation report showing all line items (un-reconciled first), their payment/invoice evidence pages, expandable sub-items, clickable page references, and CSV export.

**Architecture:** Extract `resolveVirtualPage()` and `getSourceFiles()` from PDFViewer into a shared utility. Build a new `ReconciliationReport` component that receives all state via props from ReviewPage. Replace the inline modal body in ReviewPage with this component.

**Tech Stack:** React (Vite), vitest for tests, CSS variables from the existing editorial theme (`index.html :root`).

**Worktree:** `/home/jchan/dev/dxhub/city-invoice-processor/.worktrees/payment-invoice-checkboxes`
**Branch:** `feature/payment-invoice-checkboxes`

---

### Task 1: Extract virtual page utilities

Extract `resolveVirtualPage()` and `getSourceFiles()` from PDFViewer into a shared util so both PDFViewer and the new report component can use them.

**Files:**
- Create: `frontend/src/utils/virtualPages.js`
- Create: `frontend/src/utils/virtualPages.test.js`
- Modify: `frontend/src/components/PDFViewer.jsx` (lines 12-85)

**Step 1: Write the test file**

Create `frontend/src/utils/virtualPages.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { resolveVirtualPage, getSourceFiles } from './virtualPages.js'

describe('resolveVirtualPage', () => {
  const sourceFiles = [
    { doc_id: 'telecom__att', filename: 'att_bill.pdf', page_count: 5, page_offset: 0 },
    { doc_id: 'telecom__verizon', filename: 'verizon.pdf', page_count: 3, page_offset: 5 },
  ]

  it('resolves page 1 to first source file local page 1', () => {
    const result = resolveVirtualPage(1, sourceFiles)
    expect(result).toEqual({ sourceFile: sourceFiles[0], localPage: 1 })
  })

  it('resolves page 5 to first source file local page 5', () => {
    const result = resolveVirtualPage(5, sourceFiles)
    expect(result).toEqual({ sourceFile: sourceFiles[0], localPage: 5 })
  })

  it('resolves page 6 to second source file local page 1', () => {
    const result = resolveVirtualPage(6, sourceFiles)
    expect(result).toEqual({ sourceFile: sourceFiles[1], localPage: 1 })
  })

  it('resolves page 8 to second source file local page 3', () => {
    const result = resolveVirtualPage(8, sourceFiles)
    expect(result).toEqual({ sourceFile: sourceFiles[1], localPage: 3 })
  })

  it('returns null for page 0', () => {
    expect(resolveVirtualPage(0, sourceFiles)).toBeNull()
  })

  it('returns null for page beyond total', () => {
    expect(resolveVirtualPage(9, sourceFiles)).toBeNull()
  })

  it('returns null for empty source files', () => {
    expect(resolveVirtualPage(1, [])).toBeNull()
  })
})

describe('getSourceFiles', () => {
  it('returns source_files when present on doc', () => {
    const sf = [{ doc_id: 'a', filename: 'a.pdf', page_count: 3, page_offset: 0 }]
    const doc = { source_files: sf, doc_id: 'salary', budget_item: 'Salary', page_count: 3 }
    expect(getSourceFiles(doc)).toBe(sf)
  })

  it('returns legacy fallback for known budget items', () => {
    const doc = { doc_id: 'salary', budget_item: 'Salary', page_count: 10 }
    const result = getSourceFiles(doc)
    expect(result).toEqual([{
      doc_id: 'salary',
      pdf_path: 'Salary.pdf',
      filename: 'Salary.pdf',
      page_count: 10,
      page_offset: 0,
    }])
  })

  it('returns empty array for null doc', () => {
    expect(getSourceFiles(null)).toEqual([])
  })

  it('returns empty array for unknown budget item without source_files', () => {
    const doc = { doc_id: 'x', budget_item: 'Unknown' }
    expect(getSourceFiles(doc)).toEqual([])
  })
})
```

**Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/utils/virtualPages.test.js`
Expected: FAIL — module `./virtualPages.js` not found.

**Step 3: Create the utility module**

Create `frontend/src/utils/virtualPages.js`:

```js
/**
 * Fallback filename map for backward compat with old reconciliation.json
 * that lacks source_files arrays on DocumentRef objects.
 */
const LEGACY_FILENAME_MAP = {
  'Salary': 'Salary.pdf',
  'Fringe': 'Fringe.pdf',
  'Contractual Service': 'Contractual_Service.pdf',
  'Equipment': 'Equipment.pdf',
  'Insurance': 'Insurance.pdf',
  'Travel and Conferences': 'Travel_and_Conferences.pdf',
  'Space Rental/Occupancy Costs': 'Space_Rental_Occupancy_Costs.pdf',
  'Telecommunications': 'Telecommunications.pdf',
  'Utilities': 'Utilities.pdf',
  'Supplies': 'Supplies.pdf',
  'Other': 'Other.pdf',
  'Indirect Costs': 'Indirect_Costs.pdf',
}

/**
 * Get source files for a document, with backward-compat fallback.
 * @param {Object|null} doc - DocumentRef from reconciliation.json
 * @returns {Array} source_files array
 */
export function getSourceFiles(doc) {
  if (!doc) return []
  if (doc.source_files?.length > 0) return doc.source_files
  const filename = LEGACY_FILENAME_MAP[doc.budget_item]
  if (!filename) return []
  return [{
    doc_id: doc.doc_id,
    pdf_path: filename,
    filename: filename,
    page_count: doc.page_count || 0,
    page_offset: 0,
  }]
}

/**
 * Resolve a virtual page number to a physical PDF + local page number.
 * @param {number} virtualPageNum - 1-indexed virtual page number
 * @param {Array} sourceFiles - source_files array from getSourceFiles()
 * @returns {{ sourceFile: Object, localPage: number } | null}
 */
export function resolveVirtualPage(virtualPageNum, sourceFiles) {
  for (const sf of sourceFiles) {
    if (virtualPageNum > sf.page_offset && virtualPageNum <= sf.page_offset + sf.page_count) {
      return {
        sourceFile: sf,
        localPage: virtualPageNum - sf.page_offset,
      }
    }
  }
  return null
}
```

**Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/utils/virtualPages.test.js`
Expected: All 11 tests PASS.

**Step 5: Refactor PDFViewer to use the shared util**

In `frontend/src/components/PDFViewer.jsx`:

1. Add import at top: `import { resolveVirtualPage as _resolveVirtualPage, getSourceFiles as _getSourceFiles } from '../utils/virtualPages'`
2. Remove the `LEGACY_FILENAME_MAP` constant (lines 12-25)
3. Replace the `getSourceFiles` function body (lines 59-71) to delegate:
   ```js
   const getSourceFiles = () => _getSourceFiles(doc)
   ```
4. Replace the `resolveVirtualPage` function body (lines 74-85) to delegate:
   ```js
   const resolveVirtualPage = (virtualPageNum) => _resolveVirtualPage(virtualPageNum, getSourceFiles())
   ```

**Step 6: Verify build and existing functionality**

Run: `cd frontend && npm run build`
Expected: Build succeeds with no errors.

**Step 7: Commit**

```bash
git add frontend/src/utils/virtualPages.js frontend/src/utils/virtualPages.test.js frontend/src/components/PDFViewer.jsx
git commit -m "refactor: extract resolveVirtualPage and getSourceFiles to shared util"
```

---

### Task 2: Build ReconciliationReport component (report body)

Build the main report component with two sections, item rows, and page reference rendering.

**Files:**
- Create: `frontend/src/components/ReconciliationReport.jsx`
- Create: `frontend/src/components/ReconciliationReport.css`

**Step 1: Create the component**

Create `frontend/src/components/ReconciliationReport.jsx`. Props:

| Prop | Type | Purpose |
|------|------|---------|
| `lineItems` | `Array` | All line items (confidence-filtered) |
| `subItems` | `Object` | Map of row_id → sub-item arrays |
| `documents` | `Array` | Document list for virtual→physical resolution |
| `completionStatus` | `Object` | Full completion_status state |
| `getLineItemCompletionStatus` | `Function(rowId) → {payment: bool, invoice: bool}` | Rollup status |
| `getCompletionPages` | `Function(rowId) → {payment: PageRef[], invoice: PageRef[]}` | Page evidence arrays |
| `onNavigateToItem` | `Function(item, pageNum?)` | Close modal and navigate to item |
| `onClose` | `Function` | Close modal |

Component structure:

```jsx
import { useState, useMemo } from 'react'
import { resolveVirtualPage, getSourceFiles } from '../utils/virtualPages'
import './ReconciliationReport.css'

function ReconciliationReport({
  lineItems,
  subItems,
  documents,
  completionStatus,
  getLineItemCompletionStatus,
  getCompletionPages,
  onNavigateToItem,
  onClose,
}) {
  const [expandedItems, setExpandedItems] = useState(new Set())

  // Split line items into incomplete/complete, each sorted by row_index
  const { incompleteItems, completeItems } = useMemo(() => {
    const sorted = [...lineItems].sort((a, b) => a.row_index - b.row_index)
    const incomplete = []
    const complete = []
    for (const item of sorted) {
      const status = getLineItemCompletionStatus(item.row_id)
      if (status.payment && status.invoice) {
        complete.push(item)
      } else {
        incomplete.push(item)
      }
    }
    return { incompleteItems: incomplete, completeItems: complete }
  }, [lineItems, getLineItemCompletionStatus])

  const toggleExpand = (rowId) => {
    setExpandedItems(prev => {
      const next = new Set(prev)
      if (next.has(rowId)) next.delete(rowId)
      else next.add(rowId)
      return next
    })
  }

  // Resolve a PageReference to a display string
  const formatPageRef = (pageRef) => {
    const doc = documents.find(d => d.doc_id === pageRef.doc_id)
    const sourceFiles = getSourceFiles(doc)
    const resolved = resolveVirtualPage(pageRef.page, sourceFiles)
    if (resolved) {
      return {
        label: `${resolved.sourceFile.filename} p.${resolved.localPage} (vp ${pageRef.page})`,
        virtualPage: pageRef.page,
      }
    }
    return { label: `vp ${pageRef.page}`, virtualPage: pageRef.page }
  }

  // Render page reference links for a field (payment or invoice)
  const renderPageRefs = (pages, item) => {
    if (!pages || pages.length === 0) return <span className="report-none">None</span>
    return pages.map((ref, i) => {
      const formatted = formatPageRef(ref)
      return (
        <span key={i}>
          {i > 0 && ', '}
          <button
            className="report-page-link"
            onClick={() => onNavigateToItem(item, formatted.virtualPage)}
          >
            {formatted.label}
          </button>
        </span>
      )
    })
  }

  // Render a sub-item's completion row
  const renderSubItemStatus = (si, parentItem) => {
    const pages = completionStatus[si.sub_item_id]
      ? {
          payment: Array.isArray(completionStatus[si.sub_item_id].payment) ? completionStatus[si.sub_item_id].payment : [],
          invoice: Array.isArray(completionStatus[si.sub_item_id].invoice) ? completionStatus[si.sub_item_id].invoice : [],
        }
      : { payment: [], invoice: [] }

    const amountDisplay = si.amounts?.length > 1
      ? si.amounts.map(a => `$${a.toFixed(2)}`).join(' / ')
      : si.amount != null ? `$${si.amount.toFixed(2)}` : ''

    return (
      <div key={si.sub_item_id} className="report-sub-item">
        <div className="report-sub-item-header">
          <span className="report-sub-label">{si.label || si.sub_item_id}</span>
          {amountDisplay && <span className="report-sub-amount">{amountDisplay}</span>}
        </div>
        <div className="report-evidence-row">
          <span className={`report-status-badge ${pages.payment.length > 0 ? 'verified' : 'missing'}`}>
            {pages.payment.length > 0 ? '☑' : '☐'} Payment
          </span>
          {renderPageRefs(pages.payment, parentItem)}
        </div>
        <div className="report-evidence-row">
          <span className={`report-status-badge ${pages.invoice.length > 0 ? 'verified' : 'missing'}`}>
            {pages.invoice.length > 0 ? '☑' : '☐'} Invoice
          </span>
          {renderPageRefs(pages.invoice, parentItem)}
        </div>
      </div>
    )
  }

  // Count sub-item completion for rollup display
  const getSubItemCounts = (rowId, field) => {
    const subs = subItems[rowId] || []
    if (subs.length === 0) return null
    const verified = subs.filter(si => {
      const s = completionStatus[si.sub_item_id]
      return s && Array.isArray(s[field]) && s[field].length > 0
    }).length
    return { verified, total: subs.length }
  }

  // Render a single line item row
  const renderItemRow = (item) => {
    const status = getLineItemCompletionStatus(item.row_id)
    const pages = getCompletionPages(item.row_id)
    const subs = subItems[item.row_id] || []
    const hasSubs = subs.length > 0
    const isExpanded = expandedItems.has(item.row_id)
    const paymentCounts = getSubItemCounts(item.row_id, 'payment')
    const invoiceCounts = getSubItemCounts(item.row_id, 'invoice')

    return (
      <div key={item.row_id} className={`report-item ${status.payment && status.invoice ? 'complete' : 'incomplete'}`}>
        <div className="report-item-main">
          <div className="report-item-header">
            <span className="report-row-num">#{item.row_index + 1}</span>
            <span className="report-budget-item">{item.budget_item}</span>
            {item.raw?.amount > 0 && (
              <span className="report-amount">${parseFloat(item.raw.amount).toFixed(2)}</span>
            )}
          </div>
          {(item.raw?.employee_first_name || item.raw?.employee_last_name) && (
            <div className="report-employee">
              {item.raw.employee_first_name} {item.raw.employee_last_name}
            </div>
          )}
          <div className="report-evidence-row">
            <span className={`report-status-badge ${status.payment ? 'verified' : 'missing'}`}>
              {status.payment ? '☑' : '☐'} Payment
            </span>
            {hasSubs
              ? paymentCounts && <span className="report-sub-count">({paymentCounts.verified}/{paymentCounts.total} sub-items)</span>
              : renderPageRefs(pages.payment, item)
            }
          </div>
          <div className="report-evidence-row">
            <span className={`report-status-badge ${status.invoice ? 'verified' : 'missing'}`}>
              {status.invoice ? '☑' : '☐'} Invoice
            </span>
            {hasSubs
              ? invoiceCounts && <span className="report-sub-count">({invoiceCounts.verified}/{invoiceCounts.total} sub-items)</span>
              : renderPageRefs(pages.invoice, item)
            }
          </div>
          {hasSubs && (
            <button className="report-expand-btn" onClick={() => toggleExpand(item.row_id)}>
              {isExpanded ? '▾' : '▸'} {subs.length} sub-item{subs.length !== 1 ? 's' : ''}
            </button>
          )}
        </div>
        {hasSubs && isExpanded && (
          <div className="report-sub-items">
            {subs.map(si => renderSubItemStatus(si, item))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="reconciliation-report">
      {incompleteItems.length > 0 && (
        <section className="report-section needs-attention">
          <h3 className="report-section-title">
            <span className="report-section-icon attention">⚠</span>
            Needs Attention ({incompleteItems.length} item{incompleteItems.length !== 1 ? 's' : ''})
          </h3>
          {incompleteItems.map(renderItemRow)}
        </section>
      )}

      {completeItems.length > 0 && (
        <section className="report-section completed">
          <h3 className="report-section-title">
            <span className="report-section-icon complete">✓</span>
            Completed ({completeItems.length} item{completeItems.length !== 1 ? 's' : ''})
          </h3>
          {completeItems.map(renderItemRow)}
        </section>
      )}

      {lineItems.length === 0 && (
        <div className="report-empty">No line items found.</div>
      )}
    </div>
  )
}

export default ReconciliationReport
```

**Step 2: Create the CSS file**

Create `frontend/src/components/ReconciliationReport.css`. Use existing CSS variable conventions from `index.html :root` (e.g., `--color-accent`, `--font-serif`, `--font-mono`, `--color-excellent`, `--color-partial`).

Key classes:
- `.reconciliation-report` — container
- `.report-section` — section wrapper
- `.report-section-title` — section header with icon
- `.report-item` — individual line item card
- `.report-item.complete` / `.report-item.incomplete` — left-border color coding
- `.report-evidence-row` — payment/invoice line with badge + page refs
- `.report-status-badge.verified` / `.report-status-badge.missing` — green/orange badges
- `.report-page-link` — clickable page reference (styled as inline link)
- `.report-expand-btn` — expand/collapse sub-items
- `.report-sub-items` — sub-item container
- `.report-sub-item` — individual sub-item card (indented)

```css
.reconciliation-report {
  display: flex;
  flex-direction: column;
  gap: 1.5rem;
}

.report-section-title {
  font-family: var(--font-serif);
  font-size: 1.15rem;
  font-weight: 600;
  margin-bottom: 0.75rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.report-section-icon.attention {
  color: var(--color-partial);
}

.report-section-icon.complete {
  color: var(--color-excellent);
}

.report-item {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-left: 4px solid var(--color-border);
  border-radius: 6px;
  padding: 0.75rem 1rem;
  margin-bottom: 0.5rem;
}

.report-item.incomplete {
  border-left-color: var(--color-partial);
}

.report-item.complete {
  border-left-color: var(--color-excellent);
}

.report-item-header {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  margin-bottom: 0.25rem;
}

.report-row-num {
  font-family: var(--font-mono);
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-accent);
}

.report-budget-item {
  font-family: var(--font-sans);
  font-weight: 600;
  font-size: 0.95rem;
}

.report-amount {
  font-family: var(--font-mono);
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  margin-left: auto;
}

.report-employee {
  font-family: var(--font-sans);
  font-size: 0.85rem;
  color: var(--color-text-secondary);
  margin-bottom: 0.35rem;
}

.report-evidence-row {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.85rem;
  margin-top: 0.25rem;
  flex-wrap: wrap;
}

.report-status-badge {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  font-weight: 500;
  white-space: nowrap;
}

.report-status-badge.verified {
  color: var(--color-excellent);
}

.report-status-badge.missing {
  color: var(--color-partial);
}

.report-none {
  color: var(--color-text-secondary);
  font-style: italic;
  font-size: 0.8rem;
}

.report-page-link {
  background: none;
  border: none;
  color: var(--color-accent);
  font-family: var(--font-mono);
  font-size: 0.8rem;
  cursor: pointer;
  padding: 0;
  text-decoration: underline;
  text-decoration-style: dotted;
}

.report-page-link:hover {
  color: var(--color-text);
  text-decoration-style: solid;
}

.report-sub-count {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: var(--color-text-secondary);
}

.report-expand-btn {
  background: none;
  border: none;
  color: var(--color-accent);
  font-family: var(--font-sans);
  font-size: 0.85rem;
  cursor: pointer;
  padding: 0.25rem 0;
  margin-top: 0.25rem;
}

.report-expand-btn:hover {
  text-decoration: underline;
}

.report-sub-items {
  margin-top: 0.5rem;
  padding-left: 1rem;
  border-left: 2px solid var(--color-border);
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.report-sub-item {
  padding: 0.5rem 0.75rem;
  background: var(--color-surface);
  border-radius: 4px;
  border: 1px solid var(--color-border);
}

.report-sub-item-header {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  margin-bottom: 0.25rem;
}

.report-sub-label {
  font-family: var(--font-sans);
  font-weight: 500;
  font-size: 0.85rem;
}

.report-sub-amount {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: var(--color-text-secondary);
  margin-left: auto;
}

.report-empty {
  text-align: center;
  color: var(--color-text-secondary);
  padding: 2rem;
  font-style: italic;
}
```

**Step 3: Verify build**

Run: `cd frontend && npm run build`
Expected: Build succeeds (component isn't wired into anything yet, but it should compile cleanly).

**Step 4: Commit**

```bash
git add frontend/src/components/ReconciliationReport.jsx frontend/src/components/ReconciliationReport.css
git commit -m "feat: add ReconciliationReport component with two-section layout and page refs"
```

---

### Task 3: Add CSV export functionality

Add CSV generation and download to the ReconciliationReport component.

**Files:**
- Create: `frontend/src/utils/csvExport.js`
- Create: `frontend/src/utils/csvExport.test.js`
- Modify: `frontend/src/components/ReconciliationReport.jsx`

**Step 1: Write the test file**

Create `frontend/src/utils/csvExport.test.js`:

```js
import { describe, it, expect } from 'vitest'
import { generateReportCsv, formatPageRefForCsv } from './csvExport.js'

describe('formatPageRefForCsv', () => {
  it('formats a resolved page reference', () => {
    const result = formatPageRefForCsv(
      { page: 6, doc_id: 'telecom' },
      [{ doc_id: 'telecom__att', filename: 'att.pdf', page_count: 5, page_offset: 0 },
       { doc_id: 'telecom__verizon', filename: 'verizon.pdf', page_count: 3, page_offset: 5 }]
    )
    expect(result).toBe('verizon.pdf p.1 (vp 6)')
  })

  it('falls back to vp-only when no source files', () => {
    const result = formatPageRefForCsv({ page: 3, doc_id: 'x' }, [])
    expect(result).toBe('vp 3')
  })
})

describe('generateReportCsv', () => {
  it('generates CSV header', () => {
    const csv = generateReportCsv([], {}, {}, () => ({}), () => ({}), [])
    expect(csv).toContain('Row,Budget Item,Label,Amount,Employee,Payment Status,Payment Evidence,Invoice Status,Invoice Evidence')
  })

  it('generates a line item row', () => {
    const items = [{
      row_id: 'r1', row_index: 0, budget_item: 'Salary',
      raw: { amount: 1234.56, employee_first_name: 'Jane', employee_last_name: 'Doe' },
    }]
    const completionStatus = {}
    const subItems = {}
    const getStatus = () => ({ payment: false, invoice: false })
    const getPages = () => ({ payment: [], invoice: [] })

    const csv = generateReportCsv(items, subItems, completionStatus, getStatus, getPages, [])
    const lines = csv.split('\n')
    expect(lines[1]).toBe('1,Salary,,1234.56,Jane Doe,No,,No,')
  })

  it('generates sub-item rows with dotted numbering', () => {
    const items = [{
      row_id: 'r1', row_index: 0, budget_item: 'Telecom',
      raw: { amount: 500 },
    }]
    const subItems = {
      r1: [
        { sub_item_id: 's1', label: 'AT&T', amount: 72, amounts: [72] },
        { sub_item_id: 's2', label: 'Verizon', amount: 19.16, amounts: [19.16, 2.68] },
      ]
    }
    const completionStatus = {
      s1: { payment: [{ page: 3, doc_id: 'telecom' }], invoice: [] },
      s2: { payment: [], invoice: [] },
    }
    const getStatus = () => ({ payment: false, invoice: false })
    const getPages = () => ({ payment: [], invoice: [] })

    const csv = generateReportCsv(items, subItems, completionStatus, getStatus, getPages, [])
    const lines = csv.split('\n')
    // Line item row
    expect(lines[1]).toContain('1,Telecom,')
    // Sub-item rows
    expect(lines[2]).toContain('1.1,Telecom,AT&T,72.00')
    expect(lines[3]).toContain('1.2,Telecom,Verizon,19.16 / 2.68')
  })

  it('escapes commas and quotes in CSV fields', () => {
    const items = [{
      row_id: 'r1', row_index: 0, budget_item: 'Space Rental/Occupancy Costs',
      raw: { amount: 100 },
    }]
    const csv = generateReportCsv(items, {}, {}, () => ({ payment: false, invoice: false }), () => ({ payment: [], invoice: [] }), [])
    expect(csv).toContain('"Space Rental/Occupancy Costs"')
  })
})
```

**Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/utils/csvExport.test.js`
Expected: FAIL — module not found.

**Step 3: Create the CSV utility**

Create `frontend/src/utils/csvExport.js`:

```js
import { resolveVirtualPage, getSourceFiles } from './virtualPages.js'

/**
 * Escape a value for CSV: wrap in quotes if it contains commas, quotes, or newlines.
 */
function csvEscape(value) {
  const str = String(value ?? '')
  if (str.includes(',') || str.includes('"') || str.includes('\n')) {
    return `"${str.replace(/"/g, '""')}"`
  }
  return str
}

/**
 * Format a single PageReference for CSV output.
 * @param {{ page: number, doc_id: string }} pageRef
 * @param {Array} sourceFiles - from getSourceFiles(doc)
 * @returns {string}
 */
export function formatPageRefForCsv(pageRef, sourceFiles) {
  const resolved = resolveVirtualPage(pageRef.page, sourceFiles)
  if (resolved) {
    return `${resolved.sourceFile.filename} p.${resolved.localPage} (vp ${pageRef.page})`
  }
  return `vp ${pageRef.page}`
}

/**
 * Generate a CSV string for the reconciliation report.
 */
export function generateReportCsv(
  lineItems,
  subItems,
  completionStatus,
  getLineItemCompletionStatus,
  getCompletionPages,
  documents,
) {
  const header = 'Row,Budget Item,Label,Amount,Employee,Payment Status,Payment Evidence,Invoice Status,Invoice Evidence'
  const rows = [header]

  const sorted = [...lineItems].sort((a, b) => a.row_index - b.row_index)

  for (const item of sorted) {
    const status = getLineItemCompletionStatus(item.row_id)
    const pages = getCompletionPages(item.row_id)
    const doc = documents.find(d => d.doc_id === item.budget_item?.toLowerCase?.().replace(/[^a-z0-9]+/g, '_').replace(/_+$/, ''))
      || documents.find(d => d.budget_item === item.budget_item)
    const sf = getSourceFiles(doc)

    const formatRefs = (refs) => refs.map(r => formatPageRefForCsv(r, sf)).join('; ')

    const employee = [item.raw?.employee_first_name, item.raw?.employee_last_name].filter(Boolean).join(' ')
    const amount = item.raw?.amount > 0 ? parseFloat(item.raw.amount).toFixed(2) : ''

    rows.push([
      item.row_index + 1,
      csvEscape(item.budget_item),
      '',  // label (empty for line items)
      amount,
      csvEscape(employee),
      status.payment ? 'Yes' : 'No',
      csvEscape(formatRefs(pages.payment)),
      status.invoice ? 'Yes' : 'No',
      csvEscape(formatRefs(pages.invoice)),
    ].join(','))

    // Sub-item rows
    const subs = subItems[item.row_id] || []
    subs.forEach((si, idx) => {
      const siStatus = completionStatus[si.sub_item_id] || { payment: [], invoice: [] }
      const siPayment = Array.isArray(siStatus.payment) ? siStatus.payment : []
      const siInvoice = Array.isArray(siStatus.invoice) ? siStatus.invoice : []

      const siAmount = si.amounts?.length > 1
        ? si.amounts.map(a => a.toFixed(2)).join(' / ')
        : si.amount != null ? si.amount.toFixed(2) : ''

      rows.push([
        `${item.row_index + 1}.${idx + 1}`,
        csvEscape(item.budget_item),
        csvEscape(si.label || si.sub_item_id),
        siAmount,
        '',  // employee (empty for sub-items)
        siPayment.length > 0 ? 'Yes' : 'No',
        csvEscape(formatRefs(siPayment)),
        siInvoice.length > 0 ? 'Yes' : 'No',
        csvEscape(formatRefs(siInvoice)),
      ].join(','))
    })
  }

  return rows.join('\n')
}

/**
 * Trigger a CSV file download in the browser.
 */
export function downloadCsv(csvContent, filename) {
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
```

**Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/utils/csvExport.test.js`
Expected: All tests PASS.

**Step 5: Add export button to ReconciliationReport**

In `frontend/src/components/ReconciliationReport.jsx`, add at the top of the file:

```js
import { generateReportCsv, downloadCsv } from '../utils/csvExport'
```

Add a handler inside the component:

```js
const handleExportCsv = () => {
  const csv = generateReportCsv(
    lineItems, subItems, completionStatus,
    getLineItemCompletionStatus, getCompletionPages, documents,
  )
  downloadCsv(csv, 'reconciliation-report.csv')
}
```

Add an export button in the JSX return, at the bottom of the component (after both sections, before the closing `</div>`):

```jsx
<div className="report-footer">
  <button className="report-export-btn" onClick={handleExportCsv}>
    Export CSV
  </button>
</div>
```

Add to `ReconciliationReport.css`:

```css
.report-footer {
  display: flex;
  justify-content: flex-end;
  padding-top: 1rem;
  border-top: 1px solid var(--color-border);
  margin-top: 0.5rem;
}

.report-export-btn {
  font-family: var(--font-sans);
  font-size: 0.85rem;
  font-weight: 500;
  padding: 0.5rem 1.25rem;
  border: 1px solid var(--color-accent);
  background: var(--color-surface);
  color: var(--color-accent);
  border-radius: 4px;
  cursor: pointer;
  transition: all 0.15s;
}

.report-export-btn:hover {
  background: var(--color-accent);
  color: var(--color-surface);
}
```

**Step 6: Verify build**

Run: `cd frontend && npm run build`
Expected: Build succeeds.

**Step 7: Commit**

```bash
git add frontend/src/utils/csvExport.js frontend/src/utils/csvExport.test.js frontend/src/components/ReconciliationReport.jsx frontend/src/components/ReconciliationReport.css
git commit -m "feat: add CSV export to reconciliation report"
```

---

### Task 4: Wire ReconciliationReport into ReviewPage

Replace the inline modal body in ReviewPage with the new component.

**Files:**
- Modify: `frontend/src/pages/ReviewPage.jsx` (lines 400-497)
- Modify: `frontend/src/pages/ReviewPage.css` (remove old `.summary-complete`, `.summary-list`, `.summary-item` styles that are no longer used)

**Step 1: Update ReviewPage imports**

Add at the top of `ReviewPage.jsx`:

```js
import ReconciliationReport from "../components/ReconciliationReport";
```

**Step 2: Replace modal body**

Replace the entire `{showSummary && (...)}` block (lines 420-497) with:

```jsx
{showSummary && (
  <div className="summary-overlay" onClick={() => setShowSummary(false)}>
    <div className="summary-modal" onClick={(e) => e.stopPropagation()}>
      <div className="summary-header">
        <h2>Reconciliation Report</h2>
        <button className="close-btn" onClick={() => setShowSummary(false)}>×</button>
      </div>
      <div className="summary-body">
        <ReconciliationReport
          lineItems={data?.line_items?.map(item => applyConfidenceFilter(item)) || []}
          subItems={subItems}
          documents={data?.documents || []}
          completionStatus={completionStatus}
          getLineItemCompletionStatus={getLineItemCompletionStatus}
          getCompletionPages={getCompletionPages}
          onNavigateToItem={(item, pageNum) => {
            setSelectedItem(item);
            setShowSummary(false);
          }}
          onClose={() => setShowSummary(false)}
        />
      </div>
    </div>
  </div>
)}
```

Note: The modal overlay, header, close button, and `.summary-body` wrapper remain in ReviewPage (they're part of the modal chrome). Only the body content is delegated to ReconciliationReport.

**Step 3: Remove the `incompleteItems` variable**

Delete lines 400-403 (the `incompleteItems` computation). It's no longer needed — ReconciliationReport computes its own split internally.

**Step 4: Remove the `summary-footer` from ReviewPage**

The old footer with "Close" / "Continue Reviewing" button is no longer needed. The ReconciliationReport component has its own export button footer, and the close button is in the modal header.

**Step 5: Clean up unused CSS**

In `frontend/src/pages/ReviewPage.css`, remove the following classes that are no longer used (they've been replaced by ReconciliationReport.css classes):
- `.summary-complete`, `.complete-icon`, `.summary-complete h3`, `.summary-complete p`
- `.summary-intro`, `.summary-intro p`
- `.summary-list`
- `.summary-item`, `.summary-item:hover`, `.summary-item-header`
- `.summary-row`, `.summary-budget`, `.summary-amount`, `.summary-employee`
- `.summary-footer`, `.summary-close-btn`, `.summary-close-btn:hover`, `.summary-close-btn:active`

Keep: `.summary-overlay`, `.summary-modal`, `.summary-header`, `.summary-header h2`, `.close-btn`, `.close-btn:hover`, `.summary-body` and the scrollbar styles — these are still used by the modal chrome.

**Step 6: Verify build**

Run: `cd frontend && npm run build`
Expected: Build succeeds.

**Step 7: Run all frontend tests**

Run: `cd frontend && npx vitest run`
Expected: All tests pass (virtualPages, csvExport, budgetItemClassifier, keywordSync).

**Step 8: Commit**

```bash
git add frontend/src/pages/ReviewPage.jsx frontend/src/pages/ReviewPage.css
git commit -m "feat: wire ReconciliationReport into modal, replace old summary body"
```

---

### Task 5: Verification and push

Final verification that everything works end-to-end.

**Step 1: Run all tests**

Run: `cd frontend && npx vitest run`
Expected: All tests PASS.

**Step 2: Run production build**

Run: `cd frontend && npm run build`
Expected: Build succeeds with no errors.

**Step 3: Push to remote**

```bash
git push origin feature/payment-invoice-checkboxes
```
