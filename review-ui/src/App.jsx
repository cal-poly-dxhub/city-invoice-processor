import { useState, useEffect } from 'react'
import './App.css'
import LineItemCard from './components/LineItemCard'
import PDFViewer from './components/PDFViewer'
import FilterBar from './components/FilterBar'

function App() {
  const [jobId, setJobId] = useState('my_job')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [selectedBudgetItem, setSelectedBudgetItem] = useState('all')
  const [selectedMatchType, setSelectedMatchType] = useState('all')
  const [selectedItem, setSelectedItem] = useState(null)

  useEffect(() => {
    loadReconciliation()
  }, [jobId])

  const loadReconciliation = async () => {
    try {
      setLoading(true)
      setError(null)
      // Use absolute path from project root
      const response = await fetch(`/backend/jobs/${jobId}/artifacts/reconciliation.json`)
      if (!response.ok) throw new Error(`Failed to load reconciliation data: ${response.status} ${response.statusText}`)
      const json = await response.json()
      setData(json)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const getMatchType = (item) => {
    // Check if amount is explicitly $0 - no matching needed
    if (item.raw?.amount === 0) return 'zero-amount'

    // Check if no PDF was uploaded for this budget item (evaluated BEFORE match checks)
    // Backend sets doc_id to null when no PDF found for the budget item
    if (item.selected_evidence?.doc_id === null) return 'no-pdf'

    if (!item.candidates || item.candidates.length === 0) return 'none'

    const topCandidate = item.candidates[0]
    const score = topCandidate?.score || 0
    const topRationale = topCandidate?.rationale?.[0] || ''

    // Check for specific match types first
    if (topRationale.includes('Amount-based')) return 'amount'
    if (topRationale.includes('Cross-page')) return 'cross-page'
    if (item.selected_evidence.page_numbers.length === 0) return 'none'

    // Low confidence check BEFORE too-many check
    // This catches cases where system includes many/all pages due to poor matching
    if (score < 0.40 || topRationale.includes('neighbors') || topRationale.includes('Contiguous cluster')) {
      return 'low-confidence'
    }

    // Too-many only applies to high-confidence matches spread across many pages
    if (item.selected_evidence.page_numbers.length > 8) return 'too-many'

    return 'keyword'
  }

  const filteredItems = data?.line_items?.filter(item => {
    // Filter by budget item
    if (selectedBudgetItem !== 'all' && item.budget_item !== selectedBudgetItem) return false

    // Filter by match type
    if (selectedMatchType !== 'all') {
      const matchType = getMatchType(item)
      if (selectedMatchType !== matchType) return false
    }

    return true
  }) || []

  if (loading) {
    return (
      <div className="loading-screen">
        <div className="loading-content">
          <h1>Loading Reconciliation Data</h1>
          <div className="loading-spinner"></div>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className="error-screen">
        <div className="error-content">
          <h1>Error Loading Data</h1>
          <p>{error}</p>
          <button onClick={loadReconciliation}>Retry</button>
        </div>
      </div>
    )
  }

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-content">
          <div className="header-title">
            <h1>Invoice Reconciliation Review</h1>
            <p className="subtitle">Verification Interface for Evidence Matching</p>
          </div>
          <div className="header-meta">
            <div className="job-selector">
              <label>Job ID:</label>
              <input
                type="text"
                value={jobId}
                onChange={(e) => setJobId(e.target.value)}
                onBlur={loadReconciliation}
              />
            </div>
          </div>
        </div>
      </header>

      <div className="app-body">
        <aside className="sidebar">
          <FilterBar
            budgetItems={data?.documents?.map(d => d.budget_item) || []}
            selectedBudgetItem={selectedBudgetItem}
            setSelectedBudgetItem={setSelectedBudgetItem}
            selectedMatchType={selectedMatchType}
            setSelectedMatchType={setSelectedMatchType}
          />

          <div className="items-list">
            <div className="items-header">
              <h3>Line Items</h3>
              <span className="item-count">{filteredItems.length}</span>
            </div>

            <div className="items-scroll">
              {filteredItems.map((item) => (
                <LineItemCard
                  key={item.row_id}
                  item={item}
                  matchType={getMatchType(item)}
                  isSelected={selectedItem?.row_id === item.row_id}
                  onClick={() => setSelectedItem(item)}
                />
              ))}
            </div>
          </div>
        </aside>

        <main className="main-content">
          {selectedItem ? (
            <PDFViewer
              item={selectedItem}
              documents={data.documents}
              matchType={getMatchType(selectedItem)}
            />
          ) : (
            <div className="empty-state">
              <div className="empty-state-content">
                <h2>Select a Line Item</h2>
                <p>Choose an item from the list to view its evidence pages and verify the match quality</p>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  )
}

export default App
