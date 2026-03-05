import { useState, useMemo } from 'react'
import { resolveVirtualPage, getSourceFiles } from '../utils/virtualPages.js'
import { generateReportCsv, downloadCsv } from '../utils/csvExport.js'
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

  function toggleExpand(rowId) {
    setExpandedItems(prev => {
      const next = new Set(prev)
      if (next.has(rowId)) {
        next.delete(rowId)
      } else {
        next.add(rowId)
      }
      return next
    })
  }

  function handleExportCsv() {
    const csv = generateReportCsv(
      lineItems,
      subItems,
      completionStatus,
      getLineItemCompletionStatus,
      getCompletionPages,
      documents,
    )
    downloadCsv(csv, 'reconciliation-report.csv')
  }

  // Resolve a PageReference to a display string
  function formatPageRef(pageRef) {
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
  function renderPageRefs(pages, item) {
    if (!pages || pages.length === 0) {
      return <span className="report-none">None</span>
    }
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
  function renderSubItemStatus(si, parentItem) {
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
  function getSubItemCounts(rowId, field) {
    const subs = subItems[rowId] || []
    if (subs.length === 0) return null
    const verified = subs.filter(si => {
      const s = completionStatus[si.sub_item_id]
      return s && Array.isArray(s[field]) && s[field].length > 0
    }).length
    return { verified, total: subs.length }
  }

  // Render a single line item row
  function renderItemRow(item) {
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

      <div className="report-footer">
        <button className="report-export-btn" onClick={handleExportCsv}>
          Export CSV
        </button>
      </div>
    </div>
  )
}

export default ReconciliationReport