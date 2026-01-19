"""Matching logic for line items to evidence pages."""

import logging
import re
from typing import Dict, List, Optional, Set, Tuple
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


def score_page_for_employee(
    line_item: LineItem,
    page: PageRecord,
) -> Tuple[float, List[str]]:
    """
    Score a page for an employee-based line item.

    Returns: (score, rationale)

    Scoring:
    - +0.70 if last name exact match
    - +0.20 if first name exact match OR first initial matches
    - +0.15 if token_sort_ratio(full_name) >= 90
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
    for person in people:
        p_full, p_first, p_last = normalize_person_name(person)

        # Last name exact match
        if li_last and p_last and li_last == p_last:
            score += 0.70
            rationale.append(f"Last name match: {li_last}")
            matched_person = True

        # First name exact match or initial match
        if li_first and p_first:
            if li_first == p_first:
                score += 0.20
                rationale.append(f"First name match: {li_first}")
            elif li_first[0] == p_first[0]:
                score += 0.20
                rationale.append(f"First initial match: {li_first[0]}")

        # Fuzzy full name match
        if li_full and p_full:
            ratio = fuzz.token_sort_ratio(li_full, p_full)
            if ratio >= 90:
                score += 0.15
                rationale.append(f"Full name fuzzy match: {ratio}%")

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

    # Determine if employee-based matching
    is_employee = is_employee_budget_item(line_item.budget_item)

    # Score all pages
    page_scores: List[Tuple[PageRecord, float, List[str]]] = []
    for page in pages:
        if is_employee and line_item.employee_last_name:
            score, rationale = score_page_for_employee(line_item, page)
        else:
            score, rationale = score_page_for_non_employee(line_item, page)

        page_scores.append((page, score, rationale))

    # Sort by score descending
    page_scores.sort(key=lambda x: x[1], reverse=True)

    # Build candidate sets
    candidates = []

    # Candidate 1: All strong matches (score >= 0.75)
    strong_matches = [p for p, s, r in page_scores if s >= 0.75]
    if strong_matches:
        total_score = sum(s for p, s, r in page_scores if s >= 0.75)
        rationale_combined = ["Strong matches (score >= 0.75)"]
        candidates.append(
            CandidateEvidenceSet(
                doc_id=pages[0].doc_id,
                page_numbers=[p.page_number for p in strong_matches],
                score=total_score / len(strong_matches),
                rationale=rationale_combined,
                evidence_snippets=[],
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
        candidates.append(
            CandidateEvidenceSet(
                doc_id=pages[0].doc_id,
                page_numbers=sorted(neighbor_pages),
                score=avg_score * 0.9,  # Slight penalty for including neighbors
                rationale=[f"Top {top_k_neighbors} pages with neighbors"],
                evidence_snippets=[],
            )
        )

    # Candidate 3: Contiguous clusters
    clusters = build_contiguous_clusters(
        [(p.page_number, s) for p, s, r in page_scores if s > 0.0],
        max_gap=1,
    )
    for cluster_pages in clusters[:3]:  # Top 3 clusters
        if cluster_pages:
            cluster_scores = [s for p, s, r in page_scores if p.page_number in cluster_pages]
            avg_score = sum(cluster_scores) / len(cluster_scores) if cluster_scores else 0.0
            candidates.append(
                CandidateEvidenceSet(
                    doc_id=pages[0].doc_id,
                    page_numbers=sorted(cluster_pages),
                    score=avg_score,
                    rationale=[f"Contiguous cluster of {len(cluster_pages)} pages"],
                    evidence_snippets=[],
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
