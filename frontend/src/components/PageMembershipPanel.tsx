/**
 * PageMembershipPanel - UI for manually adding/removing pages from a group.
 *
 * Allows users to override which pages belong to a group via checkboxes.
 * Does not mutate the original DocumentAnalysis; edits are stored separately.
 */

import type { Group } from '../types/DocumentAnalysis';
import type { GroupPageEdits } from '../utils/pageEdits';
import {
  getOriginalPagesForGroup,
  getEffectivePagesForGroup,
  getPageStatus,
} from '../utils/pageEdits';
import './PageMembershipPanel.css';

interface PageMembershipPanelProps {
  pageCount: number | null;
  group: Group;
  groupEdits: GroupPageEdits | undefined;
  onTogglePage: (groupId: string, pageNumber: number, shouldInclude: boolean) => void;
}

export function PageMembershipPanel({
  pageCount,
  group,
  groupEdits,
  onTogglePage,
}: PageMembershipPanelProps) {
  // Compute page lists
  const originalPages = getOriginalPagesForGroup(group);
  const effectivePages = getEffectivePagesForGroup(group, groupEdits, pageCount);

  // Determine which pages to show in the list
  const pagesToShow: number[] = [];

  if (pageCount !== null && pageCount > 0) {
    // Show all pages from 1 to pageCount
    for (let i = 1; i <= pageCount; i++) {
      pagesToShow.push(i);
    }
  } else {
    // Show only pages that are in originalPages or addedPages
    const pagesSet = new Set<number>([
      ...originalPages,
      ...(groupEdits?.addedPages ?? []),
    ]);
    pagesToShow.push(...Array.from(pagesSet).sort((a, b) => a - b));
  }

  const handleCheckboxChange = (pageNumber: number, checked: boolean) => {
    onTogglePage(group.groupId, pageNumber, checked);
  };

  return (
    <div className="page-membership-panel">
      <h3>Page Membership</h3>
      <p className="panel-description">
        Select which pages belong to this group. Pages shown are based on model
        output and your manual edits.
      </p>

      <div className="page-list">
        {pagesToShow.map((pageNumber) => {
          const isChecked = effectivePages.includes(pageNumber);
          const status = getPageStatus(group, pageNumber, groupEdits);

          return (
            <div key={pageNumber} className="page-item">
              <label className="page-checkbox-label">
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={(e) => handleCheckboxChange(pageNumber, e.target.checked)}
                />
                <span className="page-number">Page {pageNumber}</span>

                {status && (
                  <span className={`page-status status-${status}`}>
                    {status === 'model' && '(model)'}
                    {status === 'added' && '(added)'}
                    {status === 'removed' && '(removed)'}
                  </span>
                )}
              </label>
            </div>
          );
        })}
      </div>

      {/* Summary */}
      <div className="panel-summary">
        <div className="summary-item">
          <strong>Original pages:</strong> {originalPages.length}
        </div>
        <div className="summary-item">
          <strong>Effective pages:</strong> {effectivePages.length}
        </div>
        {groupEdits && (
          <>
            {groupEdits.addedPages.length > 0 && (
              <div className="summary-item summary-added">
                +{groupEdits.addedPages.length} added
              </div>
            )}
            {groupEdits.removedPages.length > 0 && (
              <div className="summary-item summary-removed">
                -{groupEdits.removedPages.length} removed
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
