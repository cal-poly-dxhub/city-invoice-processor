import { useEffect, useState, useMemo } from 'react';
import './App.css';
import { loadMockAnalysis } from './api/mockData';
import type { DocumentAnalysis, HighlightRect, Group } from './types/DocumentAnalysis';
import { ReviewScreen } from './components/ReviewScreen';
import { PDFPageWithHighlights } from './components/PDFPageWithHighlights';
import { PageMembershipPanel } from './components/PageMembershipPanel';
import { UnassignedPagesPanel } from './components/UnassignedPagesPanel';
import { usePDFDocument } from './contexts/PDFDocumentContext';
import type { PageEditsState } from './utils/pageEdits';
import {
  getOriginalPagesForGroup,
  getEffectivePagesForGroup,
  getHighlightsByPage,
  isOriginalPage,
  getUnassignedPages,
} from './utils/pageEdits';

function App() {
  // Top-level state
  const [analysis, setAnalysis] = useState<DocumentAnalysis | null>(null);
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [currentGroupId, setCurrentGroupId] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState<number>(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Per-page rotation state (persists across page navigation)
  const [pageRotations, setPageRotations] = useState<Map<number, number>>(new Map());

  // Page membership edits per group (user corrections overlay)
  const [pageEdits, setPageEdits] = useState<PageEditsState>({});

  // User-created groups (groups that don't exist in original DocumentAnalysis)
  const [userCreatedGroups, setUserCreatedGroups] = useState<Group[]>([]);

  // Tab state for right sidebar
  const [activeTab, setActiveTab] = useState<'assigned' | 'unassigned'>('assigned');

  // PDF context
  const { loadPDF, pageCount, loading: pdfLoading, error: pdfError } = usePDFDocument();

  // Combine original groups with user-created groups for a unified view
  const allGroups = useMemo(() => {
    if (!analysis) return userCreatedGroups;
    return [...analysis.groups, ...userCreatedGroups];
  }, [analysis, userCreatedGroups]);

  // Compute unassigned pages
  const unassignedPages = useMemo(() => {
    if (!analysis) return [];
    return getUnassignedPages(allGroups, pageEdits, analysis.pageCount);
  }, [allGroups, pageEdits, analysis]);

  // Load mock data on mount
  useEffect(() => {
    async function loadData() {
      try {
        setLoading(true);
        const data = await loadMockAnalysis();
        setAnalysis(data);
        setError(null);

        // Automatically set currentGroupId to the first group
        if (data.groups.length > 0) {
          setCurrentGroupId(data.groups[0].groupId);
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load data');
        console.error('Error loading analysis:', err);
      } finally {
        setLoading(false);
      }
    }

    loadData();
  }, []);

  // Auto-load sample PDF on mount
  useEffect(() => {
    loadPDF('/sample.pdf');
  }, [loadPDF]);

  // Load PDF when pdfFile changes
  useEffect(() => {
    if (pdfFile) {
      loadPDF(pdfFile);
    }
  }, [pdfFile, loadPDF]);

  // Derive effective pages and highlights for current group
  // Takes into account user edits (addedPages, removedPages)
  const currentGroup = allGroups.find(g => g.groupId === currentGroupId) ?? null;
  const currentGroupEdits = currentGroupId ? pageEdits[currentGroupId] : undefined;

  const effectivePages = currentGroup
    ? getEffectivePagesForGroup(currentGroup, currentGroupEdits, analysis?.pageCount ?? null)
    : [];

  const highlightsByPage = currentGroup
    ? getHighlightsByPage(currentGroup, effectivePages)
    : {};

  const pagesToShow = effectivePages;

  const getHighlightsForPage = (pageNum: number): HighlightRect[] => {
    return highlightsByPage[pageNum] || [];
  };

  // Get current page rotation (default to 0)
  const getCurrentRotation = (): number => {
    return pageRotations.get(currentPage) || 0;
  };

  // Rotate current page by 90 degrees
  const handleRotatePage = () => {
    const currentRotation = getCurrentRotation();
    const newRotation = (currentRotation + 90) % 360;
    setPageRotations(new Map(pageRotations).set(currentPage, newRotation));
  };

  // Handle manual page membership toggle for a group
  const handleTogglePage = (groupId: string, pageNumber: number, shouldInclude: boolean) => {
    setPageEdits((prev) => {
      const prevForGroup = prev[groupId] ?? { addedPages: [], removedPages: [] };
      let { addedPages, removedPages } = prevForGroup;

      // Find the group to check if page is originally included
      const group = allGroups.find((g) => g.groupId === groupId);
      if (!group) return prev;

      const isOriginal = isOriginalPage(group, pageNumber);

      if (shouldInclude) {
        // User wants the page included
        if (isOriginal) {
          // It was originally included; ensure it's NOT in removedPages
          removedPages = removedPages.filter((p) => p !== pageNumber);
        } else {
          // It was not originally included; add to addedPages
          if (!addedPages.includes(pageNumber)) {
            addedPages = [...addedPages, pageNumber];
          }
          // Also ensure it's not marked as removed
          removedPages = removedPages.filter((p) => p !== pageNumber);
        }
      } else {
        // User wants the page excluded
        if (isOriginal) {
          // Add to removedPages
          if (!removedPages.includes(pageNumber)) {
            removedPages = [...removedPages, pageNumber];
          }
          // And ensure it's not in addedPages
          addedPages = addedPages.filter((p) => p !== pageNumber);
        } else {
          // It was only in addedPages; remove it from addedPages
          addedPages = addedPages.filter((p) => p !== pageNumber);
          // No need to add to removedPages
        }
      }

      return {
        ...prev,
        [groupId]: { addedPages, removedPages },
      };
    });
  };

  // Handle assigning an unassigned page to an existing group
  const handleAssignToGroup = (pageNumber: number, groupId: string) => {
    handleTogglePage(groupId, pageNumber, true);
  };

  // Handle creating a new group with selected pages
  const handleCreateNewGroup = (pageNumbers: number[], groupLabel: string) => {
    // Generate a unique group ID
    const newGroupId = `user_group_${Date.now()}`;

    // Create a minimal group structure (no occurrences from model)
    const newGroup: Group = {
      groupId: newGroupId,
      label: groupLabel,
      kind: 'user-created',
      summaryPages: [],
      supportingPages: [],
      occurrences: [], // No model-detected occurrences
      meta: {
        createdByUser: true,
        createdAt: new Date().toISOString(),
      },
    };

    // Add the group to user-created groups
    setUserCreatedGroups((prev) => [...prev, newGroup]);

    // Add the pages to this group via pageEdits
    setPageEdits((prev) => ({
      ...prev,
      [newGroupId]: {
        addedPages: [...pageNumbers],
        removedPages: [],
      },
    }));

    // Switch to the new group
    setCurrentGroupId(newGroupId);
  };

  // Handle jumping to a page from unassigned pages panel
  const handleJumpToPage = (pageNumber: number) => {
    setCurrentPage(pageNumber);
  };

  // When group changes, jump to first page with highlights
  useEffect(() => {
    if (pagesToShow.length > 0) {
      setCurrentPage(pagesToShow[0]);
    }
  }, [currentGroupId]);

  // For testing: allow loading PDF from a path
  // This will be replaced with actual file upload later
  const handleLoadTestPDF = () => {
    // Placeholder - will use the Asociacion Mayab file when available
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = 'application/pdf';
    input.onchange = (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (file) {
        setPdfFile(file);
      }
    };
    input.click();
  };

  if (loading) {
    return (
      <div className="app">
        <div className="loading">Loading document analysis...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="app">
        <div className="error">
          <h2>Error</h2>
          <p>{error}</p>
        </div>
      </div>
    );
  }

  if (!analysis) {
    return (
      <div className="app">
        <div className="error">No data loaded</div>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>Document Analysis Viewer</h1>
        <button onClick={handleLoadTestPDF} className="load-pdf-button">
          {pageCount > 0 ? 'Change PDF' : 'Load PDF'}
        </button>
      </header>

      <main className="app-main">
        <div className="app-layout">
          <div className="pdf-viewer-section">
            {pdfError ? (
              <div className="pdf-error-message">
                <h3>PDF Loading Error</h3>
                <p>{pdfError}</p>
                <p style={{ fontSize: '0.9rem', marginTop: '1rem', color: '#666' }}>
                  Check browser console (F12) for more details
                </p>
              </div>
            ) : pdfLoading ? (
              <div className="no-pdf-message">
                <p>Loading PDF...</p>
              </div>
            ) : pageCount > 0 ? (
              <>
                {currentGroupId && pagesToShow.length > 0 && (
                  <div className="group-info">
                    <div className="group-name">
                      Group: {allGroups.find(g => g.groupId === currentGroupId)?.label || 'Unknown'}
                    </div>
                    <div className="pages-with-highlights">
                      Pages with highlights: {pagesToShow.join(', ')}
                    </div>
                  </div>
                )}
                <div className="pdf-controls">
                  <button
                    onClick={handleRotatePage}
                    className="rotate-button"
                    title="Rotate page 90° clockwise"
                  >
                    ↻ Rotate
                  </button>
                  {currentGroupId && currentGroup && (
                    <>
                      {effectivePages.includes(currentPage) ? (
                        <button
                          onClick={() => handleTogglePage(currentGroupId, currentPage, false)}
                          className="remove-page-button"
                          title="Remove this page from the current group"
                        >
                          − Remove from Group
                        </button>
                      ) : (
                        <button
                          onClick={() => handleTogglePage(currentGroupId, currentPage, true)}
                          className="add-page-button"
                          title="Add this page to the current group"
                        >
                          + Add to Group
                        </button>
                      )}
                    </>
                  )}
                  <button
                    onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                    disabled={currentPage <= 1}
                  >
                    Previous
                  </button>
                  <span className="page-indicator">
                    Page {currentPage} of {pageCount}
                  </span>
                  <button
                    onClick={() => setCurrentPage((p) => Math.min(pageCount, p + 1))}
                    disabled={currentPage >= pageCount}
                  >
                    Next
                  </button>
                  {pagesToShow.length > 0 && (
                    <div className="jump-to-pages">
                      {pagesToShow.map(page => (
                        <button
                          key={page}
                          onClick={() => setCurrentPage(page)}
                          className={currentPage === page ? 'active' : ''}
                        >
                          {page}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                <PDFPageWithHighlights
                  pageNumber={currentPage}
                  rotation={getCurrentRotation()}
                  highlights={getHighlightsForPage(currentPage)}
                />
              </>
            ) : (
              <div className="no-pdf-message">
                <p>No PDF loaded. Click "Load PDF" to select a file.</p>
              </div>
            )}
          </div>

          <div className="review-section">
            {/* All Groups List - Always visible */}
            <div className="groups-list-section">
              <h2>All Groups ({allGroups.length})</h2>
              <ul className="groups-list">
                {allGroups.map((group) => (
                  <li
                    key={group.groupId}
                    className={`group-item ${group.groupId === currentGroupId ? 'active' : ''} ${group.kind === 'user-created' ? 'user-created' : ''} ${group.kind === 'orphaned' ? 'orphaned' : ''}`}
                    onClick={() => setCurrentGroupId(group.groupId)}
                  >
                    <div className="group-item-content">
                      <span className="group-label">
                        {group.label}
                        {group.kind === 'user-created' && <span className="user-badge">★</span>}
                        {group.kind === 'orphaned' && <span className="orphaned-badge">⚠</span>}
                      </span>
                      <span className="group-meta">
                        {group.occurrences.length} occurrences
                      </span>
                    </div>
                  </li>
                ))}
              </ul>
            </div>

            {/* Tabbed interface for page management */}
            <div className="page-management-tabs">
              <div className="tab-buttons">
                <button
                  className={`tab-button ${activeTab === 'assigned' ? 'active' : ''}`}
                  onClick={() => setActiveTab('assigned')}
                >
                  Assigned Pages
                </button>
                <button
                  className={`tab-button ${activeTab === 'unassigned' ? 'active' : ''}`}
                  onClick={() => setActiveTab('unassigned')}
                >
                  Unassigned Pages {unassignedPages.length > 0 && `(${unassignedPages.length})`}
                </button>
              </div>

              <div className="tab-content">
                {activeTab === 'assigned' && (
                  <>
                    {currentGroup ? (
                      <PageMembershipPanel
                        pageCount={analysis.pageCount}
                        group={currentGroup}
                        groupEdits={currentGroupEdits}
                        onTogglePage={handleTogglePage}
                      />
                    ) : (
                      <div className="no-group-message">
                        Select a group to manage assigned pages
                      </div>
                    )}
                  </>
                )}

                {activeTab === 'unassigned' && (
                  <UnassignedPagesPanel
                    unassignedPages={unassignedPages}
                    groups={allGroups}
                    onAssignToGroup={handleAssignToGroup}
                    onCreateNewGroup={handleCreateNewGroup}
                    onJumpToPage={handleJumpToPage}
                  />
                )}
              </div>
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;
