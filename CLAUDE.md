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

```bash
# Full deploy (bootstraps, builds frontend, deploys all stacks)
scripts/deploy.sh

# Manual CDK commands
cd infra
python3 -m venv .venv && source .venv/bin/activate
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
| `models.py` | Pydantic models (LineItem, DocumentRef, PageRecord, CandidateEvidenceSet, SourceFile) |
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

### Matching Logic (`matching.py`)

**Salary/Fringe items** — Employee name matching with spatial proximity:
- +0.35 last name, +0.30 first name, +0.40 amount match, +0.10 doc type
- Proximity bonus: +0.25 (close), +0.10 (moderate), -0.20 penalty (far apart)
- Proximity uses word bounding boxes to verify first+last names appear near each other on the page
- Falls back to standard name matching when word geometry unavailable

**Other budget items** — Keyword matching, org name matching, expected doc type, amount-based component matching

**Budget item-aware amount matching:** Extracted amounts are tagged with their budget item (from table row labels or page doc_id). Matching filters amounts by budget item BEFORE trying sum-combinations, preventing cross-category confusion.

**Candidate filtering:** Backend keeps all candidates scoring >= 0.1. Frontend provides a dynamic confidence slider (default 50%) for real-time filtering without re-running the pipeline.

### Multi-File Document Support (Virtual Pages)

Multiple PDFs per budget item are assembled into a virtual document with sequential page numbers. `DocumentRef.source_files` maps virtual pages back to physical files via `page_offset`. Frontend resolves virtual pages to correct physical PDFs via `resolveVirtualPage()`. Flat files produce one-entry `source_files` for backward compatibility.

### Caching and Incremental Processing

SQLite cache (`index_store.py` locally, DynamoDB in Lambda) stores: document SHA256 hashes, extracted text with text hash, Bedrock entity results with entity hash, word bounding boxes, table structures. Cache invalidation triggers on hash changes. Delete `index.sqlite` to force full re-run locally.

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

## Budget Item Naming

**CSV normalization (`budget_items.py`):** Case-insensitive, handles underscores/spaces. `SALARY_TOTAL` → `Salary`, `SPACE_RENTAL` → `Space Rental/Occupancy Costs`.

**PDF filename matching:** Slugified (lowercase, non-alphanumeric → underscore). `Space Rental/Occupancy Costs` matches `space_rental_occupancy_costs.pdf`. PDFs can be flat files in `test-files/pdf/` or in subdirectories named after budget items (subdirectory takes precedence).

## Debugging Tips

**Highlight positioning:** Check console for `PDFViewer: Candidate highlights from backend` log. Coordinates must be 0-1 range — if > 1.0, backend used wrong page dimensions (delete cache, re-run). Inspect canvas `offsetWidth` vs `width` (should differ if CSS-scaled).

**Text extraction:** Check `text_source` field in output. If table pages extract poorly with PyMuPDF, set `TABLE_DETECTION_ENABLED=true`. Look for "PyMuPDF: Extracted N tables" vs "falling back to Textract" in logs.

**Matching:** Check `candidates[0].rationale` for logic used. Look for "Filtered to [BudgetItem] amounts" messages for budget item filtering. Scores: >0.8 excellent, 0.5-0.8 good, 0.1-0.5 low confidence. Lower the UI confidence slider to see hidden low-score candidates.

## Output Structure

```
backend/jobs/<job_id>/artifacts/
├── reconciliation.json   # Main output consumed by frontend
├── index.sqlite          # Extraction cache (local only)
└── user_edits.json       # Optional manual overrides
```
