import { useState, useEffect, useRef } from 'react'
import * as pdfjsLib from 'pdfjs-dist'
import './PDFViewer.css'

// Set up PDF.js worker
pdfjsLib.GlobalWorkerOptions.workerSrc = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjsLib.version}/pdf.worker.min.js`

function PDFViewer({ item, documents, matchType, onMarkGroupDone, isCompleted }) {
  const [pdfDoc, setPdfDoc] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [highlights, setHighlights] = useState({})
  const [selectedCandidateIdx, setSelectedCandidateIdx] = useState(0) // Track which candidate to display
  const [currentPageIdx, setCurrentPageIdx] = useState(0) // Track which page in the group is currently displayed
  const [viewMode, setViewMode] = useState('group') // 'group' or 'all' - determines which pages to show
  const [allPagesCurrentPage, setAllPagesCurrentPage] = useState(1) // Track current page when viewing all pages (1-indexed)
  const [pageRotations, setPageRotations] = useState({}) // Track user-applied rotation per page (0, 90, 180, 270)
  const [viewportDimensions, setViewportDimensions] = useState({}) // Store viewport dimensions for each page
  const [userEditedCandidates, setUserEditedCandidates] = useState({}) // Track user modifications: { [candidateKey]: { ...candidate, page_numbers: [...], edited: true } }
  const pageInherentRotations = useRef({}) // Track PDF's inherent rotation
  const canvasRefs = useRef({})
  const containerRefs = useRef({})

  // Helper to get current candidate (merged with user edits)
  const getCurrentCandidate = () => {
    const candidate = item.candidates?.[selectedCandidateIdx]
    if (!candidate) return null

    const candidateKey = `${item.row_id}_${selectedCandidateIdx}`
    return userEditedCandidates[candidateKey] || candidate
  }

  // Get doc and pages from the selected candidate (not selected_evidence)
  const selectedCandidate = getCurrentCandidate()
  const doc = selectedCandidate
    ? documents.find(d => d.doc_id === selectedCandidate.doc_id)
    : documents.find(d => d.doc_id === item.selected_evidence?.doc_id)
  const pageNumbers = selectedCandidate
    ? selectedCandidate.page_numbers || []
    : item.selected_evidence?.page_numbers || []

  useEffect(() => {
    if (!doc) return

    // Reset selected candidate and page index when viewing a new item
    if (item.row_id) {
      setSelectedCandidateIdx(0)
      setCurrentPageIdx(0)
      setViewMode('group')
      setAllPagesCurrentPage(1)
    }
    loadPDF()
  }, [item.row_id])

  useEffect(() => {
    if (!doc) return
    loadPDF()
  }, [doc])

  useEffect(() => {
    if (pdfDoc) {
      renderPages()
    }
  }, [pdfDoc, pageNumbers, pageRotations, currentPageIdx, viewMode, allPagesCurrentPage])

  // Re-render highlights when selected candidate changes
  useEffect(() => {
    if (pdfDoc && pageNumbers.length > 0) {
      const backendHighlights = getHighlightsFromCandidates()
      setHighlights(backendHighlights)
    }
    // Reset to first page when candidate changes
    setCurrentPageIdx(0)
  }, [selectedCandidateIdx])

  const loadPDF = async () => {
    try {
      setLoading(true)
      setError(null)

      // Construct PDF path - budget items use specific naming conventions
      // Map budget items to their actual PDF filenames
      const filenameMap = {
        'Salary': 'Salary.pdf',
        'Utilities': 'Utilities.pdf',
        'Supplies': 'Supplies.pdf',
        'Telecommunications': 'Telecommunications.pdf',
        'Space Rental/Occupancy Costs': 'Space_Rental_Occupancy_Costs.pdf',
      }

      const filename = filenameMap[doc.budget_item]
      if (!filename) {
        throw new Error(`No PDF found for budget item: ${doc.budget_item}`)
      }

      const pdfPath = `/test-files/pdf/${filename}`

      const loadingTask = pdfjsLib.getDocument(pdfPath)
      const pdf = await loadingTask.promise
      setPdfDoc(pdf)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const renderPages = async () => {
    if (!pdfDoc) return

    // Determine which page to render based on view mode
    let pageNum
    if (viewMode === 'group') {
      if (pageNumbers.length === 0) return
      pageNum = pageNumbers[currentPageIdx]
    } else {
      pageNum = allPagesCurrentPage
    }

    const canvas = canvasRefs.current[pageNum]
    if (!canvas) return

    try {
      const page = await pdfDoc.getPage(pageNum)

      // Store the page's inherent rotation in ref (immediate, no async state update)
      pageInherentRotations.current[pageNum] = page.rotate || 0

      // Apply user-selected rotation (default to 0 if not set)
      // This renders the PDF at the user's preferred orientation
      const userRotation = pageRotations[pageNum] || 0
      const viewport = page.getViewport({ scale: 1.5, rotation: userRotation })

      // Store viewport dimensions for this page (for highlight calculations)
      setViewportDimensions({
        ...viewportDimensions,
        [pageNum]: {
          width: viewport.width,
          height: viewport.height
        }
      })

      const context = canvas.getContext('2d')

      // Clear the canvas before re-rendering
      context.clearRect(0, 0, canvas.width, canvas.height)

      // Update canvas dimensions for the rotated viewport
      canvas.width = viewport.width
      canvas.height = viewport.height

      // Render PDF content
      await page.render({
        canvasContext: context,
        viewport: viewport
      }).promise

      // Get normalized (0-1) highlights from backend
      // Store them in normalized form so they scale dynamically
      const backendHighlights = getHighlightsFromCandidates()
      setHighlights(backendHighlights)
    } catch (err) {
      console.error(`Error rendering page ${pageNum}:`, err)
    }
  }

  const getHighlightsFromCandidates = () => {
    // Get highlights from the ORIGINAL candidate only
    // User-added pages won't have highlights (they weren't matched)
    if (!item.candidates || item.candidates.length === 0) {
      console.log('PDFViewer: No candidates found')
      return {}
    }

    // Use selected candidate, or fallback to first if out of bounds
    const candidateIdx = selectedCandidateIdx < item.candidates.length ? selectedCandidateIdx : 0
    const originalCandidate = item.candidates[candidateIdx]
    const candidateHighlights = originalCandidate.highlights || {}

    console.log(`PDFViewer: Candidate ${candidateIdx + 1} highlights from backend (normalized):`, candidateHighlights)

    // Return highlights in normalized (0-1) form
    // They'll be converted to pixels at render time so they scale dynamically
    return candidateHighlights
  }

  const getHighlightSummary = () => {
    // Return a summary of what's being highlighted
    const summary = []

    if (item.raw?.amount && item.raw.amount > 0) {
      summary.push(`$${parseFloat(item.raw.amount).toFixed(2)}`)
    }

    if (item.raw?.employee_first_name || item.raw?.employee_last_name) {
      const name = `${item.raw.employee_first_name || ''} ${item.raw.employee_last_name || ''}`.trim()
      if (name) summary.push(name)
    }

    return summary
  }

  const getMatchClass = () => {
    if (matchType === 'amount' || matchType === 'cross-page') return 'excellent'
    if (matchType === 'low-confidence') return 'poor'
    if (matchType === 'too-many') return 'poor'
    if (matchType === 'none') return 'none'
    return 'partial'
  }

  const transformHighlight = (rect, rotation) => {
    // Transform normalized (0-1) highlight coordinates based on page rotation
    // Rotation is in degrees: 0, 90, 180, 270
    switch (rotation) {
      case 90:
        // 90° clockwise: (x,y) -> (1-y-h, x), dimensions swap
        return {
          left: 1 - rect.top - rect.height,
          top: rect.left,
          width: rect.height,
          height: rect.width
        }
      case 180:
        // 180°: (x,y) -> (1-x-w, 1-y-h)
        return {
          left: 1 - rect.left - rect.width,
          top: 1 - rect.top - rect.height,
          width: rect.width,
          height: rect.height
        }
      case 270:
        // 270° clockwise (90° counter-clockwise): (x,y) -> (y, 1-x-w), dimensions swap
        return {
          left: rect.top,
          top: 1 - rect.left - rect.width,
          width: rect.height,
          height: rect.width
        }
      default:
        // 0° or invalid: no transformation
        return rect
    }
  }

  const rotatePage = (pageNum, direction) => {
    // Rotate page by 90° in specified direction ('cw' or 'ccw')
    setPageRotations(prev => {
      const currentRotation = prev[pageNum] || 0
      const delta = direction === 'cw' ? 90 : -90
      const newRotation = (currentRotation + delta + 360) % 360
      console.log(`PDFViewer: Rotating page ${pageNum} from ${currentRotation}° to ${newRotation}°`)
      return { ...prev, [pageNum]: newRotation }
    })
  }

  const goToPreviousPage = () => {
    if (viewMode === 'group') {
      setCurrentPageIdx(prev => Math.max(0, prev - 1))
    } else {
      setAllPagesCurrentPage(prev => Math.max(1, prev - 1))
    }
  }

  const goToNextPage = () => {
    if (viewMode === 'group') {
      setCurrentPageIdx(prev => Math.min(pageNumbers.length - 1, prev + 1))
    } else {
      const totalPages = doc?.page_count || 1
      setAllPagesCurrentPage(prev => Math.min(totalPages, prev + 1))
    }
  }

  const toggleViewMode = () => {
    if (viewMode === 'group') {
      // Switching to all-pages view: set current page to the group's current page
      const currentGroupPage = pageNumbers[currentPageIdx]
      setAllPagesCurrentPage(currentGroupPage || 1)
      setViewMode('all')
    } else {
      // Switching to group view: find the closest group page
      const closestIdx = pageNumbers.findIndex(p => p >= allPagesCurrentPage)
      setCurrentPageIdx(closestIdx >= 0 ? closestIdx : 0)
      setViewMode('group')
    }
  }

  // Check if a page is in the current candidate group
  const isPageInGroup = (pageNum) => {
    const candidate = getCurrentCandidate()
    return candidate?.page_numbers.includes(pageNum)
  }

  // Add a page to the current candidate group
  const addPageToGroup = (pageNum) => {
    const candidate = getCurrentCandidate()
    if (!candidate) return

    const candidateKey = `${item.row_id}_${selectedCandidateIdx}`
    const newPageNumbers = [...candidate.page_numbers, pageNum].sort((a, b) => a - b)

    setUserEditedCandidates({
      ...userEditedCandidates,
      [candidateKey]: {
        ...candidate,
        page_numbers: newPageNumbers,
        edited: true
      }
    })
  }

  // Remove a page from the current candidate group
  const removePageFromGroup = (pageNum) => {
    const candidate = getCurrentCandidate()
    if (!candidate) return

    // Prevent removing last page
    if (candidate.page_numbers.length <= 1) {
      return
    }

    const candidateKey = `${item.row_id}_${selectedCandidateIdx}`
    const newPageNumbers = candidate.page_numbers.filter(p => p !== pageNum)

    setUserEditedCandidates({
      ...userEditedCandidates,
      [candidateKey]: {
        ...candidate,
        page_numbers: newPageNumbers,
        edited: true
      }
    })
  }

  // Reset candidate back to original
  const resetCandidate = () => {
    const candidateKey = `${item.row_id}_${selectedCandidateIdx}`
    const newEdits = { ...userEditedCandidates }
    delete newEdits[candidateKey]
    setUserEditedCandidates(newEdits)
  }


  return (
    <div className="pdf-viewer">
      <div className="viewer-header">
        <div className="item-summary">
          <h2 className="item-title">Row #{item.row_index}: {item.budget_item}</h2>
          {item.raw?.amount && item.raw.amount > 0 && (
            <div className="item-amount">${parseFloat(item.raw.amount).toFixed(2)}</div>
          )}
        </div>

        {(item.raw?.employee_first_name || item.raw?.employee_last_name) && (
          <div className="item-employee">
            <strong>Employee:</strong> {item.raw.employee_first_name} {item.raw.employee_last_name}
          </div>
        )}

        {item.raw?.explanation && (
          <div className="item-explanation">
            <strong>Explanation:</strong> {item.raw.explanation}
          </div>
        )}
      </div>

      {item.candidates && item.candidates.length > 0 && (
        <div className="candidates-section">
          <h3 className="section-title">Match Evidence</h3>
          {item.candidates.map((candidate, idx) => {
            const candidateKey = `${item.row_id}_${idx}`
            const displayCandidate = userEditedCandidates[candidateKey] || candidate
            const isEdited = !!userEditedCandidates[candidateKey]

            return (
              <div
                key={idx}
                className={`candidate-card ${idx === selectedCandidateIdx ? 'selected-candidate' : ''} ${idx === 0 ? 'top-candidate' : ''} ${isEdited ? 'edited-candidate' : ''}`}
                onClick={() => setSelectedCandidateIdx(idx)}
                style={{ cursor: 'pointer' }}
              >
                <div className="candidate-header">
                  <span className="candidate-rank">
                    Candidate {idx + 1}
                    {idx === selectedCandidateIdx && ' (Viewing)'}
                    {isEdited && ' ✏️'}
                  </span>
                  <span className={`candidate-score ${getMatchClass()}`}>
                    Score: {candidate.score?.toFixed(2) || 'N/A'}
                  </span>
                </div>
                <div className="candidate-pages">
                  Pages: {displayCandidate.page_numbers.join(', ')}
                </div>
                <div className="candidate-rationale">
                  {candidate.rationale?.map((line, i) => (
                    <div key={i} className="rationale-line">{line}</div>
                  ))}
                </div>
                {isEdited && idx === selectedCandidateIdx && (
                  <button
                    className="reset-candidate-btn"
                    onClick={(e) => {
                      e.stopPropagation()
                      resetCandidate()
                    }}
                    title="Reset to original candidate"
                  >
                    Reset to Original
                  </button>
                )}
              </div>
            )
          })}
        </div>
      )}

      {pageNumbers.length === 0 && viewMode === 'group' ? (
        <div className="no-pages">
          <h3>No Evidence Pages</h3>
          {item.raw?.amount === 0 ? (
            <p>This line item has a $0 amount. No matching was performed since there is no transaction to verify.</p>
          ) : (
            <p>This line item has no matched evidence pages.</p>
          )}
          {doc?.page_count > 0 && (
            <button onClick={toggleViewMode} className="view-all-btn">
              View All Pages in Document
            </button>
          )}
        </div>
      ) : (
        <div className="pages-section">
          <div className="section-header">
            <h3 className="section-title">Evidence Pages</h3>
            {getHighlightSummary().length > 0 && (
              <div className="highlight-indicator">
                <span className="highlight-badge"></span>
                Highlighted: {getHighlightSummary().join(', ')}
              </div>
            )}
          </div>

          {loading && <div className="loading">Loading PDF...</div>}
          {error && <div className="error">Error loading PDF: {error}</div>}

          <div className="page-stack">
            {(() => {
              // Determine page number based on view mode
              const pageNum = viewMode === 'group'
                ? (pageNumbers.length > 0 ? pageNumbers[currentPageIdx] : 1)
                : allPagesCurrentPage

              const totalPages = doc?.page_count || 1
              const isFirstPage = viewMode === 'group' ? currentPageIdx === 0 : allPagesCurrentPage === 1
              const isLastPage = viewMode === 'group'
                ? currentPageIdx === pageNumbers.length - 1
                : allPagesCurrentPage === totalPages

              return (
                <div key={pageNum} className="page-container">
                  <div className="page-header">
                    <div className="page-info-group">
                      <span className="page-number">Page {pageNum}</span>
                      {viewMode === 'group' && pageNumbers.length > 1 && (
                        <span className="page-counter">
                          ({currentPageIdx + 1} / {pageNumbers.length} in group)
                        </span>
                      )}
                      {viewMode === 'all' && (
                        <span className="page-counter">
                          ({allPagesCurrentPage} / {totalPages} total)
                        </span>
                      )}
                    </div>

                    <div className="view-mode-toggle">
                      <button
                        className={`mode-btn ${viewMode === 'group' ? 'active' : ''}`}
                        onClick={toggleViewMode}
                        title="Switch view mode"
                      >
                        {viewMode === 'group' ? 'Group View' : 'All Pages'}
                      </button>
                    </div>

                    {selectedCandidate && (
                      <div className="page-edit-controls">
                        {viewMode === 'group' ? (
                          <button
                            className="page-edit-btn remove-btn"
                            onClick={() => removePageFromGroup(pageNum)}
                            disabled={pageNumbers.length <= 1}
                            title={pageNumbers.length <= 1 ? "Cannot remove the last page from group" : "Remove this page from the evidence group"}
                          >
                            Remove from Group
                          </button>
                        ) : (
                          isPageInGroup(allPagesCurrentPage) ? (
                            <button
                              className="page-edit-btn remove-btn"
                              onClick={() => removePageFromGroup(allPagesCurrentPage)}
                              disabled={pageNumbers.length <= 1}
                              title={pageNumbers.length <= 1 ? "Cannot remove the last page from group" : "Remove this page from the evidence group"}
                            >
                              Remove from Group
                            </button>
                          ) : (
                            <button
                              className="page-edit-btn add-btn"
                              onClick={() => addPageToGroup(allPagesCurrentPage)}
                              title="Add this page to the evidence group"
                            >
                              Add to Group
                            </button>
                          )
                        )}
                      </div>
                    )}

                    {((viewMode === 'group' && pageNumbers.length > 1) || viewMode === 'all') && (
                      <div className="page-navigation">
                        <button
                          className="nav-btn nav-btn-prev"
                          onClick={goToPreviousPage}
                          disabled={isFirstPage}
                          title="Previous page"
                        >
                          ←
                        </button>
                        <button
                          className="nav-btn nav-btn-next"
                          onClick={goToNextPage}
                          disabled={isLastPage}
                          title="Next page"
                        >
                          →
                        </button>
                      </div>
                    )}

                    <div className="rotation-controls">
                      <button
                        className="rotation-btn"
                        onClick={() => rotatePage(pageNum, 'ccw')}
                        title="Rotate counter-clockwise"
                      >
                        ↶
                      </button>
                      <button
                        className="rotation-btn"
                        onClick={() => rotatePage(pageNum, 'cw')}
                        title="Rotate clockwise"
                      >
                        ↷
                      </button>
                    </div>

                    <button
                      className={`mark-done-btn ${isCompleted ? 'completed' : ''}`}
                      onClick={() => onMarkGroupDone(item.row_id)}
                      title={isCompleted ? "This line item is already marked as done" : "Mark this line item as verified and move to next item"}
                    >
                      {isCompleted ? '✓ Done' : 'Mark Line Item Done'}
                    </button>

                    <span className="doc-name">{doc?.budget_item}</span>
                  </div>
                  <div
                    className="page-canvas-wrapper"
                    ref={(el) => {
                      if (el) containerRefs.current[pageNum] = el
                    }}
                  >
                    <div style={{ position: 'relative', display: 'inline-block' }}>
                      <canvas
                        ref={(el) => {
                          if (el) canvasRefs.current[pageNum] = el
                        }}
                        className="page-canvas"
                      />
                      <div className="highlights-overlay">
                        {highlights[pageNum]?.map((rect, idx) => {
                          // Transform highlight coordinates based on user-selected rotation
                          // Backend coordinates are in original (0° rotation) mediabox space
                          const userRotation = pageRotations[pageNum] || 0
                          const transformedRect = transformHighlight(rect, userRotation)

                          // Get viewport dimensions for this page
                          const viewport = viewportDimensions[pageNum]
                          if (!viewport) return null

                          // Get canvas for scaling factor (CSS might scale the canvas)
                          const canvas = canvasRefs.current[pageNum]
                          if (!canvas) return null

                          // Calculate scaling factor: display size / drawing buffer size
                          // This accounts for CSS scaling (max-width: 100%, height: auto)
                          const scaleX = canvas.offsetWidth / canvas.width
                          const scaleY = canvas.offsetHeight / canvas.height

                          // Convert normalized (0-1) coordinates to display pixels
                          // Use viewport dimensions (which match canvas drawing buffer)
                          // then apply CSS scaling factor
                          const pixelLeft = transformedRect.left * viewport.width * scaleX
                          const pixelTop = transformedRect.top * viewport.height * scaleY
                          const pixelWidth = transformedRect.width * viewport.width * scaleX
                          const pixelHeight = transformedRect.height * viewport.height * scaleY

                          return (
                            <div
                              key={idx}
                              className="highlight-rect"
                              style={{
                                left: `${pixelLeft}px`,
                                top: `${pixelTop}px`,
                                width: `${pixelWidth}px`,
                                height: `${pixelHeight}px`,
                              }}
                            />
                          )
                        })}
                      </div>
                    </div>
                  </div>
                </div>
              )
            })()}
          </div>
        </div>
      )}
    </div>
  )
}

export default PDFViewer
