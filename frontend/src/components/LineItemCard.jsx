import './LineItemCard.css'

function LineItemCard({
  item,
  matchType,
  isSelected,
  isCompleted,
  onClick,
  subItems = [],
  selectedSubItemId = null,
  onSubItemClick,
  onRemoveSubItem,
  completionStatus,          // NEW: { payment: bool, invoice: bool }
  subItemCompletionStatus,   // NEW: full completion map for sub-item checkboxes
  onToggleCompletion,        // NEW: (rowId, field) => void
}) {
  const pageCount = item.selected_evidence?.page_numbers?.length || 0
  const topCandidate = item.candidates?.[0]
  const score = topCandidate?.score || 0

  const getMatchLabel = (mt) => {
    switch(mt || matchType) {
      case 'amount': return 'Amount Match'
      case 'cross-page': return 'Cross-Page'
      case 'keyword': return 'Keyword Match'
      case 'low-confidence': return 'Low Confidence'
      case 'too-many': return 'Too Many Pages'
      case 'zero-amount': return '$0 - No Match Needed'
      case 'no-pdf': return 'No PDF Uploaded'
      case 'none': return 'No Match'
      default: return 'Unknown'
    }
  }

  const getMatchClass = (mt, sc) => {
    const m = mt || matchType
    const s = sc ?? score
    if (m === 'amount' || m === 'cross-page') {
      if (s >= 0.85) return 'excellent'
      if (s >= 0.70) return 'good'
      return 'partial'
    }
    if (m === 'low-confidence') return 'poor'
    if (m === 'too-many') return 'poor'
    if (m === 'zero-amount') return 'zero'
    if (m === 'no-pdf') return 'no-pdf'
    if (m === 'none') return 'none'
    return 'partial'
  }

  const getSubItemMatchType = (subItem) => {
    if (!subItem.candidates || subItem.candidates.length === 0) return 'none'
    const top = subItem.candidates[0]
    const rationale = top?.rationale?.[0] || ''
    if (rationale.includes('Amount-based')) return 'amount'
    if (rationale.includes('Cross-page')) return 'cross-page'
    if (top.score < 0.4) return 'low-confidence'
    return 'keyword'
  }

  const hasSubItems = subItems.length > 0

  return (
    <div className={`line-item-card-wrapper ${hasSubItems ? 'has-sub-items' : ''}`}>
      <div
        className={`line-item-card ${isSelected && !selectedSubItemId ? 'selected' : ''} ${isCompleted ? 'completed' : ''}`}
        onClick={onClick}
      >
        <div className="card-header">
          <span className="row-index">
            #{item.row_index + 1}
            {isCompleted && <span className="completed-checkmark"> ✓</span>}
          </span>
          <span className={`match-badge ${getMatchClass()}`}>
            {getMatchLabel()}
          </span>
        </div>

        <div className="card-body">
          <div className="budget-item">{item.budget_item}</div>

          {item.raw?.amount && item.raw.amount > 0 && (
            <div className="amount">${parseFloat(item.raw.amount).toFixed(2)}</div>
          )}

          {(item.raw?.employee_first_name || item.raw?.employee_last_name) && (
            <div className="employee-name">
              {item.raw.employee_first_name} {item.raw.employee_last_name}
            </div>
          )}
        </div>

        <div className="card-footer">
          <div className="pages-info">
            <span className="pages-count">{pageCount} page{pageCount !== 1 ? 's' : ''}</span>
            {score > 0 && (
              <span className="score">Score: {score.toFixed(2)}</span>
            )}
          </div>
          <div className="completion-checkboxes">
            <label
              className={`completion-checkbox ${hasSubItems ? 'readonly' : ''}`}
              onClick={e => e.stopPropagation()}
            >
              <input
                type="checkbox"
                checked={completionStatus?.payment || false}
                onChange={() => !hasSubItems && onToggleCompletion?.(item.row_id, 'payment')}
                disabled={hasSubItems}
              />
              <span className="checkbox-label">Payment</span>
            </label>
            <label
              className={`completion-checkbox ${hasSubItems ? 'readonly' : ''}`}
              onClick={e => e.stopPropagation()}
            >
              <input
                type="checkbox"
                checked={completionStatus?.invoice || false}
                onChange={() => !hasSubItems && onToggleCompletion?.(item.row_id, 'invoice')}
                disabled={hasSubItems}
              />
              <span className="checkbox-label">Invoice</span>
            </label>
          </div>
          {hasSubItems && (
            <div className="sub-items-badge">
              {subItems.length} sub-item{subItems.length !== 1 ? 's' : ''}
            </div>
          )}
        </div>
      </div>

      {hasSubItems && (
        <div className="sub-items-list">
          {subItems.map((subItem, idx) => {
            const siMatchType = getSubItemMatchType(subItem)
            const siScore = subItem.candidates?.[0]?.score || 0
            const siSelected = selectedSubItemId === subItem.sub_item_id
            const siPageCount = subItem.selected_evidence?.page_numbers?.length || 0
            const suffix = String.fromCharCode(97 + idx) // a, b, c, ...

            return (
              <div
                key={subItem.sub_item_id}
                className={`sub-item-card ${siSelected ? 'selected' : ''}`}
                onClick={(e) => {
                  e.stopPropagation()
                  onSubItemClick?.(subItem)
                }}
              >
                <div className="sub-item-header">
                  <span className="sub-item-index">
                    #{item.row_index + 1}{suffix}
                  </span>
                  <span className={`match-badge small ${getMatchClass(siMatchType, siScore)}`}>
                    {getMatchLabel(siMatchType)}
                  </span>
                </div>
                <div className="sub-item-body">
                  <div className="sub-item-label">{subItem.label}</div>
                  {subItem.amounts && subItem.amounts.length > 1 ? (
                    <div className="sub-item-amount merged">
                      {subItem.amounts.map(a => `$${a.toFixed(2)}`).join(' / ')}
                    </div>
                  ) : subItem.amount != null ? (
                    <div className="sub-item-amount">${subItem.amount.toFixed(2)}</div>
                  ) : null}
                </div>
                <div className="sub-item-footer">
                  <div className="completion-checkboxes">
                    <label className="completion-checkbox" onClick={e => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={subItemCompletionStatus?.[subItem.sub_item_id]?.payment || false}
                        onChange={() => onToggleCompletion?.(subItem.sub_item_id, 'payment')}
                      />
                      <span className="checkbox-label">Pmt</span>
                    </label>
                    <label className="completion-checkbox" onClick={e => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={subItemCompletionStatus?.[subItem.sub_item_id]?.invoice || false}
                        onChange={() => onToggleCompletion?.(subItem.sub_item_id, 'invoice')}
                      />
                      <span className="checkbox-label">Inv</span>
                    </label>
                  </div>
                  <span className="pages-count">
                    {siPageCount} page{siPageCount !== 1 ? 's' : ''}
                  </span>
                  {siScore > 0 && (
                    <span className="score">Score: {siScore.toFixed(2)}</span>
                  )}
                  <button
                    className="remove-sub-item-btn"
                    onClick={(e) => {
                      e.stopPropagation()
                      onRemoveSubItem?.(subItem.sub_item_id)
                    }}
                    title="Remove sub-item"
                  >
                    ×
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default LineItemCard
