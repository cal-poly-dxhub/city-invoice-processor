# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Invoice reconciliation system for the City of San Francisco. Extracts text from PDF evidence documents, matches invoice CSV line items to supporting pages using AWS Bedrock and Textract, and provides a React review UI for human validation.

**12 canonical budget items:** Salary, Fringe, Contractual Service, Equipment, Insurance, Travel and Conferences, Space Rental/Occupancy Costs, Telecommunications, Utilities, Supplies, Other, Indirect Costs

## Two Frontend Directories (IMPORTANT)

- **`frontend/`** — **Production frontend** deployed via CDK (`FrontendStack`). React Router with Upload/Review/JobHistory pages. Built with `cd frontend && npm run build`, deployed from `frontend/dist/`. **All production changes go here.**
- **`review-ui/`** — **Dev-only frontend** for local testing against `backend/jobs/` data. Single-page app, no routing. Served via `cd review-ui && npm run dev` with symlinks to local data. **Not deployed.**

Key differences:
- `frontend/` imports config from `src/config.js` (`API_BASE`, `DATA_BASE`); `review-ui/` uses inline `VITE_API_URL`
- `frontend/` loads PDFs from S3/CloudFront; `review-ui/` from local symlinks
- `frontend/` lifts `userEditedCandidates` and `userAnnotations` state to `ReviewPage.jsx`

## Common Commands

### Backend

```bash
cd backend
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Run reconciliation pipeline
python -m invoice_recon.cli run --csv /path/to/invoice.csv --pdf-dir /path/to/pdfs --job-id my_job

# Validate output
python -m invoice_recon.cli validate --job-id my_job

# Run all tests
pytest tests/

# Run a single test file or test
pytest tests/test_matching_salary.py
pytest tests/test_matching_salary.py::test_specific_function

# Clear extraction cache for re-run
rm backend/jobs/my_job/artifacts/index.sqlite
```

### Frontend (Production)

```bash
cd frontend
npm install

npm run dev          # Dev server (port 3000)
npm run build        # Production build → dist/
npm test             # vitest run (one-shot)
npm run test:watch   # vitest watch mode
```

### CDK / Deployment

**Requires Docker** — CDK synth bundles the backend Lambda layer and builds the `index_document` Docker image. If Docker is not accessible (`permission denied ... docker.sock`), ask the user to deploy from a terminal with Docker access.

```bash
# Full deploy (bootstraps, builds frontend, deploys all stacks)
scripts/deploy.sh

# Manual CDK commands
cd infra
source venv/bin/activate   # Note: venv is at infra/venv/, not .venv/
pip install -r requirements.txt
cdk synth            # Synthesize CloudFormation (also auto-builds frontend)
cdk deploy --all     # Deploy all stacks
cdk diff             # Preview changes
```

## Architecture

### Serverless Pipeline (Step Functions)

```
S3 PutObject (invoice.csv) → EventBridge → Step Functions:
  ParseCSV → DiscoverPDFs → Map(IndexDocument per PDF, max 5) → AssembleAndMatch
                                                                      ↓
                                                              reconciliation.json → S3
```

Each Lambda in `infra/lambda/`:
| Lambda | Memory | Timeout | Purpose |
|--------|--------|---------|---------|
| `parse_csv` | 512MB | 30s | Parse invoice CSV from S3 |
| `discover_pdfs` | 512MB | 30s | Find PDFs in S3, map to budget items |
| `index_document` | 2048MB | 10min | **Docker-based.** PyMuPDF extraction, calls resolve_page/extract_entities |
| `resolve_page` | 512MB | 60s | Textract fallback for single pages |
| `extract_entities` | 512MB | 5min | Bedrock entity extraction |
| `assemble_and_match` | 2048MB | 15min | Run matching, write reconciliation.json |
| `match_sub_item` | 1024MB | 30s | On-demand API for sub-item extraction |

`index_document` uses a Docker image (`infra/lambda/index_document/Dockerfile`) because PyMuPDF needs C compilation.

### Three CDK Stacks (`infra/stacks/`)

- **StorageStack** — S3 data bucket (uploads + jobs) + DynamoDB cache table (replaces SQLite in Lambda)
- **ProcessingStack** — All Lambdas, Step Functions state machine, API Gateway, EventBridge rule, shared backend layer
- **FrontendStack** — S3 static hosting + CloudFront (3 origins: frontend bucket, data bucket for `/jobs/*` and `/uploads/*`, API Gateway for `/api/*`). Auto-builds frontend during CDK synthesis.

### Backend Modules (`backend/invoice_recon/`)

| Module | Role |
|--------|------|
| `cli.py` | Typer CLI entry point, orchestrates local pipeline |
| `models.py` | Pydantic models (LineItem, DocumentRef, PageRecord, CandidateEvidenceSet, SourceFile, SubItem, PageReference, CompletionStatus, UserEdits) |
| `csv_parser.py` | Parses invoice CSV into LineItem objects |
| `budget_items.py` | Budget item naming, slug normalization, PDF discovery |
| `pdf_extract.py` | PDF text extraction (PyMuPDF + Textract fallback) |
| `table_parser.py` | Parses Textract TABLE/CELL blocks |
| `textract_text.py` | AWS Textract API integration |
| `bedrock_entities.py` | Bedrock entity extraction (names, amounts, dates, orgs) |
| `bedrock_vision.py` | Bedrock Vision for table detection |
| `matching.py` | Candidate generation and scoring |
| `index_store.py` | SQLite cache (local) / DynamoDB cache (Lambda) |
| `navigation_groups.py` | UI navigation grouping |
| `output_contract.py` | reconciliation.json output formatting |
| `config.py` | Environment variable configuration |

### Lambda Shared Utilities (`infra/lambda/shared/`)

- `cache.py` — DynamoDB cache abstraction (replaces SQLite `index_store.py` in Lambda)
- `s3_utils.py` — S3 upload/download helpers

## Critical Architecture Concepts

### PDF Coordinate Systems and Rotation

**Backend extraction (`pdf_extract.py`):**
- PyMuPDF `page.get_text("words")` returns coordinates in the **original (pre-rotation) coordinate space**
- MUST normalize using `page.mediabox` (not `page.rect`) because rotated pages have different rect dimensions
- Coordinates normalized to 0-1 scale: `left = x0 / mediabox.width`, `top = y0 / mediabox.height`

**Frontend rendering (`PDFViewer.jsx`):**
- PDF.js automatically applies inherent page rotation (from PDF metadata)
- Store `page.rotate` value in a ref (not state) to avoid timing issues
- Calculate `totalRotation = inherentRotation + userRotation`
- Transform highlights via `transformHighlight()` before pixel conversion
- Use `canvas.offsetWidth/offsetHeight` (display size), NOT `canvas.width/height` (drawing buffer) — canvas may be CSS-scaled

**Rotation transforms:**
```javascript
// 90° CW:  (x,y) → (y, 1-x-w), swap dimensions
// 180°:    (x,y) → (1-x-w, 1-y-h)
// 270° CW: (x,y) → (1-y-h, x), swap dimensions
```

### Text Extraction Strategy

**Default (two-tier):**
1. PyMuPDF first (free, fast) — extracts text + word bounding boxes
2. If text < `TEXT_MIN_CHARS` or no geometry → Textract fallback (paid, OCR)
3. Controlled by `TEXTRACT_MODE`: `auto` (default), `always`, `never`

**With `TABLE_DETECTION_ENABLED=true` (three-tier):**
0. Bedrock Vision detects if page is tabular
1. PyMuPDF `find_tables()` — free, fast, good for digital spreadsheets
2. Textract fallback if PyMuPDF quality insufficient (min rows, cells, coverage thresholds)

**Text source values:** `pymupdf`, `pymupdf_table`, `textract`, `textract_table`

**PyMuPDF vs Textract cell formats (critical difference):**
- **PyMuPDF** cannot detect merged cells — when rows are visually merged, it stacks all values into one cell with newline separators (`"2.68\n72.00\n20.60"`), always `row_span=1`. Each line corresponds to the next visual row.
- **Textract** properly detects merged cells — reports `row_span > 1` but joins text with spaces, no newlines (`"2.68 72.00 20.60"`)
- `_associate_amounts_with_budget_items()` in `bedrock_entities.py` handles both: line-offset for PyMuPDF newline cells, row_span fallback for Textract merged cells. Exact single-cell matches always take priority.

### Matching Logic (`matching.py`)

**Salary/Fringe items** — Employee name matching with spatial proximity:
- +0.35 last name, +0.30 first name, +0.40 amount match, +0.10 doc type
- Proximity bonus: +0.25 (close), +0.10 (moderate), -0.20 penalty (far apart)
- Proximity uses word bounding boxes to verify first+last names appear near each other on the page
- Falls back to standard name matching when word geometry unavailable

**Other budget items** — Keyword matching, org name matching, expected doc type, amount-based component matching

**Budget item-aware amount matching:** Extracted amounts are tagged with their budget item (from table row labels or page doc_id). Matching filters amounts by budget item BEFORE trying sum-combinations, preventing cross-category confusion.

**Candidate filtering:** Backend keeps all candidates scoring >= 0.1. Frontend provides a dynamic confidence slider (default 50%) for real-time filtering without re-running the pipeline.

### Sub-Item Matching (`infra/lambda/match_sub_item/handler.py`)

On-demand API for matching sub-items (individual vendors within a budget item GL page). Flow:

1. `handle_match()` — entry point, checks S3 cache first
2. `generate_candidates_for_line_item()` — core matching (amount match, keyword match, org match)
3. `_filter_candidates_by_row_texts()` — filters candidates by distinctive keyword tokens from table row text. Computes token specificity (vendor names appearing on <10% of pages are "specific"). **Exempt**: single-page exact amount matches (score >= 0.90 with "Exact amount match" rationale) always survive this filter.
4. `_recover_combined_matches()` — if no candidates survive filtering, scans all pages for BOTH the target amount AND a distinctive keyword token. Creates high-confidence (0.95) candidates. Amount search handles OCR variations: `$72.00`, `$ 72`, `72.00`, etc.

**Key gotcha:** `table_row_texts` used for keyword filtering come from `_extract_row_words()`, which reads table cells at the `table_row_index` assigned by `bedrock_entities.py`. If `table_row_index` is wrong (e.g., due to the PyMuPDF multi-value cell bug), ALL sub-items for that budget item will have contaminated keywords.

**Amount tolerance:** Uses `abs(v - target) < 0.01` for floating-point comparison. Be aware that `abs(149.19 - 149.20)` is ~0.00999 which passes this check — near-miss amounts can be treated as "exact" matches.

### Payment/Invoice Verification & Completion Status

Each line item and sub-item can be marked with **Payment** and **Invoice** evidence — specific pages that prove payment was made or an invoice was received.

**Data model (`models.py`):**
- `PageReference` — `{ page: int, doc_id: str }` — a virtual page number + virtual doc_id (budget slug)
- `CompletionStatus` — `{ payment: List[PageReference], invoice: List[PageReference] }` — evidence arrays per item
- `UserEdits.completion_status` — `Dict[str, CompletionStatus]` — keyed by `row_id` (line items) or `sub_item_id` (sub-items)

**Frontend state flow:**
- `ReviewPage.jsx` owns `completionStatus` state, persisted to `user_edits.json` via auto-save
- `PDFViewer.jsx` renders per-page Payment/Invoice toggle buttons. Clicking toggles a `PageReference` in/out of the item's evidence array
- `LineItemCard.jsx` shows read-only checkboxes derived from evidence arrays
- Line items with sub-items: checkboxes are **rollup** — checked only when ALL sub-items have evidence
- Line items without sub-items: derived from their own page evidence directly

**Virtual page references:** `PageReference.page` is the virtual page number, `PageReference.doc_id` is the budget slug (e.g., `"telecommunications"`). To resolve to a physical file, use `resolveVirtualPage()` from `frontend/src/utils/virtualPages.js`.

### Reconciliation Report

The "Finish Reconciliation" button opens a report modal (`ReconciliationReport.jsx`) showing all line items with their verification status.

**Layout:** Two sections — "Needs Attention" (items missing payment and/or invoice evidence) listed first, then "Completed" items. Both sections sorted numerically by `row_index`.

**Per-item display:** Row number, budget item, amount, employee name (Salary/Fringe), payment/invoice status with clickable page references. Items with sub-items show rollup counts (e.g., "2/3 sub-items") and are expandable.

**Page reference format:** `filename.pdf p.N (vp M)` — physical filename + local page, plus virtual page number. Clicking navigates to that item/page in the PDFViewer.

**CSV export:** "Export CSV" button generates a client-side CSV download (`frontend/src/utils/csvExport.js`). Columns: Row, Budget Item, Label, Amount, Employee, Payment Status, Payment Evidence, Invoice Status, Invoice Evidence. Sub-items get dotted row numbers (1.1, 1.2).

### Sub-Item Merging (Balance/Allocation)

When a GL page has both Balance and Allocation columns, `handle_auto_extract()` in `match_sub_item/handler.py` groups amounts by `table_row_index` and merges same-row amounts into a single sub-item proposal. The `SubItem.amounts` field holds multiple amounts (e.g., `[19.16, 2.68]`), displayed in the UI as `$19.16 / $2.68`. Matching runs once per amount with OR logic — deduplicates candidates by page set, keeping the highest score.

### Multi-File Document Support (Virtual Pages)

Multiple PDFs per budget item are assembled into a virtual document with sequential page numbers. `DocumentRef.source_files` maps virtual pages back to physical files via `page_offset`. Frontend resolves virtual pages to correct physical PDFs via `resolveVirtualPage()` in `frontend/src/utils/virtualPages.js` (shared between `PDFViewer.jsx`, `ReconciliationReport.jsx`, and `csvExport.js`). Flat files produce one-entry `source_files` for backward compatibility. `getSourceFiles(doc)` in the same util provides backward-compat fallback using `LEGACY_FILENAME_MAP` for older reconciliation.json files without `source_files`.

### Caching and Incremental Processing

**Two separate caches to be aware of when debugging:**

**1. Entity/extraction cache (DynamoDB in Lambda, SQLite locally):**
- Stores: document SHA256 hashes, extracted text with text hash, Bedrock entity results with entity hash, word bounding boxes, table structures
- **Entities are stored fully enriched** — includes `budget_item`, `source`, and `table_row_index` fields already computed by `_associate_amounts_with_budget_items()`
- Cache invalidation is **content-hash-based** (file_sha256, text_sha256), NOT code-version-based
- **If you change entity processing logic (e.g., amount association, budget item mapping), you MUST clear the cache** — redeploying the Lambda alone won't produce different results
- Clear locally: `rm backend/jobs/my_job/artifacts/index.sqlite`
- Clear DynamoDB: scan and batch-delete all items from the cache table (use `aws dynamodb scan` + `batch-write-item` with DeleteRequest; add sleep between batches to avoid throttling)
- DynamoDB table name: find via `aws cloudformation describe-stacks --stack-name InvoiceProcessorStorage --query "Stacks[0].Outputs"`

**2. Match result cache (S3, match_sub_item Lambda only):**
- `match_sub_item` Lambda caches match results to S3 at `jobs/{job_id}/artifacts/sub_item_matches/{sub_item_id}.json`
- Purpose: survive API Gateway's 29-second timeout (Lambda runs up to 30s, caches result, frontend polls)
- These are NOT invalidated by redeployment — if you change matching logic in `handler.py`, stale cached results may be served
- Clear by deleting the `sub_item_matches/` prefix in S3 for the affected job

**3. Re-running the pipeline:**
- Step Functions can be re-triggered with the same input JSON (same job_id) without re-uploading files
- Input format: `{"job_id": "uploads/{uuid}/invoice.csv", "bucket": "...", "csv_key": "uploads/{uuid}/invoice.csv", "pdf_prefix": "PLACEHOLDER"}`
- Find the state machine ARN via `aws stepfunctions list-state-machines`
- Start execution: `aws stepfunctions start-execution --state-machine-arn <arn> --input '<json>'`

## Configuration

### Backend Environment Variables (see `backend/.env.example`)

```env
AWS_REGION=us-west-2
BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
TEXT_MIN_CHARS=40              # Min chars for PyMuPDF sufficiency
TEXTRACT_MODE=auto             # auto | always | never
TEXTRACT_MAX_LINES=300
TABLE_DETECTION_ENABLED=false  # Bedrock vision table pre-detection
MIN_TABLE_ROWS=2               # PyMuPDF table quality thresholds
MIN_TABLE_CELLS=4
MIN_TABLE_CELL_COVERAGE=0.5
PYMUPDF_TABLE_STRATEGY=lines   # lines | text | lines_strict
MIN_CANDIDATE_SCORE=0.1
MAX_WORKERS=3
```

### Frontend Config (`frontend/src/config.js`)

- `API_BASE` — Set via `VITE_API_URL` env var; defaults to `''` (same-origin via CloudFront)
- `DATA_BASE` — Always `''` (same-origin)

### Frontend Utilities (`frontend/src/utils/`)

| Module | Role |
|--------|------|
| `virtualPages.js` | Shared `resolveVirtualPage()` and `getSourceFiles()` for virtual→physical page resolution |
| `csvExport.js` | CSV report generation (`generateReportCsv()`) and download (`downloadCsv()`) |
| `budgetItemClassifier.js` | Budget item slug normalization and PDF file classification |
| `keywordSync.js` | Keyword synchronization between sub-items and matching |

### Frontend Components (`frontend/src/components/`)

| Component | Role |
|-----------|------|
| `PDFViewer.jsx` | PDF rendering, highlights, search, page-level Payment/Invoice toggles |
| `LineItemCard.jsx` | Sidebar card with read-only Payment/Invoice checkboxes, sub-item list |
| `ReconciliationReport.jsx` | Report modal body: two-section item list, expandable sub-items, CSV export |
| `CreateSubItemDialog.jsx` | Auto-extract and manual sub-item creation dialog |
| `FilterBar.jsx` | Budget item and match type filters |
| `SearchBar.jsx` | Text search within PDF pages |
| `Stats.jsx` | Match quality statistics |

## Budget Item Naming

**CSV normalization (`budget_items.py`):** Case-insensitive, handles underscores/spaces. `SALARY_TOTAL` → `Salary`, `SPACE_RENTAL` → `Space Rental/Occupancy Costs`.

**PDF filename matching:** Slugified (lowercase, non-alphanumeric → underscore). `Space Rental/Occupancy Costs` matches `space_rental_occupancy_costs.pdf`. PDFs can be flat files in `test-files/pdf/` or in subdirectories named after budget items (subdirectory takes precedence).

## Debugging Tips

**Highlight positioning:** Check console for `PDFViewer: Candidate highlights from backend` log. Coordinates must be 0-1 range — if > 1.0, backend used wrong page dimensions (delete cache, re-run). Inspect canvas `offsetWidth` vs `width` (should differ if CSS-scaled).

**Text extraction:** Check `text_source` field in output. If table pages extract poorly with PyMuPDF, set `TABLE_DETECTION_ENABLED=true`. Look for "PyMuPDF: Extracted N tables" vs "falling back to Textract" in logs.

**Matching:** Check `candidates[0].rationale` for logic used. Look for "Filtered to [BudgetItem] amounts" messages for budget item filtering. Scores: >0.8 excellent, 0.5-0.8 good, 0.1-0.5 low confidence. Lower the UI confidence slider to see hidden low-score candidates.

**Sub-item matching issues:** When sub-items return wrong pages:
1. Check `table_row_texts` — if ALL sub-items show the same keywords (e.g., all say "granite"), the `table_row_index` assignment in `bedrock_entities.py` is likely wrong (PyMuPDF multi-value cell bug). Fix requires clearing DynamoDB entity cache and re-running the pipeline.
2. Check if amounts match — compare the sub-item's target amount with what appears on evidence pages. OCR can produce `$ 72` instead of `$72.00`. The handler's text fallback regex handles this.
3. Check keyword filtering — `_filter_candidates_by_row_texts()` may discard valid candidates if the vendor name appears in a slightly different format. Exact amount matches (score >= 0.90) are exempt from this filter.
4. Check S3 match cache — stale results at `jobs/{job_id}/artifacts/sub_item_matches/{sub_item_id}.json` may mask fixes. Delete and re-match.

## Output Structure

```
backend/jobs/<job_id>/artifacts/
├── reconciliation.json   # Main output consumed by frontend
├── index.sqlite          # Extraction cache (local only)
└── user_edits.json       # User overrides, sub-items, and completion_status
```

`user_edits.json` structure:
```json
{
  "overrides": [...],
  "sub_items": [...],
  "completion_status": {
    "row_51": {
      "payment": [{"page": 3, "doc_id": "telecommunications"}],
      "invoice": [{"page": 7, "doc_id": "telecommunications"}]
    },
    "row_51_a": {
      "payment": [{"page": 3, "doc_id": "telecommunications"}],
      "invoice": []
    }
  }
}
```
