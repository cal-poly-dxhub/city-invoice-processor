import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { PDFDocumentProvider } from './contexts/PDFDocumentContext'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <PDFDocumentProvider>
      <App />
    </PDFDocumentProvider>
  </StrictMode>,
)
