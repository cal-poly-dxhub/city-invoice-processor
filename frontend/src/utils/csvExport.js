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
 * Find the document for a budget item by matching doc_id.
 */
function findDocumentForBudgetItem(budgetItem, documents) {
  if (!budgetItem) return null

  // First try exact doc_id match with slugified budget item
  const slugifiedId = budgetItem.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/_+$/, '')
  const docById = documents.find(d => d.doc_id === slugifiedId)
  if (docById) return docById

  // Fallback to budget_item field match
  return documents.find(d => d.budget_item === budgetItem)
}

/**
 * Format an amount value for CSV display.
 */
function formatAmount(amount) {
  return amount > 0 ? parseFloat(amount).toFixed(2) : ''
}

/**
 * Format multiple amounts with separator.
 */
function formatMultipleAmounts(amounts) {
  if (!amounts || amounts.length === 0) return ''
  if (amounts.length === 1) return formatAmount(amounts[0])
  return amounts.map(a => formatAmount(a)).join(' / ')
}

/**
 * Format an employee name from first and last name parts.
 */
function formatEmployeeName(firstName, lastName) {
  return [firstName, lastName].filter(Boolean).join(' ')
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

  const sortedItems = [...lineItems].sort((a, b) => a.row_index - b.row_index)

  for (const item of sortedItems) {
    const status = getLineItemCompletionStatus(item.row_id)
    const pages = getCompletionPages(item.row_id)
    const document = findDocumentForBudgetItem(item.budget_item, documents)
    const sourceFiles = getSourceFiles(document)

    const formatPageReferences = (refs) => {
      return refs.map(r => formatPageRefForCsv(r, sourceFiles)).join('; ')
    }

    // Add line item row
    const employee = formatEmployeeName(item.raw?.employee_first_name, item.raw?.employee_last_name)
    const amount = formatAmount(item.raw?.amount)

    rows.push([
      item.row_index + 1,
      csvEscape(item.budget_item),
      '',  // label (empty for line items)
      amount,
      csvEscape(employee),
      status.payment ? 'Yes' : 'No',
      csvEscape(formatPageReferences(pages.payment)),
      status.invoice ? 'Yes' : 'No',
      csvEscape(formatPageReferences(pages.invoice)),
    ].join(','))

    // Add sub-item rows
    const subItemsList = subItems[item.row_id] || []

    for (let idx = 0; idx < subItemsList.length; idx++) {
      const subItem = subItemsList[idx]
      const subItemStatus = completionStatus[subItem.sub_item_id] || { payment: [], invoice: [] }
      const paymentPages = Array.isArray(subItemStatus.payment) ? subItemStatus.payment : []
      const invoicePages = Array.isArray(subItemStatus.invoice) ? subItemStatus.invoice : []

      const subItemAmount = subItem.amounts?.length > 1
        ? formatMultipleAmounts(subItem.amounts)
        : formatAmount(subItem.amounts?.[0] ?? subItem.amount)

      rows.push([
        `${item.row_index + 1}.${idx + 1}`,
        csvEscape(item.budget_item),
        csvEscape(subItem.label || subItem.sub_item_id),
        subItemAmount,
        '',  // employee (empty for sub-items)
        paymentPages.length > 0 ? 'Yes' : 'No',
        csvEscape(formatPageReferences(paymentPages)),
        invoicePages.length > 0 ? 'Yes' : 'No',
        csvEscape(formatPageReferences(invoicePages)),
      ].join(','))
    }
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