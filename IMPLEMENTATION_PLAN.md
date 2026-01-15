# Implementation Plan: CSV-Based Invoice Reconciliation with Multi-PDF Upload

## Overview
Change from displaying detected PDF entity groups to using CSV line items as the primary groups. The existing entity recognition and grouping (Stage 1 & 2) remain unchanged. A new Stage 3 matches CSV line items to the detected entity groups.

**Core Architecture Change**:
- **Before**: PDF entities → groups → display groups
- **After**: Multiple PDFs (by category) → entities/groups per PDF (Stage 1 & 2) → match CSV to appropriate PDF (Stage 3) → display CSV line items as groups

**Multi-PDF Upload Structure**:
- **Input**: 1 CSV file + Multiple PDF files (one per budget category)
- **Budget Categories**: Salary, Fringe, Contractual Service, Equipment, Insurance, Travel/Conferences, Space Rental/Occupancy Costs, Telecommunications, Utilities, Supplies, Other, Indirect Costs
- **Note**: Not every category will be present in every invoice (user uploads only relevant PDFs)

**Three-Stage Process (run for each PDF)**:
1. **Stage 1 (unchanged)**: Extract entities from each PDF page
2. **Stage 2 (unchanged)**: Group entities by name variations
3. **Stage 3 (NEW)**: Match CSV line items to detected entity groups **in the appropriate PDF based on budget category**

**Core Requirement**: For each CSV line item (group), verify that the CSV amount ≤ sum of amounts in the supporting documentation PDF for that budget category.

## Key Simplifications from Multi-PDF Architecture

The multi-PDF approach (one PDF per budget category) provides significant advantages:

1. **Simplified Matching**: No need to figure out which PDF contains documentation for a CSV line item - it's determined by the budget category
2. **Cleaner Stage 3 Prompts**: Can tailor matching logic per category (SALARY uses name matching, others use description matching)
3. **User Control**: User explicitly assigns PDFs to categories via upload UI (12 separate upload buttons)
4. **Better Error Handling**: Can clearly identify missing PDFs for specific categories
5. **Parallel Processing**: Can process PDFs independently (though not required initially)

**Frontend Upload Flow**:
```
1. User uploads CSV
2. Frontend parses CSV and shows which categories have line items
3. User uploads PDFs using category-specific buttons (only uploads needed categories)
4. User clicks "Process"
5. Backend receives: csv_s3_uri + [{category: "SALARY", s3_uri: "..."}, ...]
```

**Backend Processing Flow**:
```
1. Parse CSV and group line items by category
2. For each uploaded PDF:
   - Run Stage 1 (entity extraction) on that PDF
   - Run Stage 2 (entity grouping) on that PDF's entities
   - Store results by category
3. For each category with both CSV items and PDF:
   - Run Stage 3 matching for that category only
   - CSV items know their category, PDF entities from that category's PDF
4. Merge all PDFs into single virtual document with page offsets
5. Return DocumentAnalysis with all groups
```

---

## Phase 1: Backend - CSV Processing Infrastructure

### 1.1 Create CSV Parser Utility
**File**: `cdk/lambda/csv_parser.py` (new file)

**Functions to implement**:
- `parse_csv(csv_bytes: bytes) -> List[Dict]`
  - Parse CSV into list of dictionaries
  - Handle common CSV formats (comma, tab, semicolon delimiters)
  - Auto-detect delimiter
  - Handle quoted fields
  - Return normalized structure

- `normalize_csv_schema(rows: List[Dict]) -> List[InvoiceLineItem]`
  - Map CSV columns to standard schema
  - Common column names: "Name", "Entity", "Employee", "Amount", "Total", "Invoice Amount", etc.
  - Support flexible matching (case-insensitive, with/without spaces)
  - Extract: entity_name, amount, description, date (if present)
  - **For the actual CSV structure**:
    - Column 13: "Employee First Name"
    - Column 14: "Employee Last Name"
    - Column 17: "Amount" (key field for reconciliation)
    - Column 9: "Budget Item" (SALARY, FRINGE, INDIRECT_COSTS, etc.)
    - Column 10: "Explanation" (description)
  - **Logic for is_employee_item**:
    - `is_employee_item = True` if:
      - Budget Item == "SALARY" (not "SALARY_TOTAL")
      - Employee First Name is not empty
      - Employee Last Name is not empty
      - Amount > 0
    - `is_employee_item = False` for all other budget items (FRINGE, INDIRECT_COSTS, etc.)
  - **Entity name construction**:
    - For employee items: `f"{first_name} {last_name}"`
    - For non-employee items: `entity_name = None`
  - **Filtering**:
    - Only include rows where Amount > 0
    - Skip SALARY_TOTAL rows (aggregates, not individual line items)

**Data Structure**:
```python
@dataclass
class InvoiceLineItem:
    line_number: int
    entity_name: Optional[str]  # Present for SALARY rows, None for non-employee items
    budget_item: str  # SALARY, FRINGE, INDIRECT_COSTS, OTHER, EQUIPMENT, etc.
    amount: float
    description: Optional[str]
    date: Optional[str]
    unit: str  # "USD", "hours", etc.
    is_employee_item: bool  # True for SALARY rows with names, False for other budget items
    raw_row: Dict  # Preserve original CSV data
```

**Testing**:
- Create `test_csv_parser.py`
- Test various CSV formats
- Test missing columns, malformed data
- Test delimiter detection

---

### 1.2 Modify Phase 1 Lambda to Handle Multiple PDFs by Category
**File**: `cdk/lambda/lambda_function_phase1.py`

**Key Architectural Change**: User uploads multiple PDFs (one per budget category). CSV line items define the groups, and we match each CSV item to the appropriate PDF based on budget category.

**Changes to `lambda_handler()`**:

1. **New event parameters**:
```python
def lambda_handler(event, context):
    # NEW: Multiple PDFs, one per budget category
    pdf_uploads = event.get("pdf_uploads")  # List of {category: str, s3_uri: str}
    # Example: [
    #   {"category": "SALARY", "s3_uri": "s3://bucket/salary.pdf"},
    #   {"category": "FRINGE", "s3_uri": "s3://bucket/fringe.pdf"},
    #   ...
    # ]

    csv_s3_uri = event.get("csv_s3_uri")  # NEW: CSV master invoice
    csv_content = event.get("csv_content")  # NEW: Base64 encoded CSV (alternative to S3)

    # ... existing parameters (model_id, etc.)
```

2. **CSV Download/Parse Logic**:
```python
# Download and parse CSV (done once)
csv_line_items = []
if csv_s3_uri:
    csv_bytes = download_from_s3(csv_s3_uri)
    csv_line_items = parse_and_normalize_csv(csv_bytes)
elif csv_content:
    csv_bytes = base64.b64decode(csv_content)
    csv_line_items = parse_and_normalize_csv(csv_bytes)
else:
    raise ValueError("csv_s3_uri or csv_content required")

print(f"Parsed {len(csv_line_items)} CSV line items")

# Group CSV line items by budget category
csv_by_category = {}
for item in csv_line_items:
    category = item.budget_item  # SALARY, FRINGE, etc.
    if category not in csv_by_category:
        csv_by_category[category] = []
    csv_by_category[category].append(item)

print(f"CSV line items by category: {dict((k, len(v)) for k, v in csv_by_category.items())}")
```

3. **Process Each PDF Separately** (Stages 1 & 2 per PDF):
```python
# Process each PDF upload
pdf_results = {}  # category -> {stage1_outputs, entity_groups, pages_metadata}

for pdf_upload in pdf_uploads:
    category = pdf_upload["category"]
    pdf_s3_uri = pdf_upload["s3_uri"]

    print(f"Processing PDF for category: {category}")

    # Download PDF
    pdf_bytes = download_from_s3(pdf_s3_uri)
    pages = fetch_s3_pages(pdf_s3_uri)  # Existing function

    # Stage 1: Entity Extraction (UNCHANGED)
    # Keep existing Stage 1 prompt - NO CSV BIAS
    stage1_system_prompt = DEFAULT_STAGE1_SYSTEM_PROMPT
    stage1_outputs = []

    for page_data in pages:
        # Extract entities from each page (existing logic)
        stage1_response = invoke_bedrock(...)
        stage1_outputs.append(page_data)

    # Stage 2: Group Entities (UNCHANGED)
    stage2_system_prompt = DEFAULT_STAGE2_SYSTEM_PROMPT
    stage2_response = invoke_bedrock(
        model_id=model_id,
        system_prompt=stage2_system_prompt,
        messages=stage2_messages,
        max_tokens=stage2_max_tokens
    )

    stage2_json = _extract_json_candidate(stage2_response["content"][0]["text"])
    entity_groups = stage2_json.get("entities", [])

    # Store results for this PDF category
    pdf_results[category] = {
        "stage1_outputs": stage1_outputs,
        "entity_groups": entity_groups,
        "pages_metadata": [{"pageNumber": i+1, ...} for i, _ in enumerate(pages)],
        "pdf_bytes": pdf_bytes
    }

    print(f"Category {category}: Found {len(entity_groups)} entity groups")
```

Note: Stage 1 and Stage 2 run **independently** for each PDF, with no knowledge of the CSV or other PDFs.
```

4. **NEW Stage 3: Match CSV Line Items to Entity Groups (per category)**:
```python
# NEW: Stage 3 runs separately for each PDF category
# This simplifies matching because we know the CSV line items match the PDF category

STAGE3_CSV_MATCHING_PROMPT = """
You are matching CSV invoice line items to entity groups detected in the PDF supporting documentation for the {category} budget category.

CSV Line Items for {category} category (to be verified):
{csv_line_items_json}

Entity Groups Found in {category} PDF:
{entity_groups_json}

PDF Page Summaries ({category} PDF):
{page_summaries_json}

MATCHING RULES FOR {category}:

FOR SALARY CATEGORY (employee line items):
1. Match by entity name to detected entity groups, considering name variations:
   - Name order variations (FirstName LastName vs LastName, FirstName)
   - Punctuation differences (periods, commas, hyphens)
   - Middle initials (with or without)
   - Common abbreviations
2. If an employee appears under multiple name variations, list all matching groups
3. matched_group_names should contain the PDF entity group names
4. Each CSV line item should match to exactly one person (or set of matching name variations)

FOR NON-SALARY CATEGORIES (FRINGE, EQUIPMENT, OTHER, etc.):
1. These line items typically do NOT have entity names (no person names)
2. Match by identifying which PDF pages contain supporting documentation:
   - Look for pages with expense descriptions matching the CSV description
   - Look for pages with itemized breakdowns
   - Look for pages with totals or summary tables
   - Look for pages with relevant category headers
3. matched_group_names should typically be [] (empty - no person entities for non-salary items)
4. matched_group_pages should list ALL pages where supporting documentation appears
5. For SALARY-related categories like FRINGE/INDIRECT_COSTS, these may be associated with the overall project or multiple employees (not specific individuals)

Output JSON:
{{
  "category": "{category}",
  "csv_to_group_matches": [
    {{
      "csv_line_number": 1,
      "csv_entity_name": "<entity from CSV, or null for non-salary items>",
      "matched_group_names": ["<name from PDF groups>"],  // Empty [] for non-salary items
      "matched_group_pages": [1, 2, 3],
      "match_confidence": "high|medium|low|none",
      "match_reasoning": "Brief explanation"
    }}
  ],
  "unmatched_csv_lines": [2, 5],
  "unmatched_pdf_groups": ["<group name not in CSV>"]
}}

IMPORTANT:
- You are ONLY matching CSV line items for the {category} category
- All CSV line items provided have budget_item = {category}
- Do not invent or hallucinate matches
- If no supporting documentation found, set matched_group_pages to [] and match_confidence to "none"
- For SALARY: Each CSV line should match to one entity/person
- For non-SALARY: CSV lines may map to multiple pages or no specific entity groups
"""

# Run Stage 3 for each PDF category
all_csv_matches = {}

for category, csv_items in csv_by_category.items():
    # Check if we have a PDF for this category
    if category not in pdf_results:
        print(f"WARNING: No PDF provided for category {category}, skipping {len(csv_items)} line items")
        continue

    pdf_result = pdf_results[category]
    entity_groups = pdf_result["entity_groups"]
    stage1_outputs = pdf_result["stage1_outputs"]

    # Create page summaries for this PDF
    page_summaries = []
    for idx, page_output in enumerate(stage1_outputs):
        page_summaries.append({
            "page_number": idx + 1,
            "entities_found": [e.get("name") or e.get("type") for e in page_output.get("entities", [])],
            "summary": page_output.get("summary", "")
        })

    # Invoke Stage 3 for this category
    stage3_messages = [{
        "role": "user",
        "content": STAGE3_CSV_MATCHING_PROMPT.format(
            category=category,
            csv_line_items_json=json.dumps([item.to_dict() for item in csv_items], indent=2),
            entity_groups_json=json.dumps(entity_groups, indent=2),
            page_summaries_json=json.dumps(page_summaries, indent=2)
        )
    }]

    stage3_response = invoke_bedrock(
        model_id=model_id,
        system_prompt=f"You are a precise entity matching assistant for {category} budget category.",
        messages=stage3_messages,
        max_tokens=16000,
        temperature=0.0
    )

    csv_matches = _extract_json_candidate(stage3_response["content"][0]["text"])
    all_csv_matches[category] = csv_matches

    print(f"Category {category}: Matched {len(csv_matches.get('csv_to_group_matches', []))} line items")
```

5. **Transform to DocumentAnalysis with CSV-based Groups** (Multi-PDF version):
```python
def csv_to_document_analysis_multi_pdf(
    csv_line_items: List[InvoiceLineItem],
    pdf_results: Dict[str, Dict],  # category -> {stage1_outputs, entity_groups, pages_metadata, pdf_bytes}
    all_csv_matches: Dict[str, Dict]  # category -> Stage 3 match results
) -> Dict:
    """
    Convert CSV line items to DocumentAnalysis groups.
    With multi-PDF approach, each CSV line item maps to pages in a specific category PDF.
    """
    # Merge all pages from all PDFs into a single document
    # Page numbers need to be offset to avoid conflicts
    all_pages_metadata = []
    page_offset_by_category = {}
    current_page_offset = 0

    # First pass: collect all pages and calculate offsets
    for category in sorted(pdf_results.keys()):  # Sort for consistent ordering
        page_offset_by_category[category] = current_page_offset
        category_pages = pdf_results[category]["pages_metadata"]

        # Adjust page numbers with offset
        for page_meta in category_pages:
            adjusted_page = page_meta.copy()
            adjusted_page["pageNumber"] = page_meta["pageNumber"] + current_page_offset
            adjusted_page["category"] = category  # Add category metadata
            all_pages_metadata.append(adjusted_page)

        current_page_offset += len(category_pages)

    # Create analysis structure
    analysis = {
        "schemaVersion": "1.0",
        "documentId": "invoice-reconciliation",
        "pageCount": len(all_pages_metadata),
        "pages": all_pages_metadata,
        "groups": [],
        "meta": {
            "categories": list(pdf_results.keys()),
            "page_offset_by_category": page_offset_by_category
        }
    }

    # Process matches for each category
    for category, csv_matches in all_csv_matches.items():
        # Get PDF results and page offset for this category
        pdf_result = pdf_results[category]
        entity_groups = pdf_result["entity_groups"]
        stage1_outputs = pdf_result["stage1_outputs"]
        pdf_bytes = pdf_result["pdf_bytes"]
        page_offset = page_offset_by_category[category]

        # Build entity group lookup for this category
        entity_group_lookup = {eg.get("name"): eg for eg in entity_groups}

        # Process each CSV line item match for this category
        for match in csv_matches.get("csv_to_group_matches", []):
            csv_line_num = match["csv_line_number"]
            csv_item = csv_line_items[csv_line_num - 1]  # 0-indexed

            # Get matched entity groups and their pages
            matched_groups = match.get("matched_group_names", [])
            category_pages = []  # Pages within this category's PDF
            matched_entity_objects = []

            if category == "SALARY" and matched_groups:
                # SALARY: Pull pages from matched entity groups
                for group_name in matched_groups:
                    entity_group = entity_group_lookup.get(group_name)
                    if entity_group:
                        pages = entity_group.get("pages", [])
                        category_pages.extend(pages)
                        objects = entity_group.get("objects", [])
                        matched_entity_objects.extend(objects)
            else:
                # Non-SALARY: Use pages directly from Stage 3 match
                category_pages = match.get("matched_group_pages", [])

            # Apply page offset to translate to global page numbers
            global_pages = [p + page_offset for p in category_pages]
            global_pages = sorted(set(global_pages))  # Remove duplicates and sort

            # Create group from CSV line item
            group_id = f"csv_{category}_{csv_line_num}"

            # Label: entity name for SALARY, budget item + description for others
            if category == "SALARY":
                label = csv_item.entity_name
            else:
                label = f"{csv_item.budget_item}"
                if csv_item.description:
                    label += f": {csv_item.description[:50]}"  # Truncate long descriptions

            group = {
                "groupId": group_id,
                "label": label,
                "kind": "csv_line_item",
                "summaryPages": [],  # No summary pages - CSV is the summary
                "supportingPages": global_pages,  # Global page numbers
                "occurrences": [],  # Will be populated by coordinate attachment
                "meta": {
                    "csv_line_number": csv_line_num,
                    "csv_entity_name": csv_item.entity_name,
                    "csv_budget_item": csv_item.budget_item,
                    "csv_category": category,
                    "csv_amount": csv_item.amount,
                    "csv_unit": csv_item.unit,
                    "csv_description": csv_item.description,
                    "csv_raw_row": csv_item.raw_row,
                    "match_confidence": match.get("match_confidence"),
                    "matched_group_names": matched_groups,
                    "matched_entity_objects": matched_entity_objects,
                    "match_reasoning": match.get("match_reasoning"),
                    "category_pages": category_pages,  # Original pages within category PDF
                    "page_offset": page_offset  # For debugging/reference
                }
            }

            analysis["groups"].append(group)

    # Now attach coordinates for each group
    # This needs to work with multiple PDFs
    attach_coords_to_csv_groups_multi_pdf(
        analysis["groups"],
        pdf_results,
        page_offset_by_category
    )

    return analysis
```

7. **Update coordinate attachment for CSV groups**:
```python
def attach_coords_to_csv_groups(
    groups: List[Dict],
    pdf_bytes: bytes,
    stage1_outputs: List[Dict]
) -> None:
    """
    For each CSV-based group, find entity name occurrences on supporting pages.
    Similar to attach_coords_to_matched_entities but works with CSV groups.
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")

    for group in groups:
        entity_name = group["label"]
        supporting_pages = group["supportingPages"]

        # Generate name variations
        name_variations = generate_name_variations(entity_name)

        # Also try matched group names from PDF
        matched_group_names = group["meta"].get("matched_group_names", [])
        for matched_name in matched_group_names:
            if matched_name not in name_variations:
                name_variations.append(matched_name)

        # Search for entity name on each supporting page
        group_coords = []
        for page_num in supporting_pages:
            for name_var in name_variations:
                coords = _find_text_coords_on_page(doc, page_num, name_var, stage1_outputs)
                if coords:
                    for coord in coords:
                        coord["page_number"] = page_num
                        coord["role"] = "supporting"  # All pages are supporting
                    group_coords.extend(coords)
                    break

        # Create occurrences from coordinates
        occurrences = []
        for idx, coord in enumerate(group_coords):
            occurrence = {
                "occurrenceId": f"{group['groupId']}_occ{idx}",
                "groupId": group["groupId"],
                "pageNumber": coord["page_number"],
                "role": "supporting",
                "coords": [{k: v for k, v in coord.items() if k not in ["page_number", "role"]}],
                "snippet": entity_name,
                "rawSource": None
            }
            occurrences.append(occurrence)

        group["occurrences"] = occurrences

    doc.close()
```

8. **Update return structure**:
```python
return {
    "statusCode": 200,
    "body": {
        "answer": document_analysis_json,  # Groups are now CSV-based
        "csv_metadata": {
            "total_line_items": len(csv_line_items),
            "csv_line_items": [item.to_dict() for item in csv_line_items],
        },
        "csv_to_group_matches": csv_to_group_matches,  # Stage 3 output
        "original_entity_groups": entity_groups,  # Stage 2 output (for reference)
        "unmatched_csv_lines": unmatched_csv_lines,
        "unmatched_pdf_pages": unmatched_pdf_pages,
        # ... existing fields
    }
}
```

**Testing**:
- Update `test_lambda_phase1.py` to pass CSV
- Test with sample CSV + PDF combinations
- Verify that entity detection runs without CSV bias
- Verify that CSV matching correctly finds entity groups
- Test edge cases: CSV item with no match, PDF group not in CSV

---

## Phase 2: Backend - Reconciliation Logic (Phase 2 Lambda)

### 2.1 Modify Phase 2 Lambda for CSV-Based Verification
**File**: `cdk/lambda/lambda_function_phase2.py`

**Major changes**:

1. **New input structure**:
```python
def lambda_handler(event, context):
    pdf_s3_uri = event.get("pdf_s3_uri")
    csv_s3_uri = event.get("csv_s3_uri")  # NEW
    phase1_output = event.get("phase1_output")  # Contains csv_to_group_matches
```

2. **New system prompt** (replace `RECONCILIATION_SYSTEM_PROMPT`):
```python
CSV_RECONCILIATION_PROMPT = """
You are verifying invoice line items against supporting documentation.

CSV Master Invoice Line Items (what needs to be verified):
{csv_line_items}

For each CSV line item, I will show you the corresponding pages from the PDF.

Your task:
1. EXTRACT all relevant amounts from the PDF pages
2. SUM the amounts that relate to this CSV line item
3. VERIFY: CSV amount <= Sum of PDF amounts
4. REPORT any discrepancies

Output JSON:
{
  "line_item_verifications": [
    {
      "csv_line_number": 1,
      "csv_entity": "<entity name>",
      "csv_amount": 1500.00,
      "csv_unit": "USD",
      "pdf_pages_reviewed": [5, 6, 7],
      "pdf_amounts_found": [
        {"page": 5, "amount": 800.00, "label": "Invoice #123", "unit": "USD"},
        {"page": 6, "amount": 700.00, "label": "Invoice #124", "unit": "USD"}
      ],
      "pdf_total": 1500.00,
      "verification_passes": true,  // csv_amount <= pdf_total
      "discrepancy": null,  // or {"expected": 1500, "actual": 1400, "difference": -100}
      "confidence": 95
    }
  ],
  "summary": {
    "total_items_verified": 10,
    "items_passed": 9,
    "items_failed": 1,
    "total_discrepancy_amount": 100.00
  }
}
"""
```

3. **Implement per-line-item verification**:
```python
def verify_csv_line_item(
    line_item: InvoiceLineItem,
    pdf_pages: List[int],
    pdf_doc,
    model_id: str
) -> Dict:
    """
    Verify a single CSV line item against its supporting PDF pages.

    Returns:
        Verification result with extracted amounts and pass/fail status
    """
    # Render relevant PDF pages as images
    images = render_pages_as_images(pdf_doc, pdf_pages)

    # Build prompt with CSV line item details
    prompt = build_line_item_verification_prompt(line_item, pdf_pages)

    # Call LLM with images
    response = invoke_bedrock_with_images(model_id, prompt, images)

    # Parse response and verify
    result = parse_verification_response(response)

    # Add validation: csv_amount <= pdf_total
    result["verification_passes"] = (
        line_item.amount <= result["pdf_total"]
    )

    return result
```

4. **Batch processing for efficiency**:
```python
def reconcile_csv_against_pdf(
    csv_line_items: List[InvoiceLineItem],
    csv_to_group_matches: Dict,
    pdf_path: str,
    model_id: str
) -> Dict:
    """Process all CSV line items and return verification results."""

    doc = fitz.open(pdf_path)
    results = []

    for match in csv_to_group_matches.get("csv_to_group_matches", []):
        csv_line_num = match["csv_line_number"]
        line_item = csv_line_items[csv_line_num - 1]
        pdf_pages = match.get("matched_group_pages", [])

        if not pdf_pages:
            results.append({
                "csv_line_number": csv_line_num,
                "csv_entity": line_item.entity_name,
                "verification_passes": False,
                "error": "No supporting documentation found"
            })
            continue

        # Verify this line item
        verification = verify_csv_line_item(
            line_item,
            pdf_pages,
            doc,
            model_id
        )
        results.append(verification)

    doc.close()

    # Generate summary statistics
    summary = generate_verification_summary(results)

    return {
        "line_item_verifications": results,
        "summary": summary
    }
```

**Testing**:
- Update `test_lambda_phase2.py`
- Create sample CSV with known amounts
- Create PDF with matching documentation
- Verify pass/fail logic

---

## Phase 3: Frontend - Multi-Category File Upload UI

### 3.1 Multi-Category Upload Component
**File**: `frontend/src/components/FileUploadPanel.tsx` (new)

**Features**:
- CSV upload (master invoice)
- Separate PDF upload button for each of 12 budget categories
- Preview CSV contents (table view)
- Show which categories have line items (from CSV analysis)
- Visual indicator for which PDFs are uploaded
- Only allow processing when CSV is uploaded (PDFs optional per category)

**Budget Categories** (constants):
```tsx
const BUDGET_CATEGORIES = [
  { key: "SALARY", label: "Salary" },
  { key: "FRINGE", label: "Fringe Benefits" },
  { key: "CONTRACTUAL_SERVICE", label: "Contractual Services" },
  { key: "EQUIPMENT", label: "Equipment" },
  { key: "INSURANCE", label: "Insurance" },
  { key: "TRAVEL", label: "Travel/Conferences" },
  { key: "SPACE_RENTAL", label: "Space Rental/Occupancy Costs" },
  { key: "TELECOM", label: "Telecommunications" },
  { key: "UTILITIES", label: "Utilities" },
  { key: "SUPPLIES", label: "Supplies" },
  { key: "OTHER", label: "Other" },
  { key: "INDIRECT_COSTS", label: "Indirect Costs" }
] as const;

type BudgetCategory = typeof BUDGET_CATEGORIES[number]["key"];
```

**Component structure**:
```tsx
interface CategoryUpload {
  category: BudgetCategory;
  file: File | null;
}

interface FileUploadPanelProps {
  onFilesUploaded: (csv: File, categoryUploads: CategoryUpload[]) => void;
}

export function FileUploadPanel({ onFilesUploaded }: FileUploadPanelProps) {
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [csvPreview, setCsvPreview] = useState<any[] | null>(null);
  const [categoriesInCSV, setCategoriesInCSV] = useState<Set<BudgetCategory>>(new Set());

  // Track which categories have PDFs uploaded
  const [categoryFiles, setCategoryFiles] = useState<Record<BudgetCategory, File | null>>(
    Object.fromEntries(BUDGET_CATEGORIES.map(c => [c.key, null]))
  );

  // CSV parsing and preview
  const handleCsvChange = async (file: File) => {
    setCsvFile(file);
    const text = await file.text();
    const parsed = parseCSV(text);
    setCsvPreview(parsed.slice(0, 10)); // First 10 rows

    // Analyze which categories are present in the CSV
    const categories = new Set<BudgetCategory>();
    for (const row of parsed) {
      const budgetItem = row["Budget Item"];
      if (budgetItem && BUDGET_CATEGORIES.some(c => c.key === budgetItem)) {
        categories.add(budgetItem as BudgetCategory);
      }
    }
    setCategoriesInCSV(categories);
  };

  const handleCategoryFileChange = (category: BudgetCategory, file: File | null) => {
    setCategoryFiles(prev => ({ ...prev, [category]: file }));
  };

  const handleSubmit = () => {
    if (!csvFile) return;

    // Build array of category uploads (only those with files)
    const uploads: CategoryUpload[] = BUDGET_CATEGORIES
      .map(({ key }) => ({
        category: key,
        file: categoryFiles[key]
      }))
      .filter(upload => upload.file !== null);

    onFilesUploaded(csvFile, uploads);
  };

  return (
    <div className="file-upload-panel">
      {/* CSV Upload Section */}
      <div className="csv-upload-section">
        <h3>1. Upload Master Invoice (CSV)</h3>
        <input
          type="file"
          accept=".csv"
          onChange={(e) => e.target.files?.[0] && handleCsvChange(e.target.files[0])}
        />

        {csvPreview && (
          <div className="csv-preview">
            <h4>CSV Preview (first 10 rows)</h4>
            <table>
              <thead>
                <tr>
                  {Object.keys(csvPreview[0]).slice(0, 5).map(key => (
                    <th key={key}>{key}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {csvPreview.map((row, idx) => (
                  <tr key={idx}>
                    {Object.values(row).slice(0, 5).map((val, i) => (
                      <td key={i}>{String(val)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>

            <div className="categories-summary">
              <h4>Categories Found in CSV:</h4>
              <div className="category-chips">
                {Array.from(categoriesInCSV).map(cat => (
                  <span key={cat} className="category-chip">{cat}</span>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* PDF Uploads by Category Section */}
      {csvFile && (
        <div className="pdf-uploads-section">
          <h3>2. Upload Supporting Documentation by Category</h3>
          <p className="help-text">
            Upload a PDF for each budget category. Only categories present in your CSV are required.
          </p>

          <div className="category-upload-grid">
            {BUDGET_CATEGORIES.map(({ key, label }) => {
              const hasFile = categoryFiles[key] !== null;
              const inCSV = categoriesInCSV.has(key);

              return (
                <div
                  key={key}
                  className={`category-upload-item ${inCSV ? 'required' : 'optional'} ${hasFile ? 'uploaded' : ''}`}
                >
                  <div className="category-header">
                    <span className="category-label">{label}</span>
                    {inCSV && <span className="required-badge">In CSV</span>}
                  </div>

                  <input
                    type="file"
                    accept=".pdf"
                    id={`pdf-${key}`}
                    onChange={(e) => handleCategoryFileChange(key, e.target.files?.[0] || null)}
                    style={{ display: 'none' }}
                  />

                  <label htmlFor={`pdf-${key}`} className="upload-button">
                    {hasFile ? (
                      <>
                        <CheckIcon /> {categoryFiles[key]!.name}
                        <button
                          onClick={(e) => {
                            e.preventDefault();
                            handleCategoryFileChange(key, null);
                          }}
                          className="remove-button"
                        >
                          ×
                        </button>
                      </>
                    ) : (
                      <>
                        <UploadIcon /> Choose PDF
                      </>
                    )}
                  </label>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Submit Button */}
      <div className="submit-section">
        <button
          className="submit-button"
          disabled={!csvFile}
          onClick={handleSubmit}
        >
          Process Invoice & Documentation
        </button>

        {csvFile && (
          <div className="upload-summary">
            <p>CSV: {csvFile.name}</p>
            <p>
              PDFs: {Object.values(categoryFiles).filter(f => f !== null).length} / {categoriesInCSV.size} categories
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
```

### 3.2 Update Main App Component
**File**: `frontend/src/App.tsx`

**Changes**:

1. **Remove page type selection**:
```tsx
// DELETE: const [pageTypes, setPageTypes] = useState<...>
// No longer needed - CSV line items define the groups
```

2. **State management for multi-category uploads**:
```tsx
const [csvFile, setCsvFile] = useState<File | null>(null);
const [categoryUploads, setCategoryUploads] = useState<CategoryUpload[]>([]);
const [csvLineItems, setCsvLineItems] = useState<InvoiceLineItem[]>([]);
const [isProcessing, setIsProcessing] = useState(false);
const [analysis, setAnalysis] = useState<DocumentAnalysis | null>(null);
```

3. **Replace mock data loading with multi-category upload flow**:
```tsx
// Show upload panel if no files loaded
if (!csvFile) {
  return <FileUploadPanel onFilesUploaded={handleFilesUploaded} />;
}

// Once files uploaded, call Lambda
async function handleFilesUploaded(csv: File, uploads: CategoryUpload[]) {
  setCsvFile(csv);
  setCategoryUploads(uploads);
  setIsProcessing(true);

  try {
    // Upload files to S3
    const csvS3Uri = await uploadToS3(csv, 'csv');

    const pdfUploads = await Promise.all(
      uploads.map(async ({ category, file }) => ({
        category,
        s3_uri: await uploadToS3(file!, `pdf/${category}`)
      }))
    );

    // Call Phase 1 Lambda
    const result = await invokeLambda({
      csv_s3_uri: csvS3Uri,
      pdf_uploads: pdfUploads,  // Array of {category, s3_uri}
      model_id: "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
    });

    // Groups are now based on CSV line items
    setAnalysis(JSON.parse(result.body.answer));
    setCsvLineItems(result.body.csv_metadata.csv_line_items);
  } catch (error) {
    console.error("Processing failed:", error);
    alert("Failed to process documents. See console for details.");
  } finally {
    setIsProcessing(false);
  }
}

// Helper: Upload file to S3 and return URI
async function uploadToS3(file: File, prefix: string): Promise<string> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('prefix', prefix);

  const response = await fetch('/api/upload', {
    method: 'POST',
    body: formData
  });

  if (!response.ok) {
    throw new Error('Upload failed');
  }

  const { s3_uri } = await response.json();
  return s3_uri;
}
```

4. **Group display by category**:
```tsx
// In the left sidebar, groups are displayed as CSV line items
// Optionally grouped by budget category

const groupsByCategory = useMemo(() => {
  const grouped: Record<BudgetCategory, DocumentAnalysisGroup[]> = {};

  for (const group of allGroups) {
    const category = group.meta.csv_category;
    if (!grouped[category]) {
      grouped[category] = [];
    }
    grouped[category].push(group);
  }

  return grouped;
}, [allGroups]);

// Render groups by category
Object.entries(groupsByCategory).map(([category, groups]) => (
  <div key={category} className="category-section">
    <h3 className="category-header">{category}</h3>
    {groups.map(group => (
      <div key={group.groupId} className="csv-line-item-group">
        <div className="group-header">
          <span className="entity-name">{group.label}</span>
          <span className="csv-amount">${group.meta.csv_amount}</span>
        </div>
        <div className="supporting-pages">
          {group.supportingPages.length} supporting pages
        </div>
        <div className="category-badge">{category}</div>
      </div>
    ))}
  </div>
))
```

5. **Users can adjust page assignments**:
```tsx
// PageMembershipPanel now shows:
// - Which CSV line item (group) is currently selected
// - Which budget category it belongs to
// - Which pages are assigned to this group (with category context)
// - Allow user to add/remove pages from this group
// (This lets users correct the AI's matching if needed)
```

### 3.3 Update Group Display to Show CSV-Based Groups
**File**: `frontend/src/components/ReviewScreen.tsx` (modify existing)

**Purpose**: Display groups that are now based on CSV line items

**Key Changes**:
- Left sidebar shows CSV line items as groups
- Each group card displays CSV metadata
- Verification status shown for each group
- Summary/supporting toggle removed (all pages are supporting)

```tsx
// Groups are now CSV line items
// The existing group display just needs minor updates:

function GroupCard({ group, verification, selected, onClick }: GroupCardProps) {
  // Extract CSV metadata from group.meta
  const csvAmount = group.meta?.csv_amount;
  const csvUnit = group.meta?.csv_unit || 'USD';
  const csvLineNum = group.meta?.csv_line_number;

  return (
    <div
      className={`group-card ${selected ? 'selected' : ''}`}
      onClick={onClick}
    >
      <div className="group-header">
        <span className="line-number">#{csvLineNum}</span>
        <span className="entity-name">{group.label}</span>
      </div>

      <div className="csv-amount">
        Invoice: {csvAmount} {csvUnit}
      </div>

      <div className="supporting-info">
        {group.supportingPages.length} supporting page(s)
        {/* No summaryPages - CSV is the summary */}
      </div>

      {verification && (
        <div className={`verification-status ${verification.verification_passes ? 'pass' : 'fail'}`}>
          <StatusBadge passes={verification.verification_passes} />
          <span className="pdf-total">
            PDF Total: {verification.pdf_total} {verification.csv_unit}
          </span>
          {verification.discrepancy && (
            <div className="discrepancy">
              ⚠ Discrepancy: {verification.discrepancy.difference}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Group list shows all CSV line items
function GroupList({ groups, verifications, selectedGroupId, onSelectGroup }) {
  return (
    <div className="group-list">
      <h3>Invoice Line Items ({groups.length})</h3>
      <div className="summary-stats">
        {verifications && (
          <>
            <div className="stat pass">
              ✓ {verifications.filter(v => v.verification_passes).length} Verified
            </div>
            <div className="stat fail">
              ✗ {verifications.filter(v => !v.verification_passes).length} Issues
            </div>
          </>
        )}
      </div>
      {groups.map(group => (
        <GroupCard
          key={group.groupId}
          group={group}
          verification={verifications?.find(v => v.csv_line_number === group.meta.csv_line_number)}
          selected={selectedGroupId === group.groupId}
          onClick={() => onSelectGroup(group.groupId)}
        />
      ))}
    </div>
  );
}
```

### 3.4 Update Page Membership Panel
**File**: `frontend/src/components/PageMembershipPanel.tsx`

**Changes**:
- Show which CSV line item (group) is selected
- Display CSV metadata (amount, description, etc.)
- Show assigned supporting pages for this CSV line item
- Allow users to add/remove pages (corrections to AI matching)
- Remove "Summary" designation (only "Supporting" pages now)

```tsx
function PageMembershipPanel({ group, pageEdits, onUpdatePages }) {
  // Extract CSV info
  const csvInfo = group.meta;

  return (
    <div className="page-membership-panel">
      <h3>CSV Line Item #{csvInfo.csv_line_number}</h3>

      <div className="csv-details">
        <div className="detail-row">
          <label>Entity:</label>
          <span>{group.label}</span>
        </div>
        <div className="detail-row">
          <label>Invoice Amount:</label>
          <span>{csvInfo.csv_amount} {csvInfo.csv_unit}</span>
        </div>
        {csvInfo.csv_description && (
          <div className="detail-row">
            <label>Description:</label>
            <span>{csvInfo.csv_description}</span>
          </div>
        )}
        <div className="detail-row">
          <label>Match Confidence:</label>
          <span className={`confidence ${csvInfo.match_confidence}`}>
            {csvInfo.match_confidence}
          </span>
        </div>
        {csvInfo.match_reasoning && (
          <div className="detail-row">
            <label>Match Reasoning:</label>
            <span>{csvInfo.match_reasoning}</span>
          </div>
        )}
      </div>

      <h4>Supporting Documentation</h4>
      <div className="supporting-pages-list">
        {group.supportingPages.map(pageNum => (
          <div key={pageNum} className="page-chip supporting">
            <span>Page {pageNum}</span>
            <button onClick={() => removePage(pageNum)}>×</button>
          </div>
        ))}
      </div>

      <div className="add-page-section">
        <label>Add page to this invoice item:</label>
        <input type="number" placeholder="Page number" />
        <button>Add Page</button>
      </div>
    </div>
  );
}
```

---

## Phase 4: Data Types & Schemas

### 4.1 TypeScript Types
**File**: `frontend/src/types/Reconciliation.ts` (new)

```typescript
export interface InvoiceLineItem {
  line_number: number;
  entity_name: string | null;  // Present for SALARY rows, null for non-employee items
  budget_item: string;  // SALARY, FRINGE, INDIRECT_COSTS, OTHER, EQUIPMENT, etc.
  amount: number;
  unit: string;
  description?: string;
  date?: string;
  is_employee_item: boolean;  // true for SALARY rows with names
  raw_row: Record<string, any>;
}

export interface LineItemVerification {
  csv_line_number: number;
  csv_entity: string;
  csv_amount: number;
  csv_unit: string;
  pdf_pages_reviewed: number[];
  pdf_amounts_found: Array<{
    page: number;
    amount: number;
    label: string;
    unit: string;
  }>;
  pdf_total: number;
  verification_passes: boolean;
  discrepancy: {
    expected: number;
    actual: number;
    difference: number;
  } | null;
  confidence: number;
}

export interface CSVToGroupMatch {
  csv_line_number: number;
  csv_entity_name: string | null;  // null for non-employee items
  csv_budget_item: string;
  is_employee_item: boolean;
  matched_group_names: string[];  // Empty for non-employee items
  matched_group_pages: number[];
  match_confidence: "high" | "medium" | "low" | "none";
  match_reasoning: string;
}

export interface ReconciliationResponse {
  statusCode: number;
  body: {
    answer: string; // DocumentAnalysis JSON (CSV-based groups)
    csv_metadata: {
      total_line_items: number;
      csv_line_items: InvoiceLineItem[];
    };
    csv_to_group_matches: {
      csv_to_group_matches: CSVToGroupMatch[];
      unmatched_csv_lines: number[];
      unmatched_pdf_groups: string[];
    };
    original_entity_groups: Array<{
      name: string;
      pages: number[];
      objects: any[];
    }>;
    line_item_verifications: LineItemVerification[];
    summary: {
      total_items_verified: number;
      items_passed: number;
      items_failed: number;
      total_discrepancy_amount: number;
    };
  };
}
```

### 4.2 Python Data Classes
**File**: `cdk/lambda/models.py` (new)

```python
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any

@dataclass
class InvoiceLineItem:
    line_number: int
    entity_name: Optional[str]  # Present for SALARY rows, None for non-employee items
    budget_item: str  # SALARY, FRINGE, INDIRECT_COSTS, OTHER, EQUIPMENT, etc.
    amount: float
    unit: str
    description: Optional[str] = None
    date: Optional[str] = None
    is_employee_item: bool = False  # True for SALARY rows with names
    raw_row: Optional[Dict[str, Any]] = None

    def to_dict(self):
        return asdict(self)

@dataclass
class PDFAmount:
    page: int
    amount: float
    label: str
    unit: str

@dataclass
class LineItemVerification:
    csv_line_number: int
    csv_entity: str
    csv_amount: float
    csv_unit: str
    pdf_pages_reviewed: List[int]
    pdf_amounts_found: List[PDFAmount]
    pdf_total: float
    verification_passes: bool
    discrepancy: Optional[Dict[str, float]]
    confidence: int

    def to_dict(self):
        result = asdict(self)
        result['pdf_amounts_found'] = [
            asdict(amt) for amt in self.pdf_amounts_found
        ]
        return result
```

---

## Phase 5: Infrastructure Changes

### 5.1 Update Lambda Stack
**File**: `cdk/stacks/lambda_stack.py`

**Changes**:
- Increase timeout (CSV processing may take longer): 15 → 20 minutes
- Update Lambda handler to point to phase1 function
- Add environment variable for CSV processing config

### 5.2 Update S3 Permissions
```python
lambda_role.add_to_policy(iam.PolicyStatement(
    actions=["s3:GetObject"],
    resources=[
        "arn:aws:s3:::sf-invoices-docs/*",
        "arn:aws:s3:::sf-invoices-csv/*"  # NEW: CSV bucket
    ]
))
```

---

## Phase 6: Testing Strategy

### 6.1 Unit Tests
- `test_csv_parser.py`: CSV parsing, column detection, normalization, category grouping
- `test_reconciliation.py`: Verification logic, pass/fail conditions (per category)
- `test_matching.py`: CSV-to-entity-group matching (Stage 3, per category)
- `test_multi_pdf.py`: Multi-PDF processing, page offset calculations

### 6.2 Integration Tests

**Test Approach**: For testing, provide local PDF files and explicitly specify which category each belongs to. Files can be renamed to any schema (e.g., `salary.pdf`, `fringe.pdf`, `category_SALARY.pdf`, etc.).

1. **Happy Path - Multi-Category**:
   - CSV with line items across 3 categories (SALARY, FRINGE, OTHER)
   - 3 separate PDFs provided, one per category
   - All verifications pass

2. **Missing Category PDF**:
   - CSV has SALARY and FRINGE line items
   - Only SALARY PDF provided
   - SALARY items should verify, FRINGE items should show "No PDF provided" warning

3. **Amount Discrepancy**:
   - CSV line item: $1000
   - PDF total: $800
   - Expect verification failure

4. **Name Variations (SALARY category)**:
   - CSV entity name differs from PDF
   - Should match correctly with high confidence

5. **Non-Employee Category (e.g., EQUIPMENT)**:
   - CSV line item: "Laptops and printers - $5000"
   - PDF pages contain equipment purchase receipts
   - Should match pages by description/content, not entity names

### 6.3 Test Data Structure

**CSV File**: `test_data/Invoice_Details_Test.csv` (use actual MOHCD format)
```csv
Project Year,Program Area,AgencyID,Agency,ProjectID,Project Name,Project Description,Fund Source,Budget Item,Explanation,Reporting Period,Agency Invoice Date,Employee First Name,Employee Last Name,Employee Title,Hourly Rate,Amount,GC Name,GC Approved,Manager Name,Manager Approved
25-26,Test Program,12345,Test Agency,246001-25,Test Project,Test Description,GF-Work Order,SALARY,,11/1/2025,12/1/2025,Test,Employee,Test Title,50.00,5000.00,GC Name,12/1/2025,Manager Name,12/1/2025
25-26,Test Program,12345,Test Agency,246001-25,Test Project,Test Description,GF-Work Order,FRINGE,Fringe benefits calculation,11/1/2025,12/1/2025,,,,,1500.00,GC Name,12/1/2025,Manager Name,12/1/2025
25-26,Test Program,12345,Test Agency,246001-25,Test Project,Test Description,GF-Work Order,EQUIPMENT,Laptops and printers,11/1/2025,12/1/2025,,,,,3000.00,GC Name,12/1/2025,Manager Name,12/1/2025
```

**PDF Files**: Local test files to be renamed and categorized by tester
- **Provided by user**: `salary_docs.pdf`, `fringe_docs.pdf`, `equipment_receipts.pdf`
- **Test script will rename to**: `test_data/category_SALARY.pdf`, `test_data/category_FRINGE.pdf`, `test_data/category_EQUIPMENT.pdf`
- **Or pass directly with category labels**:
  ```python
  # In test script
  test_files = [
      {"category": "SALARY", "path": "/path/to/salary_docs.pdf"},
      {"category": "FRINGE", "path": "/path/to/fringe_docs.pdf"},
      {"category": "EQUIPMENT", "path": "/path/to/equipment.pdf"}
  ]
  ```

**Expected Content**:
- **SALARY PDF**: Timesheets, pay stubs, or invoices showing employee hours/amounts totaling $5000
- **FRINGE PDF**: Fringe benefit calculations or summary documents totaling $1500
- **EQUIPMENT PDF**: Purchase orders, receipts, or invoices for equipment totaling $3000

**Test Script Example**:
```python
# test_lambda_phase1_multi_pdf.py

def test_multi_category_upload():
    """Test with multiple PDFs across different categories."""

    # User provides these files locally
    test_files = {
        "SALARY": "/home/user/Downloads/salary_supporting_docs.pdf",
        "FRINGE": "/home/user/Downloads/fringe_benefits_calc.pdf",
        "EQUIPMENT": "/home/user/Downloads/equipment_receipts.pdf"
    }

    # Upload to S3 with category labels
    pdf_uploads = []
    for category, filepath in test_files.items():
        s3_uri = upload_to_s3_test(filepath, f"test/{category}")
        pdf_uploads.append({
            "category": category,
            "s3_uri": s3_uri
        })

    # Upload CSV
    csv_s3_uri = upload_to_s3_test("test_data/Invoice_Details_Test.csv", "test/csv")

    # Invoke Lambda
    response = lambda_client.invoke(
        FunctionName='InvoiceProcessorPhase1',
        Payload=json.dumps({
            "csv_s3_uri": csv_s3_uri,
            "pdf_uploads": pdf_uploads
        })
    )

    result = json.loads(response['Payload'].read())
    analysis = json.loads(result['body']['answer'])

    # Assertions
    assert len(analysis['groups']) == 3  # 3 CSV line items

    # Check SALARY group has pages from SALARY PDF
    salary_group = [g for g in analysis['groups'] if g['meta']['csv_category'] == 'SALARY'][0]
    assert len(salary_group['supportingPages']) > 0

    # Check FRINGE group has pages from FRINGE PDF
    fringe_group = [g for g in analysis['groups'] if g['meta']['csv_category'] == 'FRINGE'][0]
    assert len(fringe_group['supportingPages']) > 0
```

---

## Phase 7: Migration & Rollout

### 7.1 Backward Compatibility
- Keep old Lambda handlers available
- Frontend detects if CSV is provided
- If no CSV: use old flow (auto-detect groups from PDF)
- If CSV provided: use new CSV-based flow

### 7.2 Deployment Steps
1. Deploy new Lambda functions (Phase 1 & 2)
2. Update frontend with file upload UI
3. Test with sample data
4. Gradual rollout to users
5. Monitor for errors
6. Remove old code after validation period

### 7.3 Documentation Updates
- Update `CLAUDE.md` with CSV workflow
- Create user guide for CSV format requirements
- Document column name mappings
- Add troubleshooting guide

---

## Phase 8: Future Enhancements (Post-MVP)

1. **Smart Column Detection**:
   - ML-based column type inference
   - Handle various CSV formats automatically

2. **Fuzzy Entity Matching**:
   - Use embeddings for better name matching
   - Handle typos and variations

3. **Batch Processing**:
   - Process multiple CSVs at once
   - Bulk export of verification results

4. **Interactive Corrections**:
   - Let users manually map CSV items to PDF pages
   - Override verification results with notes

5. **Export Reports**:
   - Generate PDF reports with verification results
   - Excel export with detailed breakdowns

---

## Success Criteria

- ✅ Upload CSV and multiple PDFs (one per budget category)
- ✅ Frontend UI with 12 separate upload buttons for each category
- ✅ Parse CSV and detect entity/amount columns, budget item types
- ✅ Group CSV line items by budget category
- ✅ Process each PDF independently (Stage 1 & 2) without CSV bias
- ✅ Match CSV line items to appropriate PDF by category (Stage 3):
  - SALARY: Match by employee name to entity groups
  - Other categories: Match by description/content to relevant pages
- ✅ Merge multiple PDFs into single virtual document with page offsets
- ✅ Extract amounts from appropriate category PDF
- ✅ Verify: CSV amount ≤ PDF total for each line item (all categories)
- ✅ Display groups organized by category
- ✅ Handle edge cases:
  - Missing category PDFs (user didn't upload)
  - Name variations (SALARY)
  - Description matching (non-SALARY)
  - Page offset calculations
- ✅ All tests passing with multi-PDF test data
- ✅ End-to-end flow works without errors

---

## Timeline Estimate

**Phase 1**: Backend CSV Processing - 2-3 days
**Phase 2**: Reconciliation Logic - 2-3 days
**Phase 3**: Frontend UI - 3-4 days
**Phase 4-5**: Types & Infrastructure - 1 day
**Phase 6**: Testing - 2-3 days
**Phase 7**: Deployment - 1 day

**Total**: 11-15 days

---

## Open Questions

1. **CSV Format**: Do we support multiple CSV formats, or require a specific structure?
   - **Decision**: Start with the specific structure we have (MOHCD Invoice Details format), but design CSV parser to be flexible for future formats
2. **Amount Matching**: Exact match or tolerance (e.g., ±$0.01)?
3. **Missing Docs**: Hard fail or warning for CSV items without supporting docs?
4. **Multiple CSVs**: Support or single CSV per upload?
5. **Line Item Granularity**: Does each CSV line map to exactly one entity, or can it span multiple?
6. **Stage 3 Matching**: Should we use embeddings for better matching, or stick with LLM-based matching?
   - **Decision**: Start with LLM-based matching (Claude via Bedrock), can enhance with embeddings later if needed
7. **~~Employee vs Non-Employee Reconciliation~~**: ✅ **RESOLVED**
   - **Decision**: Reconcile ALL line items (both employee and non-employee)
   - Employee items (SALARY): Match by name to entity groups
   - Non-employee items (FRINGE, INDIRECT_COSTS, etc.): Match by category/description to relevant pages
