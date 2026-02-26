import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useDropzone } from 'react-dropzone'
import { API_BASE } from '../config'
import { authFetch } from '../services/authenticatedFetch'
import { BUDGET_ITEMS, slugify, classifyFile } from '../utils/budgetItemClassifier'
import { classifyFilenames } from '../services/api'
import UnassignedFilesModal from '../components/UnassignedFilesModal'
import './UploadPage.css'

function UploadPage() {
  const navigate = useNavigate()
  const [jobName, setJobName] = useState('')
  const [csvFile, setCsvFile] = useState(null)
  const [pdfFiles, setPdfFiles] = useState({}) // { budgetItem: File[] }
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState('')
  const [uploadError, setUploadError] = useState(null)
  const [unassignedFiles, setUnassignedFiles] = useState(null)
  const [classifyingFiles, setClassifyingFiles] = useState(false)

  const handleCsvChange = (e) => {
    const file = e.target.files[0]
    if (file && file.name.endsWith('.csv')) {
      setCsvFile(file)
    } else {
      alert('Please select a CSV file')
      e.target.value = ''
    }
  }

  const addClassifiedFiles = (fileItems) => {
    setPdfFiles(prev => {
      const next = { ...prev }
      for (const { file, budgetItem } of fileItems) {
        const existing = next[budgetItem] || []
        const existingKeys = new Set(existing.map(f => `${f.name}__${f.size}`))
        if (!existingKeys.has(`${file.name}__${file.size}`)) {
          next[budgetItem] = [...existing, file]
        }
      }
      return next
    })
  }

  const processNewFiles = async (acceptedFiles) => {
    const pdfs = acceptedFiles.filter(f => f.name.toLowerCase().endsWith('.pdf'))
    if (pdfs.length === 0) return

    const classified = []   // { file, budgetItem }
    const unclassified = [] // File[]

    for (const file of pdfs) {
      const budgetItem = classifyFile(file)
      if (budgetItem) {
        classified.push({ file, budgetItem })
      } else {
        unclassified.push(file)
      }
    }

    // Add slug-matched files to state immediately
    if (classified.length > 0) {
      addClassifiedFiles(classified)
    }

    // Try LLM classification for unmatched files
    if (unclassified.length > 0) {
      setClassifyingFiles(true)
      try {
        const { assignments } = await classifyFilenames(
          unclassified.map(f => f.name)
        )

        const llmClassified = []
        const stillUnclassified = []

        for (const file of unclassified) {
          const budgetItem = assignments[file.name]
          if (budgetItem) {
            llmClassified.push({ file, budgetItem })
          } else {
            stillUnclassified.push(file)
          }
        }

        if (llmClassified.length > 0) {
          addClassifiedFiles(llmClassified)
        }

        if (stillUnclassified.length > 0) {
          setUnassignedFiles(stillUnclassified)
        }
      } catch (err) {
        console.error('LLM classification failed, falling back to manual:', err)
        setUnassignedFiles(unclassified)
      } finally {
        setClassifyingFiles(false)
      }
    }
  }

  const handleModalConfirm = (assignmentMap) => {
    setPdfFiles(prev => {
      const next = { ...prev }
      for (const [file, budgetItem] of assignmentMap) {
        const existing = next[budgetItem] || []
        const existingKeys = new Set(existing.map(f => `${f.name}__${f.size}`))
        if (!existingKeys.has(`${file.name}__${file.size}`)) {
          next[budgetItem] = [...existing, file]
        }
      }
      return next
    })
    setUnassignedFiles(null)
  }

  const handleModalCancel = () => {
    setUnassignedFiles(null)
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

  const handleClearAll = () => {
    setPdfFiles({})
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
      const startResp = await authFetch(`${API_BASE}/api/upload/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pdf_files: pdfFilesPayload,
          job_name: jobName.trim() || undefined,
          csv_filename: csvFile?.name || '',
        }),
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

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: processNewFiles,
    accept: { 'application/pdf': ['.pdf'] },
    multiple: true,
    disabled: uploading || !!unassignedFiles || classifyingFiles,
    noClick: false,
  })

  const totalFiles = Object.values(pdfFiles).reduce((sum, files) => sum + files.length, 0)
  const budgetItemsWithFiles = Object.keys(pdfFiles).filter(k => pdfFiles[k].length > 0)
  const csvReady = !!csvFile
  const canUpload = csvReady && budgetItemsWithFiles.length > 0

  return (
    <div className="upload-page">
      <header className="upload-header">
        <div className="header-content">
          <div className="header-title">
            <h1>Invoice Document Upload</h1>
            <p className="subtitle">Upload CSV Invoice and Supporting PDF Documents</p>
          </div>
          <button
            className={`upload-button ${!canUpload ? 'disabled' : ''} ${uploading ? 'uploading' : ''}`}
            onClick={handleUpload}
            disabled={!canUpload || uploading}
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

      {classifyingFiles && (
        <div className="upload-progress-bar">
          <span>Classifying files with AI...</span>
        </div>
      )}

      <div className="upload-stats">
        <div className="stat">
          <span className="stat-label">CSV File:</span>
          <span className={`stat-value ${csvReady ? 'ready' : ''}`}>
            {csvReady ? 'Ready' : 'Not selected'}
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">PDF Files:</span>
          <span className={`stat-value ${budgetItemsWithFiles.length > 0 ? 'ready' : ''}`}>
            {budgetItemsWithFiles.length} of {BUDGET_ITEMS.length} budget items
            {totalFiles > budgetItemsWithFiles.length && ` (${totalFiles} files)`}
          </span>
        </div>
      </div>

      <main className="upload-main-content">
        <div className="job-name-inline">
          <input
            type="text"
            value={jobName}
            onChange={(e) => setJobName(e.target.value)}
            placeholder="Job Name (Optional)"
            className="job-name-text-input"
            disabled={uploading}
          />
        </div>

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
            <p className="section-description">
              Drop all PDF files here — files are auto-sorted by filename
            </p>
          </div>

          {/* Dropzone */}
          <div
            {...getRootProps()}
            className={`dropzone ${isDragActive ? 'dropzone-active' : ''} ${totalFiles > 0 ? 'dropzone-has-files' : ''}`}
          >
            <input {...getInputProps()} />
            <div className="dropzone-content">
              <p className="dropzone-text">
                {isDragActive
                  ? 'Drop PDF files here'
                  : 'Drag & drop PDF files or folders here, or click to browse'}
              </p>
              <p className="dropzone-hint">
                Files named after budget items (e.g. Salary.pdf, Fringe_Q1.pdf) are auto-classified
              </p>
            </div>
          </div>

          {/* Grouped file summary */}
          {totalFiles > 0 && (
            <div className="file-summary">
              <div className="file-summary-header">
                <span className="file-summary-title">
                  {totalFiles} file{totalFiles !== 1 ? 's' : ''} across{' '}
                  {budgetItemsWithFiles.length} budget item{budgetItemsWithFiles.length !== 1 ? 's' : ''}
                </span>
                <button className="clear-all-btn" onClick={handleClearAll} disabled={uploading}>
                  Clear All
                </button>
              </div>

              {BUDGET_ITEMS.filter((item) => pdfFiles[item]?.length > 0).map((item) => (
                <div key={item} className="file-group">
                  <div className="file-group-header">
                    <span className="file-group-name">{item}</span>
                    <span className="file-count-badge">
                      {pdfFiles[item].length} file{pdfFiles[item].length !== 1 ? 's' : ''}
                    </span>
                  </div>
                  <div className="file-list">
                    {pdfFiles[item].map((file, idx) => (
                      <div key={`${file.name}-${idx}`} className="file-list-item">
                        <span className="file-list-name" title={file.name}>{file.name}</span>
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
                </div>
              ))}
            </div>
          )}
        </section>
      </main>

      {unassignedFiles && (
        <UnassignedFilesModal
          files={unassignedFiles}
          onConfirm={handleModalConfirm}
          onCancel={handleModalCancel}
        />
      )}
    </div>
  )
}

export default UploadPage
