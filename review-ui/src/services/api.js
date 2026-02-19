/**
 * API service layer for backend Lambda invocations.
 *
 * The API_URL is set via the VITE_API_URL environment variable.
 * In production, this points to the API Gateway endpoint.
 * For local development, set it in .env.local:
 *   VITE_API_URL=https://<api-id>.execute-api.us-west-2.amazonaws.com/prod
 */

const API_URL = (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '')

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
  return _post(`/api/jobs/${jobId}/sub-items/match`, {
    mode: 'match',
    ...params,
  })
}

/**
 * Load user edits (overrides + sub-items) from S3.
 *
 * @param {string} jobId
 * @returns {Promise<{overrides: Array, sub_items: Array}>}
 */
export async function loadUserEdits(jobId) {
  const resp = await fetch(`${API_URL}/api/jobs/${jobId}/edits`)
  if (!resp.ok) throw new Error(`Failed to load edits: ${resp.status}`)
  const data = await resp.json()
  // API Gateway wraps the response body as a string
  return typeof data === 'string' ? JSON.parse(data) : data
}

/**
 * Save user edits (overrides + sub-items) to S3.
 *
 * @param {string} jobId
 * @param {object} edits - { overrides: [...], sub_items: [...] }
 * @returns {Promise<{status: string}>}
 */
export async function saveUserEdits(jobId, edits) {
  return _post(`/api/jobs/${jobId}/edits`, edits, 'PUT')
}

// ---------------------------------------------------------------------------
// Internal
// ---------------------------------------------------------------------------

async function _post(path, body, method = 'POST') {
  const resp = await fetch(`${API_URL}${path}`, {
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
