import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { API_BASE } from '../config'
import './UploadPage.css'

const BUDGET_ITEMS = [
  "Salary",
  "Fringe",
  "Contractual Service",
  "Equipment",
  "Insurance",
  "Travel and Conferences",
  "Space Rental/Occupancy Costs",
  "Telecommunications",
  "Utilities",
  "Supplies",
  "Other",
  "Indirect Costs",
]

const slugify = (text) =>
  text.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/_+/g, '_').replace(/^_|_$/g, '')

function UploadPage() {
  const navigate = useNavigate()
  const [csvFile, setCsvFile] = useState(null)
  const [pdfFiles, setPdfFiles] = useState({}) // { budgetItem: File[] }
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState('')
  const [uploadError, setUploadError] = useState(null)

  const handleCsvChange = (e) => {
    const file = e.target.files[0]
    if (file && file.name.endsWith('.csv')) {
      setCsvFile(file)
    } else {
      alert('Please select a CSV file')
      e.target.value = ''
    }
  }

  const handlePdfFileSelect = (budgetItem, e) => {
    const newFiles = Array.from(e.target.files).filter(f => f.name.toLowerCase().endsWith('.pdf'))
    if (newFiles.length === 0) {
      alert('No PDF files found in selection')
      e.target.value = ''
      return
    }
    setPdfFiles(prev => {
      const existing = prev[budgetItem] || []
      const existingKeys = new Set(existing.map(f => `${f.name}__${f.size}`))
      const unique = newFiles.filter(f => !existingKeys.has(`${f.name}__${f.size}`))
      const merged = [...existing, ...unique]
      return { ...prev, [budgetItem]: merged }
    })
    e.target.value = ''
  }

  const handlePdfFolderSelect = (budgetItem, e) => {
    const newFiles = Array.from(e.target.files).filter(f => f.name.toLowerCase().endsWith('.pdf'))
    if (newFiles.length === 0) {
      alert('No PDF files found in the selected folder')
      e.target.value = ''
      return
    }
    setPdfFiles(prev => {
      const existing = prev[budgetItem] || []
      const existingKeys = new Set(existing.map(f => `${f.name}__${f.size}`))
      const unique = newFiles.filter(f => !existingKeys.has(`${f.name}__${f.size}`))
      const merged = [...existing, ...unique]
      return { ...prev, [budgetItem]: merged }
    })
    e.target.value = ''
  }

  const handleRemoveFile = (budgetItem, index) => {
    setPdfFiles(prev => {
      const files = [...(prev[budgetItem] || [])]
      files.splice(index, 1)
      if (files.length === 0) {
        const { [budgetItem]: _, ...rest } = prev
        return rest
      }
      return { ...prev, [budgetItem]: files }
    })
  }

  const handleClearFiles = (budgetItem) => {
    setPdfFiles(prev => {
      const { [budgetItem]: _, ...rest } = prev
      return rest
    })
  }

  const handleUpload = async () => {
    if (!csvFile) {
      alert('Please select a CSV file before uploading')
      return
    }

    const budgetItemsWithFiles = Object.entries(pdfFiles).filter(([_, files]) => files.length > 0)
    if (budgetItemsWithFiles.length === 0) {
      alert('Please select at least one PDF file before uploading')
      return
    }

    setUploading(true)
    setUploadError(null)
    setUploadProgress('Requesting upload URLs...')

    try {
      // Build pdf_files map and fileMap for upload
      const pdfFilesPayload = {}
      const fileMap = [] // { budgetItem, s3RelPath, file }

      for (const [budgetItem, files] of budgetItemsWithFiles) {
        const slug = slugify(budgetItem)
        const paths = []
        const nameCount = {}

        for (let i = 0; i < files.length; i++) {
          const file = files[i]
          let name = file.name
          const key = name.toLowerCase()

          // Handle duplicate filenames within same budget item
          if (nameCount[key]) {
            nameCount[key]++
            const stem = name.replace(/\.pdf$/i, '')
            name = `${stem}_${nameCount[key]}.pdf`
          } else {
            nameCount[key] = 1
          }

          // Single file: flat path; Multiple files: subdirectory path
          const s3RelPath = files.length === 1 ? `${slug}.pdf` : `${slug}/${name}`
          paths.push(s3RelPath)
          fileMap.push({ budgetItem, s3RelPath, file })
        }

        pdfFilesPayload[slug] = paths
      }

      // Step 1: Get presigned URLs from API
      const startResp = await fetch(`${API_BASE}/api/upload/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pdf_files: pdfFilesPayload }),
      })

      if (!startResp.ok) throw new Error(`Failed to get upload URLs: ${startResp.status}`)
      const { job_id, presigned_urls } = await startResp.json()

      // Step 2: Upload PDFs first (CSV upload triggers pipeline, so it goes last)
      for (let i = 0; i < fileMap.length; i++) {
        const { budgetItem, s3RelPath, file } = fileMap[i]
        const url = presigned_urls.pdfs[s3RelPath]

        setUploadProgress(`Uploading PDF ${i + 1}/${fileMap.length}: ${budgetItem} - ${file.name}...`)

        await fetch(url, {
          method: 'PUT',
          body: file,
          headers: { 'Content-Type': 'application/pdf' },
        })
      }

      // Step 3: Upload CSV last (triggers the processing pipeline)
      setUploadProgress('Uploading CSV (triggers processing)...')
      await fetch(presigned_urls.csv, {
        method: 'PUT',
        body: csvFile,
        headers: { 'Content-Type': 'text/csv' },
      })

      setUploadProgress('Upload complete! Redirecting to review...')

      // Redirect to review page after short delay
      setTimeout(() => {
        navigate(`/review/${job_id}`)
      }, 1500)
    } catch (err) {
      setUploadError(err.message)
      setUploading(false)
      setUploadProgress('')
    }
  }

  const getUploadStats = () => {
    const budgetItemsWithFiles = Object.keys(pdfFiles).filter(k => pdfFiles[k].length > 0)
    const totalFiles = Object.values(pdfFiles).reduce((sum, files) => sum + files.length, 0)
    return {
      csvReady: !!csvFile,
      pdfCount: budgetItemsWithFiles.length,
      totalFiles,
      totalPdfs: BUDGET_ITEMS.length,
      canUpload: csvFile && budgetItemsWithFiles.length > 0
    }
  }

  const stats = getUploadStats()

  return (
    <div className="upload-page">
      <header className="upload-header">
        <div className="header-content">
          <div className="header-title">
            <h1>Invoice Document Upload</h1>
            <p className="subtitle">Upload CSV Invoice and Supporting PDF Documents</p>
          </div>
          <button
            className={`upload-button ${!stats.canUpload ? 'disabled' : ''} ${uploading ? 'uploading' : ''}`}
            onClick={handleUpload}
            disabled={!stats.canUpload || uploading}
          >
            {uploading ? 'Uploading...' : 'Upload'}
          </button>
        </div>
      </header>

      {uploadProgress && (
        <div className="upload-progress-bar">
          <span>{uploadProgress}</span>
        </div>
      )}

      {uploadError && (
        <div className="upload-error-bar">
          <span>Error: {uploadError}</span>
        </div>
      )}

      <div className="upload-stats">
        <div className="stat">
          <span className="stat-label">CSV File:</span>
          <span className={`stat-value ${stats.csvReady ? 'ready' : ''}`}>
            {stats.csvReady ? 'Ready' : 'Not selected'}
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">PDF Files:</span>
          <span className={`stat-value ${stats.pdfCount > 0 ? 'ready' : ''}`}>
            {stats.pdfCount} of {stats.totalPdfs} budget items
            {stats.totalFiles > stats.pdfCount && ` (${stats.totalFiles} files)`}
          </span>
        </div>
      </div>

      <main className="upload-main-content">
        <section className="upload-section">
          <div className="section-header">
            <h2>Invoice CSV File</h2>
            <p className="section-description">Upload the invoice CSV containing all line items</p>
          </div>

          <div className="file-input-card">
            <div className="file-input-wrapper">
              <input
                type="file"
                id="csv-upload"
                accept=".csv"
                onChange={handleCsvChange}
                className="file-input"
              />
              <label htmlFor="csv-upload" className="file-input-label">
                <span className="file-input-button">Select CSV File</span>
                <span className="file-input-text">
                  {csvFile ? csvFile.name : 'No file selected'}
                </span>
              </label>
            </div>
          </div>
        </section>

        <section className="upload-section">
          <div className="section-header">
            <h2>Budget Item PDFs</h2>
            <p className="section-description">Upload supporting PDF documents for each budget item</p>
          </div>

          <div className="pdf-grid">
            {BUDGET_ITEMS.map((item) => {
              const files = pdfFiles[item] || []
              const hasFiles = files.length > 0

              return (
                <div key={item} className={`file-input-card ${hasFiles ? 'has-file' : ''}`}>
                  <div className="budget-item-header">
                    <div className="budget-item-label">{item}</div>
                    {hasFiles && (
                      <span className="file-count-badge">
                        {files.length} file{files.length !== 1 ? 's' : ''}
                      </span>
                    )}
                  </div>

                  {hasFiles && (
                    <div className="file-list">
                      {files.map((file, idx) => (
                        <div key={`${file.name}-${idx}`} className="file-list-item">
                          <span className="file-list-name" title={file.name}>
                            {file.name}
                          </span>
                          <button
                            className="file-remove-btn"
                            onClick={() => handleRemoveFile(item, idx)}
                            title="Remove file"
                            disabled={uploading}
                          >
                            &times;
                          </button>
                        </div>
                      ))}
                    </div>
                  )}

                  <div className="file-actions">
                    <div className="file-input-wrapper">
                      <input
                        type="file"
                        id={`pdf-files-${item}`}
                        accept=".pdf"
                        multiple
                        onChange={(e) => handlePdfFileSelect(item, e)}
                        className="file-input"
                        disabled={uploading}
                      />
                      <label htmlFor={`pdf-files-${item}`} className="file-input-label-btn">
                        {hasFiles ? 'Add Files' : 'Select Files'}
                      </label>
                    </div>

                    <div className="file-input-wrapper">
                      <input
                        type="file"
                        id={`pdf-folder-${item}`}
                        webkitdirectory=""
                        onChange={(e) => handlePdfFolderSelect(item, e)}
                        className="file-input"
                        disabled={uploading}
                      />
                      <label htmlFor={`pdf-folder-${item}`} className="file-input-label-btn folder-btn">
                        Select Folder
                      </label>
                    </div>

                    {hasFiles && (
                      <button
                        className="clear-all-btn"
                        onClick={() => handleClearFiles(item)}
                        disabled={uploading}
                      >
                        Clear All
                      </button>
                    )}
                  </div>

                  {!hasFiles && (
                    <div className="file-input-hint">
                      Select one or more PDFs, or an entire folder
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </section>
      </main>
    </div>
  )
}

export default UploadPage
