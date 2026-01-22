# Invoice Reconciliation Review UI

A distinctive editorial-style interface for reviewing and verifying invoice reconciliation results.

## Features

- **Line Item Browsing**: View all line items with match indicators
- **PDF Preview**: Inline PDF page rendering for evidence verification
- **Filtering**: Filter by budget item and match type
- **Match Quality Indicators**: Visual coding for match types and scores
- **Statistics Dashboard**: Overview of matching performance

## Setup

1. Create symlinks to backend and test-files directories (already done):
```bash
cd public
ln -s ../../backend backend
ln -s ../../test-files test-files
cd ..
```

2. Install dependencies:
```bash
npm install
```

3. Start the development server:
```bash
npm run dev
```

4. Open http://localhost:3000 in your browser

Note: The symlinks allow Vite to serve backend files (reconciliation.json) and test files (PDFs) during development.

## Usage

### Loading Results

By default, the UI loads results from `backend/jobs/my_job/artifacts/reconciliation.json`. Change the job ID in the header to load different results.

### Filtering

- **Budget Item**: Filter line items by budget category
- **Match Type**: Filter by:
  - Amount-Based: Items matched using amount verification
  - Cross-Page: Items matched across multiple pages
  - Keyword: Items matched using keyword/entity matching
  - Too Many Pages: Items with > 8 pages (needs review)
  - No Match: Items with no evidence pages

### Verifying Matches

1. Click on a line item in the left sidebar
2. Review the match candidates and scores
3. View the PDF evidence pages
4. Verify that the evidence supports the line item

## File Structure

```
review-ui/
├── src/
│   ├── components/
│   │   ├── LineItemCard.jsx     # Individual line item display
│   │   ├── PDFViewer.jsx        # PDF rendering and evidence display
│   │   ├── FilterBar.jsx        # Filter controls
│   │   └── Stats.jsx            # Statistics dashboard
│   ├── App.jsx                  # Main application component
│   └── main.jsx                 # Application entry point
├── index.html                   # HTML template
├── vite.config.js              # Vite configuration
└── package.json                # Dependencies
```

## Design

The UI uses an **editorial/audit aesthetic**:
- Crimson Pro (serif) for headers
- IBM Plex Mono for data and numbers
- DM Sans for UI text
- Color-coded match quality indicators
- Clean, data-focused layout

## PDF Path Configuration

PDFs are located at: `../test-files/pdf/{filename}.pdf`

Budget items map to specific PDF files:
- "Salary" → `Salary.pdf`
- "Utilities" → `Utilities.pdf`
- "Supplies" → `Supplies.pdf`
- "Telecommunications" → `Telecommunications.pdf`
- "Space Rental/Occupancy Costs" → `Space_Rental_Occupancy_Costs.pdf`
