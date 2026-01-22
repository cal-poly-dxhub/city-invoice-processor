import { useState, useEffect, useRef } from 'react'
import * as pdfjsLib from 'pdfjs-dist'
import './PDFViewer.css'

// Set up PDF.js worker
pdfjsLib.GlobalWorkerOptions.workerSrc = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjsLib.version}/pdf.worker.min.js`

function PDFViewer({ item, documents, matchType }) {
  const [pdfDoc, setPdfDoc] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [highlights, setHighlights] = useState({})
  const [pageRotations, setPageRotations] = useState({}) // Track user rotation per page (0, 90, 180, 270)
  const [pageInherentRotations, setPageInherentRotations] = useState({}) // Track PDF's inherent rotation
  const canvasRefs = useRef({})
  const containerRefs = useRef({})

  const doc = documents.find(d => d.doc_id === item.selected_evidence?.doc_id)
  const pageNumbers = item.selected_evidence?.page_numbers || []

  useEffect(() => {
    if (!doc) return

    loadPDF()
  }, [doc])

  useEffect(() => {
    if (pdfDoc && pageNumbers.length > 0) {
      renderPages()
    }
  }, [pdfDoc, pageNumbers, pageRotations])

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

    const inherentRotations = {}

    for (const pageNum of pageNumbers) {
      const canvas = canvasRefs.current[pageNum]
      if (!canvas) continue

      try {
        const page = await pdfDoc.getPage(pageNum)

        // Store the page's inherent rotation
        inherentRotations[pageNum] = page.rotate || 0

        const userRotation = pageRotations[pageNum] || 0
        const viewport = page.getViewport({ scale: 1.5, rotation: userRotation })

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
      } catch (err) {
        console.error(`Error rendering page ${pageNum}:`, err)
      }
    }

    // Store inherent rotations
    setPageInherentRotations(inherentRotations)

    // Get normalized (0-1) highlights from backend
    // Store them in normalized form so they scale dynamically
    const backendHighlights = getHighlightsFromCandidates()
    setHighlights(backendHighlights)
  }

  const getHighlightsFromCandidates = () => {
    // Get highlights from the top candidate (if available)
    if (!item.candidates || item.candidates.length === 0) {
      console.log('PDFViewer: No candidates found')
      return {}
    }

    const topCandidate = item.candidates[0]
    const candidateHighlights = topCandidate.highlights || {}

    console.log('PDFViewer: Candidate highlights from backend (normalized):', candidateHighlights)

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
    if (matchType === 'too-many') return 'poor'
    if (matchType === 'none') return 'none'
    return 'partial'
  }

  const transformHighlight = (rect, rotation) => {
    // Transform normalized (0-1) highlight coordinates based on page rotation
    // Rotation is in degrees: 0, 90, 180, 270
    switch (rotation) {
      case 90:
        // 90° clockwise: (x,y) -> (y, 1-x-w), dimensions swap
        return {
          left: rect.top,
          top: 1 - rect.left - rect.width,
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
        // 270° clockwise (90° counter-clockwise): (x,y) -> (1-y-h, x), dimensions swap
        return {
          left: 1 - rect.top - rect.height,
          top: rect.left,
          width: rect.height,
          height: rect.width
        }
      default:
        // 0° or invalid: no transformation
        return rect
    }
  }

  const rotatePage = (pageNum, direction) => {
    setPageRotations(prev => {
      const currentRotation = prev[pageNum] || 0
      const newRotation = (currentRotation + direction + 360) % 360
      return { ...prev, [pageNum]: newRotation }
    })
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
          {item.candidates.map((candidate, idx) => (
            <div key={idx} className={`candidate-card ${idx === 0 ? 'top-candidate' : ''}`}>
              <div className="candidate-header">
                <span className="candidate-rank">Candidate {idx + 1}</span>
                <span className={`candidate-score ${getMatchClass()}`}>
                  Score: {candidate.score?.toFixed(2) || 'N/A'}
                </span>
              </div>
              <div className="candidate-pages">
                Pages: {candidate.page_numbers.join(', ')}
              </div>
              <div className="candidate-rationale">
                {candidate.rationale?.map((line, i) => (
                  <div key={i} className="rationale-line">{line}</div>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {pageNumbers.length === 0 ? (
        <div className="no-pages">
          <h3>No Evidence Pages</h3>
          {item.raw?.amount === 0 ? (
            <p>This line item has a $0 amount. No matching was performed since there is no transaction to verify.</p>
          ) : (
            <p>This line item has no matched evidence pages.</p>
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

          <div className="pages-grid">
            {pageNumbers.map((pageNum) => {
              const rotation = pageRotations[pageNum] || 0
              return (
              <div key={`${pageNum}-${rotation}`} className="page-container">
                <div className="page-header">
                  <span className="page-number">Page {pageNum}</span>
                  <span className="doc-name">{doc?.budget_item}</span>
                  <div className="rotation-controls">
                    <button
                      className="rotation-btn"
                      onClick={() => rotatePage(pageNum, -90)}
                      title="Rotate left (counter-clockwise)"
                    >
                      ↺
                    </button>
                    <button
                      className="rotation-btn"
                      onClick={() => rotatePage(pageNum, 90)}
                      title="Rotate right (clockwise)"
                    >
                      ↻
                    </button>
                  </div>
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
                        // Apply rotation transformation to highlight coordinates
                        // Total rotation = inherent (from PDF) + user (from buttons)
                        const inherentRotation = pageInherentRotations[pageNum] || 0
                        const userRotation = pageRotations[pageNum] || 0
                        const totalRotation = (inherentRotation + userRotation) % 360
                        const transformedRect = transformHighlight(rect, totalRotation)

                        // Convert normalized (0-1) coordinates to pixels at render time
                        // IMPORTANT: Use offsetWidth/offsetHeight (display size) not width/height (drawing size)
                        // The canvas may be scaled by CSS (max-width: 100%, height: auto)
                        const canvas = canvasRefs.current[pageNum]
                        if (!canvas) return null

                        const pixelLeft = transformedRect.left * canvas.offsetWidth
                        const pixelTop = transformedRect.top * canvas.offsetHeight
                        const pixelWidth = transformedRect.width * canvas.offsetWidth
                        const pixelHeight = transformedRect.height * canvas.offsetHeight

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
            })}
          </div>
        </div>
      )}
    </div>
  )
}

export default PDFViewer
