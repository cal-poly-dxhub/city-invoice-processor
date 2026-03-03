/**
 * API service layer for backend Lambda invocations.
 *
 * Uses API_BASE from config.js (set via VITE_API_URL env var).
 */

import { API_BASE } from '../config'
import { authFetch } from './authenticatedFetch'

/**
 * Auto-extract proposed sub-items from table rows on a general ledger page.
 *
 * @param {string} jobId
 * @param {object} params
 * @param {string} params.doc_id - Budget item doc_id (e.g., "telecommunications")
 * @param {string} params.budget_item - Canonical budget item name
 * @param {number} params.source_page - The GL page number to extract from
 * @param {string} params.parent_row_id - Parent line item row_id
 * @returns {Promise<{proposed_sub_items: Array}>}
 */
export async function autoExtractSubItems(jobId, params) {
  return _post(`/api/jobs/${jobId}/sub-items/match`, {
    mode: 'auto_extract',
    ...params,
  })
}

/**
 * Run matching for a specific sub-item.
 *
 * On 504 (API Gateway timeout), the Lambda continues processing and caches
 * the result in S3. This function automatically polls until the result is
 * ready, so callers don't need to handle timeouts.
 *
 * @param {string} jobId
 * @param {object} params
 * @param {string} params.sub_item_id - Sub-item identifier (e.g., "row_51_a")
 * @param {string} params.doc_id - Budget item doc_id
 * @param {string} params.budget_item - Canonical budget item name
 * @param {string} params.parent_row_id - Parent line item row_id
 * @param {string[]} params.keywords - Search keywords
 * @param {number|null} params.amount - Dollar amount to match
 * @param {number|null} params.source_page - GL page to exclude from results
 * @returns {Promise<{sub_item_id: string, candidates: Array, selected_evidence: object}>}
 */
export async function matchSubItem(jobId, params) {
  const matchBody = { mode: 'match', ...params }

  try {
    return await _post(`/api/jobs/${jobId}/sub-items/match`, matchBody)
  } catch (err) {
    // On 504 (API Gateway timeout), the Lambda is still running and will
    // cache the result. Poll until it's ready.
    if (!err.message.includes('504')) throw err

    console.log(`matchSubItem: 504 timeout for ${params.sub_item_id}, polling...`)
    return _pollForResult(jobId, matchBody)
  }
}

/**
 * Poll for a match result after a 504 timeout.
 * Retries the same match request (which checks S3 cache first) with backoff.
 */
async function _pollForResult(jobId, matchBody, maxAttempts = 20, intervalMs = 3000) {
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    await new Promise(r => setTimeout(r, intervalMs))
    console.log(`matchSubItem: poll attempt ${attempt}/${maxAttempts}`)
    try {
      const result = await _post(`/api/jobs/${jobId}/sub-items/match`, matchBody)
      // If we got a real result (not just "processing"), return it
      if (result.candidates || result.selected_evidence) return result
      if (result.status === 'processing') continue
      return result
    } catch (retryErr) {
      if (retryErr.message.includes('504')) continue
      throw retryErr
    }
  }
  throw new Error('Match timed out after polling')
}

/**
 * Classify unrecognized PDF filenames via Bedrock Nova Lite LLM.
 *
 * @param {string[]} filenames - List of PDF filenames to classify
 * @returns {Promise<{assignments: Object<string, string|null>}>}
 */
export async function classifyFilenames(filenames) {
  return _post('/api/classify-filenames', { filenames })
}

// ---------------------------------------------------------------------------
// Internal
// ---------------------------------------------------------------------------

async function _post(path, body, method = 'POST') {
  const resp = await authFetch(`${API_BASE}${path}`, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })

  if (!resp.ok) {
    const errBody = await resp.text()
    let message
    try {
      message = JSON.parse(errBody).error || errBody
    } catch {
      message = errBody
    }
    throw new Error(`API ${method} ${path} failed (${resp.status}): ${message}`)
  }

  const data = await resp.json()
  // API Gateway may wrap body as string
  return typeof data === 'string' ? JSON.parse(data) : data
}
