/**
 * Helper functions for managing manual page membership edits per group.
 * These functions compute effective pages and highlights based on user edits,
 * without mutating the original DocumentAnalysis structure.
 */

import type { Group, HighlightRect } from '../types/DocumentAnalysis';

/**
 * Per-group page edit state
 */
export type GroupPageEdits = {
  addedPages: number[];   // Pages manually added by user
  removedPages: number[]; // Pages manually removed by user
};

/**
 * State for all page edits across all groups
 */
export type PageEditsState = {
  [groupId: string]: GroupPageEdits;
};

/**
 * Get the original pages for a group based on its occurrences.
 * This is the "model's view" of which pages belong to this group.
 *
 * @param group - The group to get original pages for
 * @returns Sorted, unique array of page numbers where this group has occurrences
 */
export function getOriginalPagesForGroup(group: Group): number[] {
  const pageSet = new Set<number>();

  for (const occurrence of group.occurrences) {
    pageSet.add(occurrence.pageNumber);
  }

  return Array.from(pageSet).sort((a, b) => a - b);
}

/**
 * Get the effective pages for a group, taking into account user edits.
 * effectivePages = (originalPages ∪ addedPages) - removedPages
 *
 * @param group - The group to compute effective pages for
 * @param edits - User edits for this group (optional)
 * @param pageCount - Total page count for validation (optional)
 * @returns Sorted array of page numbers that effectively belong to this group
 */
export function getEffectivePagesForGroup(
  group: Group,
  edits: GroupPageEdits | undefined,
  pageCount: number | null
): number[] {
  // Start with original pages from occurrences
  const originalPages = getOriginalPagesForGroup(group);

  // If no edits, just return original pages
  if (!edits) {
    return originalPages;
  }

  const addedPages = edits.addedPages ?? [];
  const removedPages = edits.removedPages ?? [];

  // Compute union of original and added pages
  const candidateSet = new Set<number>([...originalPages, ...addedPages]);

  // Remove any pages in removedPages
  for (const page of removedPages) {
    candidateSet.delete(page);
  }

  // Convert to sorted array
  let effectivePages = Array.from(candidateSet).sort((a, b) => a - b);

  // Optionally clamp to valid page range if pageCount is known
  if (pageCount !== null && pageCount > 0) {
    effectivePages = effectivePages.filter(
      page => page >= 1 && page <= pageCount
    );
  }

  return effectivePages;
}

/**
 * Build a map of page numbers to highlight rectangles for the given group.
 * Only pages in effectivePages will be included in the result.
 * Pages that were manually added but have no occurrences will have empty arrays.
 *
 * @param group - The group to get highlights for
 * @param effectivePages - The effective pages for this group (after edits)
 * @returns Map from page number to array of highlight rectangles
 */
export function getHighlightsByPage(
  group: Group,
  effectivePages: number[]
): Record<number, HighlightRect[]> {
  const highlightsByPage: Record<number, HighlightRect[]> = {};

  // Initialize all effective pages with empty arrays
  for (const page of effectivePages) {
    highlightsByPage[page] = [];
  }

  // Populate highlights from occurrences
  for (const occurrence of group.occurrences) {
    const page = occurrence.pageNumber;

    // Only include highlights for pages in effectivePages
    if (effectivePages.includes(page)) {
      // Add all coordinate rectangles for this occurrence
      highlightsByPage[page].push(...occurrence.coords);
    }
  }

  return highlightsByPage;
}

/**
 * Check if a page is originally included in a group (from model output).
 *
 * @param group - The group to check
 * @param pageNumber - The page number to check
 * @returns True if the page has occurrences in the original group data
 */
export function isOriginalPage(group: Group, pageNumber: number): boolean {
  return group.occurrences.some(occ => occ.pageNumber === pageNumber);
}

/**
 * Get a human-readable status for a page's membership in a group.
 *
 * @param group - The group to check
 * @param pageNumber - The page number to check
 * @param edits - User edits for this group (optional)
 * @returns Status string: "model", "added", "removed", or null if not in group
 */
export function getPageStatus(
  group: Group,
  pageNumber: number,
  edits: GroupPageEdits | undefined
): 'model' | 'added' | 'removed' | null {
  const isOriginal = isOriginalPage(group, pageNumber);
  const isAdded = edits?.addedPages?.includes(pageNumber) ?? false;
  const isRemoved = edits?.removedPages?.includes(pageNumber) ?? false;

  if (isOriginal && isRemoved) {
    return 'removed';
  }

  if (isAdded) {
    return 'added';
  }

  if (isOriginal) {
    return 'model';
  }

  return null;
}

/**
 * Get all pages that don't currently belong to any group.
 * These are "orphaned" or "unassigned" pages that exist in the document
 * but aren't in any group's effective pages.
 *
 * @param groups - All groups from DocumentAnalysis
 * @param pageEdits - All page edits
 * @param pageCount - Total number of pages in document
 * @returns Sorted array of unassigned page numbers
 */
export function getUnassignedPages(
  groups: Group[],
  pageEdits: PageEditsState,
  pageCount: number | null
): number[] {
  if (pageCount === null || pageCount <= 0) {
    return [];
  }

  // Build set of all pages that belong to at least one group
  const assignedPages = new Set<number>();

  for (const group of groups) {
    const groupEdits = pageEdits[group.groupId];
    const effectivePages = getEffectivePagesForGroup(group, groupEdits, pageCount);

    for (const page of effectivePages) {
      assignedPages.add(page);
    }
  }

  // Find pages that aren't assigned to any group
  const unassignedPages: number[] = [];
  for (let page = 1; page <= pageCount; page++) {
    if (!assignedPages.has(page)) {
      unassignedPages.push(page);
    }
  }

  return unassignedPages;
}
