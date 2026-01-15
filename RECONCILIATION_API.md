# Reconciliation Report API

## Overview

The Lambda function now generates a comprehensive reconciliation report that compares CSV invoice amounts against PDF supporting documentation. This report is automatically included in the response for multi-PDF CSV reconciliation requests.

## API Response Structure

### Response Location

The reconciliation report is available at:
```
response.body.reconciliation_report
```

### Report Schema

```typescript
interface ReconciliationReport {
  summary: ReconciliationSummary;
  line_items: LineItemResult[];
}

interface ReconciliationSummary {
  total_line_items: number;
  perfect_matches: number;      // Items that match perfectly
  amount_mismatches: number;     // Items with amount differences
  missing_data: number;          // Items with no PDF data
  has_issues: number;            // Items that match but have data quality issues
}

interface LineItemResult {
  csv_line_number: number;
  entity_name: string | null;
  category: string;              // SALARY, FRINGE, OTHER, etc.
  csv_amount: number;
  reporting_period: string;      // e.g., "11/1/2025"
  status: ReconciliationStatus;
  pdf_amount: number;
  difference: number;
  issues: string[];              // Human-readable issue descriptions

  // SALARY-specific fields
  date_details?: DateDetail[];   // Only for SALARY items

  // Non-SALARY fields
  supporting_pages?: number[];   // Page numbers with supporting docs
  supporting_page_count?: number;
}

type ReconciliationStatus =
  | "match"                // Perfect match, no issues
  | "match_with_issues"    // Amounts match but has data quality issues
  | "amount_mismatch"      // CSV amount != PDF amount
  | "missing_data"         // No PDF data for reporting period
  | "has_support"          // Non-SALARY: has supporting pages
  | "no_match";            // No matching PDF group found

interface DateDetail {
  date: string;                   // e.g., "11/14/2025"
  inconsistent?: boolean;         // Multiple representations disagree
  representations: PayRepresentation[];
}

interface PayRepresentation {
  source_type: "paystub" | "summary" | "pay_period";
  source_page: number;
  amount: number;
  hours?: number;
  rate?: number;
  expected_amount?: number;       // hours × rate
  has_calc_error?: boolean;       // true if hours × rate != amount
}
```

## Example Response

```json
{
  "statusCode": 200,
  "body": {
    "answer": "...",  // DocumentAnalysis JSON
    "reconciliation_report": {
      "summary": {
        "total_line_items": 47,
        "perfect_matches": 0,
        "amount_mismatches": 11,
        "missing_data": 7,
        "has_issues": 1
      },
      "line_items": [
        {
          "csv_line_number": 28,
          "entity_name": "Donna Chan",
          "category": "SALARY",
          "csv_amount": 5408.92,
          "reporting_period": "11/1/2025",
          "status": "amount_mismatch",
          "pdf_amount": 4920.21,
          "difference": 488.71,
          "issues": [
            "Amount mismatch: CSV $5408.92 vs PDF $4920.21",
            "Date 11/14/2025 pay_period: Missing hours or rate"
          ],
          "date_details": [
            {
              "date": "11/14/2025",
              "representations": [
                {
                  "source_type": "pay_period",
                  "source_page": 5,
                  "amount": 4920.21
                }
              ]
            }
          ]
        },
        {
          "csv_line_number": 29,
          "entity_name": "Jose Chavez",
          "category": "SALARY",
          "csv_amount": 4222.80,
          "reporting_period": "11/1/2025",
          "status": "amount_mismatch",
          "pdf_amount": 6123.06,
          "difference": 1900.26,
          "issues": [
            "Amount mismatch: CSV $4222.80 vs PDF $6123.06",
            "Date 11/14/2025: inconsistent amounts across representations: [1900.26, 2111.4]",
            "Date 11/14/2025 summary: calc error ($0.18 difference)",
            "Date 11/21/2025 paystub: calc error ($0.20 difference)"
          ],
          "date_details": [
            {
              "date": "11/14/2025",
              "inconsistent": true,
              "representations": [
                {
                  "source_type": "summary",
                  "source_page": 2,
                  "amount": 1900.26,
                  "hours": 72.0,
                  "rate": 26.39,
                  "expected_amount": 1900.08,
                  "has_calc_error": true
                },
                {
                  "source_type": "pay_period",
                  "source_page": 4,
                  "amount": 2111.40,
                  "hours": 80.0,
                  "rate": 26.39,
                  "expected_amount": 2111.20,
                  "has_calc_error": true
                }
              ]
            },
            {
              "date": "11/21/2025",
              "representations": [
                {
                  "source_type": "paystub",
                  "source_page": 11,
                  "amount": 2111.40,
                  "hours": 80.0,
                  "rate": 26.39,
                  "expected_amount": 2111.20,
                  "has_calc_error": true
                }
              ]
            }
          ]
        },
        {
          "csv_line_number": 13,
          "entity_name": null,
          "category": "OTHER",
          "csv_amount": 258449.00,
          "reporting_period": "11/1/2025",
          "status": "has_support",
          "pdf_amount": null,
          "difference": 0,
          "issues": [],
          "supporting_pages": [1, 2, 3, 5],
          "supporting_page_count": 4
        }
      ]
    },
    "csv_metadata": { ... },
    "csv_matches_by_category": { ... }
  }
}
```

## Reconciliation Logic

### SALARY Items

For SALARY items, the reconciliation:

1. **Filters by reporting period**: Only includes pay records for the month/year specified in the CSV
2. **Deduplicates by date**: Multiple PDF representations of the same pay date are consolidated
3. **Validates calculations**: Checks if hours × rate = amount (within $0.02 tolerance)
4. **Flags inconsistencies**: Reports when different PDF pages show different amounts for the same date
5. **Prioritizes paystubs**: When inconsistencies exist, prefers paystub data over summary data

### Non-SALARY Items

For non-SALARY items (FRINGE, OTHER, SUPPLIES, etc.):
- Checks if supporting pages exist
- Does NOT validate amounts (amounts are typically aggregate/budgetary)
- Status is either `has_support` or `missing_data`

## Frontend Integration

### Display Recommendations

#### Summary Dashboard
```typescript
const { summary } = reconciliationReport;

// Show overall status
<div>
  <StatusBadge
    label="Perfect Matches"
    count={summary.perfect_matches}
    color="green"
  />
  <StatusBadge
    label="Amount Mismatches"
    count={summary.amount_mismatches}
    color="red"
  />
  <StatusBadge
    label="Missing Data"
    count={summary.missing_data}
    color="orange"
  />
</div>
```

#### Line Item Table
```typescript
lineItems.map(item => (
  <TableRow key={item.csv_line_number}>
    <Cell>{item.entity_name || item.category}</Cell>
    <Cell>${item.csv_amount.toFixed(2)}</Cell>
    <Cell>${item.pdf_amount?.toFixed(2) ?? 'N/A'}</Cell>
    <Cell>
      <StatusIcon status={item.status} />
      {item.status === 'amount_mismatch' && (
        <Tooltip>
          Difference: ${item.difference.toFixed(2)}
        </Tooltip>
      )}
    </Cell>
    <Cell>
      {item.issues.length > 0 && (
        <IssuesList issues={item.issues} />
      )}
    </Cell>
  </TableRow>
))
```

#### Detailed View (SALARY)
```typescript
// Show date-by-date breakdown for SALARY items
{item.date_details?.map(detail => (
  <DateBreakdown key={detail.date}>
    <DateHeader>
      {detail.date}
      {detail.inconsistent && <Warning>Inconsistent representations</Warning>}
    </DateHeader>
    {detail.representations.map((rep, i) => (
      <Representation key={i}>
        <Badge>{rep.source_type}</Badge>
        <span>Page {rep.source_page}</span>
        <Amount>${rep.amount.toFixed(2)}</Amount>
        {rep.has_calc_error && (
          <Error>
            Expected: ${rep.expected_amount?.toFixed(2)}
            ({rep.hours}h × ${rep.rate}/h)
          </Error>
        )}
      </Representation>
    ))}
  </DateBreakdown>
))}
```

## Status Icon Mapping

```typescript
const statusConfig = {
  match: { icon: '✅', color: 'green', label: 'Match' },
  match_with_issues: { icon: '⚠️', color: 'yellow', label: 'Match (with issues)' },
  amount_mismatch: { icon: '❌', color: 'red', label: 'Mismatch' },
  missing_data: { icon: '🔴', color: 'orange', label: 'Missing Data' },
  has_support: { icon: '📄', color: 'blue', label: 'Has Support' },
  no_match: { icon: '❓', color: 'gray', label: 'No Match' }
};
```

## Filtering and Sorting

### Common Filters
```typescript
// Show only items requiring attention
const needsReview = lineItems.filter(item =>
  item.status === 'amount_mismatch' ||
  item.status === 'missing_data'
);

// Show by category
const salaryItems = lineItems.filter(item => item.category === 'SALARY');

// Show by status
const mismatches = lineItems.filter(item => item.status === 'amount_mismatch');
```

### Sorting
```typescript
// Sort by difference (largest first)
const sortedByDiff = [...lineItems].sort((a, b) =>
  b.difference - a.difference
);

// Sort by status priority
const statusPriority = {
  amount_mismatch: 1,
  missing_data: 2,
  match_with_issues: 3,
  has_support: 4,
  match: 5
};

const sortedByStatus = [...lineItems].sort((a, b) =>
  statusPriority[a.status] - statusPriority[b.status]
);
```

## Backend Implementation

The reconciliation report is generated in `lambda_function_phase1.py`:

- `_parse_date()`: Parse various date formats
- `_extract_pay_records()`: Extract pay records from entity objects
- `_generate_reconciliation_report()`: Main reconciliation logic
- Integrated into `_handle_multi_pdf_csv_mode()` response

The report is automatically generated as Step 5 after DocumentAnalysis transformation.
