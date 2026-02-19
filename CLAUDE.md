# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Invoice reconciliation system with two components:
1. **Backend**: Python pipeline that extracts text from PDFs, matches invoice CSV line items to supporting evidence pages using AWS Bedrock and Textract
2. **Frontend**: React-based review UI for viewing reconciliation results with PDF rendering and text highlighting

### Two Frontend Directories (IMPORTANT)

There are **two separate frontend codebases** — changes must go in the correct one:

- **`frontend/`** — **Production frontend** deployed via CDK (`FrontendStack`). Uses React Router with Upload/Review/JobHistory pages. Built with `cd frontend && npm run build`, deployed from `frontend/dist/`. **All production changes should be made here.**
- **`review-ui/`** — **Original dev frontend** used for local development against local `backend/jobs/` data. Single-page app without routing. Served via `cd review-ui && npm run dev` with symlinks to local `backend/` and `test-files/`. **Not deployed to production.**

Key differences in `frontend/` vs `review-ui/`:
- `frontend/` imports config from `src/config.js` (`API_BASE`, `DATA_BASE`), not inline `VITE_API_URL`
- `frontend/` loads PDFs from S3/CloudFront via `DATA_BASE`, not from local symlinks
- `frontend/` lifts `userEditedCandidates` and `userAnnotations` state to `ReviewPage.jsx` (not in PDFViewer)
- `review-ui/` has `services/api.js` for sub-item API calls; `frontend/` has the same at `src/services/api.js`

## Critical Architecture Concepts

### PDF Coordinate Systems and Rotation

**Backend extraction (pdf_extract.py):**
- PyMuPDF's `page.get_text("words")` returns coordinates in the **original (pre-rotation) coordinate space**
- MUST normalize using `page.mediabox` (not `page.rect`) because rotated pages have different rect dimensions
- Coordinates are normalized to 0-1 scale: `left = x0 / mediabox.width`, `top = y0 / mediabox.height`
- Stored in `PageRecord.words` as list of dicts with keys: `text`, `left`, `top`, `width`, `height`

**Frontend rendering (PDFViewer.jsx):**
- PDF.js automatically applies inherent page rotation (stored in PDF metadata)
- Store `page.rotate` value immediately in a ref (not state) to avoid timing issues
- When rendering highlights, calculate `totalRotation = inherentRotation + userRotation`
- Transform highlight coordinates using `transformHighlight()` before converting to pixels
- Convert normalized coords to pixels using `canvas.offsetWidth/offsetHeight` (display size), NOT `canvas.width/height` (drawing buffer size)
- Canvas may be CSS-scaled, so display size ≠ buffer size

**Rotation transformation formula:**
```javascript
// 90° clockwise: (x,y) → (y, 1-x-w), dimensions swap
// 180°: (x,y) → (1-x-w, 1-y-h)
// 270° clockwise: (x,y) → (1-y-h, x), dimensions swap
```

### Text Extraction Strategy

**Three-Tier Extraction (when TABLE_DETECTION_ENABLED=true):**
When table detection is enabled, the system uses a cost-optimized approach:

0. **Bedrock Vision Detection** - Use Claude to detect if page is primarily tabular
1. **PyMuPDF Table Extraction** (NEW) - Try extracting tables with PyMuPDF `find_tables()` (FREE, FAST)
   - Quality validation: min rows, min cells, cell coverage ratio
   - If sufficient → use PyMuPDF tables (source: `pymupdf_table`)
2. **Textract Fallback** - If PyMuPDF insufficient → use AWS Textract (PAID, SLOWER)
   - Better for scanned PDFs, irregular tables, merged cells
   - Source marked as `textract_table`

**Without Table Detection (default):**
Standard two-tier approach in `pdf_extract.py`:
1. Try PyMuPDF first (fast, free) - extracts text AND word bounding boxes
2. If text insufficient (`< TEXT_MIN_CHARS`) OR no geometry extracted, fall back to AWS Textract (slow, paid)
3. Mode controlled by `TEXTRACT_MODE` env var: `auto` (default), `always`, `never`

**Text Source Values:**
- `pymupdf` - PyMuPDF text extraction (no tables)
- `pymupdf_table` - PyMuPDF with table extraction (NEW)
- `textract` - Textract fallback (insufficient text/geometry)
- `textract_table` - Textract for table pages (after PyMuPDF failed or skipped)

**Why this matters:**
- **Cost savings**: PyMuPDF table extraction is free and fast (30-40% reduction in Textract costs for digital spreadsheets)
- **Quality validation**: System automatically falls back to Textract for complex cases
- **Best of both worlds**: Fast/free for simple tables, paid API for complex cases
- PyMuPDF works best for digital PDFs with visible grid lines
- Textract excels at scanned PDFs, irregular tables, and merged cells
- Enable table detection (`TABLE_DETECTION_ENABLED=true`) when dealing with invoice spreadsheets

### Matching Logic Flow

**Candidate Filtering:**
- Backend includes nearly all candidates (only filters out score < 0.1 garbage matches)
- Frontend provides a dynamic confidence slider (0-100%) to filter candidates in real-time
- Default threshold: 50% (0.5 score)
- Users can adjust the slider to see more/fewer candidates without re-running the pipeline
- Items with only below-threshold candidates show as "No Match" instead of displaying unhelpful pages

**Salary/Fringe items (matching.py):**
- Uses employee name matching with spatial proximity validation:
  - +0.35 for last name exact match
  - +0.30 for first name exact match
  - +0.25 bonus when first/last names appear very close together (proximity >= 0.7)
  - +0.10 bonus when names moderately close (proximity >= 0.4)
  - -0.20 penalty when names far apart (proximity < 0.2, likely different people)
  - +0.40 for amount match validation
  - +0.10 for doc type (timecard/paystub)
- Proximity validation solves the "same last name" problem by verifying first and last names appear spatially close on the page using word bounding boxes
- Only applies when both first and last names present AND word geometry available (`page.words`)
- Falls back to standard name matching when word geometry unavailable
- Generates amount-based candidates when full name + amount found on single page
- Generates cross-page candidates when name/amount split across pages

**Other budget items:**
- Keyword-based matching using explanation text
- Organization name matching
- Expected document type matching
- **Amount-based component matching with budget item filtering** (see below)

**Budget Item-Aware Amount Matching (NEW):**
- Each extracted amount is associated with a budget item:
  - **Table-based**: If amount appears in a Textract TABLE structure, it inherits the budget item from that table row
  - **Page default**: If amount is not in a table, it inherits the page's doc_id (e.g., amounts in Salary.pdf → "Salary")
- Component-summing algorithm filters amounts by budget item BEFORE trying combinations
- Example: A Telecommunications line item will ONLY match amounts labeled "Telecommunications", never Salary/Fringe amounts from summary tables
- Amount schema includes: `budget_item`, `source` ("table_row" | "page_default"), `table_row_index` (optional)
- Rationale includes filtering messages like "Filtered to Telecommunications amounts (excluded 2 from other budget items)"

**Candidate generation always produces:**
- `score`: float ranking confidence
- `rationale`: list of human-readable reasons (includes proximity information for salary/fringe, budget item filtering)
- `highlights`: dict mapping page_number → list of bounding boxes for matched terms

### Caching and Incremental Processing

SQLite cache (`index_store.py`) stores:
- Document hashes (SHA256) to detect file changes
- Extracted text and source (pymupdf/textract) with text hash
- Bedrock entity extraction results with entity hash
- Word bounding boxes (JSON blob)
- **Table structures** (JSON blob, from Textract TABLE/CELL blocks)

**Cache invalidation:**
- Document re-extracted if file SHA256 changes
- Page entities re-extracted if text SHA256 changes
- Delete `index.sqlite` to force full re-run

### Frontend State Management

**Critical state in App.jsx:**
- `minConfidenceScore`: Dynamic confidence threshold (0-1) controlled by slider
- Candidates are filtered client-side based on this threshold
- Items with no above-threshold candidates show as "No Match"

**Critical state in PDFViewer.jsx:**
- `highlights`: normalized (0-1) coordinates from backend, never modified
- `pageRotations`: user-applied rotation per page (0, 90, 180, 270)
- `pageInherentRotations`: ref storing PDF's inherent rotation (from metadata)
- `canvasRefs`/`containerRefs`: DOM element references for rendering

**Re-render triggers:**
- `minConfidenceScore` changes → re-filter candidates and update match types
- `pageRotations` changes → re-render canvas with new rotation
- `highlights` changes → re-render overlay
- Window resize → highlights auto-scale (normalized coords → pixels at render time)

## Common Commands

### Backend

```bash
# Setup
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run reconciliation
python -m invoice_recon.cli run \
  --csv /path/to/invoice.csv \
  --pdf-dir /path/to/pdfs \
  --job-id my_job

# Validate output
python -m invoice_recon.cli validate --job-id my_job

# Run tests
pytest tests/

# Clear cache for re-run
rm backend/jobs/my_job/artifacts/index.sqlite
```

### Frontend

```bash
# Setup
cd review-ui
npm install

# Development server (with hot reload)
npm run dev

# Production build
npm run build

# Preview production build
npm run preview
```

### Multi-File Support (Virtual Page Numbering)

Each budget item can have **one flat PDF** or **multiple PDFs in a subdirectory**:

```
test-files/pdf/
  Salary.pdf                          # Flat file (single PDF)
  telecommunications/                 # Subdirectory (multiple PDFs)
    phone_bill_q1.pdf
    phone_bill_q2.pdf
```

**Virtual document assembly (cli.py):**
- Multiple physical PDFs per budget item are concatenated into a single virtual document
- Virtual page numbers are sequential: if file A has 5 pages and file B has 3, virtual pages are 1-5 (file A) and 6-8 (file B)
- The virtual document's `doc_id` is the budget_item_slug (e.g., `"salary"`)
- Physical files are indexed with unique doc_ids: `"{budget_slug}__{file_stem_slug}"` (e.g., `"salary__payroll_jan"`)
- `DocumentRef.source_files` maps virtual pages back to physical files via `page_offset`

**Matching sees one document:** Virtual pages are passed to matching as if from a single PDF. Candidates can span pages from different physical files. No matching logic changes needed.

**Frontend resolution (PDFViewer.jsx):**
- `resolveVirtualPage(virtualPageNum)` maps virtual page → `{sourceFile, localPage}`
- Multiple PDF.js documents are cached in `pdfDocsRef` (keyed by source doc_id)
- Each page render resolves its virtual number to load the correct physical PDF + local page
- Source filename shown in header when viewing multi-file documents

**Backward compatibility:** Flat files produce `source_files` with one entry (page_offset=0). Frontend falls back to `LEGACY_FILENAME_MAP` if `source_files` is empty.

### File Structure Requirements

**Test files (not committed to git):**
- PDFs can be flat files in `test-files/pdf/` OR in subdirectories named after budget items
- Flat: filenames must match slugified budget item names (case-insensitive)
- Subdirectory: directory name must match a budget item; PDFs inside can have any name
- If both a flat file and subdirectory exist for the same budget item, the subdirectory takes precedence
- Examples: `Salary.pdf`, `Space_Rental_Occupancy_Costs.pdf`, `salary/payroll_jan.pdf`
- Symlink from `review-ui/public/test-files` → `../../test-files` enables frontend access

**Output structure:**
```
backend/jobs/<job_id>/artifacts/
├── reconciliation.json      # Main output, consumed by frontend
├── index.sqlite            # Extraction cache
└── user_edits.json         # Optional manual overrides
```

## Configuration

### Backend (.env)

```env
# Required
AWS_REGION=us-west-2
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-5-20250929-v1:0

# Text extraction
TEXT_MIN_CHARS=40              # Min chars for PyMuPDF sufficiency
TEXTRACT_MODE=auto             # auto | always | never
TEXTRACT_MAX_LINES=300         # Max lines per page from Textract

# Table detection
TABLE_DETECTION_ENABLED=false  # Use Bedrock vision to pre-detect table pages (adds cost/latency)

# PyMuPDF table extraction (quality thresholds)
MIN_TABLE_ROWS=2                    # Minimum rows for valid table
MIN_TABLE_CELLS=4                   # Minimum total cells (e.g., 2x2)
MIN_TABLE_CELL_COVERAGE=0.5         # Minimum ratio of non-empty cells (0.5 = 50%)
PYMUPDF_TABLE_STRATEGY=lines        # lines | text | lines_strict

# Matching
MIN_CANDIDATE_SCORE=0.1        # Backend filter for garbage matches (frontend has dynamic slider)

# Performance
MAX_WORKERS=3                  # Parallel processing workers
```

### Frontend

Development server serves from `review-ui/public/`:
- `/backend/` → symlink to `../../backend`
- `/test-files/` → symlink to `../../test-files`

Loads `reconciliation.json` from: `/backend/jobs/{jobId}/artifacts/reconciliation.json`

## Budget Item Naming

**12 canonical budget items:**
Salary, Fringe, Contractual Service, Equipment, Insurance, Travel and Conferences, Space Rental/Occupancy Costs, Telecommunications, Utilities, Supplies, Other, Indirect Costs

**CSV normalization (budget_items.py):**
- `SALARY_TOTAL` → `Salary`
- `SPACE_RENTAL` → `Space Rental/Occupancy Costs`
- Case-insensitive, handles underscores/spaces

**PDF filename matching:**
- Uses slugification: lowercase, non-alphanumeric → underscore
- `Space Rental/Occupancy Costs` → matches `space_rental_occupancy_costs.pdf` or `Space_Rental_Occupancy_Costs.pdf`

## Debugging Tips

**Highlight positioning issues:**
1. Check browser console for `PDFViewer: Candidate highlights from backend` log
2. Verify coordinates are in 0-1 range (not > 1.0)
3. If > 1.0: backend extracted with wrong page dimensions, delete cache and re-run
4. Check `page.rotate` value in console to verify inherent rotation captured
5. Use browser dev tools to inspect canvas element: `offsetWidth` vs `width` (should differ if CSS-scaled)

**Text extraction issues:**
1. Check `text_source` field in output: `pymupdf`, `pymupdf_table`, `textract`, or `textract_table`
   - `pymupdf_table` means PyMuPDF successfully extracted tables (free, fast)
   - `textract_table` means PyMuPDF failed/skipped, used Textract (paid)
2. If PyMuPDF used but text quality poor on table pages: set `TABLE_DETECTION_ENABLED=true`
3. If table detection is too aggressive/slow: set `TABLE_DETECTION_ENABLED=false` (default)
4. **Cost optimization**: With `TABLE_DETECTION_ENABLED=true`, PyMuPDF extracts tables for free before falling back to Textract
5. Tune quality thresholds (`MIN_TABLE_ROWS`, `MIN_TABLE_CELLS`, `MIN_TABLE_CELL_COVERAGE`) to adjust PyMuPDF vs Textract usage
6. Check logs for "PyMuPDF: Extracted N tables" vs "falling back to Textract" messages

**Matching issues:**
1. Check `candidates[0].rationale` for matching logic used
2. Salary/Fringe: verify employee names normalized correctly (lowercase, no punctuation)
3. Other items: check explanation text contains relevant keywords
4. Scores: >0.8 = excellent, 0.5-0.8 = good, 0.1-0.5 = low confidence
5. If item shows "No Match": try lowering the confidence slider in the UI to see if there are lower-scored candidates
6. Frontend confidence slider dynamically filters candidates without re-running the pipeline
7. **Budget item filtering**: Check rationale for "Filtered to [BudgetItem] amounts" messages - indicates amounts from other budget items were excluded
8. **Table parsing**: Check entities in SQLite to verify amounts have `budget_item` and `source` fields
9. **Summary spreadsheets**: If a PDF contains multiple budget items in tables, verify amounts are correctly associated with their row labels

## AWS Services

**Textract:**
- Used for text extraction fallback on scanned PDFs
- Returns word-level bounding boxes (normalized 0-1 coordinates)
- Can throttle with high request rates (reduce MAX_WORKERS if needed)

**Bedrock (Claude Sonnet 4.5):**
- Used for structured entity extraction from page text
- Extracts: names, amounts, dates, organizations, document types
- Model ID region-specific: `us.anthropic.claude-sonnet-4-5-20250929-v1:0` for us-west-2
