/**
 * UnassignedPagesPanel - Shows pages that don't belong to any group.
 *
 * Allows users to:
 * - View all unassigned/orphaned pages
 * - Assign pages to existing groups
 * - Create new groups for unassigned pages
 */

import { useState } from 'react';
import type { Group } from '../types/DocumentAnalysis';
import './UnassignedPagesPanel.css';

interface UnassignedPagesPanelProps {
  unassignedPages: number[];
  groups: Group[];
  onAssignToGroup: (pageNumber: number, groupId: string) => void;
  onCreateNewGroup: (pageNumbers: number[], groupLabel: string) => void;
  onJumpToPage: (pageNumber: number) => void;
}

export function UnassignedPagesPanel({
  unassignedPages,
  groups,
  onAssignToGroup,
  onCreateNewGroup,
  onJumpToPage,
}: UnassignedPagesPanelProps) {
  const [selectedPages, setSelectedPages] = useState<Set<number>>(new Set());
  const [targetGroupId, setTargetGroupId] = useState<string>('');
  const [newGroupLabel, setNewGroupLabel] = useState<string>('');
  const [showNewGroupForm, setShowNewGroupForm] = useState(false);

  const handleTogglePageSelection = (pageNumber: number) => {
    const newSelection = new Set(selectedPages);
    if (newSelection.has(pageNumber)) {
      newSelection.delete(pageNumber);
    } else {
      newSelection.add(pageNumber);
    }
    setSelectedPages(newSelection);
  };

  const handleSelectAll = () => {
    setSelectedPages(new Set(unassignedPages));
  };

  const handleClearSelection = () => {
    setSelectedPages(new Set());
  };

  const handleAssignToExisting = () => {
    if (!targetGroupId || selectedPages.size === 0) {
      return;
    }

    // Assign each selected page to the target group
    for (const page of selectedPages) {
      onAssignToGroup(page, targetGroupId);
    }

    // Clear selection
    setSelectedPages(new Set());
    setTargetGroupId('');
  };

  const handleCreateNewGroup = () => {
    if (!newGroupLabel.trim() || selectedPages.size === 0) {
      return;
    }

    onCreateNewGroup(Array.from(selectedPages), newGroupLabel.trim());

    // Clear selection and form
    setSelectedPages(new Set());
    setNewGroupLabel('');
    setShowNewGroupForm(false);
  };

  if (unassignedPages.length === 0) {
    return (
      <div className="unassigned-pages-panel">
        <h3>Unassigned Pages</h3>
        <p className="no-unassigned-message">
          ✓ All pages are assigned to groups
        </p>
      </div>
    );
  }

  return (
    <div className="unassigned-pages-panel">
      <h3>Unassigned Pages</h3>
      <p className="panel-description">
        These pages don't belong to any group. Assign them to existing groups or create new groups.
      </p>

      <div className="unassigned-summary">
        <strong>{unassignedPages.length}</strong> unassigned page{unassignedPages.length !== 1 ? 's' : ''}
        {selectedPages.size > 0 && (
          <span className="selection-count">
            {' '}· {selectedPages.size} selected
          </span>
        )}
      </div>

      {/* Selection controls */}
      <div className="selection-controls">
        <button onClick={handleSelectAll} className="select-all-button">
          Select All
        </button>
        {selectedPages.size > 0 && (
          <button onClick={handleClearSelection} className="clear-selection-button">
            Clear
          </button>
        )}
      </div>

      {/* Page list */}
      <div className="unassigned-page-list">
        {unassignedPages.map((pageNumber) => {
          const isSelected = selectedPages.has(pageNumber);

          return (
            <div key={pageNumber} className={`unassigned-page-item ${isSelected ? 'selected' : ''}`}>
              <label className="page-checkbox-label">
                <input
                  type="checkbox"
                  checked={isSelected}
                  onChange={() => handleTogglePageSelection(pageNumber)}
                />
                <span className="page-number">Page {pageNumber}</span>
              </label>
              <button
                className="jump-button"
                onClick={() => onJumpToPage(pageNumber)}
                title="Jump to this page"
              >
                →
              </button>
            </div>
          );
        })}
      </div>

      {/* Assignment actions */}
      {selectedPages.size > 0 && (
        <div className="assignment-actions">
          <div className="action-section">
            <h4>Assign to Existing Group</h4>
            <div className="assign-to-existing">
              <select
                value={targetGroupId}
                onChange={(e) => setTargetGroupId(e.target.value)}
                className="group-selector"
              >
                <option value="">Select a group...</option>
                {groups.map((group) => (
                  <option key={group.groupId} value={group.groupId}>
                    {group.label}
                  </option>
                ))}
              </select>
              <button
                onClick={handleAssignToExisting}
                disabled={!targetGroupId}
                className="assign-button"
              >
                Assign ({selectedPages.size})
              </button>
            </div>
          </div>

          <div className="action-section">
            {!showNewGroupForm ? (
              <button
                onClick={() => setShowNewGroupForm(true)}
                className="create-new-group-button"
              >
                + Create New Group
              </button>
            ) : (
              <div className="new-group-form">
                <h4>Create New Group</h4>
                <input
                  type="text"
                  value={newGroupLabel}
                  onChange={(e) => setNewGroupLabel(e.target.value)}
                  placeholder="Enter group name..."
                  className="group-name-input"
                  autoFocus
                />
                <div className="form-buttons">
                  <button
                    onClick={handleCreateNewGroup}
                    disabled={!newGroupLabel.trim()}
                    className="create-button"
                  >
                    Create with {selectedPages.size} page{selectedPages.size !== 1 ? 's' : ''}
                  </button>
                  <button
                    onClick={() => {
                      setShowNewGroupForm(false);
                      setNewGroupLabel('');
                    }}
                    className="cancel-button"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
