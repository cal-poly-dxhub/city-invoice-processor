import './LineItemCard.css'

function LineItemCard({ item, matchType, isSelected, onClick }) {
  const pageCount = item.selected_evidence?.page_numbers?.length || 0
  const topCandidate = item.candidates?.[0]
  const score = topCandidate?.score || 0

  const getMatchLabel = () => {
    switch(matchType) {
      case 'amount': return 'Amount Match'
      case 'cross-page': return 'Cross-Page'
      case 'keyword': return 'Keyword Match'
      case 'too-many': return 'Too Many Pages'
      case 'zero-amount': return '$0 - No Match Needed'
      case 'none': return 'No Match'
      default: return 'Unknown'
    }
  }

  const getMatchClass = () => {
    if (matchType === 'amount' || matchType === 'cross-page') {
      if (score >= 0.85) return 'excellent'
      if (score >= 0.70) return 'good'
      return 'partial'
    }
    if (matchType === 'too-many') return 'poor'
    if (matchType === 'zero-amount') return 'zero'
    if (matchType === 'none') return 'none'
    return 'partial'
  }

  return (
    <div
      className={`line-item-card ${isSelected ? 'selected' : ''}`}
      onClick={onClick}
    >
      <div className="card-header">
        <span className="row-index">#{item.row_index}</span>
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

        {item.raw?.explanation && (
          <div className="explanation">{item.raw.explanation}</div>
        )}
      </div>

      <div className="card-footer">
        <div className="pages-info">
          <span className="pages-count">{pageCount} page{pageCount !== 1 ? 's' : ''}</span>
          {score > 0 && (
            <span className="score">Score: {score.toFixed(2)}</span>
          )}
        </div>
      </div>
    </div>
  )
}

export default LineItemCard
