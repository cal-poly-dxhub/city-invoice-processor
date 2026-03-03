# HITL Keyword Review Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add editable keyword tags to the auto-extract sub-item dialog so users can review and edit parsed keywords before matching runs.

**Architecture:** Frontend-only change. Add expandable detail rows to `CreateSubItemDialog.jsx` showing keyword tags (editable) and table reference words (read-only). Extract keyword sync logic into a testable utility. No backend changes — the API contract already supports user-edited keywords.

**Tech Stack:** React (JSX), CSS, vitest for utility tests

**Design doc:** `docs/plans/2026-03-03-hitl-keyword-review-design.md`

---

### Task 1: Extract keyword sync utility + write tests

**Files:**
- Create: `frontend/src/utils/keywordSync.js`
- Create: `frontend/src/utils/keywordSync.test.js`

**Step 1: Write failing tests**

```javascript
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
```

**Step 2: Run tests to verify they fail**

Run: `cd frontend && npx vitest run src/utils/keywordSync.test.js`
Expected: FAIL — module not found

**Step 3: Write minimal implementation**

```javascript
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
```

**Step 4: Run tests to verify they pass**

Run: `cd frontend && npx vitest run src/utils/keywordSync.test.js`
Expected: All 6 tests PASS

**Step 5: Commit**

```bash
git add frontend/src/utils/keywordSync.js frontend/src/utils/keywordSync.test.js
git commit -m "feat: add keyword sync utilities for HITL sub-item review"
```

---

### Task 2: Add CSS styles for keyword tags and expand/collapse

**Files:**
- Modify: `frontend/src/components/CreateSubItemDialog.css` (append after line 338)

**Step 1: Append new styles**

Add at the end of `CreateSubItemDialog.css`:

```css
/* Expand/collapse toggle */
.proposal-expand-toggle {
  flex-shrink: 0;
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  padding: 0.125rem 0.25rem;
  line-height: 1;
  transition: color 0.15s ease;
}

.proposal-expand-toggle:hover {
  color: var(--color-text);
}

/* Expanded detail section */
.proposal-details {
  padding: 0.25rem 0 0.25rem 1.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

/* Keyword tags container */
.proposal-keywords {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.25rem;
}

.proposal-keywords-label {
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--color-text-secondary);
  font-weight: 600;
  margin-right: 0.25rem;
}

/* Individual keyword tag */
.keyword-tag {
  display: inline-flex;
  align-items: center;
  gap: 0.125rem;
  padding: 0.125rem 0.375rem;
  background: rgba(43, 76, 126, 0.08);
  border: 1px solid rgba(43, 76, 126, 0.2);
  border-radius: 3px;
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--color-accent);
  line-height: 1.4;
}

.keyword-tag-remove {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 0.625rem;
  color: var(--color-text-secondary);
  padding: 0;
  line-height: 1;
  margin-left: 0.125rem;
}

.keyword-tag-remove:hover {
  color: #dc2626;
}

/* Inline add input */
.keyword-add-input {
  border: 1px dashed var(--color-border);
  border-radius: 3px;
  padding: 0.125rem 0.375rem;
  font-family: var(--font-mono);
  font-size: 0.6875rem;
  color: var(--color-text);
  background: transparent;
  width: 80px;
  outline: none;
}

.keyword-add-input:focus {
  border-color: var(--color-accent);
  border-style: solid;
}

.keyword-add-input::placeholder {
  color: var(--color-text-secondary);
  opacity: 0.6;
}

/* Read-only table reference */
.proposal-table-ref {
  font-family: var(--font-mono);
  font-size: 0.625rem;
  color: var(--color-text-secondary);
  opacity: 0.7;
}

.proposal-table-ref-label {
  font-weight: 600;
}

/* Expand/collapse all button in header */
.expand-collapse-btn {
  background: none;
  border: none;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--color-accent);
  cursor: pointer;
  padding: 0.125rem 0.25rem;
  border-radius: 3px;
  font-weight: 500;
}

.expand-collapse-btn:hover {
  text-decoration: underline;
}

/* Wrap proposal row content for expand/collapse layout */
.proposal-row-inner {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
}
```

**Step 2: Verify no syntax errors**

Run: `cd frontend && npx vite build 2>&1 | tail -5`
Expected: Build succeeds (CSS is valid)

**Step 3: Commit**

```bash
git add frontend/src/components/CreateSubItemDialog.css
git commit -m "feat: add CSS styles for keyword tags and expand/collapse"
```

---

### Task 3: Add state, handlers, and expand/collapse UI to CreateSubItemDialog

**Files:**
- Modify: `frontend/src/components/CreateSubItemDialog.jsx`

This task adds the core state management and restructures the proposal row layout. Changes described relative to the current file.

**Step 1: Add import and new state**

At top of file (line 2), add the import:

```javascript
import { addKeyword, removeKeyword } from '../utils/keywordSync'
```

Inside the component, after the auto mode state block (after line 38), add:

```javascript
  // Keyword review state
  const [expandedRows, setExpandedRows] = useState(new Set())
```

**Step 2: Initialize expandedRows when proposals load**

In the `handleAutoExtract` function, after `setProposals(items)` (line 135), add:

```javascript
      setExpandedRows(new Set(items.map((_, i) => i)))
```

This ensures all rows start expanded after auto-extract (the "nudge").

Also in the `useEffect` reset block (around line 49), add `setExpandedRows(new Set())` alongside the other resets.

**Step 3: Add handler functions**

After `updateProposalAmount` (line 174), add these handlers:

```javascript
  const updateProposalKeywords = (idx, newKeywords) => {
    setProposals(prev =>
      prev.map((p, i) => i === idx ? { ...p, keywords: newKeywords, row_texts: [...newKeywords] } : p)
    )
    // Clear stale match results for this proposal
    setMatchedProposals(prev => {
      const next = { ...prev }
      delete next[idx]
      return next
    })
  }

  const toggleRowExpanded = (idx) => {
    setExpandedRows(prev => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  const allExpanded = proposals.length > 0 && expandedRows.size === proposals.length
```

**Step 4: Add expand/collapse all toggle to proposals header**

Modify the `.proposals-header` section (lines 371-381). Add an expand/collapse button next to the existing batch-select button:

Replace the `proposals-header` div content with:

```jsx
                  <div className="proposals-header">
                    <span>
                      {enabledCount} of {proposals.length} items selected
                    </span>
                    <div style={{ display: 'flex', gap: '0.5rem' }}>
                      <button
                        className="expand-collapse-btn"
                        onClick={() => setExpandedRows(
                          allExpanded ? new Set() : new Set(proposals.map((_, i) => i))
                        )}
                      >
                        {allExpanded ? 'Collapse All' : 'Expand All'}
                      </button>
                      <button
                        className="batch-select-btn"
                        onClick={() => setAllEnabled(enabledCount < proposals.length)}
                      >
                        {enabledCount === proposals.length ? 'Deselect All' : 'Select All'}
                      </button>
                    </div>
                  </div>
```

**Step 5: Restructure proposal rows with expand/collapse and keyword detail section**

Replace the `.proposals-list` map (lines 383-416) with:

```jsx
                  <div className="proposals-list">
                    {proposals.map((p, idx) => (
                      <div
                        key={idx}
                        className={`proposal-row ${!p.enabled ? 'disabled' : ''}`}
                      >
                        <div className="proposal-row-inner">
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
                          <button
                            className="proposal-expand-toggle"
                            onClick={() => toggleRowExpanded(idx)}
                            title={expandedRows.has(idx) ? 'Collapse keywords' : 'Expand keywords'}
                          >
                            {expandedRows.has(idx) ? '\u25BE' : '\u25B8'}
                          </button>
                          {matchedProposals[idx] && (
                            <span className="proposal-match-status">
                              {matchedProposals[idx].error
                                ? 'Error'
                                : `${matchedProposals[idx].candidates.length} match${matchedProposals[idx].candidates.length !== 1 ? 'es' : ''}`
                              }
                            </span>
                          )}
                        </div>

                        {expandedRows.has(idx) && (
                          <div className="proposal-details">
                            <div className="proposal-keywords">
                              <span className="proposal-keywords-label">Keywords:</span>
                              {(p.keywords || []).map((kw, ki) => (
                                <span key={ki} className="keyword-tag">
                                  {kw}
                                  <button
                                    className="keyword-tag-remove"
                                    onClick={() => {
                                      const { keywords } = removeKeyword(p, kw)
                                      updateProposalKeywords(idx, keywords)
                                    }}
                                    title={`Remove "${kw}"`}
                                  >
                                    ×
                                  </button>
                                </span>
                              ))}
                              <input
                                type="text"
                                className="keyword-add-input"
                                placeholder="+ add"
                                onKeyDown={e => {
                                  if (e.key === 'Enter' && e.target.value.trim()) {
                                    const { keywords } = addKeyword(p, e.target.value)
                                    updateProposalKeywords(idx, keywords)
                                    e.target.value = ''
                                  }
                                }}
                              />
                            </div>
                            {p.table_row_texts && p.table_row_texts.length > 0 && (
                              <div className="proposal-table-ref">
                                <span className="proposal-table-ref-label">Table ref: </span>
                                {p.table_row_texts.join(', ')}
                              </div>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
```

**Step 6: Verify the build compiles**

Run: `cd frontend && npx vite build 2>&1 | tail -5`
Expected: Build succeeds with no errors

**Step 7: Commit**

```bash
git add frontend/src/components/CreateSubItemDialog.jsx
git commit -m "feat: add keyword review UI with expand/collapse to sub-item dialog"
```

---

### Task 4: Manual verification

**Step 1: Start dev server and verify the auto-extract dialog**

Run: `cd frontend && npm run dev`

Open the app, navigate to a job with sub-items, and open the auto-extract dialog on a GL summary page. Verify:

1. After auto-extract, all proposal rows show expanded keyword tags
2. Keywords display as pill/chip tags with `×` remove buttons
3. Clicking `×` removes the keyword and clears any match results for that row
4. Typing in the `[+ add]` input and pressing Enter adds a new keyword (lowercased)
5. Duplicate keywords are rejected (no visual change)
6. Table ref words appear as muted text below keywords (only when present)
7. Chevron toggles expand/collapse per row
8. "Expand All" / "Collapse All" button works in the header
9. Clicking "Match N Items" sends the user-edited keywords to the API
10. Editing keywords after matching clears the match status for that row

**Step 2: Verify manual mode is unchanged**

Switch to the Manual tab. Verify the existing comma-separated keyword input still works identically.

**Step 3: Run existing tests**

Run: `cd frontend && npm test`
Expected: All existing tests pass (no regressions)

**Step 4: Run new keyword sync tests**

Run: `cd frontend && npx vitest run src/utils/keywordSync.test.js`
Expected: All 6 tests pass

**Step 5: Commit (if any fixups needed)**

```bash
git add -A
git commit -m "fix: address issues found during manual verification"
```

Only commit if fixes were needed. Skip if everything passed cleanly.
