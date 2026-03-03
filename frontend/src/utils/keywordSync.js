/**
 * Keyword sync utilities for sub-item HITL review.
 * Keeps proposal.keywords and proposal.row_texts in sync.
 */

export function normalizeKeyword(raw) {
  if (typeof raw !== 'string') return ''
  return raw.trim().toLowerCase()
}

export function addKeyword(proposal, raw) {
  const normalized = normalizeKeyword(raw)
  const keywords = proposal.keywords || []
  const row_texts = proposal.row_texts || []
  if (!normalized || keywords.includes(normalized)) {
    return { keywords, row_texts }
  }
  const next = [...keywords, normalized]
  return { keywords: next, row_texts: [...next] }
}

export function removeKeyword(proposal, keyword) {
  const keywords = proposal.keywords || []
  if (!keywords.includes(keyword)) {
    return { keywords, row_texts: proposal.row_texts || [] }
  }
  const next = keywords.filter(k => k !== keyword)
  return { keywords: next, row_texts: [...next] }
}
