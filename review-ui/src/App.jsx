import { useState, useEffect } from "react";
import "./App.css";
import LineItemCard from "./components/LineItemCard";
import PDFViewer from "./components/PDFViewer";
import FilterBar from "./components/FilterBar";

function App() {
  const [jobId, setJobId] = useState("my_job");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const [selectedBudgetItem, setSelectedBudgetItem] = useState("all");
  const [selectedMatchType, setSelectedMatchType] = useState("low-confidence");
  const [selectedAgency, setSelectedAgency] = useState("all");
  const [selectedItem, setSelectedItem] = useState(null);
  const [verificationMode, setVerificationMode] =
    useState("needs-verification"); // 'needs-verification' or 'all'
  const [completedItems, setCompletedItems] = useState(new Set()); // Track row_ids that are marked done
  const [showSummary, setShowSummary] = useState(false); // Track whether to show finish summary
  const [minConfidenceScore, setMinConfidenceScore] = useState(0.5); // Min confidence threshold (0-1)

  useEffect(() => {
    loadReconciliation();
  }, [jobId]);

  const loadReconciliation = async () => {
    try {
      setLoading(true);
      setError(null);
      // Use absolute path from project root
      const response = await fetch(
        `/backend/jobs/${jobId}/artifacts/reconciliation.json`,
      );
      if (!response.ok)
        throw new Error(
          `Failed to load reconciliation data: ${response.status} ${response.statusText}`,
        );
      const json = await response.json();
      setData(json);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  const markGroupDone = (rowId) => {
    // Check if item is already completed
    const isCurrentlyCompleted = completedItems.has(rowId);

    if (isCurrentlyCompleted) {
      // Toggle off - remove from completed items
      setCompletedItems((prev) => {
        const newSet = new Set(prev);
        newSet.delete(rowId);
        return newSet;
      });
    } else {
      // Toggle on - mark as completed
      // If in "needs-verification" mode, advance to the next item before marking as done
      if (verificationMode === "needs-verification") {
        const currentIndex = filteredItems.findIndex(
          (item) => item.row_id === rowId,
        );

        // Find next item (skip the current one since it will be marked done)
        let nextItem = null;
        if (currentIndex >= 0 && currentIndex < filteredItems.length - 1) {
          // Move to next item
          nextItem = filteredItems[currentIndex + 1];
        } else if (currentIndex > 0) {
          // If we're at the last item, go to the first one
          nextItem = filteredItems[0];
        }

        // Mark as done and advance
        setCompletedItems((prev) => new Set([...prev, rowId]));
        setSelectedItem(nextItem);
      } else {
        // Just mark as done without navigating
        setCompletedItems((prev) => new Set([...prev, rowId]));
      }
    }
  };

  // Filter candidates based on minConfidenceScore
  const filterCandidates = (candidates) => {
    if (!candidates) return [];
    return candidates.filter(c => c.score >= minConfidenceScore);
  };

  // Apply confidence filtering to a line item
  const applyConfidenceFilter = (item) => {
    const filteredCandidates = filterCandidates(item.candidates);

    // If PDF exists but all candidates filtered out, update selected_evidence
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
    // Check if amount is explicitly $0 - no matching needed
    if (item.raw?.amount === 0) return "zero-amount";

    // Check if no PDF was uploaded for this budget item (evaluated BEFORE match checks)
    // Backend sets doc_id to null when no PDF found for the budget item
    if (item.selected_evidence?.doc_id === null) return "no-pdf";

    if (!item.candidates || item.candidates.length === 0) return "none";

    const topCandidate = item.candidates[0];
    const score = topCandidate?.score || 0;
    const topRationale = topCandidate?.rationale?.[0] || "";

    // Check for specific match types first
    if (topRationale.includes("Amount-based")) return "amount";
    if (topRationale.includes("Cross-page")) return "cross-page";
    if (item.selected_evidence.page_numbers.length === 0) return "none";

    // Low confidence check BEFORE too-many check
    // This catches cases where system includes many/all pages due to poor matching
    if (
      score < 0.4 ||
      topRationale.includes("neighbors") ||
      topRationale.includes("Contiguous cluster")
    ) {
      return "low-confidence";
    }

    // Too-many only applies to high-confidence matches spread across many pages
    if (item.selected_evidence.page_numbers.length > 8) return "too-many";

    return "keyword";
  };

  const filteredItems =
    data?.line_items
      ?.map(item => applyConfidenceFilter(item)) // Apply confidence filtering first
      ?.filter((item) => {
        // Filter by budget item
        if (
          selectedBudgetItem !== "all" &&
          item.budget_item !== selectedBudgetItem
        )
          return false;

        // Filter by match type
        if (selectedMatchType !== "all") {
          const matchType = getMatchType(item);
          if (selectedMatchType !== matchType) return false;
        }

        // Filter by agency
        if (selectedAgency !== "all" && item.raw?.agency !== selectedAgency)
          return false;

        // Filter by verification status
        if (
          verificationMode === "needs-verification" &&
          completedItems.has(item.row_id)
        ) {
          return false;
        }

        return true;
      }) || [];

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
          <button onClick={loadReconciliation}>Retry</button>
        </div>
      </div>
    );
  }

  const incompleteItems =
    data?.line_items
      ?.map(item => applyConfidenceFilter(item))
      ?.filter((item) => !completedItems.has(item.row_id)) || [];

  return (
    <div className="app">
      <header className="app-header">
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
                  <div className="complete-icon">✓</div>
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

      <div className="app-body">
        <aside className="sidebar">
          <FilterBar
            budgetItems={[...new Set(data?.documents?.map((d) => d.budget_item) || [])]}
            selectedBudgetItem={selectedBudgetItem}
            setSelectedBudgetItem={setSelectedBudgetItem}
            selectedMatchType={selectedMatchType}
            setSelectedMatchType={setSelectedMatchType}
            agencies={[
              ...new Set(
                data?.line_items
                  ?.map((item) => item.raw?.agency)
                  .filter(Boolean) || [],
              ),
            ].sort()}
            selectedAgency={selectedAgency}
            setSelectedAgency={setSelectedAgency}
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
                  isCompleted={completedItems.has(item.row_id)}
                  onClick={() => setSelectedItem(item)}
                />
              ))}
            </div>
          </div>
        </aside>

        <main className="main-content">
          {selectedItem ? (
            <PDFViewer
              item={selectedItem}
              documents={data.documents}
              matchType={getMatchType(selectedItem)}
              onMarkGroupDone={markGroupDone}
              isCompleted={completedItems.has(selectedItem.row_id)}
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

export default App;
