/**
 * Keyword sync utilities for sub-item HITL review.
 * Keeps proposal.keywords and proposal.row_texts in sync.
 */

export function normalizeKeyword(raw) {
  return raw.trim().toLowerCase()
}

export function addKeyword(proposal, raw) {
  const normalized = normalizeKeyword(raw)
  if (!normalized || proposal.keywords.includes(normalized)) {
    return { keywords: proposal.keywords, row_texts: proposal.row_texts }
  }
  const keywords = [...proposal.keywords, normalized]
  return { keywords, row_texts: [...keywords] }
}

export function removeKeyword(proposal, keyword) {
  const keywords = proposal.keywords.filter(k => k !== keyword)
  return { keywords, row_texts: [...keywords] }
}
