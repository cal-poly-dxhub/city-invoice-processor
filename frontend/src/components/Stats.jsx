import './Stats.css'

function Stats({ data, filteredItems }) {
  if (!data) return null

  const totalItems = data.line_items?.length || 0
  const zeroAmountItems = data.line_items?.filter(
    item => item.raw?.amount === 0
  ).length || 0
  const noPdfItems = data.line_items?.filter(
    item => item.raw?.amount !== 0 && item.selected_evidence?.doc_id === null
  ).length || 0
  const itemsNeedingMatch = totalItems - zeroAmountItems - noPdfItems
  const itemsWithMatches = data.line_items?.filter(
    item => item.raw?.amount !== 0 && item.selected_evidence?.doc_id !== null && item.selected_evidence?.page_numbers?.length > 0
  ).length || 0
  const amountBasedMatches = data.line_items?.filter(
    item => item.candidates?.[0]?.rationale?.[0]?.includes('Amount-based')
  ).length || 0
  const lowConfidenceMatches = data.line_items?.filter(
    item => {
      const candidate = item.candidates?.[0]
      const score = candidate?.score || 0
      const rationale = candidate?.rationale?.[0] || ''
      return score < 0.40 || rationale.includes('neighbors') || rationale.includes('Contiguous cluster')
    }
  ).length || 0

  const matchRate = itemsNeedingMatch > 0 ? ((itemsWithMatches / itemsNeedingMatch) * 100).toFixed(1) : 0

  return (
    <div className="stats">
      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-value">{totalItems}</div>
          <div className="stat-label">Total Items</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{zeroAmountItems}</div>
          <div className="stat-label">$0 Items</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{noPdfItems}</div>
          <div className="stat-label">No PDF Uploaded</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{itemsWithMatches}/{itemsNeedingMatch}</div>
          <div className="stat-label">Matched</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{amountBasedMatches}</div>
          <div className="stat-label">Amount-Based</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{lowConfidenceMatches}</div>
          <div className="stat-label">Low Confidence</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{matchRate}%</div>
          <div className="stat-label">Match Rate</div>
        </div>
      </div>
    </div>
  )
}

export default Stats
