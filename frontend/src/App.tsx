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

// Simplified data structure for reconciliation (passed to next LLM stage)
interface ReconciledGroupings {
  pdfName: string;
  groups: Array<{
    label: string;
    pages: Array<{
      page: number;
      type: 'summary' | 'supporting';
    }>;
  }>;
  unassignedPages?: number[];  // pages not assigned to any group
}

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

  // Page types - global per-page setting (default: 'supporting')
  const [pageTypes, setPageTypes] = useState<Map<number, 'summary' | 'supporting'>>(new Map());

  // Page membership edits per group (user corrections overlay)
  const [pageEdits, setPageEdits] = useState<PageEditsState>({});

  // User-created groups (groups that don't exist in original DocumentAnalysis)
  const [userCreatedGroups, setUserCreatedGroups] = useState<Group[]>([]);

  // Group management state
  const [renamedGroups, setRenamedGroups] = useState<Map<string, string>>(new Map());
  const [deletedGroups, setDeletedGroups] = useState<Set<string>>(new Set());
  const [editingGroupId, setEditingGroupId] = useState<string | null>(null);
  const [combiningGroupId, setCombiningGroupId] = useState<string | null>(null);

  // Tab state for right sidebar
  const [activeTab, setActiveTab] = useState<'assigned' | 'unassigned'>('assigned');

  // PDF context
  const { loadPDF, pageCount, loading: pdfLoading, error: pdfError } = usePDFDocument();

  // Combine original groups with user-created groups, filter deleted, apply renames
  const allGroups = useMemo(() => {
    const baseGroups = analysis ? [...analysis.groups, ...userCreatedGroups] : userCreatedGroups;

    // Filter out deleted groups and apply renames
    return baseGroups
      .filter(g => !deletedGroups.has(g.groupId))
      .map(g => {
        const renamedLabel = renamedGroups.get(g.groupId);
        return renamedLabel ? { ...g, label: renamedLabel } : g;
      });
  }, [analysis, userCreatedGroups, deletedGroups, renamedGroups]);

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

  // Get adjusted occurrence count for a group (taking user edits into account)
  const getAdjustedOccurrenceCount = (group: Group): number => {
    const edits = pageEdits[group.groupId];
    if (!edits || edits.removedPages.length === 0) {
      // No edits or no removals, return original count
      return group.occurrences.length;
    }

    // Filter out occurrences on removed pages
    const activeOccurrences = group.occurrences.filter(
      (occ) => !edits.removedPages.includes(occ.pageNumber)
    );
    return activeOccurrences.length;
  };

  // Get current page rotation (default to 0)
  const getCurrentRotation = (): number => {
    return pageRotations.get(currentPage) || 0;
  };

  // Rotate current page by 90 degrees (positive = clockwise, negative = counter-clockwise)
  const handleRotatePage = (degrees: number) => {
    const currentRotation = getCurrentRotation();
    const newRotation = (currentRotation + degrees + 360) % 360;
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

  // Handle changing page type (summary or supporting)
  const handlePageTypeChange = (pageNumber: number, type: 'summary' | 'supporting') => {
    setPageTypes(prev => {
      const newMap = new Map(prev);
      newMap.set(pageNumber, type);
      return newMap;
    });
  };

  // Get page type (default to 'supporting' if not set)
  const getPageType = (pageNumber: number): 'summary' | 'supporting' => {
    return pageTypes.get(pageNumber) ?? 'supporting';
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

  // Handle renaming a group
  const handleRenameGroup = (groupId: string, newLabel: string) => {
    setRenamedGroups(new Map(renamedGroups).set(groupId, newLabel));
    setEditingGroupId(null);
  };

  // Handle deleting a group
  const handleDeleteGroup = (groupId: string) => {
    if (window.confirm('Are you sure you want to delete this group? Pages will become unassigned.')) {
      // Mark group as deleted
      const newDeleted = new Set(deletedGroups);
      newDeleted.add(groupId);
      setDeletedGroups(newDeleted);

      // Remove page edits for this group (pages will become unassigned)
      setPageEdits((prev) => {
        const newEdits = { ...prev };
        delete newEdits[groupId];
        return newEdits;
      });

      // If this was the current group, switch to first available group
      if (currentGroupId === groupId) {
        const remainingGroups = allGroups.filter(g => g.groupId !== groupId);
        setCurrentGroupId(remainingGroups.length > 0 ? remainingGroups[0].groupId : null);
      }
    }
  };

  // Handle combining two groups (merge sourceGroupId into targetGroupId)
  const handleCombineGroups = (sourceGroupId: string, targetGroupId: string) => {
    // Get all pages from source group
    const sourceGroup = allGroups.find(g => g.groupId === sourceGroupId);
    if (!sourceGroup) return;

    const sourceEdits = pageEdits[sourceGroupId];
    const sourcePages = getEffectivePagesForGroup(sourceGroup, sourceEdits, analysis?.pageCount ?? null);

    // Add all source pages to target group
    setPageEdits((prev) => {
      const targetEdits = prev[targetGroupId] ?? { addedPages: [], removedPages: [] };
      const newAddedPages = [...new Set([...targetEdits.addedPages, ...sourcePages])];

      return {
        ...prev,
        [targetGroupId]: {
          addedPages: newAddedPages,
          removedPages: targetEdits.removedPages,
        },
      };
    });

    // Delete the source group
    const newDeleted = new Set(deletedGroups);
    newDeleted.add(sourceGroupId);
    setDeletedGroups(newDeleted);

    // Switch to target group
    setCurrentGroupId(targetGroupId);
  };

  // Compute reconciled groupings (simplified for LLM consumption)
  const computeReconciledGroupings = (): ReconciledGroupings => {
    const groups = allGroups.map(group => {
      const pageNumbers = getEffectivePagesForGroup(group, pageEdits[group.groupId], analysis?.pageCount ?? null);
      const pagesWithTypes = pageNumbers
        .sort((a, b) => a - b)  // sorted ascending
        .map(pageNum => ({
          page: pageNum,
          type: getPageType(pageNum),
        }));

      return {
        label: group.label,
        pages: pagesWithTypes,
      };
    });

    const result: ReconciledGroupings = {
      pdfName: pdfFile?.name || 'sample.pdf',
      groups,
    };

    // Add unassigned pages as a separate field if there are any
    if (unassignedPages.length > 0) {
      result.unassignedPages = [...unassignedPages].sort((a, b) => a - b);
    }

    return result;
  };

  // Handle reconcile action - download JSON with corrected groupings
  const handleReconcile = () => {
    const reconciled = computeReconciledGroupings();

    // Create JSON blob and download
    const jsonString = JSON.stringify(reconciled, null, 2);
    const blob = new Blob([jsonString], { type: 'application/json' });
    const url = URL.createObjectURL(blob);

    // Create temporary download link
    const link = document.createElement('a');
    link.href = url;
    link.download = `${reconciled.pdfName.replace('.pdf', '')}-reconciled.json`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);

    // Clean up
    URL.revokeObjectURL(url);

    console.log('Reconciled groupings:', reconciled);
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
        <div className="header-buttons">
          <button onClick={handleLoadTestPDF} className="load-pdf-button">
            {pageCount > 0 ? 'Change PDF' : 'Load PDF'}
          </button>
          <button
            className="reconcile-button"
            onClick={handleReconcile}
            title="Export corrected groupings for reconciliation"
          >
            📊 Reconcile
          </button>
        </div>
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
                  <div className="rotate-buttons">
                    <button
                      onClick={() => handleRotatePage(-90)}
                      className="rotate-button rotate-ccw"
                      title="Rotate 90° counter-clockwise"
                    >
                      ↺
                    </button>
                    <button
                      onClick={() => handleRotatePage(90)}
                      className="rotate-button rotate-cw"
                      title="Rotate 90° clockwise"
                    >
                      ↻
                    </button>
                  </div>
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
                  <div className="page-type-selector-container">
                    <label className="page-type-label">Page Type:</label>
                    <select
                      className="page-type-selector-topbar"
                      value={getPageType(currentPage)}
                      onChange={(e) => handlePageTypeChange(currentPage, e.target.value as 'summary' | 'supporting')}
                      title="Set page type for reconciliation"
                    >
                      <option value="supporting">Supporting</option>
                      <option value="summary">Summary</option>
                    </select>
                  </div>
                  <button
                    onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                    disabled={currentPage <= 1}
                    className="nav-arrow"
                  >
                    ←
                  </button>
                  <span className="page-indicator">
                    {currentPage}/{pageCount}
                  </span>
                  <button
                    onClick={() => setCurrentPage((p) => Math.min(pageCount, p + 1))}
                    disabled={currentPage >= pageCount}
                    className="nav-arrow"
                  >
                    →
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
                    className={`group-item ${group.groupId === currentGroupId ? 'active' : ''} ${group.kind === 'user-created' ? 'user-created' : ''}`}
                  >
                    <div className="group-item-content" onClick={() => setCurrentGroupId(group.groupId)}>
                      {editingGroupId === group.groupId ? (
                        <input
                          type="text"
                          className="group-rename-input"
                          defaultValue={group.label}
                          autoFocus
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              handleRenameGroup(group.groupId, e.currentTarget.value);
                            } else if (e.key === 'Escape') {
                              setEditingGroupId(null);
                            }
                          }}
                          onBlur={(e) => {
                            if (e.currentTarget.value.trim()) {
                              handleRenameGroup(group.groupId, e.currentTarget.value);
                            } else {
                              setEditingGroupId(null);
                            }
                          }}
                          onClick={(e) => e.stopPropagation()}
                        />
                      ) : (
                        <>
                          <span className="group-label">
                            {group.label}
                            {group.kind === 'user-created' && <span className="user-badge">★</span>}
                          </span>
                          <span className="group-meta">
                            {getAdjustedOccurrenceCount(group)} occurrences
                          </span>
                        </>
                      )}
                    </div>
                    <div className="group-actions" onClick={(e) => e.stopPropagation()}>
                      <button
                        className="group-action-btn"
                        title="Rename group"
                        onClick={() => setEditingGroupId(group.groupId)}
                      >
                        ✏️
                      </button>
                      <button
                        className="group-action-btn"
                        title="Combine with another group"
                        onClick={() => setCombiningGroupId(group.groupId)}
                      >
                        🔗
                      </button>
                      <button
                        className="group-action-btn delete"
                        title="Delete group"
                        onClick={() => handleDeleteGroup(group.groupId)}
                      >
                        🗑️
                      </button>
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
                        getPageType={getPageType}
                        onPageTypeChange={handlePageTypeChange}
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

      {/* Combine Groups Modal */}
      {combiningGroupId && (
        <div className="modal-overlay" onClick={() => setCombiningGroupId(null)}>
          <div className="modal-content" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Combine "{allGroups.find(g => g.groupId === combiningGroupId)?.label}" into:</h3>
              <button
                className="modal-close-btn"
                onClick={() => setCombiningGroupId(null)}
              >
                ✕
              </button>
            </div>
            <div className="modal-body">
              <div className="combine-options-list">
                {allGroups
                  .filter(g => g.groupId !== combiningGroupId)
                  .map(targetGroup => (
                    <button
                      key={targetGroup.groupId}
                      className="combine-option"
                      onClick={() => {
                        handleCombineGroups(combiningGroupId, targetGroup.groupId);
                        setCombiningGroupId(null);
                      }}
                    >
                      <span className="combine-option-label">{targetGroup.label}</span>
                      <span className="combine-option-meta">
                        {getEffectivePagesForGroup(targetGroup, pageEdits[targetGroup.groupId], analysis?.pageCount ?? null).length} pages
                      </span>
                    </button>
                  ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export default App;
