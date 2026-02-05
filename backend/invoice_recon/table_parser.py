"""Textract table structure parsing and budget item identification."""

import logging
import re
from typing import Dict, List, Optional, Set
from pydantic import BaseModel
from invoice_recon.budget_items import BUDGET_ITEMS

logger = logging.getLogger(__name__)


class TableCell(BaseModel):
    """Represents a single table cell."""

    row_index: int  # 1-based
    col_index: int  # 1-based
    row_span: int = 1
    col_span: int = 1
    text: str
    bbox: Dict[str, float]  # {left, top, width, height}, normalized 0-1
    word_ids: List[str] = []  # Block IDs of child WORD blocks


class TableStructure(BaseModel):
    """Represents a parsed table from Textract."""

    table_id: str
    cells: List[TableCell]
    row_count: int
    col_count: int
    bbox: Dict[str, float]  # Table bounding box


def parse_textract_tables(blocks: List[Dict]) -> List[TableStructure]:
    """
    Parse Textract response to extract table structures.

    Args:
        blocks: List of block dictionaries from Textract response

    Returns:
        List of TableStructure objects
    """
    # Build block ID lookup
    blocks_by_id = {block.get("Id"): block for block in blocks}

    # Find all TABLE blocks
    table_blocks = [b for b in blocks if b.get("BlockType") == "TABLE"]

    if not table_blocks:
        logger.debug("No TABLE blocks found in Textract response")
        return []

    tables = []

    for table_block in table_blocks:
        table_id = table_block.get("Id")

        # Get table bounding box
        geometry = table_block.get("Geometry", {})
        table_bbox = geometry.get("BoundingBox", {})
        table_bbox_dict = {
            "left": table_bbox.get("Left", 0),
            "top": table_bbox.get("Top", 0),
            "width": table_bbox.get("Width", 0),
            "height": table_bbox.get("Height", 0),
        }

        # Find child CELL blocks
        relationships = table_block.get("Relationships", [])
        cell_ids = []
        for rel in relationships:
            if rel.get("Type") == "CHILD":
                cell_ids.extend(rel.get("Ids", []))

        cells = []
        max_row = 0
        max_col = 0

        for cell_id in cell_ids:
            cell_block = blocks_by_id.get(cell_id)
            if not cell_block or cell_block.get("BlockType") != "CELL":
                continue

            # Extract cell properties
            row_index = cell_block.get("RowIndex", 0)
            col_index = cell_block.get("ColumnIndex", 0)
            row_span = cell_block.get("RowSpan", 1)
            col_span = cell_block.get("ColumnSpan", 1)

            # Track table dimensions
            max_row = max(max_row, row_index + row_span - 1)
            max_col = max(max_col, col_index + col_span - 1)

            # Get cell bounding box
            cell_geometry = cell_block.get("Geometry", {})
            cell_bbox = cell_geometry.get("BoundingBox", {})
            cell_bbox_dict = {
                "left": cell_bbox.get("Left", 0),
                "top": cell_bbox.get("Top", 0),
                "width": cell_bbox.get("Width", 0),
                "height": cell_bbox.get("Height", 0),
            }

            # Extract cell text from child WORD blocks
            word_ids = []
            cell_text_parts = []
            cell_relationships = cell_block.get("Relationships", [])

            for rel in cell_relationships:
                if rel.get("Type") == "CHILD":
                    word_ids.extend(rel.get("Ids", []))

            for word_id in word_ids:
                word_block = blocks_by_id.get(word_id)
                if word_block and word_block.get("BlockType") == "WORD":
                    word_text = word_block.get("Text", "")
                    if word_text:
                        cell_text_parts.append(word_text)

            cell_text = " ".join(cell_text_parts)

            cells.append(TableCell(
                row_index=row_index,
                col_index=col_index,
                row_span=row_span,
                col_span=col_span,
                text=cell_text,
                bbox=cell_bbox_dict,
                word_ids=word_ids,
            ))

        table = TableStructure(
            table_id=table_id,
            cells=cells,
            row_count=max_row,
            col_count=max_col,
            bbox=table_bbox_dict,
        )

        tables.append(table)
        logger.info(
            f"Parsed table {table_id}: {table.row_count} rows, "
            f"{table.col_count} cols, {len(cells)} cells"
        )

    return tables


def normalize_text_for_matching(text: str) -> str:
    """
    Normalize text for fuzzy budget item matching.

    Args:
        text: Raw text

    Returns:
        Normalized text (lowercase, alphanumeric only, spaces preserved)
    """
    # Lowercase
    text = text.lower()
    # Remove non-alphanumeric except spaces
    text = re.sub(r'[^a-z0-9\s]', '', text)
    # Collapse multiple spaces
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def fuzzy_match_budget_item(row_text: str, canonical_items: List[str]) -> Optional[str]:
    """
    Fuzzy match row text against canonical budget items.

    Args:
        row_text: Concatenated text from table row
        canonical_items: List of canonical budget item names

    Returns:
        Matched canonical budget item name or None
    """
    normalized_row = normalize_text_for_matching(row_text)

    # Try exact matches first
    for item in canonical_items:
        normalized_item = normalize_text_for_matching(item)
        if normalized_item == normalized_row:
            return item

    # Try substring matches
    for item in canonical_items:
        normalized_item = normalize_text_for_matching(item)

        # Check if row contains the budget item
        if normalized_item in normalized_row:
            return item

        # Check if budget item contains significant part of row
        # (for cases like "Sal" matching "Salary")
        if len(normalized_row) >= 3 and normalized_row in normalized_item:
            return item

    # Try word-based matching (any significant word matches)
    row_words = set(normalized_row.split())
    for item in canonical_items:
        normalized_item = normalize_text_for_matching(item)
        item_words = set(normalized_item.split())

        # If any significant word (4+ chars) matches, consider it
        common_words = row_words & item_words
        significant_common = [w for w in common_words if len(w) >= 4]
        if significant_common:
            return item

    return None


def identify_budget_items_in_table(
    table: TableStructure,
    canonical_budget_items: List[str]
) -> Dict[int, Optional[str]]:
    """
    Identify which budget item each table row represents.

    Args:
        table: Parsed table structure
        canonical_budget_items: List of canonical budget item names

    Returns:
        Dict mapping row_index -> budget_item (or None if no match)
    """
    # Group cells by row
    rows: Dict[int, List[TableCell]] = {}
    for cell in table.cells:
        row_idx = cell.row_index
        if row_idx not in rows:
            rows[row_idx] = []
        rows[row_idx].append(cell)

    # Identify budget item for each row
    row_budget_items: Dict[int, Optional[str]] = {}

    for row_idx in sorted(rows.keys()):
        row_cells = rows[row_idx]

        # Concatenate all cell text in the row
        row_text = " ".join(cell.text for cell in row_cells)

        # Fuzzy match against canonical budget items
        matched_item = fuzzy_match_budget_item(row_text, canonical_budget_items)

        row_budget_items[row_idx] = matched_item

        if matched_item:
            logger.debug(f"Row {row_idx}: '{row_text}' -> {matched_item}")
        else:
            logger.debug(f"Row {row_idx}: '{row_text}' -> No match")

    return row_budget_items


def get_row_cells(table: TableStructure, row: int) -> List[TableCell]:
    """
    Get all cells in a specific row.

    Args:
        table: Table structure
        row: Row index (1-based)

    Returns:
        List of cells in the row, sorted by column index
    """
    row_cells = [cell for cell in table.cells if cell.row_index == row]
    return sorted(row_cells, key=lambda c: c.col_index)


def get_cell_at_position(
    table: TableStructure,
    row: int,
    col: int
) -> Optional[TableCell]:
    """
    Get cell at specific row/column coordinates.

    Handles merged cells (row_span/col_span).

    Args:
        table: Table structure
        row: Row index (1-based)
        col: Column index (1-based)

    Returns:
        TableCell if found, None otherwise
    """
    for cell in table.cells:
        # Check if this cell occupies the requested position
        row_start = cell.row_index
        row_end = row_start + cell.row_span
        col_start = cell.col_index
        col_end = col_start + cell.col_span

        if row_start <= row < row_end and col_start <= col < col_end:
            return cell

    return None
