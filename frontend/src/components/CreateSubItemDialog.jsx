import { useState, useEffect } from 'react'
import { autoExtractSubItems, matchSubItem } from '../services/api'
import './CreateSubItemDialog.css'

/**
 * Dialog for creating sub-items from a general ledger page.
 *
 * Supports two modes:
 * - "manual": User fills in label/keywords/amount and runs matching for one sub-item.
 * - "auto": System proposes sub-items from table rows; user edits and bulk-matches.
 */
function CreateSubItemDialog({
  open,
  onClose,
  jobId,
  parentRowId,
  docId,
  budgetItem,
  sourcePage,
  getNextSubItemSuffix,
  onAddSubItem,
  onAddSubItems,
  initialMode = 'manual',
}) {
  const [mode, setMode] = useState(initialMode)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  // Manual mode state
  const [label, setLabel] = useState('')
  const [keywords, setKeywords] = useState('')
  const [amount, setAmount] = useState('')
  const [matchResults, setMatchResults] = useState(null)

  // Auto mode state
  const [proposals, setProposals] = useState([])
  const [matchedProposals, setMatchedProposals] = useState({}) // { index: { candidates, selected_evidence } }
  const [matchingAll, setMatchingAll] = useState(false)

  // Reset state when dialog opens
  useEffect(() => {
    if (open) {
      setLabel('')
      setKeywords('')
      setAmount('')
      setMatchResults(null)
      setProposals([])
      setMatchedProposals({})
      setError(null)
      setMode(initialMode)
    }
  }, [open, initialMode])

  if (!open) return null

  // --- Manual mode handlers ---

  const handleManualSearch = async () => {
    if (!label.trim() && !amount) {
      setError('Provide at least a label or amount')
      return
    }

    setLoading(true)
    setError(null)
    setMatchResults(null)

    try {
      const suffix = getNextSubItemSuffix(parentRowId)
      const subItemId = `${parentRowId}_${suffix}`
      const kws = keywords.split(',').map(k => k.trim()).filter(Boolean)

      const result = await matchSubItem(jobId, {
        sub_item_id: subItemId,
        parent_row_id: parentRowId,
        doc_id: docId,
        budget_item: budgetItem,
        keywords: kws,
        amount: amount ? parseFloat(amount) : null,
        source_page: sourcePage,
      })

      setMatchResults({
        subItemId,
        candidates: result.candidates || [],
        selectedEvidence: result.selected_evidence,
      })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleManualConfirm = () => {
    if (!matchResults) return

    const kws = keywords.split(',').map(k => k.trim()).filter(Boolean)
    onAddSubItem({
      sub_item_id: matchResults.subItemId,
      parent_row_id: parentRowId,
      label: label.trim() || `Sub-item ${matchResults.subItemId}`,
      doc_id: docId,
      keywords: kws,
      amount: amount ? parseFloat(amount) : null,
      source_page: sourcePage,
      candidates: matchResults.candidates,
      selected_evidence: matchResults.selectedEvidence,
    })

    onClose()
  }

  // --- Auto mode handlers ---

  const handleAutoExtract = async () => {
    setLoading(true)
    setError(null)
    setProposals([])
    setMatchedProposals({})

    try {
      const result = await autoExtractSubItems(jobId, {
        parent_row_id: parentRowId,
        doc_id: docId,
        budget_item: budgetItem,
        source_page: sourcePage,
      })

      const items = (result.proposed_sub_items || []).map((p, i) => ({
        ...p,
        enabled: true,
        label: p.label || `Item ${i + 1}`,
      }))
      setProposals(items)

      if (items.length === 0) {
        setError(`No ${budgetItem} line items found in the table on page ${sourcePage}`)
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  // Load proposals on auto mode
  useEffect(() => {
    if (open && mode === 'auto') {
      handleAutoExtract()
    }
  }, [open, mode])

  const toggleProposal = (idx) => {
    setProposals(prev =>
      prev.map((p, i) => i === idx ? { ...p, enabled: !p.enabled } : p)
    )
  }

  const setAllEnabled = (enabled) => {
    setProposals(prev => prev.map(p => ({ ...p, enabled })))
  }

  const updateProposalLabel = (idx, val) => {
    setProposals(prev =>
      prev.map((p, i) => i === idx ? { ...p, label: val } : p)
    )
  }

  const updateProposalAmount = (idx, val) => {
    setProposals(prev =>
      prev.map((p, i) => i === idx ? { ...p, amount: val ? parseFloat(val) : null } : p)
    )
  }

  const handleMatchAll = async () => {
    const enabledProposals = proposals
      .map((p, i) => ({ ...p, _idx: i }))
      .filter(p => p.enabled)

    if (enabledProposals.length === 0) return

    setMatchingAll(true)
    setError(null)
    const results = {}

    // Match in parallel
    const existingCount = getNextSubItemSuffix(parentRowId).charCodeAt(0) - 97
    const promises = enabledProposals.map(async (proposal, i) => {
      const suffix = String.fromCharCode(97 + existingCount + i)
      const subItemId = `${parentRowId}_${suffix}`
      const kws = proposal.keywords || []

      try {
        const result = await matchSubItem(jobId, {
          sub_item_id: subItemId,
          parent_row_id: parentRowId,
          doc_id: docId,
          budget_item: budgetItem,
          keywords: kws,
          amount: proposal.amount,
          source_page: sourcePage,
          row_texts: proposal.row_texts || [],
          table_row_texts: proposal.table_row_texts || [],
        })

        results[proposal._idx] = {
          subItemId,
          candidates: result.candidates || [],
          selectedEvidence: result.selected_evidence,
        }
      } catch (err) {
        results[proposal._idx] = {
          subItemId,
          candidates: [],
          selectedEvidence: null,
          error: err.message,
        }
      }
    })

    await Promise.all(promises)
    setMatchedProposals(results)
    setMatchingAll(false)
  }

  const handleAutoConfirm = () => {
    const newSubItems = []
    for (const proposal of proposals) {
      if (!proposal.enabled) continue
      const idx = proposals.indexOf(proposal)
      const matched = matchedProposals[idx]
      if (!matched) continue

      newSubItems.push({
        sub_item_id: matched.subItemId,
        parent_row_id: parentRowId,
        label: proposal.label,
        doc_id: docId,
        keywords: proposal.keywords || [],
        amount: proposal.amount,
        source_page: sourcePage,
        candidates: matched.candidates || [],
        selected_evidence: matched.selectedEvidence,
      })
    }

    if (newSubItems.length > 0) {
      onAddSubItems(parentRowId, newSubItems)
    }
    onClose()
  }

  const enabledCount = proposals.filter(p => p.enabled).length
  const matchedCount = Object.keys(matchedProposals).length
  const hasMatches = matchedCount > 0

  return (
    <div className="dialog-overlay" onClick={onClose}>
      <div className="dialog-content" onClick={e => e.stopPropagation()}>
        <div className="dialog-header">
          <h2>
            {mode === 'manual' ? 'Create Sub-Item' : 'Auto-Extract Sub-Items'}
          </h2>
          <button className="dialog-close" onClick={onClose}>×</button>
        </div>

        <div className="dialog-mode-tabs">
          <button
            className={`mode-tab ${mode === 'manual' ? 'active' : ''}`}
            onClick={() => setMode('manual')}
          >
            Manual
          </button>
          <button
            className={`mode-tab ${mode === 'auto' ? 'active' : ''}`}
            onClick={() => setMode('auto')}
          >
            Auto-Extract from Table
          </button>
        </div>

        <div className="dialog-body">
          <div className="dialog-context">
            <span className="context-label">Budget Item:</span> {budgetItem}
            <span className="context-sep">|</span>
            <span className="context-label">Source Page:</span> {sourcePage}
          </div>

          {error && <div className="dialog-error">{error}</div>}

          {mode === 'manual' && (
            <div className="manual-form">
              <div className="form-field">
                <label>Label</label>
                <input
                  type="text"
                  value={label}
                  onChange={e => setLabel(e.target.value)}
                  placeholder="e.g., AT&T Phone Bill"
                />
              </div>
              <div className="form-field">
                <label>Keywords (comma-separated)</label>
                <input
                  type="text"
                  value={keywords}
                  onChange={e => setKeywords(e.target.value)}
                  placeholder="e.g., AT&T, phone, wireless"
                />
              </div>
              <div className="form-field">
                <label>Amount ($)</label>
                <input
                  type="number"
                  step="0.01"
                  value={amount}
                  onChange={e => setAmount(e.target.value)}
                  placeholder="e.g., 42.50"
                />
              </div>

              <button
                className="search-btn"
                onClick={handleManualSearch}
                disabled={loading}
              >
                {loading ? 'Searching...' : 'Search'}
              </button>

              {matchResults && (
                <div className="match-results">
                  <h4>
                    {matchResults.candidates.length} candidate{matchResults.candidates.length !== 1 ? 's' : ''} found
                  </h4>
                  {matchResults.candidates.slice(0, 3).map((c, i) => (
                    <div key={i} className="result-candidate">
                      <span className="result-score">
                        Score: {c.score.toFixed(2)}
                      </span>
                      <span className="result-pages">
                        Page{c.page_numbers.length !== 1 ? 's' : ''}: {c.page_numbers.join(', ')}
                      </span>
                      <div className="result-rationale">
                        {c.rationale?.[0]}
                      </div>
                    </div>
                  ))}

                  <button
                    className="confirm-btn"
                    onClick={handleManualConfirm}
                  >
                    Add Sub-Item
                  </button>
                </div>
              )}
            </div>
          )}

          {mode === 'auto' && (
            <div className="auto-form">
              {loading && (
                <div className="auto-loading">
                  Extracting line items from table...
                </div>
              )}

              {proposals.length > 0 && (
                <>
                  <div className="proposals-header">
                    <span>
                      {enabledCount} of {proposals.length} items selected
                    </span>
                    <button
                      className="batch-select-btn"
                      onClick={() => setAllEnabled(enabledCount < proposals.length)}
                    >
                      {enabledCount === proposals.length ? 'Deselect All' : 'Select All'}
                    </button>
                  </div>

                  <div className="proposals-list">
                    {proposals.map((p, idx) => (
                      <div
                        key={idx}
                        className={`proposal-row ${!p.enabled ? 'disabled' : ''}`}
                      >
                        <input
                          type="checkbox"
                          checked={p.enabled}
                          onChange={() => toggleProposal(idx)}
                        />
                        <input
                          type="text"
                          className="proposal-label"
                          value={p.label}
                          onChange={e => updateProposalLabel(idx, e.target.value)}
                        />
                        <input
                          type="number"
                          className="proposal-amount"
                          step="0.01"
                          value={p.amount ?? ''}
                          onChange={e => updateProposalAmount(idx, e.target.value)}
                        />
                        {matchedProposals[idx] && (
                          <span className="proposal-match-status">
                            {matchedProposals[idx].error
                              ? 'Error'
                              : `${matchedProposals[idx].candidates.length} match${matchedProposals[idx].candidates.length !== 1 ? 'es' : ''}`
                            }
                          </span>
                        )}
                      </div>
                    ))}
                  </div>

                  <div className="auto-actions">
                    <button
                      className="search-btn"
                      onClick={handleMatchAll}
                      disabled={matchingAll || enabledCount === 0}
                    >
                      {matchingAll
                        ? `Matching ${enabledCount} items...`
                        : `Match ${enabledCount} Items`
                      }
                    </button>

                    {hasMatches && (
                      <button
                        className="confirm-btn"
                        onClick={handleAutoConfirm}
                      >
                        Add {Object.keys(matchedProposals).length} Sub-Items
                      </button>
                    )}
                  </div>
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default CreateSubItemDialog
