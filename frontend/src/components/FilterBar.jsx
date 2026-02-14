import './FilterBar.css'

function FilterBar({
  budgetItems,
  selectedBudgetItem,
  setSelectedBudgetItem,
  selectedMatchType,
  setSelectedMatchType,
  verificationMode,
  setVerificationMode,
  minConfidenceScore,
  setMinConfidenceScore
}) {
  return (
    <div className="filter-bar">
      <div className="filter-section">
        <label className="filter-label">Verification Status</label>
        <select
          value={verificationMode}
          onChange={(e) => setVerificationMode(e.target.value)}
          className="filter-select"
        >
          <option value="needs-verification">Needs Verification</option>
          <option value="all">All Items</option>
        </select>
      </div>

      <div className="filter-section">
        <label className="filter-label">Budget Item</label>
        <select
          value={selectedBudgetItem}
          onChange={(e) => setSelectedBudgetItem(e.target.value)}
          className="filter-select"
        >
          <option value="all">All Items</option>
          {budgetItems.map((item) => (
            <option key={item} value={item}>{item}</option>
          ))}
        </select>
      </div>

      <div className="filter-section">
        <label className="filter-label">Filter By</label>
        <select
          value={selectedMatchType}
          onChange={(e) => setSelectedMatchType(e.target.value)}
          className="filter-select"
        >
          <option value="all">All Matches</option>
          <option value="low-confidence">Low Confidence</option>
          <option value="no-pdf">No PDF Uploaded</option>
          <option value="none">No Match</option>
        </select>
      </div>

      <div className="filter-section">
        <label className="filter-label">
          Min Confidence: {Math.round(minConfidenceScore * 100)}%
        </label>
        <input
          type="range"
          min="0"
          max="100"
          value={Math.round(minConfidenceScore * 100)}
          onChange={(e) => setMinConfidenceScore(parseInt(e.target.value) / 100)}
          className="confidence-slider"
        />
        <div className="slider-labels">
          <span>0%</span>
          <span>50%</span>
          <span>100%</span>
        </div>
      </div>
    </div>
  )
}

export default FilterBar
