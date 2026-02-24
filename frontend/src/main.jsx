import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import AuthGuard from './components/AuthGuard'
import App from './App'

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthGuard>
        <Routes>
          <Route path="/*" element={<App />} />
        </Routes>
      </AuthGuard>
    </BrowserRouter>
  </React.StrictMode>
)
