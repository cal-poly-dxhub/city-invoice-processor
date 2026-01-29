import './FilterBar.css'

function FilterBar({
  budgetItems,
  selectedBudgetItem,
  setSelectedBudgetItem,
  selectedMatchType,
  setSelectedMatchType
}) {
  return (
    <div className="filter-bar">
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
        <label className="filter-label">Match Type</label>
        <select
          value={selectedMatchType}
          onChange={(e) => setSelectedMatchType(e.target.value)}
          className="filter-select"
        >
          <option value="all">All Matches</option>
          <option value="amount">Amount-Based</option>
          <option value="cross-page">Cross-Page</option>
          <option value="keyword">Keyword</option>
          <option value="low-confidence">Low Confidence</option>
          <option value="too-many">Too Many Pages</option>
          <option value="zero-amount">$0 - No Match Needed</option>
          <option value="no-pdf">No PDF Uploaded</option>
          <option value="none">No Match</option>
        </select>
      </div>
    </div>
  )
}

export default FilterBar
