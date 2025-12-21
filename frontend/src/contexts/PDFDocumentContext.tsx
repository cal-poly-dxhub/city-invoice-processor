import { createContext, useContext, useState, useCallback, useEffect, ReactNode } from 'react';
import * as pdfjsLib from 'pdfjs-dist';

// Configure PDF.js worker - using unpkg CDN with specific version
pdfjsLib.GlobalWorkerOptions.workerSrc = `https://unpkg.com/pdfjs-dist@${pdfjsLib.version}/build/pdf.worker.min.mjs`;

interface PDFDocumentContextType {
  pdfDocument: pdfjsLib.PDFDocumentProxy | null;
  loadPDF: (file: File | string) => Promise<void>;
  renderPage: (pageNumber: number, canvasRef: HTMLCanvasElement) => Promise<void>;
  pageCount: number;
  loading: boolean;
  error: string | null;
}

const PDFDocumentContext = createContext<PDFDocumentContextType | undefined>(undefined);

export function usePDFDocument() {
  const context = useContext(PDFDocumentContext);
  if (!context) {
    throw new Error('usePDFDocument must be used within PDFDocumentProvider');
  }
  return context;
}

interface PDFDocumentProviderProps {
  children: ReactNode;
}

export function PDFDocumentProvider({ children }: PDFDocumentProviderProps) {
  const [pdfDocument, setPdfDocument] = useState<pdfjsLib.PDFDocumentProxy | null>(null);
  const [pageCount, setPageCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadPDF = useCallback(async (fileOrUrl: File | string) => {
    try {
      console.log('[PDFContext] Loading PDF:', typeof fileOrUrl === 'string' ? fileOrUrl : fileOrUrl.name);
      setLoading(true);
      setError(null);

      let loadingTask;

      if (typeof fileOrUrl === 'string') {
        // Load from URL
        console.log('[PDFContext] Loading from URL:', fileOrUrl);
        loadingTask = pdfjsLib.getDocument(fileOrUrl);
      } else {
        // Load from File object
        console.log('[PDFContext] Loading from File object');
        const arrayBuffer = await fileOrUrl.arrayBuffer();
        loadingTask = pdfjsLib.getDocument({ data: arrayBuffer });
      }

      const pdf = await loadingTask.promise;
      console.log('[PDFContext] PDF loaded successfully. Pages:', pdf.numPages);
      setPdfDocument(pdf);
      setPageCount(pdf.numPages);
      setError(null);
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load PDF';
      setError(errorMessage);
      console.error('[PDFContext] Error loading PDF:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  const renderPage = useCallback(
    async (pageNumber: number, canvas: HTMLCanvasElement) => {
      if (!pdfDocument) {
        throw new Error('No PDF document loaded');
      }

      if (pageNumber < 1 || pageNumber > pdfDocument.numPages) {
        throw new Error(`Invalid page number: ${pageNumber}`);
      }

      try {
        const page = await pdfDocument.getPage(pageNumber);
        const viewport = page.getViewport({ scale: 1.5 });

        const context = canvas.getContext('2d');
        if (!context) {
          throw new Error('Could not get canvas context');
        }

        canvas.height = viewport.height;
        canvas.width = viewport.width;

        const renderContext = {
          canvasContext: context,
          viewport: viewport,
        };

        await page.render(renderContext).promise;
      } catch (err) {
        console.error(`Error rendering page ${pageNumber}:`, err);
        throw err;
      }
    },
    [pdfDocument]
  );

  const value: PDFDocumentContextType = {
    pdfDocument,
    loadPDF,
    renderPage,
    pageCount,
    loading,
    error,
  };

  return (
    <PDFDocumentContext.Provider value={value}>
      {children}
    </PDFDocumentContext.Provider>
  );
}
