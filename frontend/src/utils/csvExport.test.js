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
      row_id: 'r1', row_index: 0, budget_item: 'Travel, Conferences',
      raw: { amount: 100, employee_first_name: 'John "JD"', employee_last_name: 'Doe' },
    }]
    const csv = generateReportCsv(items, {}, {}, () => ({ payment: false, invoice: false }), () => ({ payment: [], invoice: [] }), [])
    expect(csv).toContain('"Travel, Conferences"')
    expect(csv).toContain('"John ""JD"" Doe"')
  })
})