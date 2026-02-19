import { useState } from 'react';
import { BUDGET_ITEMS } from '../utils/budgetItemClassifier';
import './UnassignedFilesModal.css';

/**
 * Modal that appears when drag-and-dropped files cannot be auto-classified
 * by filename. Displays each unrecognized file with a dropdown so the user
 * can manually assign it to a budget item.
 *
 * Props:
 *   files       - File[]              list of unrecognized File objects
 *   onConfirm   - (Map<File, string>) called with file->budgetItem map when all assigned
 *   onCancel    - () => void           called when the user clicks "Remove These Files"
 */
export default function UnassignedFilesModal({ files, onConfirm, onCancel }) {
  // Track assignment per file index: { 0: '', 1: 'Salary', ... }
  const [assignments, setAssignments] = useState(() =>
    Object.fromEntries(files.map((_, i) => [i, '']))
  );

  const allAssigned = Object.values(assignments).every((v) => v !== '');

  const handleChange = (index, value) => {
    setAssignments((prev) => ({ ...prev, [index]: value }));
  };

  const handleConfirm = () => {
    const result = new Map();
    files.forEach((file, i) => {
      result.set(file, assignments[i]);
    });
    onConfirm(result);
  };

  return (
    <div className="unassigned-overlay" onClick={onCancel}>
      <div className="unassigned-modal" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="unassigned-header">
          <div>
            <h2>Assign Budget Items</h2>
            <p className="unassigned-subtitle">
              {files.length} file{files.length !== 1 ? 's' : ''} could not be
              classified automatically. Please assign each to a budget item.
            </p>
          </div>
          <button className="unassigned-close" onClick={onCancel}>
            &times;
          </button>
        </div>

        {/* File list */}
        <div className="unassigned-body">
          {files.map((file, i) => (
            <div key={i} className="unassigned-row">
              <span className="unassigned-filename" title={file.name}>
                {file.name}
              </span>
              <select
                className="unassigned-select"
                value={assignments[i]}
                onChange={(e) => handleChange(i, e.target.value)}
              >
                <option value="" disabled>
                  Select budget item...
                </option>
                {BUDGET_ITEMS.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>

        {/* Footer */}
        <div className="unassigned-footer">
          <button className="unassigned-cancel-btn" onClick={onCancel}>
            Remove These Files
          </button>
          <button
            className="unassigned-confirm-btn"
            disabled={!allAssigned}
            onClick={handleConfirm}
          >
            Confirm Assignments
          </button>
        </div>
      </div>
    </div>
  );
}
