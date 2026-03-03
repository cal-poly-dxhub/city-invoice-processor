# Human-in-the-Loop Keyword Review for Sub-Item Matching

**Date:** 2026-03-03
**Status:** Approved

## Problem

When auto-extracting sub-items from GL summary pages, the system parses keywords from Bedrock-extracted context strings using heuristic logic (collapsing hyphens, removing stop words, filtering budget item words). These keywords drive candidate page filtering during matching. Currently, keywords are invisible to the user — they can't verify or correct extraction errors before matching runs, leading to avoidable bad matches.

## Solution

Add an optional keyword review step to the auto-extract dialog. After extraction returns proposals, each proposal row expands to show editable keyword tags. Users can add, remove, or edit keywords before triggering matching. This reduces reliance on heuristic parsing by letting humans confirm what the system extracted.

## Design Decisions

- **Approach:** Expandable detail rows (Approach A). Each proposal row has a collapsible section showing keyword tags + read-only table reference words.
- **Strings shown:** Parsed keyword tokens only (not raw LLM context strings). Editable as tag chips.
- **Mandatory vs optional:** Optional with nudge — keywords are visible (rows start expanded) but the user can match immediately without reviewing.
- **Table row texts:** Shown as read-only reference below the editable keywords. Users can manually copy relevant words into the keyword list.
- **Keyword/row_texts sync:** When the user edits keywords, both `keywords` and `row_texts` parameters update in sync. The backend already accepts both separately, but they should reflect the same user intent.

## Scope

**Frontend only** — no backend or Lambda changes needed.

### Files to modify

1. `frontend/src/components/CreateSubItemDialog.jsx` — State, handlers, and JSX for keyword editing
2. `frontend/src/components/CreateSubItemDialog.css` — Styles for keyword tags, detail section, expand/collapse

### No changes to

- `infra/lambda/match_sub_item/handler.py` — API contract unchanged
- `frontend/src/services/api.js` — API calls unchanged
- `backend/invoice_recon/matching.py` — Matching logic unchanged

## Data Flow

1. Auto-extract returns proposals with `keywords`, `row_texts`, `table_row_texts` (existing)
2. All proposal rows start expanded, showing keyword tags (new)
3. User can add/remove keyword tags (new)
4. Edits update both `proposal.keywords` and `proposal.row_texts` in sync (new)
5. User clicks "Match N Items" — `handleMatchAll` reads keywords from state (existing, no changes)
6. Match API receives updated keywords (existing, no changes)

## UI Layout

```
┌─────────────────────────────────────────────────────┐
│ [✓]  AT&T Wireless Phone Service    $42.50   ▾  2m │  ← existing + chevron
│                                                      │
│  Keywords: [att] [wireless] [phone] [service] [+]   │  ← editable tags
│  Table ref: att, wireless, february, invoice         │  ← read-only muted text
└─────────────────────────────────────────────────────┘
```

### Tag interaction

- Click `×` on a tag to remove it
- Click `[+]` / type in inline input, press Enter to add a new keyword
- Input auto-lowercases and trims
- No external tag library — controlled input + array state

### Proposals header additions

- "Expand All / Collapse All" toggle alongside existing "Select All / Deselect All"

## Edge Cases

- **Empty keywords:** Show empty tag area with just the add input
- **No table_row_texts:** "Table ref:" line not rendered
- **Edit after matching:** Editing keywords on a matched proposal clears its match results, signaling re-match needed
- **Manual mode:** No changes (already has free-text keyword input)
- **Collapse state:** Resets on dialog open (always starts expanded)

## New CSS Classes

| Class | Purpose |
|-------|---------|
| `.proposal-details` | Container for expanded keyword section |
| `.proposal-keywords` | Flex-wrap container for tag chips |
| `.keyword-tag` | Individual keyword pill with remove button |
| `.keyword-tag-remove` | The `×` button inside a tag |
| `.keyword-add-input` | Inline text input for adding keywords |
| `.proposal-table-ref` | Read-only table reference line |
| `.proposal-expand-toggle` | Chevron expand/collapse button |
| `.expand-collapse-btn` | Header-level expand/collapse all link |

## New State

| State | Type | Purpose |
|-------|------|---------|
| `expandedRows` | `Set<number>` | Tracks which proposal indices are expanded. Initialized to all indices after auto-extract. |

## New Handlers

| Handler | Purpose |
|---------|---------|
| `updateProposalKeywords(idx, newKeywords)` | Updates `proposal.keywords` and `proposal.row_texts` in sync. Clears match results for that proposal. |
| `toggleRowExpanded(idx)` | Toggles expand/collapse for one row |
| `expandAll()` / `collapseAll()` | Batch expand/collapse |
