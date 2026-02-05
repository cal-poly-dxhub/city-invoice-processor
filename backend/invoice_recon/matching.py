"""Matching logic for line items to evidence pages."""

import logging
import re
from decimal import Decimal
from itertools import combinations
from typing import Any, Dict, List, Optional, Set, Tuple
from rapidfuzz import fuzz
from invoice_recon.budget_items import is_employee_budget_item
from invoice_recon.models import (
    CandidateEvidenceSet,
    LineItem,
    PageRecord,
    SelectedEvidence,
)

logger = logging.getLogger(__name__)


def normalize_name(name: Optional[str]) -> str:
    """Normalize a name for matching (lowercase, collapse whitespace)."""
    if not name:
        return ""
    # Collapse whitespace and punctuation
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"[^\w\s]", "", name)
    return name.lower().strip()


def extract_name_tokens(name: str) -> Set[str]:
    """Extract tokens from a name."""
    return set(normalize_name(name).split())


def normalize_person_name(person: Dict) -> Tuple[str, str, str]:
    """
    Normalize person name from entities.

    Returns: (full_name, first_name, last_name) all normalized
    """
    full_name = normalize_name(person.get("full_name", ""))
    first_name = normalize_name(person.get("first_name", ""))
    last_name = normalize_name(person.get("last_name", ""))
    return (full_name, first_name, last_name)


def find_word_boxes_for_terms(
    page: PageRecord,
    search_terms: List[str],
) -> List[Dict[str, float]]:
    """
    Find word bounding boxes for search terms in a page.

    Args:
        page: Page with word boxes (from Textract)
        search_terms: List of terms to search for (amounts, names, etc.)

    Returns:
        List of bounding boxes (normalized 0-1) that match the search terms
    """
    if not page.words or not search_terms:
        return []

    # Normalize search terms for matching
    normalized_terms = [normalize_name(term) for term in search_terms]

    matched_boxes = []
    for word_obj in page.words:
        word_text = word_obj.get("text", "")
        word_normalized = normalize_name(word_text)

        # Check if this word matches any search term
        for term in normalized_terms:
            if not term:
                continue

            # Exact match after normalization
            if word_normalized == term:
                matched_boxes.append({
                    "left": word_obj.get("left", 0),
                    "top": word_obj.get("top", 0),
                    "width": word_obj.get("width", 0),
                    "height": word_obj.get("height", 0),
                })
                break

            # For amounts: allow flexible matching with punctuation removed, but exact only
            # Only match if the term contains digits (likely an amount)
            if re.search(r'\d', term):
                word_alphanum = re.sub(r'[^\w]', '', word_text.lower())
                term_alphanum = re.sub(r'[^\w]', '', term.lower())

                # Exact match only (e.g., "$12.34" matches "1234" but not "234")
                if word_alphanum and term_alphanum and word_alphanum == term_alphanum:
                    logger.debug(f"Amount match: word='{word_text}' ({word_alphanum}) matches term='{term}' ({term_alphanum})")
                    matched_boxes.append({
                        "left": word_obj.get("left", 0),
                        "top": word_obj.get("top", 0),
                        "width": word_obj.get("width", 0),
                        "height": word_obj.get("height", 0),
                    })
                    break

    return matched_boxes


def find_all_word_boxes_for_term(
    page: PageRecord,
    term: str,
) -> List[Dict[str, float]]:
    """
    Find ALL word boxes matching a term (returns all occurrences).

    Similar to find_word_boxes_for_terms but returns all occurrences
    of a single term rather than stopping at first match.

    Args:
        page: Page with word boxes
        term: Search term (will be normalized)

    Returns:
        List of all bounding boxes matching the term
    """
    if not page.words or not term:
        return []

    term_normalized = normalize_name(term)
    if not term_normalized:
        return []

    matched_boxes = []
    for word_obj in page.words:
        word_text = word_obj.get("text", "")
        word_normalized = normalize_name(word_text)

        # Exact match after normalization
        if word_normalized == term_normalized:
            matched_boxes.append({
                "left": word_obj.get("left", 0),
                "top": word_obj.get("top", 0),
                "width": word_obj.get("width", 0),
                "height": word_obj.get("height", 0),
            })

    return matched_boxes


def calculate_proximity_score(
    first_box: Dict[str, float],
    last_box: Dict[str, float],
) -> float:
    """
    Calculate spatial proximity score between two word boxes.

    Uses normalized 0-1 coordinates to determine if first and last names
    appear close together on the page (indicating same person).

    Args:
        first_box: Bounding box for first name {left, top, width, height}
        last_box: Bounding box for last name {left, top, width, height}

    Returns:
        Proximity score from 0.0 (far apart) to 1.0 (very close)
    """
    # Calculate vertical distance
    vertical_dist = abs(first_box['top'] - last_box['top'])

    # Calculate horizontal distance between centers
    first_center_x = first_box['left'] + first_box['width'] / 2
    last_center_x = last_box['left'] + last_box['width'] / 2
    horizontal_dist = abs(first_center_x - last_center_x)

    # Same line (very close vertically)
    if vertical_dist < 0.02:
        # Names on same line - check horizontal proximity
        if horizontal_dist < 0.15:
            return 1.0  # Very close (adjacent)
        elif horizontal_dist < 0.30:
            return 0.7  # Same line, moderate distance
        else:
            return 0.3  # Same line but far apart

    # Adjacent lines
    elif vertical_dist < 0.04:
        if horizontal_dist < 0.30:
            return 0.6  # Close vertically and horizontally aligned
        else:
            return 0.4  # Adjacent lines but misaligned

    # Nearby (within same section)
    elif vertical_dist < 0.10:
        if horizontal_dist < 0.30:
            return 0.4  # Nearby and aligned
        else:
            return 0.2  # Nearby but misaligned

    # Distant
    else:
        return 0.0  # Too far apart


def find_name_pairs_with_proximity(
    page: PageRecord,
    first_name: str,
    last_name: str,
) -> List[Tuple[Dict[str, float], Dict[str, float], float]]:
    """
    Find all (first_box, last_box, proximity_score) pairs on page.

    Finds all occurrences of first and last names, calculates proximity
    between all combinations, and returns sorted by proximity score.

    Args:
        page: Page with word boxes
        first_name: First name to search for (will be normalized)
        last_name: Last name to search for (will be normalized)

    Returns:
        List of (first_box, last_box, proximity_score) tuples
        sorted by proximity_score descending (best matches first)
    """
    # Find all occurrences of first and last names
    first_boxes = find_all_word_boxes_for_term(page, first_name)
    last_boxes = find_all_word_boxes_for_term(page, last_name)

    if not first_boxes or not last_boxes:
        return []

    # Calculate proximity for all combinations
    pairs = []
    for first_box in first_boxes:
        for last_box in last_boxes:
            proximity_score = calculate_proximity_score(first_box, last_box)
            pairs.append((first_box, last_box, proximity_score))

    # Sort by proximity score descending (best matches first)
    pairs.sort(key=lambda x: x[2], reverse=True)

    return pairs


def calculate_proximity_modifier(proximity_score: float) -> float:
    """
    Calculate score modifier based on spatial proximity.

    Args:
        proximity_score: Proximity score from 0.0 to 1.0

    Returns:
        Score modifier to apply:
        - +0.25: High confidence (names very close)
        - +0.10: Moderate confidence (names nearby)
        - 0.00: Weak signal (names somewhat distant)
        - -0.20: Penalty (names far apart, likely different people)
    """
    if proximity_score >= 0.7:
        return +0.25  # High confidence bonus
    elif proximity_score >= 0.4:
        return +0.10  # Moderate confidence bonus
    elif proximity_score >= 0.2:
        return 0.0    # Neutral (weak signal)
    else:
        return -0.20  # Penalty for distant names


def get_search_terms_for_line_item(line_item: LineItem) -> List[str]:
    """
    Extract search terms from a line item for highlighting.

    Returns:
        List of terms to search for (employee names, amount, key explanation words)
    """
    terms = []

    # Add employee names if present
    if line_item.employee_first_name:
        terms.append(line_item.employee_first_name)
    if line_item.employee_last_name:
        terms.append(line_item.employee_last_name)
    if line_item.employee_first_name and line_item.employee_last_name:
        terms.append(f"{line_item.employee_first_name} {line_item.employee_last_name}")

    # Add amount if present - multiple formatting variations
    if line_item.amount:
        amount_str = str(line_item.amount)

        # Add raw amount
        terms.append(amount_str)

        # Add with dollar sign
        terms.append(f"${amount_str}")

        # Add with comma thousands separator (e.g., "5,000.00")
        try:
            amount_float = float(line_item.amount)
            amount_formatted = f"{amount_float:,.2f}"
            terms.append(amount_formatted)
            terms.append(f"${amount_formatted}")
        except (ValueError, TypeError):
            pass

    logger.debug(f"Search terms for amount {line_item.amount}: {[t for t in terms if re.search(r'\\d', t)]}")
    return terms


def score_page_for_employee(
    line_item: LineItem,
    page: PageRecord,
) -> Tuple[float, List[str]]:
    """
    Score a page for an employee-based line item.

    Returns: (score, rationale)

    Scoring (updated to reduce false positives from common surnames):
    - +0.35 if last name exact match (reduced from 0.70)
    - +0.30 if first name exact match
    - +0.20 if first name fuzzy match (ratio >= 80, catches nicknames)
    - +0.15 if first initial matches (only if no fuzzy match)
    - +0.15 if token_sort_ratio(full_name) >= 90
    - +0.25 if first/last names very close together (proximity >= 0.7)
    - +0.10 if first/last names moderately close (proximity >= 0.4)
    - -0.20 if first/last names far apart (proximity < 0.2, likely different people)
    - +0.40 if amount on page matches line item amount (validation)
    - +0.10 if doc_type in {"timecard", "paystub"}
    - Cap to 1.0
    """
    score = 0.0
    rationale = []

    # Normalize line item names
    li_first = normalize_name(line_item.employee_first_name)
    li_last = normalize_name(line_item.employee_last_name)
    li_full = f"{li_first} {li_last}".strip()

    if not li_last:
        # Can't match without last name
        return (0.0, ["No employee last name in line item"])

    # Check each person in page entities
    people = page.entities.get("people", [])

    matched_person = False
    best_first_name_score = 0.0
    best_first_name_rationale = None

    # Proximity tracking (calculated after finding matched person)
    proximity_modifier = 0.0
    proximity_rationale = None

    for person in people:
        p_full, p_first, p_last = normalize_person_name(person)

        # Last name exact match (reduced weight to avoid false positives)
        if li_last and p_last and li_last == p_last:
            if not matched_person:
                score += 0.35
                rationale.append(f"Last name match: {li_last}")
                matched_person = True

        # First name matching - track best match to avoid double-counting
        if li_first and p_first and matched_person:
            first_score = 0.0
            first_rationale = None

            # Exact match (highest priority)
            if li_first == p_first:
                first_score = 0.30
                first_rationale = f"First name match: {li_first}"
            else:
                # Fuzzy match for nicknames (Bob/Robert, Maggie/Margaret)
                first_ratio = fuzz.ratio(li_first, p_first)
                if first_ratio >= 80:
                    first_score = 0.20
                    first_rationale = f"First name fuzzy match: {li_first} ~ {p_first} ({first_ratio}%)"
                # Initial match (fallback, only if no fuzzy match)
                elif li_first[0] == p_first[0]:
                    first_score = 0.15
                    first_rationale = f"First initial match: {li_first[0]}"

            # Keep best first name match
            if first_score > best_first_name_score:
                best_first_name_score = first_score
                best_first_name_rationale = first_rationale

        # Fuzzy full name match (only if BOTH first and last names have some similarity)
        # This prevents false positives from shared surnames
        if li_full and p_full and matched_person and best_first_name_score > 0:
            ratio = fuzz.token_sort_ratio(li_full, p_full)
            if ratio >= 90:
                score += 0.10
                rationale.append(f"Full name fuzzy match: {ratio}%")

    # Add best first name match
    if best_first_name_rationale:
        score += best_first_name_score
        rationale.append(best_first_name_rationale)

    # Calculate proximity modifier if we have both names and word geometry
    if li_first and li_last and page.words and matched_person:
        name_pairs = find_name_pairs_with_proximity(page, li_first, li_last)

        if name_pairs:
            # Use best (closest) pair
            first_box, last_box, proximity_score = name_pairs[0]
            proximity_modifier = calculate_proximity_modifier(proximity_score)

            # Build rationale message
            if proximity_score >= 0.7:
                proximity_rationale = f"Name proximity: very close (score={proximity_score:.2f}, bonus={proximity_modifier:+.2f})"
            elif proximity_score >= 0.4:
                proximity_rationale = f"Name proximity: moderate (score={proximity_score:.2f}, bonus={proximity_modifier:+.2f})"
            elif proximity_score >= 0.2:
                proximity_rationale = f"Name proximity: weak (score={proximity_score:.2f}, neutral)"
            else:
                proximity_rationale = f"Name proximity: distant (score={proximity_score:.2f}, penalty={proximity_modifier:+.2f})"

    # Apply proximity modifier
    if proximity_modifier != 0.0:
        score += proximity_modifier
        if proximity_rationale:
            rationale.append(proximity_rationale)

    # Amount validation - strong signal when combined with last name
    if line_item.amount and matched_person:
        target_amount = float(line_item.amount)
        page_amounts = page.entities.get("amounts", [])

        for amt_obj in page_amounts:
            amt_value = amt_obj.get("value")
            if amt_value is not None:
                try:
                    page_amount = float(amt_value)
                    # Exact match (within $0.01)
                    if abs(page_amount - target_amount) < 0.01:
                        score += 0.40
                        rationale.append(f"Amount match: ${target_amount:.2f}")
                        break
                except (ValueError, TypeError):
                    continue

    # Doc type bonus
    doc_type = page.entities.get("doc_type", "unknown")
    if doc_type in ("timecard", "paystub"):
        score += 0.10
        rationale.append(f"Doc type: {doc_type}")

    # Cap to 1.0
    score = min(score, 1.0)

    if not matched_person and score == 0.0:
        rationale.append("No matching person found")

    return (score, rationale)


def score_page_for_non_employee(
    line_item: LineItem,
    page: PageRecord,
) -> Tuple[float, List[str]]:
    """
    Score a page for a non-employee line item.

    Uses:
    - Explanation keywords
    - Organization names
    - Doc type matching

    Scoring:
    - +0.05 per keyword token found in page text (capped)
    - +0.15 if organization appears in explanation (fuzzy)
    - +0.10 if doc_type matches expected types for budget item
    """
    score = 0.0
    rationale = []

    explanation = line_item.explanation or ""
    if not explanation:
        return (0.0, ["No explanation text to match"])

    # Tokenize explanation (remove short words)
    tokens = [
        t for t in normalize_name(explanation).split()
        if len(t) >= 3
    ]

    # Count keyword matches in page text
    page_text_lower = page.text.lower()
    matched_tokens = []
    for token in tokens[:20]:  # Limit to first 20 tokens
        if token in page_text_lower:
            matched_tokens.append(token)

    if matched_tokens:
        keyword_score = min(len(matched_tokens) * 0.05, 0.40)
        score += keyword_score
        rationale.append(
            f"Keyword matches: {len(matched_tokens)} "
            f"({', '.join(matched_tokens[:5])}...)"
        )

    # Organization matching
    organizations = page.entities.get("organizations", [])
    for org in organizations:
        org_norm = normalize_name(org)
        if org_norm and org_norm in normalize_name(explanation):
            score += 0.15
            rationale.append(f"Organization match: {org}")
            break

    # Doc type matching (heuristic mapping)
    doc_type = page.entities.get("doc_type", "unknown")
    expected_doc_types = get_expected_doc_types(line_item.budget_item)
    if doc_type in expected_doc_types:
        score += 0.10
        rationale.append(f"Doc type match: {doc_type}")

    if score == 0.0:
        rationale.append("No meaningful matches found")

    return (score, rationale)


def get_expected_doc_types(budget_item: str) -> Set[str]:
    """Get expected document types for a budget item."""
    mapping = {
        "Equipment": {"invoice", "receipt"},
        "Supplies": {"invoice", "receipt"},
        "Utilities": {"utility_bill", "invoice"},
        "Telecommunications": {"utility_bill", "invoice"},
        "Space Rental/Occupancy Costs": {"invoice", "receipt"},
        "Travel and Conferences": {"invoice", "receipt"},
        "Contractual Service": {"invoice"},
        "Insurance": {"invoice"},
        "Other": {"invoice", "receipt", "other"},
        "Indirect Costs": {"invoice", "other"},
    }
    return mapping.get(budget_item, {"invoice", "receipt", "other"})


def score_page_by_amount(
    line_item: LineItem,
    page: PageRecord,
    tolerance: float = 0.01,
) -> Tuple[float, List[str], Optional[List[Dict[str, Any]]]]:
    """
    Score a page by amount matching.

    Checks if the page contains amounts that match or sum to the line item amount.

    Args:
        line_item: Line item to match
        page: Page to score
        tolerance: Tolerance for amount matching (default $0.01)

    Returns:
        Tuple of (score, rationale, matched_amounts)
        - matched_amounts: List of amount dicts for highlighting (for component matches)
                          None for exact/partial matches (use target amount instead)
    """
    score = 0.0
    rationale = []

    # Skip if line item has no amount
    if not line_item.amount:
        return (0.0, ["No amount in line item"], None)

    target_amount = float(line_item.amount)

    # Extract amounts from page entities
    page_amounts = page.entities.get("amounts", [])
    if not page_amounts:
        return (0.0, ["No amounts extracted from page"], None)

    # Build list of numeric amounts with their context AND budget_item
    amounts_with_context = []
    for amt_obj in page_amounts:
        amt_value = amt_obj.get("value")
        if amt_value is not None:
            try:
                amounts_with_context.append({
                    "value": float(amt_value),
                    "raw": amt_obj.get("raw", ""),
                    "context": amt_obj.get("context", "")[:50],
                    "budget_item": amt_obj.get("budget_item"),  # NEW: include budget_item
                })
            except (ValueError, TypeError):
                continue

    if not amounts_with_context:
        return (0.0, ["No valid amounts on page"], None)

    # Strategy 1: Exact match (single amount equals target)
    for amt in amounts_with_context:
        if abs(amt["value"] - target_amount) < tolerance:
            score = 0.95
            rationale.append(f"Exact amount match: ${amt['value']:.2f}")
            rationale.append(f"Context: {amt['context']}")
            return (score, rationale, None)  # None = use target amount for highlighting

    # Strategy 2: Component match (2-4 amounts sum to target)
    # Only try if we have multiple amounts and target is reasonable
    if len(amounts_with_context) >= 2 and target_amount < 10000:
        # NEW: Filter amounts by budget item BEFORE trying combinations
        line_item_budget = line_item.budget_item  # e.g., "Salary"

        filtered_amounts = []
        excluded_count = 0
        for amt in amounts_with_context:
            amt_budget = amt.get("budget_item")

            # Include if:
            # 1. Amount budget matches line item budget, OR
            # 2. Amount has no budget_item field (backward compat)
            if amt_budget is None or amt_budget == line_item_budget:
                filtered_amounts.append(amt)
            else:
                excluded_count += 1

        # Log filtering if any amounts were excluded
        if excluded_count > 0:
            logger.debug(
                f"Filtered amounts for {line_item_budget}: "
                f"kept {len(filtered_amounts)}, excluded {excluded_count}"
            )
            rationale.append(
                f"Filtered to {line_item_budget} amounts (excluded {excluded_count} from other budget items)"
            )

        # Try combinations of filtered amounts
        for combo_size in [2, 3, 4]:
            if combo_size > len(filtered_amounts):
                continue

            for combo in combinations(filtered_amounts, combo_size):
                combo_sum = sum(amt["value"] for amt in combo)
                if abs(combo_sum - target_amount) < tolerance:
                    score = 0.85
                    combo_values = [f"${amt['value']:.2f}" for amt in combo]
                    rationale.append(
                        f"Component match: {' + '.join(combo_values)} = ${combo_sum:.2f}"
                    )
                    rationale.append("Multiple amounts sum to target")
                    # Return the component amounts for highlighting
                    return (score, rationale, list(combo))

    # Strategy 3: Partial match (amount is close but not exact)
    for amt in amounts_with_context:
        # Within 10% of target
        if target_amount > 0 and abs(amt["value"] - target_amount) / target_amount < 0.10:
            score = 0.50
            diff = amt["value"] - target_amount
            rationale.append(
                f"Close amount: ${amt['value']:.2f} (diff: ${diff:+.2f})"
            )
            return (score, rationale, None)  # None = use target amount for highlighting

    return (0.0, ["No amount matches found"], None)


def generate_amount_based_candidates(
    line_item: LineItem,
    pages: List[PageRecord],
) -> List[CandidateEvidenceSet]:
    """
    Generate candidates based on amount matching.

    Args:
        line_item: Line item to match
        pages: Pages from the relevant budget item PDF

    Returns:
        List of amount-based candidate evidence sets
    """
    if not line_item.amount or not pages:
        return []

    # Score all pages by amount
    page_scores = []
    for page in pages:
        score, rationale, matched_amounts = score_page_by_amount(line_item, page)
        if score > 0:
            page_scores.append((page, score, rationale, matched_amounts))

    if not page_scores:
        return []

    # Sort by score descending
    page_scores.sort(key=lambda x: x[1], reverse=True)

    candidates = []

    # Return only the top scoring page with the best amount match
    # Component matches already capture all amounts from the same page
    if page_scores:
        best_page, best_score, best_rationale, best_matched_amounts = page_scores[0]

        # Find word boxes for the amount
        search_terms = []

        if best_matched_amounts:
            # Component match: highlight each component amount
            logger.debug(f"Component match: highlighting {len(best_matched_amounts)} component amounts")
            for amt in best_matched_amounts:
                amt_str = str(amt["value"])
                search_terms.append(amt_str)
                search_terms.append(f"${amt_str}")
                if amt.get("raw"):
                    search_terms.append(amt["raw"])

                # Add formatted version with 2 decimal places
                try:
                    amt_float = float(amt["value"])
                    amt_formatted = f"{amt_float:,.2f}"
                    search_terms.append(amt_formatted)
                    search_terms.append(f"${amt_formatted}")
                except (ValueError, TypeError):
                    pass
        else:
            # Exact or partial match: highlight the target amount
            amount_str = str(line_item.amount)
            search_terms.append(amount_str)
            search_terms.append(f"${amount_str}")

            # Add formatted version with 2 decimal places
            try:
                amount_float = float(line_item.amount)
                amount_formatted = f"{amount_float:,.2f}"
                search_terms.append(amount_formatted)
                search_terms.append(f"${amount_formatted}")
            except (ValueError, TypeError):
                pass

        logger.debug(f"Amount highlighting search terms: {search_terms}")
        highlights = {
            best_page.page_number: find_word_boxes_for_terms(best_page, search_terms)
        }

        candidates.append(
            CandidateEvidenceSet(
                doc_id=pages[0].doc_id,
                page_numbers=[best_page.page_number],
                score=best_score,
                rationale=["Amount-based match"] + best_rationale,
                evidence_snippets=[],
                highlights=highlights,
            )
        )

    return candidates


def generate_cross_page_component_candidates(
    line_item: LineItem,
    pages: List[PageRecord],
    tolerance: float = 0.01,
    max_pages_in_combo: int = 4,
) -> List[CandidateEvidenceSet]:
    """
    Generate candidates by finding amounts across multiple pages that sum to target.

    This is a fallback strategy when same-page component matching doesn't work.
    Looks for combinations of 2-4 pages where each page has at least one amount,
    and those amounts sum to the target.

    Args:
        line_item: Line item to match
        pages: Pages from the relevant budget item PDF
        tolerance: Tolerance for amount matching (default $0.01)
        max_pages_in_combo: Maximum pages to combine (default 4)

    Returns:
        List of cross-page component candidates
    """
    if not line_item.amount or not pages:
        return []

    target_amount = float(line_item.amount)

    # Extract the best (largest) amount from each page
    page_amounts = []
    for page in pages:
        amounts = page.entities.get("amounts", [])
        if not amounts:
            continue

        # Get the largest amount on this page (often the most relevant)
        valid_amounts = []
        for amt_obj in amounts:
            amt_value = amt_obj.get("value")
            if amt_value is not None:
                try:
                    valid_amounts.append({
                        "page": page,
                        "value": float(amt_value),
                        "raw": amt_obj.get("raw", ""),
                        "context": amt_obj.get("context", "")[:50],
                    })
                except (ValueError, TypeError):
                    continue

        if valid_amounts:
            # Take the largest amount from this page
            largest = max(valid_amounts, key=lambda x: x["value"])
            page_amounts.append(largest)

    if len(page_amounts) < 2:
        # Need at least 2 pages to do cross-page matching
        return []

    # Try combinations of 2-4 pages
    candidates = []
    for combo_size in [2, 3, 4]:
        if combo_size > len(page_amounts) or combo_size > max_pages_in_combo:
            continue

        for combo in combinations(page_amounts, combo_size):
            combo_sum = sum(amt["value"] for amt in combo)
            if abs(combo_sum - target_amount) < tolerance:
                # Found a cross-page component match!
                combo_pages = sorted(set(amt["page"].page_number for amt in combo))
                combo_values = [f"${amt['value']:.2f}" for amt in combo]
                page_refs = [f"p{amt['page'].page_number}" for amt in combo]

                rationale = [
                    "Cross-page component match",
                    f"Amounts across pages: {' + '.join(f'{v} ({p})' for v, p in zip(combo_values, page_refs))} = ${combo_sum:.2f}",
                    "Multiple pages contain amounts that sum to target"
                ]

                # Find highlights for each page's amount
                highlights = {}
                for amt in combo:
                    page_num = amt["page"].page_number
                    amt_str = str(amt["value"])
                    search_terms = [amt_str, f"${amt_str}", amt["raw"]]

                    # Add formatted version with 2 decimal places
                    try:
                        amt_float = float(amt["value"])
                        amt_formatted = f"{amt_float:,.2f}"
                        search_terms.append(amt_formatted)
                        search_terms.append(f"${amt_formatted}")
                    except (ValueError, TypeError):
                        pass

                    boxes = find_word_boxes_for_terms(amt["page"], search_terms)
                    if boxes:
                        highlights[page_num] = boxes

                candidates.append(
                    CandidateEvidenceSet(
                        doc_id=pages[0].doc_id,
                        page_numbers=combo_pages,
                        score=0.80,  # Slightly lower than same-page component (0.85)
                        rationale=rationale,
                        evidence_snippets=[],
                        highlights=highlights,
                    )
                )

                # Return first match found (avoid combinatorial explosion)
                return candidates

    return []


def generate_candidates_for_line_item(
    line_item: LineItem,
    pages: List[PageRecord],
    max_candidates: int = 5,
    top_k_neighbors: int = 5,
) -> List[CandidateEvidenceSet]:
    """
    Generate candidate evidence sets for a line item.

    Args:
        line_item: Line item to match
        pages: Pages from the relevant budget item PDF
        max_candidates: Maximum number of candidates to return
        top_k_neighbors: Number of top pages to include with neighbors

    Returns:
        List of candidate evidence sets, sorted by score descending
    """
    if not pages:
        return []

    # Skip matching for line items with $0 amount (no transaction to verify)
    if line_item.amount is not None and line_item.amount == 0:
        logger.debug(f"Row {line_item.row_index}: Skipping match (amount is $0)")
        return []

    # Determine if employee-based matching
    is_employee = is_employee_budget_item(line_item.budget_item)

    # Build candidate sets
    candidates = []

    # For non-employee items with amounts, try amount-based matching FIRST
    if not is_employee and line_item.amount:
        amount_candidates = generate_amount_based_candidates(line_item, pages)

        # If we found high-quality amount matches (score >= 0.80), prioritize them
        if amount_candidates and amount_candidates[0].score >= 0.80:
            logger.info(
                f"Row {line_item.row_index}: Found high-quality amount match "
                f"(score={amount_candidates[0].score:.2f}, pages={amount_candidates[0].page_numbers})"
            )
            # Add amount-based candidates at the front
            candidates.extend(amount_candidates)
        else:
            # No high-quality same-page match found, try cross-page component matching
            logger.debug(
                f"Row {line_item.row_index}: No high-quality same-page match, "
                "trying cross-page component matching"
            )
            cross_page_candidates = generate_cross_page_component_candidates(line_item, pages)

            if cross_page_candidates:
                logger.info(
                    f"Row {line_item.row_index}: Found cross-page component match "
                    f"(score={cross_page_candidates[0].score:.2f}, pages={cross_page_candidates[0].page_numbers})"
                )
                candidates.extend(cross_page_candidates)
                # Also add the partial same-page match if it exists
                if amount_candidates:
                    candidates.extend(amount_candidates)
            elif amount_candidates:
                # Add lower-scoring amount matches but also generate keyword matches
                candidates.extend(amount_candidates)
                logger.debug(
                    f"Row {line_item.row_index}: Found partial amount match, "
                    "will also generate keyword-based candidates"
                )

    # Score all pages using traditional keyword/employee matching
    page_scores: List[Tuple[PageRecord, float, List[str]]] = []
    for page in pages:
        if is_employee and line_item.employee_last_name:
            score, rationale = score_page_for_employee(line_item, page)
        else:
            score, rationale = score_page_for_non_employee(line_item, page)

        page_scores.append((page, score, rationale))

    # Sort by score descending
    page_scores.sort(key=lambda x: x[1], reverse=True)

    # Candidate 1: All strong matches (score >= 0.75)
    strong_matches = [p for p, s, r in page_scores if s >= 0.75]
    if strong_matches:
        total_score = sum(s for p, s, r in page_scores if s >= 0.75)
        rationale_combined = ["Strong matches (score >= 0.75)"]

        # Find highlights for strong matches
        search_terms = get_search_terms_for_line_item(line_item)
        logger.debug(f"Row {line_item.row_index} ({line_item.budget_item}): search_terms={search_terms}")
        highlights = {}
        for page in strong_matches:
            boxes = find_word_boxes_for_terms(page, search_terms)
            logger.debug(f"Row {line_item.row_index}: page {page.page_number} found {len(boxes)} boxes")
            if boxes:
                highlights[page.page_number] = boxes
        logger.debug(f"Row {line_item.row_index}: total highlights for {len(strong_matches)} pages: {len(highlights)} pages with boxes")

        candidates.append(
            CandidateEvidenceSet(
                doc_id=pages[0].doc_id,
                page_numbers=[p.page_number for p in strong_matches],
                score=total_score / len(strong_matches),
                rationale=rationale_combined,
                evidence_snippets=[],
                highlights=highlights,
            )
        )

    # Candidate 2: Top K pages + neighbors
    top_k = page_scores[:top_k_neighbors]
    neighbor_pages = set()
    for page, score, rationale in top_k:
        if score > 0.0:
            neighbor_pages.add(page.page_number)
            # Add ±1 neighbor
            if page.page_number > 1:
                neighbor_pages.add(page.page_number - 1)
            if page.page_number < len(pages):
                neighbor_pages.add(page.page_number + 1)

    if neighbor_pages:
        avg_score = sum(s for p, s, r in top_k if s > 0.0) / max(
            len([s for p, s, r in top_k if s > 0.0]), 1
        )

        # Find highlights for top-K pages (not neighbors)
        search_terms = get_search_terms_for_line_item(line_item)
        highlights = {}
        pages_by_number = {p.page_number: p for p in pages}
        for page_num in neighbor_pages:
            if page_num in pages_by_number:
                boxes = find_word_boxes_for_terms(pages_by_number[page_num], search_terms)
                if boxes:
                    highlights[page_num] = boxes

        candidates.append(
            CandidateEvidenceSet(
                doc_id=pages[0].doc_id,
                page_numbers=sorted(neighbor_pages),
                score=avg_score * 0.9,  # Slight penalty for including neighbors
                rationale=[f"Top {top_k_neighbors} pages with neighbors"],
                evidence_snippets=[],
                highlights=highlights,
            )
        )

    # Candidate 3: Contiguous clusters
    clusters = build_contiguous_clusters(
        [(p.page_number, s) for p, s, r in page_scores if s > 0.0],
        max_gap=1,
    )
    search_terms = get_search_terms_for_line_item(line_item)
    pages_by_number = {p.page_number: p for p in pages}

    for cluster_pages in clusters[:3]:  # Top 3 clusters
        if cluster_pages:
            cluster_scores = [s for p, s, r in page_scores if p.page_number in cluster_pages]
            avg_score = sum(cluster_scores) / len(cluster_scores) if cluster_scores else 0.0

            # Find highlights for cluster pages
            highlights = {}
            for page_num in cluster_pages:
                if page_num in pages_by_number:
                    boxes = find_word_boxes_for_terms(pages_by_number[page_num], search_terms)
                    if boxes:
                        highlights[page_num] = boxes

            candidates.append(
                CandidateEvidenceSet(
                    doc_id=pages[0].doc_id,
                    page_numbers=sorted(cluster_pages),
                    score=avg_score,
                    rationale=[f"Contiguous cluster of {len(cluster_pages)} pages"],
                    evidence_snippets=[],
                    highlights=highlights,
                )
            )

    # Deduplicate candidates by page_numbers set
    seen = set()
    unique_candidates = []
    for cand in candidates:
        key = tuple(sorted(cand.page_numbers))
        if key not in seen:
            seen.add(key)
            unique_candidates.append(cand)

    # Sort by score and return top N
    unique_candidates.sort(key=lambda c: c.score, reverse=True)
    return unique_candidates[:max_candidates]


def build_contiguous_clusters(
    page_scores: List[Tuple[int, float]],
    max_gap: int = 1,
) -> List[List[int]]:
    """
    Build contiguous clusters of pages.

    Args:
        page_scores: List of (page_number, score) tuples
        max_gap: Maximum gap between pages to consider contiguous

    Returns:
        List of clusters (each cluster is a list of page numbers)
    """
    if not page_scores:
        return []

    # Sort by page number
    sorted_pages = sorted(page_scores, key=lambda x: x[0])

    clusters = []
    current_cluster = [sorted_pages[0][0]]

    for i in range(1, len(sorted_pages)):
        page_num = sorted_pages[i][0]
        prev_page = current_cluster[-1]

        if page_num - prev_page <= max_gap:
            current_cluster.append(page_num)
        else:
            # Start new cluster
            if len(current_cluster) >= 2:  # Only keep clusters with 2+ pages
                clusters.append(current_cluster)
            current_cluster = [page_num]

    # Add last cluster
    if len(current_cluster) >= 2:
        clusters.append(current_cluster)

    return clusters


def select_default_evidence(
    candidates: List[CandidateEvidenceSet],
) -> SelectedEvidence:
    """
    Select default evidence from candidates.

    Chooses the highest-ranked candidate.
    """
    if not candidates:
        return SelectedEvidence(
            doc_id=None,
            page_numbers=[],
            selection_source="auto",
        )

    best_candidate = candidates[0]
    return SelectedEvidence(
        doc_id=best_candidate.doc_id,
        page_numbers=best_candidate.page_numbers,
        selection_source="auto",
    )
