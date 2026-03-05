import { useState, useEffect, useRef, useCallback } from "react";
import { useParams } from "react-router-dom";
import { API_BASE, DATA_BASE } from "../config";
import { authFetch } from "../services/authenticatedFetch";
import LineItemCard from "../components/LineItemCard";
import PDFViewer from "../components/PDFViewer";
import FilterBar from "../components/FilterBar";
import "./ReviewPage.css";

const POLL_INTERVAL_MS = 5000;
const SAVE_DEBOUNCE_MS = 1000;

function ReviewPage() {
  const { jobId } = useParams();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [jobStatus, setJobStatus] = useState(null); // RUNNING | SUCCEEDED | FAILED | NOT_FOUND | null
  const pollRef = useRef(null);

  const [selectedBudgetItem, setSelectedBudgetItem] = useState("all");
  const [selectedMatchType, setSelectedMatchType] = useState("low-confidence");
  const [selectedItem, setSelectedItem] = useState(null);
  const [verificationMode, setVerificationMode] =
    useState("needs-verification");
  const [showSummary, setShowSummary] = useState(false);
  const [minConfidenceScore, setMinConfidenceScore] = useState(0.5);

  // User edits state (lifted from PDFViewer for persistence)
  const [userEditedCandidates, setUserEditedCandidates] = useState({});
  const [userAnnotations, setUserAnnotations] = useState({});
  const [subItems, setSubItems] = useState({}); // { parentRowId: SubItem[] }
  const [completionStatus, setCompletionStatus] = useState({}); // { rowId: { payment: bool, invoice: bool } }
  const editsLoadedRef = useRef(false);
  const saveTimerRef = useRef(null);

  useEffect(() => {
    checkJobAndLoad();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    };
  }, [jobId]);

  const checkJobStatus = async () => {
    try {
      const resp = await authFetch(`${API_BASE}/api/jobs/${jobId}/status`);
      if (!resp.ok) return null;
      const json = await resp.json();
      return json.status;
    } catch {
      return null;
    }
  };

  const checkJobAndLoad = async () => {
    setLoading(true);
    setError(null);

    // First try loading the data directly — it may already exist
    const loaded = await tryLoadReconciliation();
    if (loaded) return;

    // Data not ready yet — check job status to decide what to show
    const status = await checkJobStatus();
    setJobStatus(status);

    if (status === "RUNNING") {
      // Start polling
      setLoading(false);
      pollRef.current = setInterval(async () => {
        const s = await checkJobStatus();
        setJobStatus(s);
        if (s !== "RUNNING") {
          clearInterval(pollRef.current);
          pollRef.current = null;
          if (s === "SUCCEEDED") {
            await tryLoadReconciliation();
          } else {
            setError(`Processing failed with status: ${s}`);
          }
        }
      }, POLL_INTERVAL_MS);
    } else if (status === "SUCCEEDED") {
      // Pipeline done but first fetch failed — retry once
      const retried = await tryLoadReconciliation();
      if (!retried) {
        setError("Processing completed but reconciliation data could not be loaded.");
        setLoading(false);
      }
    } else {
      setError(`Job status: ${status || "unknown"}. The processing pipeline may not have started.`);
      setLoading(false);
    }
  };

  const tryLoadReconciliation = async () => {
    try {
      setLoading(true);
      setError(null);
      const response = await fetch(
        `${DATA_BASE}/jobs/${jobId}/reconciliation.json`,
      );
      if (!response.ok) {
        setLoading(false);
        return false;
      }
      const json = await response.json();
      setData(json);
      setJobStatus("SUCCEEDED");

      // Load saved user edits
      await loadUserEdits();

      setLoading(false);
      return true;
    } catch {
      setLoading(false);
      return false;
    }
  };

  const loadUserEdits = async () => {
    try {
      const resp = await authFetch(`${API_BASE}/api/jobs/${jobId}/edits`);
      if (!resp.ok) return;
      const edits = await resp.json();
      if (edits.edited_candidates) setUserEditedCandidates(edits.edited_candidates);
      if (edits.annotations) setUserAnnotations(edits.annotations);
      if (edits.sub_items?.length > 0) {
        const grouped = {};
        for (const si of edits.sub_items) {
          if (!grouped[si.parent_row_id]) grouped[si.parent_row_id] = [];
          grouped[si.parent_row_id].push(si);
        }
        setSubItems(grouped);
      }
      if (edits.completion_status) setCompletionStatus(edits.completion_status);
    } catch {
      // Edits not available — start fresh
    } finally {
      editsLoadedRef.current = true;
    }
  };

  const saveUserEdits = useCallback((candidates, annotations, subs, completion) => {
    if (!editsLoadedRef.current) return;

    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
      try {
        const allSubItems = Object.values(subs).flat();
        await authFetch(`${API_BASE}/api/jobs/${jobId}/edits`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            edited_candidates: candidates,
            annotations: annotations,
            sub_items: allSubItems,
            completion_status: completion,
          }),
        });
      } catch {
        // Silent fail — edits will be retried on next change
      }
    }, SAVE_DEBOUNCE_MS);
  }, [jobId]);

  // Auto-save when edits change
  useEffect(() => {
    saveUserEdits(userEditedCandidates, userAnnotations, subItems, completionStatus);
  }, [userEditedCandidates, userAnnotations, subItems, completionStatus, saveUserEdits]);

  const addSubItem = useCallback((subItem) => {
    setSubItems((prev) => {
      const parentList = prev[subItem.parent_row_id] || [];
      return { ...prev, [subItem.parent_row_id]: [...parentList, subItem] };
    });
  }, []);

  const addSubItems = useCallback((parentRowId, newItems) => {
    setSubItems((prev) => {
      const parentList = prev[parentRowId] || [];
      return { ...prev, [parentRowId]: [...parentList, ...newItems] };
    });
  }, []);

  const removeSubItem = useCallback((parentRowId, subItemId) => {
    setSubItems((prev) => {
      const parentList = (prev[parentRowId] || []).filter(
        (si) => si.sub_item_id !== subItemId
      );
      const updated = { ...prev, [parentRowId]: parentList };
      if (parentList.length === 0) delete updated[parentRowId];
      return updated;
    });
    setCompletionStatus(prev => {
      const next = { ...prev };
      delete next[subItemId];
      return next;
    });
  }, []);

  const togglePageEvidence = useCallback((rowId, field, pageNum, docId) => {
    setCompletionStatus(prev => {
      const current = prev[rowId] || { payment: [], invoice: [] };
      const arr = Array.isArray(current[field]) ? current[field] : [];
      const existingIdx = arr.findIndex(e => e.page === pageNum && e.doc_id === docId);
      const updated = existingIdx >= 0
        ? arr.filter((_, i) => i !== existingIdx)
        : [...arr, { page: pageNum, doc_id: docId }];
      return { ...prev, [rowId]: { ...current, [field]: updated } };
    });
  }, []);

  const getLineItemCompletionStatus = useCallback((rowId) => {
    const subs = subItems[rowId] || [];
    if (subs.length === 0) {
      const status = completionStatus[rowId] || { payment: [], invoice: [] };
      const payArr = Array.isArray(status.payment) ? status.payment : [];
      const invArr = Array.isArray(status.invoice) ? status.invoice : [];
      return { payment: payArr.length > 0, invoice: invArr.length > 0 };
    }
    const allPayment = subs.every(si => {
      const s = completionStatus[si.sub_item_id] || { payment: [], invoice: [] };
      return Array.isArray(s.payment) && s.payment.length > 0;
    });
    const allInvoice = subs.every(si => {
      const s = completionStatus[si.sub_item_id] || { payment: [], invoice: [] };
      return Array.isArray(s.invoice) && s.invoice.length > 0;
    });
    return { payment: allPayment, invoice: allInvoice };
  }, [completionStatus, subItems]);

  const isItemCompleted = useCallback((rowId) => {
    const status = getLineItemCompletionStatus(rowId);
    return status.payment && status.invoice;
  }, [getLineItemCompletionStatus]);

  const getCompletionPages = useCallback((rowId) => {
    const status = completionStatus[rowId] || { payment: [], invoice: [] };
    return {
      payment: Array.isArray(status.payment) ? status.payment : [],
      invoice: Array.isArray(status.invoice) ? status.invoice : [],
    };
  }, [completionStatus]);

  const getNextSubItemSuffix = useCallback(
    (parentRowId) => {
      const existing = subItems[parentRowId] || [];
      return String.fromCharCode(97 + existing.length); // a, b, c, ...
    },
    [subItems]
  );

  // Shape a sub-item to look like a line item for PDFViewer
  const shapeSubItemAsLineItem = (subItem, parentItem) => ({
    row_id: subItem.sub_item_id,
    row_index: parentItem.row_index,
    budget_item: parentItem.budget_item,
    raw: {
      amount: subItem.amount,
      amounts: subItem.amounts || [],
      explanation: subItem.label,
    },
    normalized: {
      budget_item: parentItem.budget_item,
      amount: subItem.amount,
      explanation: subItem.keywords?.join(", ") || "",
    },
    candidates: subItem.candidates || [],
    selected_evidence: subItem.selected_evidence || {
      doc_id: subItem.doc_id,
      page_numbers: [],
      selection_source: "auto",
    },
    _isSubItem: true,
    _parentRowId: subItem.parent_row_id,
    _label: subItem.label,
  });

  const filterCandidates = (candidates) => {
    if (!candidates) return [];
    return candidates.filter(c => c.score >= minConfidenceScore);
  };

  const applyConfidenceFilter = (item) => {
    const filteredCandidates = filterCandidates(item.candidates);

    const hasMatchingDoc = item.candidates && item.candidates.length > 0 && item.candidates[0].doc_id;
    const allFiltered = filteredCandidates.length === 0 && item.candidates && item.candidates.length > 0;

    if (allFiltered && hasMatchingDoc) {
      return {
        ...item,
        candidates: filteredCandidates,
        selected_evidence: {
          doc_id: item.candidates[0].doc_id,
          page_numbers: [],
          selection_source: "auto"
        }
      };
    }

    return {
      ...item,
      candidates: filteredCandidates
    };
  };

  const getMatchType = (item) => {
    if (item.raw?.amount == null || item.raw?.amount === 0) return "zero-amount";
    if (item.selected_evidence?.doc_id === null) return "no-pdf";
    if (!item.candidates || item.candidates.length === 0) return "none";

    const topCandidate = item.candidates[0];
    const score = topCandidate?.score || 0;
    const topRationale = topCandidate?.rationale?.[0] || "";

    if (topRationale.includes("Amount-based")) return "amount";
    if (topRationale.includes("Cross-page")) return "cross-page";
    if (item.selected_evidence.page_numbers.length === 0) return "none";

    if (
      score < 0.4 ||
      topRationale.includes("neighbors") ||
      topRationale.includes("Contiguous cluster")
    ) {
      return "low-confidence";
    }

    if (item.selected_evidence.page_numbers.length > 8) return "too-many";

    return "keyword";
  };

  const filteredItems =
    data?.line_items
      ?.map(item => applyConfidenceFilter(item))
      ?.filter((item) => {
        if (
          selectedBudgetItem !== "all" &&
          item.budget_item !== selectedBudgetItem
        )
          return false;

        if (selectedMatchType !== "all") {
          const matchType = getMatchType(item);
          if (selectedMatchType !== matchType) return false;
        }

        if (
          verificationMode === "needs-verification" &&
          isItemCompleted(item.row_id)
        ) {
          return false;
        }

        return true;
      }) || [];

  if (jobStatus === "RUNNING" && !data) {
    return (
      <div className="loading-screen">
        <div className="loading-content">
          <h1>Processing Invoice Data</h1>
          <p className="processing-subtitle">
            Extracting text, analyzing documents, and matching line items to evidence.
          </p>
          <div className="loading-spinner"></div>
          <p className="processing-status">This page will update automatically when processing is complete.</p>
        </div>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="loading-screen">
        <div className="loading-content">
          <h1>Loading Reconciliation Data</h1>
          <div className="loading-spinner"></div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="error-screen">
        <div className="error-content">
          <h1>Error Loading Data</h1>
          <p>{error}</p>
          <button onClick={checkJobAndLoad}>Retry</button>
        </div>
      </div>
    );
  }

  const incompleteItems =
    data?.line_items
      ?.map(item => applyConfidenceFilter(item))
      ?.filter((item) => !isItemCompleted(item.row_id)) || [];

  return (
    <div className="review-page">
      <header className="review-header">
        <div className="header-content">
          <div className="header-title">
            <h1>Invoice Reconciliation</h1>
          </div>
          <div className="header-meta">
            <button className="finish-btn" onClick={() => setShowSummary(true)}>
              Finish Reconciliation
            </button>
          </div>
        </div>
      </header>

      {showSummary && (
        <div className="summary-overlay" onClick={() => setShowSummary(false)}>
          <div className="summary-modal" onClick={(e) => e.stopPropagation()}>
            <div className="summary-header">
              <h2>Reconciliation Summary</h2>
              <button
                className="close-btn"
                onClick={() => setShowSummary(false)}
              >
                ×
              </button>
            </div>
            <div className="summary-body">
              {incompleteItems.length === 0 ? (
                <div className="summary-complete">
                  <div className="complete-icon">&#10003;</div>
                  <h3>All Items Verified!</h3>
                  <p>
                    Every line item has been marked as done. The reconciliation
                    is complete.
                  </p>
                </div>
              ) : (
                <>
                  <div className="summary-intro">
                    <p>
                      <strong>{incompleteItems.length}</strong> line item
                      {incompleteItems.length !== 1 ? "s" : ""} still need
                      {incompleteItems.length === 1 ? "s" : ""} verification:
                    </p>
                  </div>
                  <div className="summary-list">
                    {incompleteItems.map((item) => (
                      <div
                        key={item.row_id}
                        className="summary-item"
                        onClick={() => {
                          setSelectedItem(item);
                          setShowSummary(false);
                        }}
                      >
                        <div className="summary-item-header">
                          <span className="summary-row">
                            #{item.row_index + 1}
                          </span>
                          <span className="summary-budget">
                            {item.budget_item}
                          </span>
                        </div>
                        {item.raw?.amount && item.raw.amount > 0 && (
                          <div className="summary-amount">
                            ${parseFloat(item.raw.amount).toFixed(2)}
                          </div>
                        )}
                        {(item.raw?.employee_first_name ||
                          item.raw?.employee_last_name) && (
                          <div className="summary-employee">
                            {item.raw.employee_first_name}{" "}
                            {item.raw.employee_last_name}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
            <div className="summary-footer">
              <button
                className="summary-close-btn"
                onClick={() => setShowSummary(false)}
              >
                {incompleteItems.length === 0 ? "Close" : "Continue Reviewing"}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="review-body">
        <aside className="sidebar">
          <FilterBar
            budgetItems={[...new Set(data?.documents?.map((d) => d.budget_item) || [])]}
            selectedBudgetItem={selectedBudgetItem}
            setSelectedBudgetItem={setSelectedBudgetItem}
            selectedMatchType={selectedMatchType}
            setSelectedMatchType={setSelectedMatchType}
            verificationMode={verificationMode}
            setVerificationMode={setVerificationMode}
            minConfidenceScore={minConfidenceScore}
            setMinConfidenceScore={setMinConfidenceScore}
          />

          <div className="items-list">
            <div className="items-header">
              <h3>Line Items</h3>
              <span className="item-count">{filteredItems.length}</span>
            </div>

            <div className="items-scroll">
              {filteredItems.map((item) => (
                <LineItemCard
                  key={item.row_id}
                  item={item}
                  matchType={getMatchType(item)}
                  isSelected={selectedItem?.row_id === item.row_id}
                  isCompleted={isItemCompleted(item.row_id)}
                  onClick={() => setSelectedItem(item)}
                  subItems={subItems[item.row_id] || []}
                  selectedSubItemId={selectedItem?._isSubItem ? selectedItem.row_id : null}
                  onSubItemClick={(subItem) =>
                    setSelectedItem(shapeSubItemAsLineItem(subItem, item))
                  }
                  onRemoveSubItem={(subItemId) =>
                    removeSubItem(item.row_id, subItemId)
                  }
                  completionStatus={getLineItemCompletionStatus(item.row_id)}
                  subItemCompletionStatus={completionStatus}
                />
              ))}
            </div>
          </div>
        </aside>

        <main className="review-main-content">
          {selectedItem ? (
            <PDFViewer
              item={selectedItem}
              documents={data.documents}
              matchType={getMatchType(selectedItem)}
              jobId={jobId}
              userEditedCandidates={userEditedCandidates}
              setUserEditedCandidates={setUserEditedCandidates}
              userAnnotations={userAnnotations}
              setUserAnnotations={setUserAnnotations}
              onAddSubItem={addSubItem}
              onAddSubItems={addSubItems}
              getNextSubItemSuffix={getNextSubItemSuffix}
              subItems={subItems[selectedItem?._isSubItem ? selectedItem._parentRowId : selectedItem?.row_id] || []}
              completionPages={getCompletionPages(selectedItem?.row_id)}
              onTogglePageEvidence={togglePageEvidence}
              itemRowId={selectedItem?.row_id}
            />
          ) : (
            <div className="empty-state">
              <div className="empty-state-content">
                <h2>Select a Line Item</h2>
                <p>
                  Choose an item from the list to view its evidence pages and
                  verify the match quality
                </p>
              </div>
            </div>
          )}
        </main>
      </div>
    </div>
  );
}

export default ReviewPage;
