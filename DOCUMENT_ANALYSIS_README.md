# DocumentAnalysis Transformation Layer

## Overview

The Lambda function now includes a transformation layer that converts the raw `matched_entities` structure (with coordinates) into a frontend-friendly `DocumentAnalysis` format. This provides a normalized, flat structure that's easier for the frontend to consume and display.

## What Was Added

### 1. **`to_document_analysis()` Function**
   - Location: `cdk/lambda/lambda_function_phase1.py` (lines 434-608)
   - Purpose: Transform raw matched_entities into DocumentAnalysis format
   - Pure Python function with no side effects or external calls

### 2. **Integration in Lambda Handler**
   - Location: `cdk/lambda/lambda_function_phase1.py` (lines 792-799)
   - Called after coordinates are attached
   - Returns DocumentAnalysis JSON in `body.answer`
   - Maintains backward compatibility with `body.stage2_answer` (raw format)

### 3. **Test Suite**
   - File: `test_document_analysis.py`
   - Tests transformation logic, edge cases, and snippet generation
   - All tests passing

## DocumentAnalysis Schema

### Top Level
```json
{
  "schemaVersion": "1.0",
  "documentId": "string",
  "pageCount": number,
  "groups": [Group, ...]
}
```

### Group Structure
```json
{
  "groupId": "g_0",
  "label": "Entity Name",
  "kind": null,
  "summaryPages": [1, 2],
  "supportingPages": [5, 6, 7],
  "occurrences": [Occurrence, ...],
  "meta": {
    "rawSummaryObjects": [...],
    "rawSupportingObjects": [...]
  }
}
```

### Occurrence Structure
```json
{
  "occurrenceId": "g0_s0_e0",
  "groupId": "g_0",
  "pageNumber": 1,
  "role": "summary" | "supporting",
  "coords": [
    {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.05},
    ...
  ],
  "snippet": "Short text preview...",
  "rawSource": {...}
}
```

## Key Features

### 1. **Flat, Predictable Structure**
   - All highlighting locations in a flat `occurrences` list
   - Unique IDs for groups and occurrences
   - Easy to iterate and render in the frontend

### 2. **Coordinate Preservation**
   - All `coords` from the matched_entities are preserved
   - Normalized coordinates (0-1 range) ready for rendering
   - Multiple bounding boxes per occurrence supported

### 3. **Smart Snippet Generation**
   - Summary entities: Uses `name` field if available
   - Supporting entities: Uses truncated `text` (first 120 chars)
   - Provides quick preview for UI without overwhelming detail

### 4. **Defensive Coding**
   - Only includes occurrences that have coords
   - Handles missing fields gracefully
   - No crashes on malformed data
   - Validates all types before processing

### 5. **Full Data Preservation**
   - `rawSource` contains complete original entity
   - `meta` contains original summary/supporting objects
   - Frontend can access any field from original data

## ID Generation Scheme

### Group IDs
- Format: `g_{index}`
- Example: `g_0`, `g_1`, `g_2`
- Unique within document

### Occurrence IDs
- Summary format: `{groupId}_s{summaryIdx}_e{entityIdx}`
- Supporting format: `{groupId}_p{supportIdx}_e{entityIdx}`
- Examples: `g0_s0_e0`, `g1_p2_e1`
- Unique within document

## Document ID

The `documentId` field is extracted from:
1. `event.document_id` parameter (if provided)
2. Falls back to `"anonymous-document"` if not provided

To provide a custom document ID, pass it in the Lambda event:
```python
event = {
    "s3_uri": "s3://...",
    "document_id": "invoice-12345",  # Custom ID
    ...
}
```

## Response Format

The Lambda now returns:
- `body.answer`: **DocumentAnalysis JSON** (new format)
- `body.stage2_answer`: Raw matched_entities JSON (for backward compatibility)
- `body.stage1_answer`: Stage 1 entity extraction output

Frontend should consume `body.answer` for the DocumentAnalysis structure.

## Testing

### Unit Tests
```bash
source venv/bin/activate
python test_document_analysis.py
```

Tests cover:
- Basic transformation
- Entities without coords (should be excluded)
- Snippet generation (truncation, field selection)

### Integration Test
```bash
source venv/bin/activate
python test_lambda_phase1.py
```

This runs the full Lambda with a real PDF and displays:
- DocumentAnalysis structure
- Sample group with occurrences
- Coordinate information

## Example Output

```json
{
  "schemaVersion": "1.0",
  "documentId": "invoice-2024-001",
  "pageCount": 34,
  "groups": [
    {
      "groupId": "g_0",
      "label": "Rolando Mutul",
      "kind": null,
      "summaryPages": [1, 2, 5, 6],
      "supportingPages": [24, 25, 26, 27, 28, 29, 30],
      "occurrences": [
        {
          "occurrenceId": "g0_s2_e0",
          "groupId": "g_0",
          "pageNumber": 5,
          "role": "summary",
          "coords": [
            {"x": 0.132, "y": 0.185, "width": 0.105, "height": 0.014},
            {"x": 0.065, "y": 0.453, "width": 0.095, "height": 0.013}
          ],
          "snippet": "Rolando Mutul",
          "rawSource": {
            "type": "employee",
            "name": "Rolando Mutul",
            "coords": [...]
          }
        },
        {
          "occurrenceId": "g0_p1_e0",
          "groupId": "g_0",
          "pageNumber": 25,
          "role": "supporting",
          "coords": [
            {"x": 0.343, "y": 0.236, "width": 0.084, "height": 0.021}
          ],
          "snippet": "ASOCIACION MAYAB\nEmployee's Name: Rolando\nMutul...",
          "rawSource": {
            "type": "textract_text",
            "text": "ASOCIACION MAYAB\nEmployee's Name: Rolando\nMutul...",
            "coords": [...]
          }
        }
      ],
      "meta": {
        "rawSummaryObjects": [...],
        "rawSupportingObjects": [...]
      }
    }
  ]
}
```

## Frontend Integration

### Rendering Groups
```typescript
analysis.groups.forEach(group => {
  console.log(`Group: ${group.label}`);
  console.log(`  Summary pages: ${group.summaryPages}`);
  console.log(`  Supporting pages: ${group.supportingPages}`);
  console.log(`  Occurrences: ${group.occurrences.length}`);
});
```

### Rendering Highlights
```typescript
group.occurrences.forEach(occ => {
  const page = getPDFPage(occ.pageNumber);

  occ.coords.forEach(coord => {
    // Coords are already normalized (0-1)
    const rect = {
      x: coord.x * page.width,
      y: coord.y * page.height,
      width: coord.width * page.width,
      height: coord.height * page.height
    };

    drawHighlight(page, rect, occ.role === 'summary' ? 'blue' : 'yellow');
  });
});
```

### Grouping by Page
```typescript
const occurrencesByPage = new Map();

analysis.groups.forEach(group => {
  group.occurrences.forEach(occ => {
    if (!occurrencesByPage.has(occ.pageNumber)) {
      occurrencesByPage.set(occ.pageNumber, []);
    }
    occurrencesByPage.get(occ.pageNumber).push({
      ...occ,
      groupLabel: group.label
    });
  });
});

// Now render all highlights for each page
occurrencesByPage.forEach((occurrences, pageNumber) => {
  renderPageHighlights(pageNumber, occurrences);
});
```

## Migration Notes

### If You Need Raw Format
The raw `matched_entities` format is still available in `body.stage2_answer`:
```python
raw_matched_entities = json.loads(response["body"]["stage2_answer"])
```

### Future Enhancements
- `kind` field can be populated with entity types (e.g., "employee", "vendor")
- Additional metadata can be added to `meta`
- Occurrence-level metadata can be extended
- Schema version can be incremented for breaking changes

## Performance

The transformation is:
- **O(n × m)** where n = number of groups, m = average entities per group
- Purely in-memory, no I/O
- Adds ~10-50ms to Lambda execution (negligible vs. LLM calls)
- No additional AWS API calls
