/**
 * Budget item classification utility.
 *
 * Mirrors the backend logic in backend/invoice_recon/budget_items.py
 * for matching PDF filenames to canonical budget item names.
 */

/** The 12 canonical budget items. */
export const BUDGET_ITEMS = [
  'Salary',
  'Fringe',
  'Contractual Service',
  'Equipment',
  'Insurance',
  'Travel and Conferences',
  'Space Rental/Occupancy Costs',
  'Telecommunications',
  'Utilities',
  'Supplies',
  'Other',
  'Indirect Costs',
];

/**
 * Convert text to a slug for matching.
 *
 * Rules:
 * - Convert to lowercase
 * - Replace non-alphanumeric characters with underscore
 * - Collapse multiple underscores to a single underscore
 * - Strip leading/trailing underscores
 *
 * @param {string} text
 * @returns {string}
 */
export function slugify(text) {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/_+/g, '_')
    .replace(/^_|_$/g, '');
}

/**
 * Build slug-to-budget-item mapping, sorted longest-slug-first
 * so prefix matching picks the most specific item.
 *
 * @returns {Array<[string, string]>} Array of [slug, canonicalName] pairs, longest first.
 */
function buildSlugMap() {
  const entries = BUDGET_ITEMS.map((item) => [slugify(item), item]);
  // Sort longest slug first so prefix matching is most specific
  entries.sort((a, b) => b[0].length - a[0].length);
  return entries;
}

const SLUG_MAP = buildSlugMap();

/**
 * Classify a File object to a canonical budget item name.
 *
 * Classification strategy:
 * 1. Only accept .pdf files (return null otherwise)
 * 2. Remove .pdf extension, slugify the stem
 * 3. Exact slug match against budget items
 * 4. Prefix match: slug starts with budgetSlug + '_'
 * 5. Slug map is sorted longest-first so prefix matching picks the most specific item
 *
 * @param {File} file - A File object (from drag-and-drop or file input)
 * @returns {string|null} The canonical budget item name, or null if unrecognized
 */
export function classifyFile(file) {
  const name = file.name;

  // Only accept .pdf files
  if (!name.toLowerCase().endsWith('.pdf')) {
    return null;
  }

  // Remove .pdf extension and slugify
  const stem = name.slice(0, -4);
  const slug = slugify(stem);

  // Try exact match, then prefix match (longest slug first)
  for (const [budgetSlug, budgetItem] of SLUG_MAP) {
    if (slug === budgetSlug) {
      return budgetItem;
    }
    if (slug.startsWith(budgetSlug + '_')) {
      return budgetItem;
    }
  }

  return null;
}
