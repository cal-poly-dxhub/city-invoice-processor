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