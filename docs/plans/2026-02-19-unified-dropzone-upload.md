# Unified Dropzone Upload Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the 12 separate budget-item file upload cards with a single react-dropzone area that auto-classifies files by filename, with a manual-assignment popup for unrecognized files.

**Architecture:** Files dropped into a single zone are parsed against budget item slugs (reusing the existing `slugify` logic already in the frontend). Recognized files are auto-assigned; unrecognized files trigger a modal for manual assignment. The S3 upload path structure (`{slug}.pdf` or `{slug}/{filename}`) is preserved unchanged — only the UI for selecting/organizing files changes.

**Tech Stack:** React 18, react-dropzone, existing CSS custom properties

---

### Task 1: Install react-dropzone

**Files:**
- Modify: `frontend/package.json`

**Step 1: Install the dependency**

Run: `cd /home/jchan/dev/dxhub/city-invoice-processor/frontend && npm install react-dropzone`

**Step 2: Verify installation**

Run: `cd /home/jchan/dev/dxhub/city-invoice-processor/frontend && node -e "require('react-dropzone'); console.log('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "chore: add react-dropzone dependency"
```

---

### Task 2: Build the filename-to-budget-item classifier utility

**Files:**
- Create: `frontend/src/utils/budgetItemClassifier.js`
- Create: `frontend/src/utils/budgetItemClassifier.test.js`

**Step 1: Write the failing tests**

Create `frontend/src/utils/budgetItemClassifier.test.js`:

```javascript
import { describe, it, expect } from 'vitest';
import { classifyFile, BUDGET_ITEMS } from './budgetItemClassifier';

describe('classifyFile', () => {
  it('matches exact budget item name as filename', () => {
    const file = new File([], 'Salary.pdf');
    expect(classifyFile(file)).toBe('Salary');
  });

  it('matches slugified budget item name', () => {
    const file = new File([], 'space_rental_occupancy_costs.pdf');
    expect(classifyFile(file)).toBe('Space Rental/Occupancy Costs');
  });

  it('matches case-insensitively', () => {
    const file = new File([], 'TELECOMMUNICATIONS.pdf');
    expect(classifyFile(file)).toBe('Telecommunications');
  });

  it('matches with mixed separators', () => {
    const file = new File([], 'Travel-and-Conferences.pdf');
    expect(classifyFile(file)).toBe('Travel and Conferences');
  });

  it('matches Indirect_Costs with underscores', () => {
    const file = new File([], 'Indirect_Costs.pdf');
    expect(classifyFile(file)).toBe('Indirect Costs');
  });

  it('matches Contractual_Service', () => {
    const file = new File([], 'Contractual_Service.pdf');
    expect(classifyFile(file)).toBe('Contractual Service');
  });

  it('returns null for unrecognized filename', () => {
    const file = new File([], 'random_report.pdf');
    expect(classifyFile(file)).toBeNull();
  });

  it('returns null for non-PDF file', () => {
    const file = new File([], 'Salary.docx');
    expect(classifyFile(file)).toBeNull();
  });

  it('matches budget item when it appears as prefix with extra text', () => {
    // e.g. "Salary_January_2025.pdf" should match "Salary"
    const file = new File([], 'Salary_January_2025.pdf');
    expect(classifyFile(file)).toBe('Salary');
  });

  it('matches budget item when it appears as prefix with dash separator', () => {
    const file = new File([], 'fringe-q1-report.pdf');
    expect(classifyFile(file)).toBe('Fringe');
  });

  it('matches longer budget items as prefix', () => {
    const file = new File([], 'travel_and_conferences_receipts.pdf');
    expect(classifyFile(file)).toBe('Travel and Conferences');
  });

  it('matches space_rental prefix in longer filename', () => {
    const file = new File([], 'Space_Rental_Occupancy_Costs_Lease.pdf');
    expect(classifyFile(file)).toBe('Space Rental/Occupancy Costs');
  });

  it('exports all 12 BUDGET_ITEMS', () => {
    expect(BUDGET_ITEMS).toHaveLength(12);
  });
});
```

**Step 2: Run tests to verify they fail**

Run: `cd /home/jchan/dev/dxhub/city-invoice-processor/frontend && npx vitest run src/utils/budgetItemClassifier.test.js 2>&1 | head -30`
Expected: FAIL — module not found

> Note: If vitest is not yet configured, first install it: `npm install -D vitest` and add `"test": "vitest"` to package.json scripts.

**Step 3: Write the implementation**

Create `frontend/src/utils/budgetItemClassifier.js`:

```javascript
/**
 * Budget item classification from filenames.
 *
 * Mirrors the backend slugify + match logic from
 * backend/invoice_recon/budget_items.py
 */

export const BUDGET_ITEMS = [
  'Salary',
  'Fringe',
  'Contractual Service',
  'Equipment',
  'Insurance',
  'Travel and Conferences',
  'Space Rental/Occupancy Costs',
  'Telecommunications',
  'Utilities',
  'Supplies',
  'Other',
  'Indirect Costs',
];

/** Slugify text — same rules as backend budget_items.py:slugify() */
export function slugify(text) {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_|_$/g, '');
}

/**
 * Build a map of slug -> canonical budget item name.
 * Sorted longest-slug-first so prefix matching picks the most specific item.
 */
const SLUG_MAP = (() => {
  const entries = BUDGET_ITEMS.map((item) => [slugify(item), item]);
  // Sort longest first for greedy prefix matching
  entries.sort((a, b) => b[0].length - a[0].length);
  return entries;
})();

/**
 * Classify a File object into a budget item based on its filename.
 *
 * Strategy:
 * 1. Exact slug match (filename stem == budget item slug)
 * 2. Prefix match (filename stem starts with budget item slug + separator)
 *
 * Returns the canonical budget item name, or null if unrecognized.
 */
export function classifyFile(file) {
  const name = file.name;

  // Only accept PDFs
  if (!name.toLowerCase().endsWith('.pdf')) {
    return null;
  }

  // Remove .pdf extension, then slugify
  const stem = name.replace(/\.pdf$/i, '');
  const slug = slugify(stem);

  // 1. Exact match
  for (const [budgetSlug, budgetItem] of SLUG_MAP) {
    if (slug === budgetSlug) {
      return budgetItem;
    }
  }

  // 2. Prefix match: slug starts with budgetSlug followed by '_' or end
  for (const [budgetSlug, budgetItem] of SLUG_MAP) {
    if (slug.startsWith(budgetSlug + '_')) {
      return budgetItem;
    }
  }

  return null;
}
```

**Step 4: Run tests to verify they pass**

Run: `cd /home/jchan/dev/dxhub/city-invoice-processor/frontend && npx vitest run src/utils/budgetItemClassifier.test.js`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add frontend/src/utils/budgetItemClassifier.js frontend/src/utils/budgetItemClassifier.test.js
git commit -m "feat: add filename-to-budget-item classifier utility"
```

---

### Task 3: Build the UnassignedFilesModal component

This modal appears when the user has dropped files that can't be auto-classified. It shows each unrecognized file with a dropdown to manually assign a budget item.

**Files:**
- Create: `frontend/src/components/UnassignedFilesModal.jsx`
- Create: `frontend/src/components/UnassignedFilesModal.css`

**Step 1: Write the modal component**

Create `frontend/src/components/UnassignedFilesModal.jsx`:

```jsx
import { useState } from 'react';
import { BUDGET_ITEMS } from '../utils/budgetItemClassifier';
import './UnassignedFilesModal.css';

/**
 * Modal for manually assigning budget items to unrecognized files.
 *
 * Props:
 *   files: File[] — list of unrecognized files
 *   onConfirm: (assignments: Map<File, string>) => void — called with file->budgetItem map
 *   onCancel: () => void — called when user cancels (removes unrecognized files)
 */
export default function UnassignedFilesModal({ files, onConfirm, onCancel }) {
  // Map of file index -> selected budget item (empty string = unassigned)
  const [assignments, setAssignments] = useState(() =>
    Object.fromEntries(files.map((_, i) => [i, '']))
  );

  const allAssigned = Object.values(assignments).every((v) => v !== '');

  const handleConfirm = () => {
    const result = new Map();
    files.forEach((file, i) => {
      result.set(file, assignments[i]);
    });
    onConfirm(result);
  };

  return (
    <div className="modal-overlay" onClick={onCancel}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Assign Budget Items</h2>
          <p>
            The following files could not be automatically matched to a budget
            item. Please assign each file manually.
          </p>
        </div>

        <div className="modal-body">
          {files.map((file, i) => (
            <div key={`${file.name}-${i}`} className="assignment-row">
              <span className="assignment-filename" title={file.name}>
                {file.name}
              </span>
              <select
                className="assignment-select"
                value={assignments[i]}
                onChange={(e) =>
                  setAssignments((prev) => ({ ...prev, [i]: e.target.value }))
                }
              >
                <option value="">-- Select budget item --</option>
                {BUDGET_ITEMS.map((item) => (
                  <option key={item} value={item}>
                    {item}
                  </option>
                ))}
              </select>
            </div>
          ))}
        </div>

        <div className="modal-footer">
          <button className="modal-btn modal-btn-cancel" onClick={onCancel}>
            Remove These Files
          </button>
          <button
            className="modal-btn modal-btn-confirm"
            onClick={handleConfirm}
            disabled={!allAssigned}
          >
            Confirm Assignments
          </button>
        </div>
      </div>
    </div>
  );
}
```

**Step 2: Write the modal CSS**

Create `frontend/src/components/UnassignedFilesModal.css`:

```css
/* Modal overlay */
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
}

.modal-content {
  background: var(--color-surface);
  border: 2px solid var(--color-text);
  border-radius: 4px;
  width: 90%;
  max-width: 600px;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
}

/* Header */
.modal-header {
  padding: 1.5rem 1.5rem 1rem;
  border-bottom: 1px solid var(--color-border);
}

.modal-header h2 {
  font-family: var(--font-serif);
  font-size: 1.5rem;
  font-weight: 600;
  margin-bottom: 0.5rem;
}

.modal-header p {
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  color: var(--color-text-secondary);
  line-height: 1.5;
}

/* Body */
.modal-body {
  padding: 1rem 1.5rem;
  overflow-y: auto;
  flex: 1;
}

.assignment-row {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.75rem 0;
  border-bottom: 1px solid var(--color-border);
}

.assignment-row:last-child {
  border-bottom: none;
}

.assignment-filename {
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.assignment-select {
  font-family: var(--font-sans);
  font-size: 0.8125rem;
  padding: 0.5rem 0.75rem;
  border: 2px solid var(--color-border);
  border-radius: 2px;
  background: var(--color-surface);
  color: var(--color-text);
  min-width: 200px;
  cursor: pointer;
}

.assignment-select:focus {
  border-color: var(--color-accent);
  outline: none;
}

/* Footer */
.modal-footer {
  padding: 1rem 1.5rem;
  border-top: 1px solid var(--color-border);
  display: flex;
  justify-content: flex-end;
  gap: 0.75rem;
}

.modal-btn {
  padding: 0.75rem 1.5rem;
  font-family: var(--font-sans);
  font-weight: 600;
  font-size: 0.8125rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border-radius: 2px;
  cursor: pointer;
  border: none;
  transition: all 0.2s;
}

.modal-btn-cancel {
  background: none;
  border: 2px solid var(--color-border);
  color: var(--color-text-secondary);
}

.modal-btn-cancel:hover {
  border-color: var(--color-text);
  color: var(--color-text);
}

.modal-btn-confirm {
  background: var(--color-accent);
  color: white;
}

.modal-btn-confirm:hover:not(:disabled) {
  background: #1e3a5f;
  transform: translateY(-1px);
}

.modal-btn-confirm:disabled {
  background: var(--color-border);
  color: var(--color-text-secondary);
  cursor: not-allowed;
}
```

**Step 3: Verify the component renders without errors**

Run: `cd /home/jchan/dev/dxhub/city-invoice-processor/frontend && npx vite build 2>&1 | tail -5`
Expected: Build succeeds (component is tree-shaken since unused yet, but syntax is validated)

**Step 4: Commit**

```bash
git add frontend/src/components/UnassignedFilesModal.jsx frontend/src/components/UnassignedFilesModal.css
git commit -m "feat: add UnassignedFilesModal for manual budget item assignment"
```

---

### Task 4: Rewrite UploadPage to use unified dropzone

This is the core task. Replace the 12-card grid with a single react-dropzone area and a file summary table grouped by budget item.

**Files:**
- Modify: `frontend/src/pages/UploadPage.jsx` (full rewrite of PDF section)
- Modify: `frontend/src/pages/UploadPage.css` (replace pdf-grid styles with dropzone + table styles)

**Step 1: Rewrite UploadPage.jsx**

Replace the entire contents of `frontend/src/pages/UploadPage.jsx` with:

```jsx
import { useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useDropzone } from 'react-dropzone';
import { API_BASE } from '../config';
import { classifyFile, slugify, BUDGET_ITEMS } from '../utils/budgetItemClassifier';
import UnassignedFilesModal from '../components/UnassignedFilesModal';
import './UploadPage.css';

function UploadPage() {
  const navigate = useNavigate();
  const [jobName, setJobName] = useState('');
  const [csvFile, setCsvFile] = useState(null);
  // pdfFiles: { [budgetItem]: File[] }
  const [pdfFiles, setPdfFiles] = useState({});
  const [uploading, setUploading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState('');
  const [uploadError, setUploadError] = useState(null);
  // Files awaiting manual assignment
  const [unassignedFiles, setUnassignedFiles] = useState(null);

  const handleCsvChange = (e) => {
    const file = e.target.files[0];
    if (file && file.name.endsWith('.csv')) {
      setCsvFile(file);
    } else {
      alert('Please select a CSV file');
      e.target.value = '';
    }
  };

  /**
   * Core handler for new files (from dropzone or any source).
   * Auto-classifies each file; any unrecognized files trigger the modal.
   */
  const processNewFiles = useCallback((acceptedFiles) => {
    const pdfs = acceptedFiles.filter((f) =>
      f.name.toLowerCase().endsWith('.pdf')
    );
    if (pdfs.length === 0) return;

    const classified = []; // { file, budgetItem }
    const unclassified = []; // File[]

    for (const file of pdfs) {
      const budgetItem = classifyFile(file);
      if (budgetItem) {
        classified.push({ file, budgetItem });
      } else {
        unclassified.push(file);
      }
    }

    // Add classified files immediately
    if (classified.length > 0) {
      setPdfFiles((prev) => {
        const next = { ...prev };
        for (const { file, budgetItem } of classified) {
          const existing = next[budgetItem] || [];
          // Deduplicate by name+size
          const key = `${file.name}__${file.size}`;
          if (!existing.some((f) => `${f.name}__${f.size}` === key)) {
            next[budgetItem] = [...existing, file];
          }
        }
        return next;
      });
    }

    // Show modal for unclassified files
    if (unclassified.length > 0) {
      setUnassignedFiles(unclassified);
    }
  }, []);

  const handleModalConfirm = useCallback((assignmentMap) => {
    // assignmentMap: Map<File, string (budgetItem)>
    setPdfFiles((prev) => {
      const next = { ...prev };
      for (const [file, budgetItem] of assignmentMap) {
        const existing = next[budgetItem] || [];
        const key = `${file.name}__${file.size}`;
        if (!existing.some((f) => `${f.name}__${f.size}` === key)) {
          next[budgetItem] = [...existing, file];
        }
      }
      return next;
    });
    setUnassignedFiles(null);
  }, []);

  const handleModalCancel = useCallback(() => {
    // Discard the unrecognized files
    setUnassignedFiles(null);
  }, []);

  const handleRemoveFile = (budgetItem, index) => {
    setPdfFiles((prev) => {
      const files = [...(prev[budgetItem] || [])];
      files.splice(index, 1);
      if (files.length === 0) {
        const { [budgetItem]: _, ...rest } = prev;
        return rest;
      }
      return { ...prev, [budgetItem]: files };
    });
  };

  const handleClearAll = () => {
    setPdfFiles({});
  };

  // Dropzone config
  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop: processNewFiles,
    accept: { 'application/pdf': ['.pdf'] },
    multiple: true,
    disabled: uploading,
    noClick: false,
  });

  const handleUpload = async () => {
    if (!csvFile) {
      alert('Please select a CSV file before uploading');
      return;
    }

    const budgetItemsWithFiles = Object.entries(pdfFiles).filter(
      ([_, files]) => files.length > 0
    );
    if (budgetItemsWithFiles.length === 0) {
      alert('Please select at least one PDF file before uploading');
      return;
    }

    setUploading(true);
    setUploadError(null);
    setUploadProgress('Requesting upload URLs...');

    try {
      // Build pdf_files map and fileMap for upload
      const pdfFilesPayload = {};
      const fileMap = []; // { budgetItem, s3RelPath, file }

      for (const [budgetItem, files] of budgetItemsWithFiles) {
        const slug = slugify(budgetItem);
        const paths = [];
        const nameCount = {};

        for (let i = 0; i < files.length; i++) {
          const file = files[i];
          let name = file.name;
          const key = name.toLowerCase();

          // Handle duplicate filenames within same budget item
          if (nameCount[key]) {
            nameCount[key]++;
            const stem = name.replace(/\.pdf$/i, '');
            name = `${stem}_${nameCount[key]}.pdf`;
          } else {
            nameCount[key] = 1;
          }

          // Single file: flat path; Multiple files: subdirectory path
          const s3RelPath =
            files.length === 1 ? `${slug}.pdf` : `${slug}/${name}`;
          paths.push(s3RelPath);
          fileMap.push({ budgetItem, s3RelPath, file });
        }

        pdfFilesPayload[slug] = paths;
      }

      // Step 1: Get presigned URLs from API
      const startResp = await fetch(`${API_BASE}/api/upload/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          pdf_files: pdfFilesPayload,
          job_name: jobName.trim() || undefined,
          csv_filename: csvFile?.name || '',
        }),
      });

      if (!startResp.ok)
        throw new Error(`Failed to get upload URLs: ${startResp.status}`);
      const { job_id, presigned_urls } = await startResp.json();

      // Step 2: Upload PDFs first (CSV upload triggers pipeline, so it goes last)
      for (let i = 0; i < fileMap.length; i++) {
        const { budgetItem, s3RelPath, file } = fileMap[i];
        const url = presigned_urls.pdfs[s3RelPath];

        setUploadProgress(
          `Uploading PDF ${i + 1}/${fileMap.length}: ${budgetItem} - ${file.name}...`
        );

        await fetch(url, {
          method: 'PUT',
          body: file,
          headers: { 'Content-Type': 'application/pdf' },
        });
      }

      // Step 3: Upload CSV last (triggers the processing pipeline)
      setUploadProgress('Uploading CSV (triggers processing)...');
      await fetch(presigned_urls.csv, {
        method: 'PUT',
        body: csvFile,
        headers: { 'Content-Type': 'text/csv' },
      });

      setUploadProgress('Upload complete! Redirecting to review...');

      // Redirect to review page after short delay
      setTimeout(() => {
        navigate(`/review/${job_id}`);
      }, 1500);
    } catch (err) {
      setUploadError(err.message);
      setUploading(false);
      setUploadProgress('');
    }
  };

  // Stats
  const budgetItemsWithFiles = Object.keys(pdfFiles).filter(
    (k) => pdfFiles[k].length > 0
  );
  const totalFiles = Object.values(pdfFiles).reduce(
    (sum, files) => sum + files.length,
    0
  );
  const canUpload = csvFile && budgetItemsWithFiles.length > 0;

  return (
    <div className="upload-page">
      <header className="upload-header">
        <div className="header-content">
          <div className="header-title">
            <h1>Invoice Document Upload</h1>
            <p className="subtitle">
              Upload CSV Invoice and Supporting PDF Documents
            </p>
          </div>
          <button
            className={`upload-button ${!canUpload ? 'disabled' : ''} ${uploading ? 'uploading' : ''}`}
            onClick={handleUpload}
            disabled={!canUpload || uploading}
          >
            {uploading ? 'Uploading...' : 'Upload'}
          </button>
        </div>
      </header>

      {uploadProgress && (
        <div className="upload-progress-bar">
          <span>{uploadProgress}</span>
        </div>
      )}

      {uploadError && (
        <div className="upload-error-bar">
          <span>Error: {uploadError}</span>
        </div>
      )}

      <div className="upload-stats">
        <div className="stat">
          <span className="stat-label">CSV File:</span>
          <span className={`stat-value ${csvFile ? 'ready' : ''}`}>
            {csvFile ? 'Ready' : 'Not selected'}
          </span>
        </div>
        <div className="stat">
          <span className="stat-label">PDF Files:</span>
          <span className={`stat-value ${totalFiles > 0 ? 'ready' : ''}`}>
            {budgetItemsWithFiles.length} of {BUDGET_ITEMS.length} budget items
            {totalFiles > budgetItemsWithFiles.length &&
              ` (${totalFiles} files)`}
          </span>
        </div>
      </div>

      <main className="upload-main-content">
        <div className="job-name-inline">
          <input
            type="text"
            value={jobName}
            onChange={(e) => setJobName(e.target.value)}
            placeholder="Job Name (Optional)"
            className="job-name-text-input"
            disabled={uploading}
          />
        </div>

        {/* CSV Section — unchanged */}
        <section className="upload-section">
          <div className="section-header">
            <h2>Invoice CSV File</h2>
            <p className="section-description">
              Upload the invoice CSV containing all line items
            </p>
          </div>

          <div className="file-input-card">
            <div className="file-input-wrapper">
              <input
                type="file"
                id="csv-upload"
                accept=".csv"
                onChange={handleCsvChange}
                className="file-input"
              />
              <label htmlFor="csv-upload" className="file-input-label">
                <span className="file-input-button">Select CSV File</span>
                <span className="file-input-text">
                  {csvFile ? csvFile.name : 'No file selected'}
                </span>
              </label>
            </div>
          </div>
        </section>

        {/* PDF Dropzone Section — new */}
        <section className="upload-section">
          <div className="section-header">
            <h2>Budget Item PDFs</h2>
            <p className="section-description">
              Drop all PDF files here — files are auto-sorted by filename
            </p>
          </div>

          <div
            {...getRootProps()}
            className={`dropzone ${isDragActive ? 'dropzone-active' : ''} ${totalFiles > 0 ? 'dropzone-has-files' : ''}`}
          >
            <input {...getInputProps()} />
            <div className="dropzone-content">
              <div className="dropzone-icon">
                {isDragActive ? '+" "' : ''}
              </div>
              <p className="dropzone-text">
                {isDragActive
                  ? 'Drop PDF files here'
                  : 'Drag & drop PDF files or folders here, or click to browse'}
              </p>
              <p className="dropzone-hint">
                Files named after budget items (e.g. Salary.pdf,
                Fringe_Q1.pdf) are auto-classified
              </p>
            </div>
          </div>

          {/* File summary grouped by budget item */}
          {totalFiles > 0 && (
            <div className="file-summary">
              <div className="file-summary-header">
                <span className="file-summary-title">
                  {totalFiles} file{totalFiles !== 1 ? 's' : ''} across{' '}
                  {budgetItemsWithFiles.length} budget item
                  {budgetItemsWithFiles.length !== 1 ? 's' : ''}
                </span>
                <button
                  className="clear-all-btn"
                  onClick={handleClearAll}
                  disabled={uploading}
                >
                  Clear All
                </button>
              </div>

              {BUDGET_ITEMS.filter((item) => pdfFiles[item]?.length > 0).map(
                (item) => (
                  <div key={item} className="file-group">
                    <div className="file-group-header">
                      <span className="file-group-name">{item}</span>
                      <span className="file-count-badge">
                        {pdfFiles[item].length} file
                        {pdfFiles[item].length !== 1 ? 's' : ''}
                      </span>
                    </div>
                    <div className="file-list">
                      {pdfFiles[item].map((file, idx) => (
                        <div
                          key={`${file.name}-${idx}`}
                          className="file-list-item"
                        >
                          <span className="file-list-name" title={file.name}>
                            {file.name}
                          </span>
                          <button
                            className="file-remove-btn"
                            onClick={() => handleRemoveFile(item, idx)}
                            title="Remove file"
                            disabled={uploading}
                          >
                            &times;
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                )
              )}
            </div>
          )}
        </section>
      </main>

      {/* Modal for unrecognized files */}
      {unassignedFiles && (
        <UnassignedFilesModal
          files={unassignedFiles}
          onConfirm={handleModalConfirm}
          onCancel={handleModalCancel}
        />
      )}
    </div>
  );
}

export default UploadPage;
```

**Step 2: Verify the dev server starts without errors**

Run: `cd /home/jchan/dev/dxhub/city-invoice-processor/frontend && npx vite build 2>&1 | tail -5`
Expected: Build succeeds

**Step 3: Commit**

```bash
git add frontend/src/pages/UploadPage.jsx
git commit -m "feat: replace 12 budget-item cards with unified dropzone upload"
```

---

### Task 5: Update UploadPage.css for dropzone and file summary styles

**Files:**
- Modify: `frontend/src/pages/UploadPage.css`

**Step 1: Replace the PDF grid CSS with dropzone + file summary styles**

Keep all existing CSS up through `.file-input-card:not(.has-file) .file-input-text` (line 256). Then **replace** the "File List" section (lines 258-384) and the "PDF Grid" section (lines 418-423) with the new styles below. Keep the responsive section but update it.

Remove these CSS blocks (they are no longer used):
- `.budget-item-header`, `.budget-item-label` (lines 176-189)
- `.file-actions`, `.file-input-label-btn`, `.file-input-label-btn.folder-btn` (lines 314-352)
- `.file-input-hint` (lines 377-384)
- `.pdf-grid` (lines 418-423)

Add these new CSS blocks:

```css
/* Dropzone */
.dropzone {
  border: 2px dashed var(--color-border);
  border-radius: 4px;
  padding: 3rem 2rem;
  text-align: center;
  cursor: pointer;
  transition: all 0.2s;
  background: var(--color-surface);
}

.dropzone:hover {
  border-color: var(--color-accent);
  background: rgba(42, 75, 124, 0.02);
}

.dropzone-active {
  border-color: var(--color-accent);
  background: rgba(42, 75, 124, 0.05);
  border-style: solid;
}

.dropzone-has-files {
  border-color: var(--color-success);
}

.dropzone-content {
  pointer-events: none;
}

.dropzone-icon {
  font-size: 2rem;
  margin-bottom: 0.75rem;
  color: var(--color-text-secondary);
}

.dropzone-text {
  font-family: var(--font-serif);
  font-size: 1.125rem;
  font-weight: 500;
  margin-bottom: 0.5rem;
  color: var(--color-text);
}

.dropzone-hint {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

/* File Summary */
.file-summary {
  margin-top: 1.5rem;
  border: 2px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-surface);
}

.file-summary-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 1rem 1.25rem;
  border-bottom: 1px solid var(--color-border);
}

.file-summary-title {
  font-family: var(--font-mono);
  font-size: 0.8125rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-text-secondary);
}

/* File Group (per budget item) */
.file-group {
  border-bottom: 1px solid var(--color-border);
}

.file-group:last-child {
  border-bottom: none;
}

.file-group-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.75rem 1.25rem;
  background: rgba(0, 0, 0, 0.02);
}

.file-group-name {
  font-family: var(--font-serif);
  font-size: 1rem;
  font-weight: 600;
}
```

**Step 2: Update the responsive section**

Replace the existing `@media (max-width: 768px)` block with:

```css
@media (max-width: 768px) {
  .upload-header .header-content {
    flex-direction: column;
    gap: 1.5rem;
    align-items: flex-start;
  }

  .upload-button {
    align-self: stretch;
  }

  .upload-stats {
    flex-direction: column;
    gap: 1rem;
  }

  .file-input-label {
    flex-direction: column;
    align-items: flex-start;
  }

  .file-input-button {
    width: 100%;
    text-align: center;
  }

  .dropzone {
    padding: 2rem 1rem;
  }

  .assignment-row {
    flex-direction: column;
    align-items: stretch;
    gap: 0.5rem;
  }

  .assignment-select {
    min-width: unset;
  }
}
```

**Step 3: Verify build succeeds**

Run: `cd /home/jchan/dev/dxhub/city-invoice-processor/frontend && npx vite build 2>&1 | tail -5`
Expected: Build succeeds

**Step 4: Commit**

```bash
git add frontend/src/pages/UploadPage.css
git commit -m "feat: replace budget-item grid CSS with dropzone and file summary styles"
```

---

### Task 6: Manual testing and edge cases

**Files:** None (testing only)

**Step 1: Start the dev server**

Run: `cd /home/jchan/dev/dxhub/city-invoice-processor/frontend && npm run dev`

**Step 2: Test auto-classification**

Open the upload page in a browser. Drop files named:
- `Salary.pdf` — should auto-classify to Salary
- `Fringe_Q1_Report.pdf` — should auto-classify to Fringe
- `Space_Rental_Occupancy_Costs.pdf` — should auto-classify to Space Rental/Occupancy Costs
- `random_document.pdf` — should trigger the assignment modal

Verify:
- Auto-classified files appear grouped in the file summary
- The modal appears for `random_document.pdf`
- Selecting a budget item in the modal and clicking "Confirm Assignments" adds the file
- Clicking "Remove These Files" dismisses the modal without adding

**Step 3: Test upload flow**

- Select a CSV file
- Drop multiple PDFs
- Verify the Upload button enables
- Verify the stats bar shows correct counts
- Click Upload and verify it calls `/api/upload/start` with the correct payload structure (check Network tab)

**Step 4: Test edge cases**

- Drop duplicate files (same name + size): should deduplicate
- Drop non-PDF files: should be ignored
- Drop a mix of recognized and unrecognized files: recognized go straight to summary, unrecognized trigger modal
- Clear All: should remove all files
- Remove individual files with the X button

**Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: address edge cases from manual testing"
```

---

## Notes

**What stays the same:**
- CSV upload section (unchanged)
- S3 path structure: `{slug}.pdf` for single files, `{slug}/{filename}` for multiple files per budget item
- The `/api/upload/start` request payload format
- The presigned URL upload flow
- The `BUDGET_ITEMS` constant (now imported from the shared classifier utility)
- Job name input
- Upload stats bar
- Header and upload button

**What changes:**
- 12 separate budget-item cards → 1 dropzone + grouped file summary
- Manual file-per-bucket selection → auto-classification from filename
- No folder select button (react-dropzone handles folders natively via drag-and-drop)
- New modal for unrecognized files
- `BUDGET_ITEMS` and `slugify` moved to shared utility (out of UploadPage.jsx)

**Prefix matching rationale:**
The classifier uses prefix matching (longest slug first) so that files like `Salary_January_2025.pdf` match "Salary" and `Travel_and_Conferences_Receipts.pdf` matches "Travel and Conferences". The sort-by-length-descending ensures "Space Rental/Occupancy Costs" is tried before "Other" or "Supplies" to avoid false positives.

**"Other" budget item risk:**
The slug `other` is short and could match filenames like `other_documents.pdf` — this is actually correct behavior. However, a filename like `otherwise.pdf` would NOT match because `otherwise` slug is `otherwise`, which neither equals `other` nor starts with `other_`.

---

Plan complete and saved to `docs/plans/2026-02-19-unified-dropzone-upload.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

Which approach?
