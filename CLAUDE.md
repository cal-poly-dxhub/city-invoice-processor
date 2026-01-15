# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

City Invoice Processor is an AWS-based document analysis system that extracts and reconciles financial entities from invoice PDFs using AI. The system uses a multi-phase approach to detect entities, match them across pages, attach coordinates for visual highlighting, and verify financial data consistency.

## Architecture

### Three-Phase Processing Pipeline

**Phase 1: Entity Detection & Grouping** (`lambda_function_phase1.py`)
- **Stage 1**: Extracts entities (employees, vendors, companies) page-by-page using Claude via Bedrock
- **Stage 2**: Groups matching entities across pages (handles name variations like "FirstName LastName" vs "LastName, FirstName")
- **Coordinate Attachment**: Uses PyMuPDF text search + AWS Textract fallback to find bounding boxes for entity names
- **Output Transformation**: Converts raw matched entities to `DocumentAnalysis` schema (see `to_document_analysis()` function at line 434)
- Returns: `DocumentAnalysis` JSON with groups, occurrences, and normalized coordinates (0-1 range)

**Phase 2: Financial Reconciliation** (`lambda_function_phase2.py`)
- Takes Phase 1 output + original PDF
- Renders pages as images and sends to Claude for vision-based analysis
- Extracts financial numbers and verifies mathematical consistency
- Special handling: Sums timesheet hours across multiple pages and compares to pay stubs
- Returns: Extracted numbers, verifications, and discrepancies with confidence scores

**Legacy Lambda** (`lambda_function.py`)
- Older implementation with different system prompts
- Likely not used in production; Phase 1 is the current implementation

### Data Flow

```
S3 PDF → Phase 1 Lambda → DocumentAnalysis JSON → Frontend Display
                       ↓
                  Phase 2 Lambda → Reconciliation Results → Frontend Display
```

### Key Components

**Backend (Python/AWS CDK)**
- `cdk/lambda/lambda_function_phase1.py`: Main entity extraction (1356 lines)
  - `lambda_handler()` at line 1137: Entry point
  - `fetch_s3_pages()`: Downloads PDF from S3, converts to images
  - `invoke_bedrock()`: Calls Claude Sonnet via Bedrock
  - `attach_coords_to_matched_entities()`: Finds text coordinates using PyMuPDF/Textract
  - `to_document_analysis()`: Transforms to frontend-friendly schema
- `cdk/lambda/lambda_function_phase2.py`: Financial reconciliation (699 lines)
  - `reconcile_document()`: Main reconciliation logic
  - `verify_group()`: Per-group verification with batching
- `cdk/stacks/lambda_stack.py`: CDK infrastructure (Lambda + IAM)
- `cdk/app.py`: CDK app entry point

**Frontend (React/TypeScript/Vite)**
- `frontend/src/App.tsx`: Main application component
- `frontend/src/types/DocumentAnalysis.ts`: TypeScript types matching Lambda output schema
- `frontend/src/components/`:
  - `PDFPageWithHighlights`: Renders PDF pages with coordinate overlays
  - `ReviewScreen`: Main review interface
  - `PageMembershipPanel`: Group assignment UI
  - `UnassignedPagesPanel`: Pages without entity matches

**Frontend Stack**
- React 19.2 + TypeScript
- Vite for build tooling
- pdfjs-dist for PDF rendering

## Development Commands

### Backend (Python)

**Setup Virtual Environment**
```bash
# From repository root
python -m venv venv
source venv/bin/activate  # or: source .venv/bin/activate
pip install boto3 PyMuPDF
```

**Test Phase 1 Lambda Locally**
```bash
# From repository root
source venv/bin/activate
python test_lambda_phase1.py
# Requires AWS credentials with access to S3 (sf-invoices-docs) and Bedrock
```

**Test Phase 2 Lambda Locally**
```bash
source venv/bin/activate
python test_lambda_phase2.py
# Requires frontend/public/sample.pdf and sample-reconciled.json
```

**Run Specific Unit Tests**
```bash
python test_document_analysis.py  # Test DocumentAnalysis transformation
```

**Deploy Infrastructure**
```bash
cd cdk
source venv/bin/activate  # Different venv for CDK
pip install -r requirements.txt
cdk deploy
```

### Frontend (React)

**Install Dependencies**
```bash
cd frontend
npm install
```

**Run Development Server**
```bash
npm run dev
# Opens on http://localhost:5173
```

**Build Production Bundle**
```bash
npm run build
# Output: frontend/dist/
```

**Lint Code**
```bash
npm run lint
```

## Important Implementation Details

### Entity Matching Rules (Phase 1, Stage 2)
- Only entities with a `name` field get coordinate highlighting
- Employer/organization entities are intentionally NOT grouped (focus is on employees/persons)
- Name variations are normalized: "Rolando Mutul" matches "Mutul, Rolando" and "Mutul,Rolando"
- Punctuation differences handled: "Lydia I Candila" matches "Lydia I. Candila"
- See `DEFAULT_STAGE2_SYSTEM_PROMPT` for grouping rules

### Coordinate System
- All coordinates stored in normalized form (0-1 range)
- Bounding boxes: `{x, y, width, height}` where (x,y) is top-left corner
- Search order for text: PyMuPDF text search → PyMuPDF cached search → Textract OCR fallback
- Textract fallback limited to 200 lines per page (see `TEXTRACT_MAX_LINES`)
- Multiple bounding boxes per occurrence supported (e.g., name appears twice on same page)

### DocumentAnalysis Schema (v1.0)
- Output format from Phase 1 Lambda in `body.answer`
- Groups contain: `groupId`, `label`, `summaryPages`, `supportingPages`, `occurrences[]`
- Occurrences contain: `occurrenceId`, `pageNumber`, `role` (summary|supporting), `coords[]`, `snippet`
- Full raw data preserved in `occurrence.rawSource` and `group.meta`
- TypeScript definitions: `frontend/src/types/DocumentAnalysis.ts`

### Model Configuration
- Default model: `us.anthropic.claude-3-5-sonnet-20241022-v2:0` (Claude 3.5 Sonnet)
- Configurable via `MODEL_ID` environment variable
- Phase 1 Stage 1: 16K max tokens per page
- Phase 1 Stage 2: 5x Stage 1 tokens (minimum 64K) for large PDFs
- Phase 2: 200K max tokens for vision analysis

### AWS Resources
- S3 bucket: `sf-invoices-docs` (hardcoded in CDK stack)
- Lambda timeout: 15 minutes
- Lambda memory: 3008 MB
- Runtime: Python 3.13
- IAM permissions: S3 GetObject, Bedrock InvokeModel

## Common Patterns

### Reading Lambda Output
```python
response = lambda_client.invoke(...)
body = json.loads(response["body"])

# DocumentAnalysis format (use this)
analysis = json.loads(body["answer"])

# Raw Stage 2 output (backward compatibility)
raw_entities = json.loads(body["stage2_answer"])

# Stage 1 page-by-page output
stage1 = json.loads(body["stage1_answer"])
```

### Adding New Entity Types
1. Modify `DEFAULT_STAGE1_SYSTEM_PROMPT` in `lambda_function_phase1.py`
2. Update entity detection rules to include new type
3. Modify `DEFAULT_STAGE2_SYSTEM_PROMPT` to handle grouping logic
4. Consider whether new type should get coordinate highlighting (requires `name` field)

### Debugging Coordinate Issues
1. Check `test_lambda_phase1.py` output for coordinate data
2. Verify entity has `name` field (only named entities get coords)
3. Check if PyMuPDF found text: Look for `coords` array in occurrence
4. If no coords, check Textract fallback logs
5. Page rotation handled: coordinates adjusted for 90°/180°/270° rotations

## Documentation Files

- `DOCUMENT_ANALYSIS_README.md`: Detailed guide to DocumentAnalysis transformation layer
- `STAGE_ANALYSIS_SUMMARY.md`: Analysis of Stage 1 vs Stage 2 entity detection/matching
- `frontend/README.md`: Frontend-specific documentation

## Notes

- `frontend2/` appears to be a duplicate frontend directory (same package.json)
- Multiple test files in root (`test_*.py`) are development/debugging scripts
- Many test output files in root (`.json`, `.log`) - not tracked in git
- CDK output in `cdk/cdk.out/` should be in .gitignore
