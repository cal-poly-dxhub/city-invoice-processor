# Stage 1 vs Stage 2 Analysis

## Summary

**Stage 1 (Entity Detection)**: ✓ Working well - detects 8 unique entity names across 34 pages

**Stage 2 (Entity Matching)**: ⚠️ Issues - Only 3 groups created, 5 entities "lost"

---

## Stage 1 Detection Results

### Entities Detected (8 unique names):

1. **Rolando Mutul** - 3 occurrences (pages 1, 5, 6)
2. **Mutul, Rolando** - 1 occurrence (page 2) - *Same person, different format*
3. **Tomas Salazar** - 1 occurrence (page 7)
4. **Salazar, Tomas** - 2 occurrences (pages 2, 3) - *Same person, different format*
5. **Lydia I Candila** - 1 occurrence (page 1)
6. **Lydia I. Candila** - 1 occurrence (page 4) - *Same person, period difference*
7. **Asociacion Mayab** - 4 occurrences (pages 4, 5, 6, 7) - *Employer/Organization*
8. **WELLS FARGO** - 1 occurrence (page 20) - *Bank*

---

## Stage 2 Matching Results

### Groups Created (3 groups):

1. **Rolando Mutul**
   - 4 occurrences
   - Pages: 1, 2, 5, 6
   - ✓ Successfully merged "Rolando Mutul" + "Mutul, Rolando"

2. **Tomas Salazar**
   - 3 occurrences
   - Pages: 2, 3, 7
   - ✓ Successfully merged "Tomas Salazar" + "Salazar, Tomas"

3. **Lydia I Candila**
   - 2 occurrences
   - Pages: 1, 4
   - ✓ Successfully merged "Lydia I Candila" + "Lydia I. Candila"

---

## Entities "Lost" in Stage 2 (5 entities)

### 1. **Asociacion Mayab** (Employer) - ❌ NOT MATCHED
- Detected on pages: 4, 5, 6, 7
- Type: employer
- Has address information
- **Reason for loss**: Stage 2 is designed to match employees, not employers
- **Should we create a group?**: Depends on use case
  - If tracking payments TO the organization: No
  - If tracking payments FROM the organization: Maybe

### 2-4. **Name Format Variations** - ✓ SUCCESSFULLY MERGED
These were actually matched correctly:
- "Mutul, Rolando" → Merged into "Rolando Mutul" group
- "Salazar, Tomas" → Merged into "Tomas Salazar" group
- "Lydia I. Candila" → Merged into "Lydia I Candila" group

### 5. **WELLS FARGO** (Bank) - ❌ NOT MATCHED
- Detected on page: 20
- Type: None
- **Reason for loss**: Only appears once, no supporting documents
- **Should we create a group?**: Probably not - single mention without context

---

## Key Findings

### ✅ What's Working:
1. **Stage 1 entity detection** is accurate
2. **Name variation handling** works well (FirstName LastName ↔ LastName, FirstName)
3. **Employee entities** are being matched correctly
4. **Coordinate attachment** is working (only entities with names get coords)

### ⚠️ Potential Issues:

1. **Employer entities not grouped**
   - "Asociacion Mayab" appears 4 times but isn't matched
   - This is by design (Stage 2 matches employees, not employers)
   - Question: Should we track employer/organization entities separately?

2. **Single-occurrence entities ignored**
   - "WELLS FARGO" appears once on page 20
   - Not matched because no supporting documents found
   - This may be intentional (filtering out noise)

3. **Non-employee entities filtered out**
   - Only entities with `name` field get coordinates
   - Payment entities, dates, amounts don't get their own groups
   - This is correct per our recent fix

---

## Recommendations

### Option 1: Current behavior is correct ✓
- Focus on employee/person entities only
- Ignore employers and single mentions
- This gives clean, focused results

### Option 2: Add employer tracking
- Create separate groups for organizations ("Asociacion Mayab")
- Track where the company name appears
- Useful for org-level analysis

### Option 3: Add bank/vendor tracking
- Create groups for vendors like "WELLS FARGO"
- Would need to handle single occurrences better
- Useful for expense tracking

---

## Files Generated

1. **stage1_analysis.json** - Full Stage 1 output (all 34 pages with entities)
2. **lambda_response_output.json** - Complete output (Stage 1 + Stage 2 + final DocumentAnalysis)
3. **This file (STAGE_ANALYSIS_SUMMARY.md)** - Human-readable analysis

---

## Conclusion

**Stage 2 is working correctly** for its intended purpose (matching employee entities). The "lost" entities are:
- **Employers** (intentionally not matched)
- **Banks/vendors** (single occurrences, no context)
- **Name variations** (actually successfully merged)

No entities are being "lost" unexpectedly. The Stage 2 matching logic is functioning as designed.
