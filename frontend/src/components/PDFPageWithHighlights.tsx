import { useEffect, useRef, useState } from 'react';
import { usePDFDocument } from '../contexts/PDFDocumentContext';
import type { HighlightRect } from '../types/DocumentAnalysis';
import './PDFPageWithHighlights.css';

interface PDFPageWithHighlightsProps {
  pageNumber: number;
  rotation?: number;
  highlights: HighlightRect[];
}

export function PDFPageWithHighlights({
  pageNumber,
  rotation = 0,
  highlights
}: PDFPageWithHighlightsProps) {
  const { pdfDocument } = usePDFDocument();
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const renderTaskRef = useRef<any>(null);
  const [rendering, setRendering] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [canvasSize, setCanvasSize] = useState({ width: 0, height: 0 });
  const [unrotatedSize, setUnrotatedSize] = useState({ width: 0, height: 0 });
  const [, setUpdateTrigger] = useState(0); // Force re-render when canvas changes

  useEffect(() => {
    if (!pdfDocument || !canvasRef.current) {
      return;
    }

    const canvas = canvasRef.current;
    let isCancelled = false;

    async function render() {
      // Cancel any ongoing render task
      if (renderTaskRef.current) {
        console.log('[PDFPageWithHighlights] Cancelling previous render task');
        renderTaskRef.current.cancel();
        renderTaskRef.current = null;
      }

      try {
        setRendering(true);
        setError(null);

        console.log('[PDFPageWithHighlights] Starting render for page', pageNumber, 'with rotation', rotation);
        const page = await pdfDocument.getPage(pageNumber);

        if (isCancelled) {
          console.log('[PDFPageWithHighlights] Render cancelled after getPage');
          return;
        }

        // Get unrotated viewport for highlight coordinates
        const unrotatedViewport = page.getViewport({ scale: 1.5, rotation: 0 });
        setUnrotatedSize({ width: unrotatedViewport.width, height: unrotatedViewport.height });

        // Get rotated viewport for rendering
        const viewport = page.getViewport({ scale: 1.5, rotation });
        const context = canvas.getContext('2d');

        if (!context) {
          throw new Error('Could not get canvas context');
        }

        // Set canvas dimensions
        canvas.height = viewport.height;
        canvas.width = viewport.width;

        // Store canvas size for overlay positioning
        setCanvasSize({ width: viewport.width, height: viewport.height });

        const renderContext = {
          canvasContext: context,
          viewport: viewport,
        };

        // Start rendering and store the task
        renderTaskRef.current = page.render(renderContext);

        await renderTaskRef.current.promise;

        console.log('[PDFPageWithHighlights] Render complete for page', pageNumber);
        renderTaskRef.current = null;

        if (!isCancelled) {
          setRendering(false);
          // Trigger re-render for overlay positioning after canvas is fully rendered
          setTimeout(() => setUpdateTrigger(prev => prev + 1), 100);
        }
      } catch (err: any) {
        if (!isCancelled) {
          // Ignore cancellation errors
          if (err?.name === 'RenderingCancelledException') {
            console.log('[PDFPageWithHighlights] Render was cancelled (expected)');
            setRendering(false);
          } else {
            console.error('[PDFPageWithHighlights] Render error:', err);
            setError(err instanceof Error ? err.message : 'Failed to render page');
            setRendering(false);
          }
        }
        renderTaskRef.current = null;
      }
    }

    render();

    return () => {
      console.log('[PDFPageWithHighlights] Cleanup - cancelling render for page', pageNumber);
      isCancelled = true;
      if (renderTaskRef.current) {
        renderTaskRef.current.cancel();
        renderTaskRef.current = null;
      }
    };
  }, [pdfDocument, pageNumber, rotation]);

  // Calculate overlay to match canvas exactly
  const getOverlayStyle = (): React.CSSProperties => {
    if (!canvasRef.current || !containerRef.current || unrotatedSize.width === 0 || canvasSize.width === 0) {
      return { display: 'none' };
    }

    const canvas = canvasRef.current;
    const parent = canvas.parentElement;

    if (!parent) {
      return { display: 'none' };
    }

    // Get bounding rects to find actual positions
    const canvasRect = canvas.getBoundingClientRect();
    const parentRect = parent.getBoundingClientRect();

    // Calculate canvas position relative to parent
    const canvasLeft = canvasRect.left - parentRect.left;
    const canvasTop = canvasRect.top - parentRect.top;

    // Get actual displayed size of canvas (after CSS scaling)
    const displayWidth = canvasRect.width;
    const displayHeight = canvasRect.height;

    // Calculate scale factor to match displayed size to viewport size
    const scale = displayWidth / canvasSize.width;

    // Overlay uses unrotated dimensions scaled to match display
    const overlayWidth = unrotatedSize.width * scale;
    const overlayHeight = unrotatedSize.height * scale;

    const baseStyle: React.CSSProperties = {
      position: 'absolute',
      width: `${overlayWidth}px`,
      height: `${overlayHeight}px`,
      transformOrigin: 'top left',
      pointerEvents: 'none',
    };

    // Apply rotation and position to match canvas
    switch (rotation) {
      case 90:
        return {
          ...baseStyle,
          transform: 'rotate(90deg)',
          left: `${canvasLeft + overlayHeight}px`,
          top: `${canvasTop}px`,
        };
      case 180:
        return {
          ...baseStyle,
          transform: 'rotate(180deg)',
          left: `${canvasLeft + overlayWidth}px`,
          top: `${canvasTop + overlayHeight}px`,
        };
      case 270:
        return {
          ...baseStyle,
          transform: 'rotate(270deg)',
          left: `${canvasLeft}px`,
          top: `${canvasTop + overlayWidth}px`,
        };
      default:
        return {
          ...baseStyle,
          left: `${canvasLeft}px`,
          top: `${canvasTop}px`,
        };
    }
  };

  return (
    <div className="pdf-page-with-highlights-container" ref={containerRef}>
      <div className="pdf-page">
        {rendering && <div className="pdf-page-loading">Rendering page {pageNumber}...</div>}
        {error && <div className="pdf-page-error">Error: {error}</div>}
        <canvas ref={canvasRef} className="pdf-page-canvas" />
      </div>

      {/* Overlay highlights - rotated to match page orientation */}
      {unrotatedSize.width > 0 && (
        <div
          className="highlights-overlay"
          style={getOverlayStyle()}
        >
          {highlights.map((highlight, index) => (
            <div
              key={index}
              className={`highlight-rect ${highlight.isOrphaned ? 'orphaned' : ''}`}
              style={{
                left: `${highlight.x * 100}%`,
                top: `${highlight.y * 100}%`,
                width: `${highlight.width * 100}%`,
                height: `${highlight.height * 100}%`,
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}
