# Invoice Reconciliation Backend

Local Python pipeline for reconciling invoice CSV line items with supporting PDF evidence.

## Features

- **Text Extraction**: PyMuPDF with automatic Textract fallback for scanned pages
- **Entity Extraction**: AWS Bedrock (Claude Sonnet 4.5) for structured data extraction
- **Smart Matching**: Employee name matching for Salary/Fringe, keyword matching for other items
- **Candidate Generation**: Multiple ranked evidence page sets per line item
- **SQLite Caching**: Incremental re-runs with hash-based cache invalidation
- **User Edits**: Manual evidence selection via overlay file

## Architecture

```
invoice_recon/
├── models.py              # Pydantic data models
├── config.py              # Environment configuration
├── budget_items.py        # Budget item mapping and slugification
├── csv_parser.py          # CSV parsing with stable row IDs
├── pdf_extract.py         # PDF text extraction (PyMuPDF + Textract)
├── textract_text.py       # AWS Textract wrapper
├── bedrock_entities.py    # AWS Bedrock entity extraction
├── index_store.py         # SQLite caching layer
├── matching.py            # Candidate generation and scoring
├── navigation_groups.py   # UI navigation structure
├── output_contract.py     # JSON output generation
└── cli.py                 # CLI entry point
```

## Setup

### 1. Create Virtual Environment

```bash
cd backend
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure AWS Credentials

The pipeline requires AWS credentials with access to:
- **Textract** (for text extraction fallback)
- **Bedrock** (for entity extraction with Claude Sonnet 4.5)

#### Option A: Environment Variables

```bash
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_REGION=us-west-2
```

#### Option B: AWS Profile

```bash
# Configure profile
aws configure --profile my-profile

# Set in .env
AWS_PROFILE=my-profile
```

### 4. Create .env File

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

**Required settings:**

```env
# AWS Configuration
AWS_REGION=us-west-2
AWS_PROFILE=              # Optional, if using profiles

# Bedrock Model (REQUIRED)
BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0

# Text Extraction
TEXT_MIN_CHARS=40         # Minimum chars for PyMuPDF to be sufficient
TEXTRACT_MODE=auto        # auto | always | never
TEXTRACT_MAX_LINES=300    # Max lines to extract per page

# Processing
MAX_WORKERS=3             # Parallel processing workers

# Output
OUTPUT_DIR=jobs           # Output directory
```

## Usage

### Run Reconciliation

```bash
python -m invoice_recon.cli run \
  --csv /path/to/invoice.csv \
  --pdf-dir /path/to/pdfs \
  --job-id job_001
```

**Inputs:**
- `--csv`: Path to invoice CSV file
- `--pdf-dir`: Directory containing PDFs (one per budget item)
- `--job-id`: Unique job identifier

**Outputs:**
- `jobs/<job_id>/artifacts/reconciliation.json` - Main output
- `jobs/<job_id>/artifacts/index.sqlite` - Extraction cache

### Validate Output

```bash
python -m invoice_recon.cli validate --job-id job_001
```

Checks that all referenced pages exist in documents.

## Budget Item Naming

### Canonical Budget Items
The system uses 12 canonical budget item names:
1. Salary
2. Fringe
3. Contractual Service
4. Equipment
5. Insurance
6. Travel and Conferences
7. Space Rental/Occupancy Costs
8. Telecommunications
9. Utilities
10. Supplies
11. Other
12. Indirect Costs

### CSV Format Flexibility
The CSV parser automatically normalizes various formats to canonical names:

| CSV Value                  | Normalized To                      |
|----------------------------|-------------------------------------|
| `SALARY_TOTAL`             | `Salary`                            |
| `SALARY`                   | `Salary`                            |
| `FRINGE_TOTAL`             | `Fringe`                            |
| `SPACE_RENTAL`             | `Space Rental/Occupancy Costs`      |
| `CONTRACTUAL_SERVICE`      | `Contractual Service`               |
| `TRAVEL_AND_CONFERENCES`   | `Travel and Conferences`            |

Both uppercase (e.g., `SALARY_TOTAL`) and lowercase (e.g., `salary_total`) are supported.

### PDF Filename Convention
PDFs must be named to match budget items. The matching is **case-insensitive** and uses slugification:

| Budget Item                    | Example PDF Filenames (all work)          |
|--------------------------------|-------------------------------------------|
| Salary                         | `Salary.pdf`, `SALARY.pdf`, `salary.pdf`  |
| Fringe                         | `Fringe.pdf`, `FRINGE.pdf`                |
| Space Rental/Occupancy Costs   | `Space_Rental_Occupancy_Costs.pdf`, `space_rental_occupancy_costs.pdf` |
| Travel and Conferences         | `Travel_and_Conferences.pdf`, `travel_and_conferences.pdf` |

**Slugification rules:**
- Case-insensitive matching
- Non-alphanumeric characters → underscore
- Collapse multiple underscores
- Strip leading/trailing underscores

## Output Format

`reconciliation.json` structure:

```json
{
  "job": {
    "job_id": "job_001",
    "created_at": "2025-01-19T12:00:00Z",
    "aws_region": "us-west-2",
    "bedrock_model_id": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "textract_mode": "auto"
  },
  "inputs": {
    "csv_path": "/path/to/invoice.csv",
    "pdf_dir": "/path/to/pdfs",
    "documents": [
      {"budget_item": "Salary", "path": "/path/to/pdfs/Salary.pdf"}
    ]
  },
  "documents": [
    {
      "doc_id": "salary",
      "budget_item": "Salary",
      "path": "/path/to/pdfs/Salary.pdf",
      "file_sha256": "abc123...",
      "page_count": 10
    }
  ],
  "navigation_groups": [
    {
      "group_id": "bi:salary",
      "label": "Salary",
      "budget_item": "Salary",
      "employee_key": null,
      "line_item_ids": ["row_0_a1b2c3d4", "row_1_e5f6g7h8"]
    },
    {
      "group_id": "bi:salary:emp:john_doe",
      "label": "Salary — John Doe",
      "budget_item": "Salary",
      "employee_key": "john_doe",
      "line_item_ids": ["row_0_a1b2c3d4"]
    }
  ],
  "line_items": [
    {
      "row_id": "row_0_a1b2c3d4",
      "row_index": 0,
      "budget_item": "Salary",
      "raw": { /* original CSV row */ },
      "normalized": {
        "budget_item": "Salary",
        "amount": 5000.00,
        "employee_first_name": "John",
        "employee_last_name": "Doe",
        ...
      },
      "candidates": [
        {
          "doc_id": "salary",
          "page_numbers": [1, 2, 3],
          "score": 0.95,
          "rationale": ["Last name match: doe", "Doc type: timecard"],
          "evidence_snippets": []
        }
      ],
      "selected_evidence": {
        "doc_id": "salary",
        "page_numbers": [1, 2, 3],
        "selection_source": "auto"
      }
    }
  ]
}
```

## Manual Evidence Selection

Create `jobs/<job_id>/artifacts/user_edits.json`:

```json
{
  "overrides": [
    {
      "row_id": "row_0_a1b2c3d4",
      "doc_id": "salary",
      "page_numbers": [4, 5, 6]
    }
  ]
}
```

Re-run the pipeline to apply edits. Overridden items will have `selection_source: "user"`.

## Text Extraction Modes

### auto (default)
- Try PyMuPDF first
- If text < TEXT_MIN_CHARS, fall back to Textract

### always
- Always use Textract for all pages

### never
- Never use Textract, only PyMuPDF

## Matching Logic

### Salary/Fringe Items
Matches pages by employee name:
- **+0.70**: Last name exact match
- **+0.20**: First name or initial match
- **+0.15**: Full name fuzzy match (>90% similarity)
- **+0.10**: Doc type is timecard/paystub

### Other Budget Items
Matches pages by keywords and organizations:
- **+0.05** per explanation keyword found in page
- **+0.15**: Organization name match
- **+0.10**: Expected doc type match

## Caching

The SQLite cache (`index.sqlite`) stores:
- Document metadata and SHA256 hashes
- Extracted text and source (pymupdf/textract)
- Entity extraction results

**Cache invalidation:**
- Document re-extracted if file SHA256 changes
- Entities re-extracted if page text SHA256 changes

Delete `index.sqlite` to force full re-extraction.

## Troubleshooting

### Textract Throttling
If you see throttling errors, reduce `MAX_WORKERS` in `.env`:
```env
MAX_WORKERS=1
```

### Missing Bedrock Model
Ensure you're using the correct model ID for us-west-2:
```env
BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0
```

### PDF Not Found
Check that PDF filenames match slugified budget item names. Use:
```bash
python -c "from invoice_recon.budget_items import slugify; print(slugify('Space Rental/Occupancy Costs'))"
# Output: space_rental_occupancy_costs
```

Your PDF should be named: `space_rental_occupancy_costs.pdf` (or any capitalization like `Space_Rental_Occupancy_Costs.pdf`)

### Unknown Budget Item in CSV
If you see warnings about unknown budget items, check your CSV values. The system expects values like:
- `SALARY_TOTAL`, `SALARY`, or `Salary`
- `SPACE_RENTAL` or `Space Rental/Occupancy Costs`
- `CONTRACTUAL_SERVICE` or `Contractual Service`

To test normalization:
```bash
python -c "from invoice_recon.budget_items import normalize_csv_budget_item; print(normalize_csv_budget_item('SALARY_TOTAL'))"
# Output: Salary
```

If your CSV uses a different format, add a mapping in `invoice_recon/budget_items.py` in the `CSV_TO_CANONICAL` dict.

## Development

Run tests:
```bash
pytest tests/
```

Enable debug logging:
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## License

MIT
