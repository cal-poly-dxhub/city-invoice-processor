import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { API_BASE } from '../config'
import './JobHistoryPage.css'

function JobHistoryPage() {
  const [jobs, setJobs] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [editingJobId, setEditingJobId] = useState(null)
  const [editName, setEditName] = useState('')

  useEffect(() => {
    fetchJobs()
  }, [])

  const fetchJobs = async () => {
    try {
      setLoading(true)
      setError(null)
      const resp = await fetch(`${API_BASE}/api/jobs`)
      if (!resp.ok) throw new Error(`Failed to load jobs: ${resp.status}`)
      const data = await resp.json()
      setJobs(data.jobs)
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const handleRename = async (jobId) => {
    if (!editName.trim()) return
    try {
      const resp = await fetch(`${API_BASE}/api/jobs/${jobId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ job_name: editName.trim() }),
      })
      if (!resp.ok) throw new Error('Failed to rename job')

      setJobs(prev => prev.map(j =>
        j.job_id === jobId ? { ...j, job_name: editName.trim() } : j
      ))
      setEditingJobId(null)
      setEditName('')
    } catch (err) {
      alert(err.message)
    }
  }

  const startEditing = (job) => {
    setEditingJobId(job.job_id)
    setEditName(job.job_name)
  }

  const cancelEditing = () => {
    setEditingJobId(null)
    setEditName('')
  }

  const formatDate = (isoString) => {
    if (!isoString) return ''
    const d = new Date(isoString)
    return d.toLocaleDateString('en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const getStatusLabel = (status) => {
    switch (status) {
      case 'SUCCEEDED': return 'Succeeded'
      case 'PROCESSING': return 'Processing'
      case 'UPLOADING': return 'Uploading'
      case 'FAILED': return 'Failed'
      default: return status || 'Unknown'
    }
  }

  const getStatusClass = (status) => {
    switch (status) {
      case 'SUCCEEDED': return 'status-succeeded'
      case 'PROCESSING': return 'status-processing'
      case 'UPLOADING': return 'status-uploading'
      case 'FAILED': return 'status-failed'
      default: return 'status-unknown'
    }
  }

  if (loading) {
    return (
      <div className="jobs-page">
        <header className="jobs-header">
          <div className="header-content">
            <div className="header-title">
              <h1>Job History</h1>
              <p className="subtitle">Previous Reconciliation Runs</p>
            </div>
          </div>
        </header>
        <div className="jobs-loading">
          <div className="loading-spinner"></div>
        </div>
      </div>
    )
  }

  return (
    <div className="jobs-page">
      <header className="jobs-header">
        <div className="header-content">
          <div className="header-title">
            <h1>Job History</h1>
            <p className="subtitle">Previous Reconciliation Runs</p>
          </div>
          <div className="header-actions">
            <button className="refresh-btn" onClick={fetchJobs} title="Refresh">
              Refresh
            </button>
            <Link to="/" className="new-job-btn">New Upload</Link>
          </div>
        </div>
      </header>

      {error && (
        <div className="jobs-error-bar">
          <span>Error: {error}</span>
          <button onClick={fetchJobs} className="retry-link">Retry</button>
        </div>
      )}

      <main className="jobs-main-content">
        {jobs.length === 0 && !error ? (
          <div className="jobs-empty">
            <h2>No Jobs Yet</h2>
            <p>Upload an invoice CSV and supporting PDFs to get started.</p>
            <Link to="/" className="empty-upload-link">Go to Upload</Link>
          </div>
        ) : (
          <div className="jobs-list">
            <div className="jobs-table-header">
              <span className="col-name">Name</span>
              <span className="col-date">Date</span>
              <span className="col-files">Files</span>
              <span className="col-status">Status</span>
              <span className="col-action"></span>
            </div>
            {jobs.map(job => (
              <div key={job.job_id} className="job-row">
                <div className="col-name">
                  {editingJobId === job.job_id ? (
                    <div className="job-name-edit">
                      <input
                        type="text"
                        value={editName}
                        onChange={(e) => setEditName(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') handleRename(job.job_id)
                          if (e.key === 'Escape') cancelEditing()
                        }}
                        className="job-name-input"
                        autoFocus
                      />
                      <button onClick={() => handleRename(job.job_id)} className="save-btn">Save</button>
                      <button onClick={cancelEditing} className="cancel-btn">Cancel</button>
                    </div>
                  ) : (
                    <div className="job-name-display">
                      <span className="job-name">{job.job_name}</span>
                      <button
                        onClick={() => startEditing(job)}
                        className="rename-btn"
                        title="Rename job"
                      >
                        Rename
                      </button>
                    </div>
                  )}
                  {job.csv_filename && (
                    <span className="job-csv-name">{job.csv_filename}</span>
                  )}
                </div>
                <div className="col-date">
                  <span className="job-date">{formatDate(job.created_at)}</span>
                </div>
                <div className="col-files">
                  {job.pdf_count > 0 && (
                    <span className="job-file-count">
                      {job.pdf_count} PDF{job.pdf_count !== 1 ? 's' : ''}
                    </span>
                  )}
                  {job.budget_items && job.budget_items.length > 0 && (
                    <div className="job-budget-tags">
                      {job.budget_items.slice(0, 4).map(bi => (
                        <span key={bi} className="budget-tag">{bi}</span>
                      ))}
                      {job.budget_items.length > 4 && (
                        <span className="budget-tag budget-tag-more">
                          +{job.budget_items.length - 4}
                        </span>
                      )}
                    </div>
                  )}
                </div>
                <div className="col-status">
                  <span className={`job-status-badge ${getStatusClass(job.status)}`}>
                    {getStatusLabel(job.status)}
                  </span>
                </div>
                <div className="col-action">
                  <Link to={`/review/${job.job_id}`} className="view-btn">
                    {job.status === 'SUCCEEDED' ? 'Review' : 'View'}
                  </Link>
                </div>
              </div>
            ))}
          </div>
        )}
      </main>
    </div>
  )
}

export default JobHistoryPage
