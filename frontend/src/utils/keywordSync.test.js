import { describe, it, expect } from 'vitest'
import { addKeyword, removeKeyword, normalizeKeyword } from './keywordSync.js'

describe('normalizeKeyword', () => {
  it('lowercases and trims input', () => {
    expect(normalizeKeyword('  ATT  ')).toBe('att')
  })

  it('returns empty string for whitespace-only input', () => {
    expect(normalizeKeyword('   ')).toBe('')
  })
})

describe('addKeyword', () => {
  it('adds a normalized keyword to both keywords and row_texts', () => {
    const proposal = {
      keywords: ['att', 'wireless'],
      row_texts: ['att', 'wireless'],
    }
    const result = addKeyword(proposal, 'Phone')
    expect(result.keywords).toEqual(['att', 'wireless', 'phone'])
    expect(result.row_texts).toEqual(['att', 'wireless', 'phone'])
  })

  it('does not add duplicate keywords', () => {
    const proposal = {
      keywords: ['att', 'wireless'],
      row_texts: ['att', 'wireless'],
    }
    const result = addKeyword(proposal, 'att')
    expect(result.keywords).toEqual(['att', 'wireless'])
    expect(result.row_texts).toEqual(['att', 'wireless'])
  })

  it('does not add empty string', () => {
    const proposal = {
      keywords: ['att'],
      row_texts: ['att'],
    }
    const result = addKeyword(proposal, '  ')
    expect(result.keywords).toEqual(['att'])
    expect(result.row_texts).toEqual(['att'])
  })
})

describe('removeKeyword', () => {
  it('removes a keyword from both keywords and row_texts', () => {
    const proposal = {
      keywords: ['att', 'wireless', 'phone'],
      row_texts: ['att', 'wireless', 'phone'],
    }
    const result = removeKeyword(proposal, 'wireless')
    expect(result.keywords).toEqual(['att', 'phone'])
    expect(result.row_texts).toEqual(['att', 'phone'])
  })

  it('returns unchanged arrays if keyword not found', () => {
    const proposal = {
      keywords: ['att'],
      row_texts: ['att'],
    }
    const result = removeKeyword(proposal, 'missing')
    expect(result.keywords).toEqual(['att'])
    expect(result.row_texts).toEqual(['att'])
  })
})
