import { useState } from 'react'
import './App.css'

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

function App() {
  const [csvFile, setCsvFile] = useState(null)
  const [pdfFiles, setPdfFiles] = useState({})
  const [uploading, setUploading] = useState(false)
  const [uploadSuccess, setUploadSuccess] = useState(false)

  const handleCsvChange = (e) => {
    const file = e.target.files[0]
    if (file && file.name.endsWith('.csv')) {
      setCsvFile(file)
    } else {
      alert('Please select a CSV file')
      e.target.value = ''
    }
  }

  const handlePdfChange = (budgetItem, e) => {
    const file = e.target.files[0]
    if (file && file.name.endsWith('.pdf')) {
      setPdfFiles(prev => ({
        ...prev,
        [budgetItem]: file
      }))
    } else {
      alert('Please select a PDF file')
      e.target.value = ''
    }
  }

  const handleUpload = async () => {
    if (!csvFile) {
      alert('Please select a CSV file before uploading')
      return
    }

    const pdfCount = Object.keys(pdfFiles).length
    if (pdfCount === 0) {
      alert('Please select at least one PDF file before uploading')
      return
    }

    setUploading(true)

    // Simulate upload delay
    await new Promise(resolve => setTimeout(resolve, 2000))

    setUploading(false)
    setUploadSuccess(true)

    // Reset success message after 3 seconds
    setTimeout(() => {
      setUploadSuccess(false)
    }, 3000)

    console.log('Upload would send:', {
      csv: csvFile.name,
      pdfs: Object.entries(pdfFiles).map(([item, file]) => ({
        budgetItem: item,
        filename: file.name
      }))
    })
  }

  const getUploadStats = () => {
    const pdfCount = Object.keys(pdfFiles).length
    return {
      csvReady: !!csvFile,
      pdfCount,
      totalPdfs: BUDGET_ITEMS.length,
      canUpload: csvFile && pdfCount > 0
    }
  }

  const stats = getUploadStats()

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-content">
          <div className="header-title">
            <h1>Invoice Document Upload</h1>
            <p className="subtitle">Upload CSV Invoice and Supporting PDF Documents</p>
          </div>
          <button
            className={`upload-button ${!stats.canUpload ? 'disabled' : ''} ${uploading ? 'uploading' : ''} ${uploadSuccess ? 'success' : ''}`}
            onClick={handleUpload}
            disabled={!stats.canUpload || uploading}
          >
            {uploading ? 'Uploading...' : uploadSuccess ? 'Success!' : 'Upload'}
          </button>
        </div>
      </header>

      <div className="upload-stats">
        <div className="stat">
          <span className="stat-label">CSV File:</span>
          <span className={`stat-value ${stats.csvReady ? 'ready' : ''}`}>
            {stats.csvReady ? '✓ Ready' : 'Not selected'}
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">PDF Files:</span>
          <span className={`stat-value ${stats.pdfCount > 0 ? 'ready' : ''}`}>
            {stats.pdfCount} of {stats.totalPdfs}
          </span>
        </div>
      </div>

      <main className="main-content">
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
            {BUDGET_ITEMS.map((item) => (
              <div key={item} className={`file-input-card ${pdfFiles[item] ? 'has-file' : ''}`}>
                <div className="budget-item-label">{item}</div>
                <div className="file-input-wrapper">
                  <input
                    type="file"
                    id={`pdf-${item}`}
                    accept=".pdf"
                    onChange={(e) => handlePdfChange(item, e)}
                    className="file-input"
                  />
                  <label htmlFor={`pdf-${item}`} className="file-input-label">
                    <span className="file-input-button">
                      {pdfFiles[item] ? 'Change' : 'Select PDF'}
                    </span>
                    <span className="file-input-text">
                      {pdfFiles[item] ? pdfFiles[item].name : 'No file selected'}
                    </span>
                  </label>
                </div>
              </div>
            ))}
          </div>
        </section>
      </main>
    </div>
  )
}

export default App
