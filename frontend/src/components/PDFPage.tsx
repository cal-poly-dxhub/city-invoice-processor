import { useEffect, useRef, useState } from 'react';
import { usePDFDocument } from '../contexts/PDFDocumentContext';
import './PDFPage.css';

interface PDFPageProps {
  pageNumber: number;
  rotation?: number; // 0, 90, 180, or 270 degrees
}

export function PDFPage({ pageNumber, rotation = 0 }: PDFPageProps) {
  const { pdfDocument } = usePDFDocument();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const renderTaskRef = useRef<any>(null);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!pdfDocument || !canvasRef.current) {
      return;
    }

    const canvas = canvasRef.current;
    let isCancelled = false;

    async function render() {
      // Cancel any ongoing render task
      if (renderTaskRef.current) {
        console.log('[PDFPage] Cancelling previous render task');
        renderTaskRef.current.cancel();
        renderTaskRef.current = null;
      }

      try {
        setRendering(true);
        setError(null);

        console.log('[PDFPage] Starting render for page', pageNumber, 'with rotation', rotation);
        const page = await pdfDocument.getPage(pageNumber);

        if (isCancelled) {
          console.log('[PDFPage] Render cancelled after getPage');
          return;
        }

        const viewport = page.getViewport({ scale: 1.5, rotation });
        const context = canvas.getContext('2d');

        if (!context) {
          throw new Error('Could not get canvas context');
        }

        // Set canvas dimensions
        canvas.height = viewport.height;
        canvas.width = viewport.width;

        const renderContext = {
          canvasContext: context,
          viewport: viewport,
        };

        // Start rendering and store the task
        renderTaskRef.current = page.render(renderContext);

        await renderTaskRef.current.promise;

        console.log('[PDFPage] Render complete for page', pageNumber);
        renderTaskRef.current = null;

        if (!isCancelled) {
          setRendering(false);
        }
      } catch (err: any) {
        if (!isCancelled) {
          // Ignore cancellation errors
          if (err?.name === 'RenderingCancelledException') {
            console.log('[PDFPage] Render was cancelled (expected)');
            setRendering(false);
          } else {
            console.error('[PDFPage] Render error:', err);
            setError(err instanceof Error ? err.message : 'Failed to render page');
            setRendering(false);
          }
        }
        renderTaskRef.current = null;
      }
    }

    render();

    return () => {
      console.log('[PDFPage] Cleanup - cancelling render for page', pageNumber);
      isCancelled = true;
      if (renderTaskRef.current) {
        renderTaskRef.current.cancel();
        renderTaskRef.current = null;
      }
    };
  }, [pdfDocument, pageNumber, rotation]);

  return (
    <div className="pdf-page">
      {rendering && <div className="pdf-page-loading">Rendering page {pageNumber}...</div>}
      {error && <div className="pdf-page-error">Error: {error}</div>}
      <canvas ref={canvasRef} className="pdf-page-canvas" />
    </div>
  );
}
