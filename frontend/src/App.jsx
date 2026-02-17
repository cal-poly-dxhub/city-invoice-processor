import { Routes, Route, Link, useLocation } from 'react-router-dom'
import UploadPage from './pages/UploadPage'
import ReviewPage from './pages/ReviewPage'
import JobHistoryPage from './pages/JobHistoryPage'
import './App.css'

function App() {
  const location = useLocation()
  const path = location.pathname

  const isUpload = path === '/' || path === ''
  const isJobs = path === '/jobs'
  const isReview = path.startsWith('/review/')

  return (
    <div className="app-root">
      <nav className="global-nav">
        <div className="nav-content">
          <span className="nav-brand">Invoice Processor</span>
          <div className="nav-links">
            <Link
              to="/"
              className={`nav-link ${isUpload ? 'active' : ''}`}
            >
              Upload
            </Link>
            <Link
              to="/jobs"
              className={`nav-link ${isJobs || isReview ? 'active' : ''}`}
            >
              Jobs
            </Link>
          </div>
        </div>
      </nav>

      <Routes>
        <Route path="/" element={<UploadPage />} />
        <Route path="/jobs" element={<JobHistoryPage />} />
        <Route path="/review/:jobId" element={<ReviewPage />} />
      </Routes>
    </div>
  )
}

export default App
