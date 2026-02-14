import { Routes, Route, Link, useLocation } from 'react-router-dom'
import UploadPage from './pages/UploadPage'
import ReviewPage from './pages/ReviewPage'
import './App.css'

function App() {
  const location = useLocation()
  const isUpload = location.pathname === '/' || location.pathname === ''

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
              to="/review/my_job"
              className={`nav-link ${!isUpload ? 'active' : ''}`}
            >
              Review
            </Link>
          </div>
        </div>
      </nav>

      <Routes>
        <Route path="/" element={<UploadPage />} />
        <Route path="/review/:jobId" element={<ReviewPage />} />
      </Routes>
    </div>
  )
}

export default App
