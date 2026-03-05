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