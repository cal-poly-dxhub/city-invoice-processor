import { useState, useEffect } from 'react'
import './SearchBar.css'

function SearchBar({ query, onQueryChange, resultSummary }) {
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
          placeholder="Search"
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
      {resultSummary && (
        <div className="search-results-summary">
          {resultSummary}
        </div>
      )}
    </div>
  )
}

export default SearchBar
