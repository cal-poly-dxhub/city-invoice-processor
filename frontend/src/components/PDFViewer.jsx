import { useState, useEffect, useRef, useMemo } from 'react'
import * as pdfjsLib from 'pdfjs-dist'
import SearchBar from './SearchBar'
import CreateSubItemDialog from './CreateSubItemDialog'
import { DATA_BASE } from '../config'
import './PDFViewer.css'

// Set up PDF.js worker
pdfjsLib.GlobalWorkerOptions.workerSrc = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjsLib.version}/pdf.worker.min.js`

// Fallback filename map for backward compat with old reconciliation.json without source_files
const LEGACY_FILENAME_MAP = {
  'Salary': 'Salary.pdf',
  'Fringe': 'Fringe.pdf',
  'Contractual Service': 'Contractual_Service.pdf',
  'Equipment': 'Equipment.pdf',
  'Insurance': 'Insurance.pdf',
  'Travel and Conferences': 'Travel_and_Conferences.pdf',
  'Space Rental/Occupancy Costs': 'Space_Rental_Occupancy_Costs.pdf',
  'Telecommunications': 'Telecommunications.pdf',
  'Utilities': 'Utilities.pdf',
  'Supplies': 'Supplies.pdf',
  'Other': 'Other.pdf',
  'Indirect Costs': 'Indirect_Costs.pdf',
}

function PDFViewer({ item, documents, matchType, onMarkGroupDone, isCompleted, jobId, userEditedCandidates, setUserEditedCandidates, userAnnotations, setUserAnnotations, onAddSubItem, onAddSubItems, getNextSubItemSuffix, subItems = [] }) {
  const [pdfsReady, setPdfsReady] = useState(false) // True when all source PDFs are loaded
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [highlights, setHighlights] = useState({})
  const [selectedCandidateIdx, setSelectedCandidateIdx] = useState(0) // Track which candidate to display
  const [currentPageIdx, setCurrentPageIdx] = useState(0) // Track which page in the group is currently displayed
  const [viewMode, setViewMode] = useState('group') // 'group', 'all', or 'search' - determines which pages to show
  const [allPagesCurrentPage, setAllPagesCurrentPage] = useState(1) // Track current page when viewing all pages (1-indexed)
  const [searchPageIdx, setSearchPageIdx] = useState(0) // Track current page index in search results
  const [pageRotations, setPageRotations] = useState({}) // Track user-applied rotation per page (0, 90, 180, 270)
  const [viewportDimensions, setViewportDimensions] = useState({}) // Store viewport dimensions for each page
  const [searchQuery, setSearchQuery] = useState('') // User's search query
  const [searchMode, setSearchMode] = useState('any') // 'any' or 'all' for multi-term search
  const [searchResults, setSearchResults] = useState({}) // Search results: { page_number: [boxes] }
  const [drawingMode, setDrawingMode] = useState(false) // Whether drawing mode is active
  const [drawingRect, setDrawingRect] = useState(null) // Pixel coords of in-progress rubber-band rect
  const pageInherentRotations = useRef({}) // Track PDF's inherent rotation
  const canvasRefs = useRef({})
  const containerRefs = useRef({})
  const drawingStartRef = useRef(null) // { startX, startY } during active drag
  const pdfDocsRef = useRef({}) // Cache of loaded PDF.js documents: { source_doc_id: pdfDoc }
  const renderTaskRef = useRef(null) // Current PDF.js render task (for cancellation)
  const renderGenRef = useRef(0) // Generation counter to discard stale retry callbacks

  // Get source files for the current document (with backward compat fallback)
  const getSourceFiles = () => {
    if (doc?.source_files?.length > 0) return doc.source_files
    // Backward compat: synthesize a single source file from budget_item
    const filename = LEGACY_FILENAME_MAP[doc?.budget_item]
    if (!filename) return []
    return [{
      doc_id: doc.doc_id,
      pdf_path: filename,
      filename: filename,
      page_count: doc.page_count || 0,
      page_offset: 0,
    }]
  }

  // Resolve a virtual page number to a physical PDF + local page number
  const resolveVirtualPage = (virtualPageNum) => {
    const sourceFiles = getSourceFiles()
    for (const sf of sourceFiles) {
      if (virtualPageNum > sf.page_offset && virtualPageNum <= sf.page_offset + sf.page_count) {
        return {
          sourceFile: sf,
          localPage: virtualPageNum - sf.page_offset,
        }
      }
    }
    return null
  }

  // Get the source filename for a virtual page (for display)
  const getSourceFilenameForPage = (virtualPageNum) => {
    const resolved = resolveVirtualPage(virtualPageNum)
    if (!resolved) return null
    const sourceFiles = getSourceFiles()
    // Only show filename if there are multiple source files
    if (sourceFiles.length <= 1) return null
    return resolved.sourceFile.filename
  }

  // Helper to get current candidate (merged with user edits)
  const getCurrentCandidate = () => {
    const candidateKey = `${item.row_id}_${selectedCandidateIdx}`
    // Check user edits first — may contain a user-created candidate when none existed originally
    if (userEditedCandidates[candidateKey]) {
      return userEditedCandidates[candidateKey]
    }
    return item.candidates?.[selectedCandidateIdx] || null
  }

  // Get doc and pages from the selected candidate (not selected_evidence)
  const selectedCandidate = getCurrentCandidate()
  const doc = selectedCandidate
    ? documents.find(d => d.doc_id === selectedCandidate.doc_id)
    : documents.find(d => d.doc_id === item.selected_evidence?.doc_id)
  const pageNumbers = selectedCandidate
    ? selectedCandidate.page_numbers || []
    : item.selected_evidence?.page_numbers || []

  // Get search result pages (sorted)
  const searchResultPages = Object.keys(searchResults).map(n => parseInt(n)).sort((a, b) => a - b)

  useEffect(() => {
    if (!doc) return

    // Reset selected candidate and page index when viewing a new item
    if (item.row_id) {
      setSelectedCandidateIdx(0)
      setCurrentPageIdx(0)
      setViewMode('group')
      setAllPagesCurrentPage(1)
      setSearchQuery('')
      setSearchResults({})
      setSearchPageIdx(0)
      setHighlights({}) // Clear old highlights to prevent stale data
      setDrawingMode(false)
      setDrawingRect(null)
    }
    loadPDFs()
  }, [item.row_id])

  useEffect(() => {
    if (!doc) return
    loadPDFs()
  }, [doc])

  useEffect(() => {
    if (pdfsReady) {
      // Use requestAnimationFrame to ensure canvas is fully laid out before rendering
      requestAnimationFrame(() => {
        renderPages()
      })
    }
    // Cleanup: cancel any in-progress render when dependencies change or unmount
    return () => {
      if (renderTaskRef.current) {
        renderTaskRef.current.cancel()
        renderTaskRef.current = null
      }
    }
  }, [pdfsReady, pageNumbers, pageRotations, currentPageIdx, viewMode, allPagesCurrentPage, searchPageIdx, searchResults])

  // Reset to first page when candidate changes
  // Note: highlights will be updated by renderPages() after the page renders and inherent rotation is captured
  useEffect(() => {
    setCurrentPageIdx(0)
  }, [selectedCandidateIdx])

  const loadPDFs = async () => {
    try {
      setLoading(true)
      setError(null)
      setPdfsReady(false)

      const sourceFiles = getSourceFiles()
      if (sourceFiles.length === 0) {
        throw new Error(`No PDF source files found for: ${doc?.budget_item}`)
      }

      // Load all source PDFs in parallel (skip already-cached ones)
      const loadPromises = sourceFiles.map(async (sf) => {
        if (pdfDocsRef.current[sf.doc_id]) return // Already loaded
        const pdfPath = jobId
          ? `${DATA_BASE}/uploads/${jobId}/pdf/${sf.pdf_path}`
          : `/test-files/pdf/${sf.pdf_path}`
        const pdf = await pdfjsLib.getDocument(pdfPath).promise
        pdfDocsRef.current[sf.doc_id] = pdf
      })

      await Promise.all(loadPromises)
      setPdfsReady(true)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const renderPages = async () => {
    if (!pdfsReady) return

    // Increment generation so stale retry callbacks are discarded
    const generation = ++renderGenRef.current

    // Determine which page to render based on view mode
    let pageNum
    if (viewMode === 'group') {
      if (pageNumbers.length === 0) return
      pageNum = pageNumbers[currentPageIdx]
    } else if (viewMode === 'all') {
      pageNum = allPagesCurrentPage
    } else if (viewMode === 'search') {
      if (searchResultPages.length === 0) return
      pageNum = searchResultPages[searchPageIdx]
    } else {
      pageNum = 1
    }

    const canvas = canvasRefs.current[pageNum]
    const container = containerRefs.current[pageNum]

    // Wait for canvas to be properly mounted with dimensions
    if (!canvas || !container) {
      console.log(`PDFViewer: Canvas or container not ready for page ${pageNum}, will retry`)
      setTimeout(() => {
        if (renderGenRef.current === generation) renderPages()
      }, 100)
      return
    }

    // Additional safety check: ensure canvas is in DOM and has layout
    if (canvas.offsetParent === null) {
      console.log(`PDFViewer: Canvas for page ${pageNum} not yet in layout, will retry`)
      setTimeout(() => {
        if (renderGenRef.current === generation) renderPages()
      }, 100)
      return
    }

    // Ensure canvas has been laid out and has dimensions
    // On first render, offsetWidth/Height might be 0
    if (canvas.offsetWidth === 0 || canvas.offsetHeight === 0) {
      console.log(`PDFViewer: Canvas for page ${pageNum} has zero dimensions (${canvas.offsetWidth}x${canvas.offsetHeight}), will retry`)
      setTimeout(() => {
        if (renderGenRef.current === generation) renderPages()
      }, 100)
      return
    }

    try {
      // Cancel any in-progress render before starting a new one
      // This prevents concurrent renders from corrupting the canvas (mirroring/rotation glitch)
      if (renderTaskRef.current) {
        renderTaskRef.current.cancel()
        renderTaskRef.current = null
      }

      // Resolve virtual page to physical PDF + local page number
      const resolved = resolveVirtualPage(pageNum)
      if (!resolved) {
        console.error(`PDFViewer: Could not resolve virtual page ${pageNum}`)
        return
      }
      const { sourceFile, localPage } = resolved
      const pdfDoc = pdfDocsRef.current[sourceFile.doc_id]
      if (!pdfDoc) {
        console.error(`PDFViewer: PDF not loaded for source ${sourceFile.doc_id}`)
        return
      }
      const page = await pdfDoc.getPage(localPage)

      // Check if this render has been superseded while we awaited getPage
      if (renderGenRef.current !== generation) return

      // Store the page's inherent rotation in ref
      const inherentRotation = page.rotate || 0
      pageInherentRotations.current[pageNum] = inherentRotation

      // Render PDF with proper rotation handling
      // If no user rotation: omit rotation param so PDF.js uses inherent rotation
      // If user rotation: add it to inherent rotation
      const userRotation = pageRotations[pageNum] || 0
      const viewport = userRotation === 0
        ? page.getViewport({ scale: 1.5 })  // Let PDF.js apply inherent rotation
        : page.getViewport({ scale: 1.5, rotation: inherentRotation + userRotation })

      console.log(`PDFViewer: Rendering page ${pageNum}, inherent=${inherentRotation}°, user=${userRotation}°, viewport=${viewport.width}x${viewport.height}`)

      const context = canvas.getContext('2d')

      // Update canvas dimensions (this resets the canvas state entirely)
      canvas.width = viewport.width
      canvas.height = viewport.height

      // Ensure clean transform state before rendering
      context.setTransform(1, 0, 0, 1, 0, 0)
      context.clearRect(0, 0, canvas.width, canvas.height)

      // Store viewport dimensions for this page (for highlight calculations)
      setViewportDimensions(prev => ({
        ...prev,
        [pageNum]: {
          width: viewport.width,
          height: viewport.height
        }
      }))

      // Render PDF content — track the task so we can cancel it if a new render starts
      const renderTask = page.render({
        canvasContext: context,
        viewport: viewport
      })
      renderTaskRef.current = renderTask

      await renderTask.promise

      // Check if this render has been superseded while we awaited the render
      if (renderGenRef.current !== generation) return

      renderTaskRef.current = null

      console.log(`PDFViewer: Successfully rendered page ${pageNum}`)

      // Get normalized (0-1) highlights from backend
      // Store them in normalized form so they scale dynamically
      const backendHighlights = getHighlightsFromCandidates()
      console.log(`PDFViewer: Setting highlights for page ${pageNum}:`, backendHighlights[pageNum]?.length || 0, 'boxes')
      setHighlights(backendHighlights)
    } catch (err) {
      // PDF.js throws when a render is cancelled — this is expected, not an error
      if (err?.name === 'RenderingCancelledException' || err?.message?.includes('Rendering cancelled')) {
        console.log(`PDFViewer: Render cancelled for page ${pageNum} (superseded by newer render)`)
        return
      }
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

  // Parse comma-separated search terms, trim, lowercase, filter short terms
  const parseSearchTerms = (query) => {
    if (!query) return []
    return query.split(',').map(t => t.trim().toLowerCase()).filter(t => t.length >= 2)
  }

  // Memoize parsed terms to avoid re-parsing on every render
  const parsedTerms = useMemo(() => parseSearchTerms(searchQuery), [searchQuery])

  const performSearch = (query, mode) => {
    const terms = parseSearchTerms(query)

    // Clear results if no valid terms
    if (terms.length === 0) {
      setSearchResults({})
      if (viewMode === 'search') {
        setViewMode('group')
      }
      return
    }

    // Check if document has pages_data
    if (!doc || !doc.pages_data) {
      console.log('PDFViewer: No pages_data available for search')
      setSearchResults({})
      return
    }

    const results = {}
    let totalMatches = 0

    // Search through all pages in the document
    Object.entries(doc.pages_data).forEach(([pageNumStr, pageData]) => {
      const pageNum = parseInt(pageNumStr)

      if (!pageData.words || pageData.words.length === 0) {
        return
      }

      // Collect matching bounding boxes and track which terms matched on this page
      const matchedBoxes = []
      const termsMatchedOnPage = new Set()

      for (const word of pageData.words) {
        if (!word.text) continue
        const wordLower = word.text.toLowerCase()

        let wordMatched = false
        for (const term of terms) {
          if (wordLower.includes(term)) {
            termsMatchedOnPage.add(term)
            // Only add one bounding box per word even if it matches multiple terms
            if (!wordMatched) {
              matchedBoxes.push({
                left: word.left,
                top: word.top,
                width: word.width,
                height: word.height
              })
              wordMatched = true
            }
          }
        }
      }

      // Decide if this page qualifies based on search mode.
      // In "all" mode, we still highlight ALL matched words on qualifying pages
      // (not just co-occurring terms), so users can see every relevant match.
      const pageQualifies = mode === 'all'
        ? terms.every(t => termsMatchedOnPage.has(t))
        : termsMatchedOnPage.size > 0

      if (pageQualifies && matchedBoxes.length > 0) {
        results[pageNum] = matchedBoxes
        totalMatches += matchedBoxes.length
      }
    })

    console.log(`PDFViewer: Search for "${query}" (${mode}) found ${totalMatches} matches on ${Object.keys(results).length} pages`)
    setSearchResults(results)

    // Switch to search mode if we have results
    if (Object.keys(results).length > 0) {
      setViewMode('search')
      setSearchPageIdx(0) // Start at first search result page
    } else if (viewMode === 'search') {
      // No results, go back to group mode
      setViewMode('group')
    }
  }

  // Effect to perform search when query or mode changes
  useEffect(() => {
    if (doc && doc.pages_data) {
      performSearch(searchQuery, searchMode)
    }
  }, [searchQuery, searchMode, doc])

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

  const getSearchResultSummary = () => {
    if (parsedTerms.length === 0) return null

    const pageCount = Object.keys(searchResults).length
    const totalMatches = Object.values(searchResults).reduce((sum, boxes) => sum + boxes.length, 0)

    if (pageCount === 0) {
      if (parsedTerms.length === 1) {
        return `No matches for "${parsedTerms[0]}"`
      }
      return `No pages match ${searchMode === 'all' ? 'all' : 'any'} terms`
    }

    // Single term: keep existing page-list format
    if (parsedTerms.length === 1) {
      const pageNums = Object.keys(searchResults).map(n => parseInt(n)).sort((a, b) => a - b)
      const pagesList = pageNums.length <= 5
        ? pageNums.join(', ')
        : `${pageNums.slice(0, 5).join(', ')}, ...`
      return `Found on pages: ${pagesList} (${totalMatches} matches)`
    }

    // Multi-term: concise summary
    return `${totalMatches} matches on ${pageCount} page${pageCount !== 1 ? 's' : ''}`
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
        // 90° clockwise: rectangle top-left (x,y) -> (1-y-height, x)
        return {
          left: 1 - rect.top - rect.height,
          top: rect.left,
          width: rect.height,
          height: rect.width
        }
      case 180:
        // 180°: (x,y) -> (1-x-width, 1-y-height)
        return {
          left: 1 - rect.left - rect.width,
          top: 1 - rect.top - rect.height,
          width: rect.width,
          height: rect.height
        }
      case 270:
        // 270° clockwise (90° counter-clockwise): rectangle top-left (x,y) -> (y, 1-x-width)
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

  const reverseTransformHighlight = (rect, rotation) => {
    // Inverse of transformHighlight: display coords -> original (0°) coords
    const normalizedRotation = ((rotation % 360) + 360) % 360
    switch (normalizedRotation) {
      case 90:
        // Forward 90°: (x,y,w,h) -> (1-y-h, x, h, w)
        // Reverse: (x,y,w,h) -> (y, 1-x-w, h, w)
        return {
          left: rect.top,
          top: 1 - rect.left - rect.width,
          width: rect.height,
          height: rect.width
        }
      case 180:
        // Forward 180° is self-inverse
        return {
          left: 1 - rect.left - rect.width,
          top: 1 - rect.top - rect.height,
          width: rect.width,
          height: rect.height
        }
      case 270:
        // Forward 270°: (x,y,w,h) -> (y, 1-x-w, h, w)
        // Reverse: (x,y,w,h) -> (1-y-h, x, h, w)
        return {
          left: 1 - rect.top - rect.height,
          top: rect.left,
          width: rect.height,
          height: rect.width
        }
      default:
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
    } else if (viewMode === 'all') {
      setAllPagesCurrentPage(prev => Math.max(1, prev - 1))
    } else if (viewMode === 'search') {
      setSearchPageIdx(prev => Math.max(0, prev - 1))
    }
  }

  const goToNextPage = () => {
    if (viewMode === 'group') {
      setCurrentPageIdx(prev => Math.min(pageNumbers.length - 1, prev + 1))
    } else if (viewMode === 'all') {
      const totalPages = doc?.page_count || 1
      setAllPagesCurrentPage(prev => Math.min(totalPages, prev + 1))
    } else if (viewMode === 'search') {
      setSearchPageIdx(prev => Math.min(searchResultPages.length - 1, prev + 1))
    }
  }

  const toggleViewMode = () => {
    if (viewMode === 'group') {
      // Switching to all-pages view: set current page to the group's current page
      const currentGroupPage = pageNumbers[currentPageIdx]
      setAllPagesCurrentPage(currentGroupPage || 1)
      setViewMode('all')
    } else if (viewMode === 'all') {
      // Switching to group view: find the closest group page
      const closestIdx = pageNumbers.findIndex(p => p >= allPagesCurrentPage)
      setCurrentPageIdx(closestIdx >= 0 ? closestIdx : 0)
      setViewMode('group')
      setDrawingMode(false)
      setDrawingRect(null)
    } else if (viewMode === 'search') {
      // Switching from search view: clear search and go to group view
      setSearchQuery('')
      setSearchResults({})
      setViewMode('group')
      setDrawingMode(false)
      setDrawingRect(null)
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
    const candidateKey = `${item.row_id}_${selectedCandidateIdx}`

    if (candidate) {
      // Existing candidate: add page to its list
      const newPageNumbers = [...candidate.page_numbers, pageNum].sort((a, b) => a - b)
      setUserEditedCandidates({
        ...userEditedCandidates,
        [candidateKey]: {
          ...candidate,
          page_numbers: newPageNumbers,
          edited: true
        }
      })
    } else {
      // No candidate exists: create a new user-generated one
      const docId = doc?.doc_id || item.selected_evidence?.doc_id || item.budget_item
      setUserEditedCandidates({
        ...userEditedCandidates,
        [candidateKey]: {
          doc_id: docId,
          page_numbers: [pageNum],
          score: null,
          rationale: ['Manually added by user'],
          highlights: {},
          evidence_snippets: [],
          edited: true
        }
      })
    }
  }

  // Remove a page from the current candidate group
  const removePageFromGroup = (pageNum) => {
    const candidate = getCurrentCandidate()
    if (!candidate) return

    const candidateKey = `${item.row_id}_${selectedCandidateIdx}`
    const newPageNumbers = candidate.page_numbers.filter(p => p !== pageNum)

    if (newPageNumbers.length === 0) {
      // Last page removed: delete the entire candidate/group
      const newEdits = { ...userEditedCandidates }
      delete newEdits[candidateKey]
      setUserEditedCandidates(newEdits)
      setViewMode('all')
    } else {
      setUserEditedCandidates({
        ...userEditedCandidates,
        [candidateKey]: {
          ...candidate,
          page_numbers: newPageNumbers,
          edited: true
        }
      })
      // Clamp page index so it doesn't point past the end of the shortened group
      setCurrentPageIdx(prev => Math.min(prev, newPageNumbers.length - 1))
    }
  }

  // Reset candidate back to original
  const resetCandidate = () => {
    const candidateKey = `${item.row_id}_${selectedCandidateIdx}`
    const newEdits = { ...userEditedCandidates }
    delete newEdits[candidateKey]
    setUserEditedCandidates(newEdits)
  }

  // Drawing event handlers for user annotations
  const handleDrawStart = (e, pageNum) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top
    drawingStartRef.current = { startX: x, startY: y }
    setDrawingRect(null)
  }

  const handleDrawMove = (e, pageNum) => {
    if (!drawingStartRef.current) return
    const rect = e.currentTarget.getBoundingClientRect()
    const x = e.clientX - rect.left
    const y = e.clientY - rect.top

    const left = Math.min(drawingStartRef.current.startX, x)
    const top = Math.min(drawingStartRef.current.startY, y)
    const width = Math.abs(x - drawingStartRef.current.startX)
    const height = Math.abs(y - drawingStartRef.current.startY)

    setDrawingRect({ left, top, width, height })
  }

  const handleDrawEnd = (e, pageNum) => {
    if (!drawingStartRef.current) return
    const currentRect = drawingRect
    drawingStartRef.current = null
    setDrawingRect(null)

    if (!currentRect || currentRect.width < 5 || currentRect.height < 5) return

    // Convert pixel coordinates to normalized 0-1 coordinates
    const viewport = viewportDimensions[pageNum]
    const canvas = canvasRefs.current[pageNum]
    if (!viewport || !canvas) return

    const scaleX = canvas.offsetWidth / canvas.width
    const scaleY = canvas.offsetHeight / canvas.height

    let normalizedRect = {
      left: currentRect.left / (viewport.width * scaleX),
      top: currentRect.top / (viewport.height * scaleY),
      width: currentRect.width / (viewport.width * scaleX),
      height: currentRect.height / (viewport.height * scaleY),
    }

    // Reverse the total rotation (inherent + user) so stored coords are in original (0-deg) space
    const inherentRotation = pageInherentRotations.current[pageNum] || 0
    const userRotation = pageRotations[pageNum] || 0
    const totalRotation = inherentRotation + userRotation
    if (totalRotation !== 0) {
      normalizedRect = reverseTransformHighlight(normalizedRect, totalRotation)
    }

    // Store the annotation
    const candidateKey = `${item.row_id}_${selectedCandidateIdx}`
    setUserAnnotations(prev => {
      const candidateAnnotations = prev[candidateKey] || {}
      const pageAnnotations = candidateAnnotations[pageNum] || []
      return {
        ...prev,
        [candidateKey]: {
          ...candidateAnnotations,
          [pageNum]: [...pageAnnotations, normalizedRect]
        }
      }
    })

    // Auto-add page to group if not already in it
    if (!isPageInGroup(pageNum)) {
      addPageToGroup(pageNum)
    }
  }

  const deleteAnnotation = (pageNum, annotationIdx) => {
    const candidateKey = `${item.row_id}_${selectedCandidateIdx}`
    setUserAnnotations(prev => {
      const candidateAnnotations = prev[candidateKey] || {}
      const pageAnnotations = [...(candidateAnnotations[pageNum] || [])]
      pageAnnotations.splice(annotationIdx, 1)
      return {
        ...prev,
        [candidateKey]: {
          ...candidateAnnotations,
          [pageNum]: pageAnnotations
        }
      }
    })
  }

  // Sub-item dialog state
  const [subItemDialogOpen, setSubItemDialogOpen] = useState(false)
  const [subItemDialogMode, setSubItemDialogMode] = useState('manual') // 'manual' or 'auto'

  const openAutoExtractDialog = () => {
    setSubItemDialogMode('auto')
    setSubItemDialogOpen(true)
  }

  // Get the current page for the dialog's source_page
  const getCurrentPageNum = () => {
    if (viewMode === 'group') return pageNumbers[currentPageIdx] || 1
    if (viewMode === 'all') return allPagesCurrentPage
    if (viewMode === 'search') return searchResultPages[searchPageIdx] || 1
    return 1
  }

  // Determine the parent row_id (for sub-items, use the parent; otherwise use current)
  const effectiveParentRowId = item._isSubItem ? item._parentRowId : item.row_id

  return (
    <div className="pdf-viewer">
      <div className="viewer-header">
        <div className="item-summary">
          <h2 className="item-title">Row #{item.row_index + 1}: {item.budget_item}</h2>
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

          {doc && doc.pages_data && (
            <SearchBar
              key={item.row_id}
              query={searchQuery}
              onQueryChange={setSearchQuery}
              resultSummary={getSearchResultSummary()}
              searchMode={searchMode}
              onSearchModeChange={setSearchMode}
              hasMultipleTerms={parsedTerms.length > 1}
            />
          )}

          <div className="page-stack">
            {(() => {
              // Determine page number based on view mode
              let pageNum
              if (viewMode === 'group') {
                pageNum = pageNumbers.length > 0 ? pageNumbers[currentPageIdx] : 1
              } else if (viewMode === 'all') {
                pageNum = allPagesCurrentPage
              } else if (viewMode === 'search') {
                pageNum = searchResultPages.length > 0 ? searchResultPages[searchPageIdx] : 1
              } else {
                pageNum = 1
              }

              const totalPages = doc?.page_count || 1
              const isFirstPage = viewMode === 'group'
                ? currentPageIdx === 0
                : viewMode === 'all'
                  ? allPagesCurrentPage === 1
                  : searchPageIdx === 0
              const isLastPage = viewMode === 'group'
                ? currentPageIdx === pageNumbers.length - 1
                : viewMode === 'all'
                  ? allPagesCurrentPage === totalPages
                  : searchPageIdx === searchResultPages.length - 1

              console.log(`PDFViewer: Rendering page ${pageNum}, viewMode=${viewMode}, highlights available for pages:`, Object.keys(highlights))

              return (
                <div key={pageNum} className="page-container">
                  <div className="page-header">
                    <div className="page-header-row page-nav-row">
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
                        {viewMode === 'search' && (
                          <span className="page-counter">
                            ({searchPageIdx + 1} / {searchResultPages.length} results)
                          </span>
                        )}
                      </div>

                      {((viewMode === 'group' && pageNumbers.length > 1) || viewMode === 'all' || (viewMode === 'search' && searchResultPages.length > 1)) && (
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

                    </div>

                    <div className="page-header-row page-actions-row">
                      <span className="doc-name">
                        {doc?.budget_item}
                        {getSourceFilenameForPage(pageNum) && (
                          <span className="source-filename"> — {getSourceFilenameForPage(pageNum)}</span>
                        )}
                      </span>

                      <div className="view-mode-toggle">
                        <button
                          className={`mode-btn ${viewMode === 'group' ? 'active' : ''} ${viewMode === 'all' ? 'active' : ''} ${viewMode === 'search' ? 'active' : ''}`}
                          onClick={toggleViewMode}
                          title="Switch view mode"
                        >
                          {viewMode === 'group' ? 'Group View' : viewMode === 'all' ? 'All Pages' : 'Search Results'}
                        </button>
                      </div>

                      {selectedCandidate && (
                        <div className="page-edit-controls">
                          {viewMode === 'group' ? (
                            <button
                              className="page-edit-btn remove-btn"
                              onClick={() => removePageFromGroup(pageNum)}
                              title="Remove this page from the evidence group"
                            >
                              Remove from Group
                            </button>
                          ) : (
                            isPageInGroup(allPagesCurrentPage) ? (
                              <button
                                className="page-edit-btn remove-btn"
                                onClick={() => removePageFromGroup(allPagesCurrentPage)}
                                title="Remove this page from the evidence group"
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

                      {(viewMode === 'all' || viewMode === 'search') && (
                        <button
                          className={`draw-mode-btn ${drawingMode ? 'active' : ''}`}
                          onClick={() => setDrawingMode(prev => !prev)}
                          title={drawingMode ? 'Exit drawing mode' : 'Draw highlight boxes on the page'}
                        >
                          {drawingMode ? 'Done Drawing' : 'Draw Highlight'}
                        </button>
                      )}

                      {onAddSubItems && !item._isSubItem && (
                        <button
                          className="auto-extract-btn"
                          onClick={openAutoExtractDialog}
                          title="Auto-extract sub-items from table rows on this page"
                        >
                          Auto-Extract Sub-Items
                        </button>
                      )}

                      <button
                        className={`mark-done-btn ${isCompleted ? 'completed' : ''}`}
                        onClick={() => onMarkGroupDone(item.row_id)}
                        title={isCompleted ? "This line item is already marked as done" : "Mark this line item as verified and move to next item"}
                      >
                        {isCompleted ? '✓ Done' : 'Mark Line Item Done'}
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
                        {/* Render candidate highlights (yellow) */}
                        {highlights[String(pageNum)]?.map((rect, idx) => {
                          // Validate highlight coordinates are in 0-1 range
                          if (rect.left > 1 || rect.top > 1 || rect.width > 1 || rect.height > 1 ||
                              rect.left < 0 || rect.top < 0 || rect.width < 0 || rect.height < 0) {
                            console.warn(`PDFViewer: Invalid highlight coordinates on page ${pageNum}:`, rect)
                            return null
                          }

                          // Transform highlight coordinates based on total rotation (inherent + user)
                          // Backend coordinates are in original (0° rotation) mediabox space
                          // PDF is rendered with inherent + user rotation applied
                          const inherentRotation = pageInherentRotations.current[pageNum] || 0
                          const userRotation = pageRotations[pageNum] || 0
                          const totalRotation = inherentRotation + userRotation
                          const transformedRect = transformHighlight(rect, totalRotation)

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
                              key={`candidate-${idx}`}
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

                        {/* Render search highlights (cyan) */}
                        {searchResults[pageNum]?.map((rect, idx) => {
                          // Apply same transformation as candidate highlights (inherent + user rotation)
                          const inherentRotation = pageInherentRotations.current[pageNum] || 0
                          const userRotation = pageRotations[pageNum] || 0
                          const totalRotation = inherentRotation + userRotation
                          const transformedRect = transformHighlight(rect, totalRotation)

                          const viewport = viewportDimensions[pageNum]
                          if (!viewport) return null

                          const canvas = canvasRefs.current[pageNum]
                          if (!canvas) return null

                          const scaleX = canvas.offsetWidth / canvas.width
                          const scaleY = canvas.offsetHeight / canvas.height

                          const pixelLeft = transformedRect.left * viewport.width * scaleX
                          const pixelTop = transformedRect.top * viewport.height * scaleY
                          const pixelWidth = transformedRect.width * viewport.width * scaleX
                          const pixelHeight = transformedRect.height * viewport.height * scaleY

                          return (
                            <div
                              key={`search-${idx}`}
                              className="highlight-rect-search"
                              style={{
                                left: `${pixelLeft}px`,
                                top: `${pixelTop}px`,
                                width: `${pixelWidth}px`,
                                height: `${pixelHeight}px`,
                              }}
                            />
                          )
                        })}

                        {/* Render user-drawn annotations (green) */}
                        {(() => {
                          const candidateKey = `${item.row_id}_${selectedCandidateIdx}`
                          const annotations = userAnnotations[candidateKey]?.[pageNum] || []
                          return annotations.map((rect, idx) => {
                            const inherentRotation = pageInherentRotations.current[pageNum] || 0
                            const userRotation = pageRotations[pageNum] || 0
                            const totalRotation = inherentRotation + userRotation
                            const transformedRect = transformHighlight(rect, totalRotation)

                            const viewport = viewportDimensions[pageNum]
                            if (!viewport) return null

                            const canvas = canvasRefs.current[pageNum]
                            if (!canvas) return null

                            const scaleX = canvas.offsetWidth / canvas.width
                            const scaleY = canvas.offsetHeight / canvas.height

                            const pixelLeft = transformedRect.left * viewport.width * scaleX
                            const pixelTop = transformedRect.top * viewport.height * scaleY
                            const pixelWidth = transformedRect.width * viewport.width * scaleX
                            const pixelHeight = transformedRect.height * viewport.height * scaleY

                            return (
                              <div
                                key={`annotation-${idx}`}
                                className="highlight-rect-annotation"
                                style={{
                                  left: `${pixelLeft}px`,
                                  top: `${pixelTop}px`,
                                  width: `${pixelWidth}px`,
                                  height: `${pixelHeight}px`,
                                }}
                              >
                                <button
                                  className="annotation-delete-btn"
                                  onClick={() => deleteAnnotation(pageNum, idx)}
                                  title="Delete this annotation"
                                >
                                  ×
                                </button>
                              </div>
                            )
                          })
                        })()}
                      </div>

                      {/* Interactive drawing overlay - only when drawing mode active */}
                      {drawingMode && (
                        <div
                          className="drawing-overlay"
                          onMouseDown={(e) => handleDrawStart(e, pageNum)}
                          onMouseMove={(e) => handleDrawMove(e, pageNum)}
                          onMouseUp={(e) => handleDrawEnd(e, pageNum)}
                          onMouseLeave={(e) => handleDrawEnd(e, pageNum)}
                        >
                          {drawingRect && (
                            <div
                              className="highlight-rect-drawing"
                              style={{
                                left: `${drawingRect.left}px`,
                                top: `${drawingRect.top}px`,
                                width: `${drawingRect.width}px`,
                                height: `${drawingRect.height}px`,
                              }}
                            />
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )
            })()}
          </div>
        </div>
      )}
      {/* Sub-item creation dialog */}
      {subItemDialogOpen && (
        <CreateSubItemDialog
          open={subItemDialogOpen}
          onClose={() => setSubItemDialogOpen(false)}
          jobId={jobId}
          parentRowId={effectiveParentRowId}
          docId={doc?.doc_id || item.selected_evidence?.doc_id}
          budgetItem={item.budget_item}
          sourcePage={getCurrentPageNum()}
          getNextSubItemSuffix={getNextSubItemSuffix}
          onAddSubItem={onAddSubItem}
          onAddSubItems={onAddSubItems}
          initialMode={subItemDialogMode}
        />
      )}
    </div>
  )
}

export default PDFViewer
