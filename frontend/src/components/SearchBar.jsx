import { useState, useEffect } from 'react'
import './SearchBar.css'

function SearchBar({ query, onQueryChange, resultSummary, searchMode, onSearchModeChange, hasMultipleTerms }) {
  const [localQuery, setLocalQuery] = useState(query)

  // Debounce the search query (300ms)
  useEffect(() => {
    const timer = setTimeout(() => {
      onQueryChange(localQuery)
    }, 300)

    return () => clearTimeout(timer)
  }, [localQuery, onQueryChange])

  const handleClear = () => {
    setLocalQuery('')
    onQueryChange('')
  }

  return (
    <div className="search-bar">
      <div className="search-input-container">
        <input
          type="text"
          className="search-input"
          placeholder="Search (comma-separated)"
          value={localQuery}
          onChange={(e) => setLocalQuery(e.target.value)}
        />
        {localQuery && (
          <button
            className="search-clear-button"
            onClick={handleClear}
            title="Clear search"
          >
            ×
          </button>
        )}
      </div>
      {hasMultipleTerms && (
        <div className="search-mode-toggle" role="group" aria-label="Search mode">
          <button
            className={`search-mode-btn ${searchMode === 'any' ? 'active' : ''}`}
            onClick={() => onSearchModeChange('any')}
            aria-pressed={searchMode === 'any'}
          >
            Any
          </button>
          <button
            className={`search-mode-btn ${searchMode === 'all' ? 'active' : ''}`}
            onClick={() => onSearchModeChange('all')}
            aria-pressed={searchMode === 'all'}
          >
            All
          </button>
        </div>
      )}
      {resultSummary && (
        <div className="search-results-summary">
          {resultSummary}
        </div>
      )}
    </div>
  )
}

export default SearchBar
