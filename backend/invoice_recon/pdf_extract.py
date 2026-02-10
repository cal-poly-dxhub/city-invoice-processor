"""PDF text extraction with fallback to Textract."""

import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import fitz  # PyMuPDF
from invoice_recon.config import Config
from invoice_recon.textract_text import extract_text_from_image_bytes, extract_text_and_words_from_image_bytes
from invoice_recon.bedrock_vision import detect_table_page
from invoice_recon.table_parser import TableStructure, TableCell

logger = logging.getLogger(__name__)


def compute_file_sha256(file_path: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()


def is_text_sufficient(text: str) -> bool:
    """
    Check if extracted text is sufficient.

    Text is insufficient if:
    - Empty or only whitespace
    - Length < TEXT_MIN_CHARS
    """
    text = text.strip()
    return len(text) >= Config.TEXT_MIN_CHARS


def extract_page_text_pymupdf(page) -> str:
    """Extract text from a PyMuPDF page object."""
    try:
        text = page.get_text("text")
        return text.strip()
    except Exception as e:
        logger.error(f"PyMuPDF text extraction failed: {e}")
        return ""


def extract_page_text_and_words_pymupdf(page) -> Tuple[str, List[Dict]]:
    """
    Extract text and word-level bounding boxes from a PyMuPDF page.

    Returns:
        Tuple of (text, word_boxes) where:
        - text: Extracted text (lines joined with newlines)
        - word_boxes: List of dicts with keys: text, left, top, width, height
          (normalized 0-1 coordinates, matching Textract format)
    """
    try:
        # Extract text
        text = page.get_text("text").strip()

        # Extract word-level geometry
        # get_text("words") returns list of (x0, y0, x1, y1, "word", block_no, line_no, word_no)
        words = page.get_text("words")

        # Get page dimensions for normalization
        # IMPORTANT: Use mediabox, not rect, because get_text("words") returns coordinates
        # in the original (pre-rotation) coordinate space, not the rotated display space
        mediabox = page.mediabox
        page_width = mediabox.width
        page_height = mediabox.height

        word_boxes = []
        for word_tuple in words:
            x0, y0, x1, y1, word_text, *_ = word_tuple

            # Skip empty words
            if not word_text or not word_text.strip():
                continue

            # Calculate normalized coordinates (0-1 scale like Textract)
            # PyMuPDF coordinates: (0,0) is top-left, which matches our needs
            left = x0 / page_width
            top = y0 / page_height
            width = (x1 - x0) / page_width
            height = (y1 - y0) / page_height

            word_boxes.append({
                "text": word_text,
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            })

        return (text, word_boxes)
    except Exception as e:
        logger.error(f"PyMuPDF text+words extraction failed: {e}")
        return ("", [])


def render_page_to_png_bytes(page, dpi: int = 200) -> bytes:
    """Render a PyMuPDF page to PNG bytes."""
    try:
        pixmap = page.get_pixmap(dpi=dpi)
        return pixmap.tobytes("png")
    except Exception as e:
        logger.error(f"Page rendering failed: {e}")
        return b""


def _is_pymupdf_table_sufficient(cell_data: List[List[str]]) -> bool:
    """
    Validate if PyMuPDF table extraction is sufficient.

    Criteria:
    - At least MIN_TABLE_ROWS rows
    - At least MIN_TABLE_CELLS total cells
    - At least MIN_TABLE_CELL_COVERAGE of cells have text

    Args:
        cell_data: 2D array of cell text from PyMuPDF

    Returns:
        True if table quality is sufficient, False otherwise
    """
    if not cell_data or len(cell_data) < Config.MIN_TABLE_ROWS:
        return False

    total_cells = sum(len(row) for row in cell_data)
    if total_cells < Config.MIN_TABLE_CELLS:
        return False

    non_empty_cells = sum(
        1 for row in cell_data
        for cell in row
        if cell and cell.strip()
    )

    coverage = non_empty_cells / total_cells if total_cells > 0 else 0
    if coverage < Config.MIN_TABLE_CELL_COVERAGE:
        return False

    return True


def _convert_pymupdf_table_to_structure(
    pymupdf_table,
    cell_data: List[List[str]],
    table_idx: int,
    page
) -> TableStructure:
    """
    Convert PyMuPDF Table to our TableStructure format.

    Challenges:
    - PyMuPDF uses PDF points, need to normalize to 0-1
    - No row_span/col_span, default to 1
    - Cell ordering may differ from Textract

    Args:
        pymupdf_table: PyMuPDF Table object
        cell_data: 2D array of cell text
        table_idx: Table index for unique ID
        page: PyMuPDF page object

    Returns:
        TableStructure with normalized coordinates
    """
    # Get page dimensions for normalization
    mediabox = page.mediabox
    page_width = mediabox.width
    page_height = mediabox.height

    # Get table bbox (in PDF points)
    table_bbox_points = pymupdf_table.bbox  # (x0, y0, x1, y1)

    # Normalize table bbox to 0-1
    table_bbox = {
        "left": table_bbox_points[0] / page_width,
        "top": table_bbox_points[1] / page_height,
        "width": (table_bbox_points[2] - table_bbox_points[0]) / page_width,
        "height": (table_bbox_points[3] - table_bbox_points[1]) / page_height,
    }

    # Convert cells
    cells = []
    cell_bboxes = pymupdf_table.cells  # List of cell bboxes in PDF points

    for row_idx, row in enumerate(cell_data, start=1):  # 1-based
        for col_idx, cell_text in enumerate(row, start=1):  # 1-based
            # Calculate cell index in flat list
            cell_flat_idx = (row_idx - 1) * len(row) + (col_idx - 1)

            if cell_flat_idx < len(cell_bboxes):
                cell_bbox_points = cell_bboxes[cell_flat_idx]  # (x0, y0, x1, y1)

                # Normalize cell bbox to 0-1
                cell_bbox = {
                    "left": cell_bbox_points[0] / page_width,
                    "top": cell_bbox_points[1] / page_height,
                    "width": (cell_bbox_points[2] - cell_bbox_points[0]) / page_width,
                    "height": (cell_bbox_points[3] - cell_bbox_points[1]) / page_height,
                }
            else:
                # Fallback: empty bbox if index out of range
                cell_bbox = {"left": 0, "top": 0, "width": 0, "height": 0}

            cells.append(TableCell(
                row_index=row_idx,
                col_index=col_idx,
                row_span=1,  # PyMuPDF doesn't detect merged cells
                col_span=1,
                text=cell_text.strip() if cell_text else "",
                bbox=cell_bbox,
                word_ids=[],  # PyMuPDF doesn't provide word IDs
            ))

    return TableStructure(
        table_id=f"pymupdf-table-{table_idx}",
        cells=cells,
        row_count=len(cell_data),
        col_count=max(len(row) for row in cell_data) if cell_data else 0,
        bbox=table_bbox,
    )


def extract_tables_pymupdf(page) -> List[TableStructure]:
    """
    Extract table structures using PyMuPDF's find_tables().

    Returns:
        List of TableStructure objects (empty if no tables found or quality insufficient)
    """
    try:
        # Find tables using PyMuPDF
        table_finder = page.find_tables(
            vertical_strategy=Config.PYMUPDF_TABLE_STRATEGY,
            horizontal_strategy=Config.PYMUPDF_TABLE_STRATEGY
        )

        if not table_finder.tables:
            logger.debug("PyMuPDF: No tables found")
            return []

        tables = []
        for idx, pymupdf_table in enumerate(table_finder.tables):
            # Extract 2D array of cell text
            cell_data = pymupdf_table.extract()  # Returns List[List[str]]

            # Validate table quality
            if not _is_pymupdf_table_sufficient(cell_data):
                logger.debug(
                    f"PyMuPDF table {idx}: Insufficient quality "
                    f"(rows={len(cell_data)}, cells={sum(len(r) for r in cell_data)}), skipping"
                )
                continue

            # Convert to TableStructure
            table_struct = _convert_pymupdf_table_to_structure(
                pymupdf_table,
                cell_data,
                idx,
                page
            )
            tables.append(table_struct)

        logger.info(f"PyMuPDF: Extracted {len(tables)} valid tables")
        return tables

    except Exception as e:
        logger.warning(f"PyMuPDF table extraction failed: {e}")
        return []


def transform_bbox_from_rotated(bbox: Dict[str, float], rotation: int) -> Dict[str, float]:
    """
    Transform a single bounding box from rotated space to original mediabox space.

    Args:
        bbox: Dict with keys: left, top, width, height (normalized 0-1)
        rotation: Page rotation in degrees (0, 90, 180, 270)

    Returns:
        Transformed bounding box
    """
    if rotation == 0:
        return bbox

    left, top, width, height = bbox['left'], bbox['top'], bbox['width'], bbox['height']

    if rotation == 90:
        # Inverse of (x,y) → (1-y-h, x)
        return {
            'left': top,
            'top': 1 - left - width,
            'width': height,
            'height': width
        }
    elif rotation == 180:
        # Inverse of (x,y) → (1-x-w, 1-y-h)
        return {
            'left': 1 - left - width,
            'top': 1 - top - height,
            'width': width,
            'height': height
        }
    elif rotation == 270:
        # Inverse of (x,y) → (y, 1-x-w)
        return {
            'left': 1 - top - height,
            'top': left,
            'width': height,
            'height': width
        }
    else:
        return bbox


def transform_coordinates_from_rotated(word_boxes: List[Dict], rotation: int) -> List[Dict]:
    """
    Transform word boxes from rotated space back to original (0°) mediabox space.

    When Textract processes a rotated PNG, it returns coordinates relative to the
    rotated image. This function transforms them back to match PyMuPDF's coordinate
    space (original mediabox).

    Args:
        word_boxes: List of word boxes with normalized (0-1) coordinates
        rotation: Page rotation in degrees (0, 90, 180, 270)

    Returns:
        Transformed word boxes in original coordinate space
    """
    if rotation == 0:
        return word_boxes

    transformed = []
    for box in word_boxes:
        new_bbox = transform_bbox_from_rotated(
            {'left': box['left'], 'top': box['top'], 'width': box['width'], 'height': box['height']},
            rotation
        )
        transformed.append({
            'text': box['text'],
            **new_bbox
        })

    return transformed


def transform_tables_from_rotated(tables: List[TableStructure], rotation: int) -> List[TableStructure]:
    """
    Transform table structures from rotated space to original mediabox space.

    Args:
        tables: List of TableStructure objects
        rotation: Page rotation in degrees (0, 90, 180, 270)

    Returns:
        Transformed table structures
    """
    if rotation == 0:
        return tables

    transformed_tables = []
    for table in tables:
        # Transform table bounding box
        new_table_bbox = transform_bbox_from_rotated(table.bbox, rotation)

        # Transform each cell
        new_cells = []
        for cell in table.cells:
            new_cell_bbox = transform_bbox_from_rotated(cell.bbox, rotation)
            new_cells.append(TableCell(
                row_index=cell.row_index,
                col_index=cell.col_index,
                row_span=cell.row_span,
                col_span=cell.col_span,
                text=cell.text,
                bbox=new_cell_bbox,
                word_ids=cell.word_ids
            ))

        transformed_tables.append(TableStructure(
            table_id=table.table_id,
            cells=new_cells,
            row_count=table.row_count,
            col_count=table.col_count,
            bbox=new_table_bbox
        ))

    return transformed_tables


def extract_page_with_fallback(
    page, page_number: int
) -> Tuple[str, str, List[Dict], Optional[List[TableStructure]]]:
    """
    Extract text, word boxes, and tables from a page with Textract fallback.

    Strategy:
    0. If TABLE_DETECTION_ENABLED, use Bedrock vision to detect table pages
       - If detected as table, try PyMuPDF table extraction first
       - If PyMuPDF succeeds, use it (free, fast)
       - Otherwise fall back to Textract (paid, slower)
    1. Try PyMuPDF first for text + geometry (fast, free)
    2. If text is sufficient, use PyMuPDF geometry
    3. If text insufficient, fall back to Textract (slow, paid)

    Args:
        page: PyMuPDF page object
        page_number: Page number (1-based)

    Returns:
        Tuple of (extracted_text, text_source, word_boxes, tables)
        - text_source is "pymupdf", "pymupdf_table", "textract", or "textract_table"
        - word_boxes is a list of dicts with keys: text, left, top, width, height
          (normalized 0-1 coordinates)
        - tables is a list of TableStructure objects
    """
    # Step 0: Check if table detection is enabled and detect tables
    if Config.TABLE_DETECTION_ENABLED and Config.TEXTRACT_MODE != "never":
        logger.debug(f"Page {page_number}: Running table detection")
        png_bytes = render_page_to_png_bytes(page)
        if png_bytes:
            is_table = detect_table_page(png_bytes)
            if is_table:
                logger.info(f"Page {page_number}: Detected as table, trying PyMuPDF first")

                # NEW: Try PyMuPDF table extraction first
                pymupdf_tables = extract_tables_pymupdf(page)

                if pymupdf_tables:
                    # PyMuPDF succeeded, use it
                    logger.info(
                        f"Page {page_number}: PyMuPDF extracted {len(pymupdf_tables)} tables, "
                        "using PyMuPDF (skipping Textract)"
                    )
                    pymupdf_text, pymupdf_word_boxes = extract_page_text_and_words_pymupdf(page)
                    return (pymupdf_text, "pymupdf_table", pymupdf_word_boxes, pymupdf_tables)
                else:
                    # PyMuPDF failed, fall back to Textract
                    logger.info(
                        f"Page {page_number}: PyMuPDF table extraction insufficient, "
                        "falling back to Textract"
                    )
                    textract_text, word_boxes, tables = extract_text_and_words_from_image_bytes(png_bytes)
                    # Transform coordinates back to original mediabox space
                    page_rotation = page.rotation
                    word_boxes = transform_coordinates_from_rotated(word_boxes, page_rotation)
                    tables = transform_tables_from_rotated(tables, page_rotation)
                    return (textract_text, "textract_table", word_boxes, tables)
        else:
            logger.warning(f"Page {page_number}: Failed to render for table detection")

    # Try PyMuPDF first (extract both text and geometry)
    pymupdf_text, pymupdf_word_boxes = extract_page_text_and_words_pymupdf(page)

    # Check mode
    if Config.TEXTRACT_MODE == "never":
        return (pymupdf_text, "pymupdf", pymupdf_word_boxes, [])

    if Config.TEXTRACT_MODE == "always":
        # Always use Textract
        logger.info(f"Page {page_number}: TEXTRACT_MODE=always, using Textract")
        png_bytes = render_page_to_png_bytes(page)
        if png_bytes:
            textract_text, word_boxes, tables = extract_text_and_words_from_image_bytes(png_bytes)
            # Transform coordinates back to original mediabox space
            page_rotation = page.rotation
            word_boxes = transform_coordinates_from_rotated(word_boxes, page_rotation)
            tables = transform_tables_from_rotated(tables, page_rotation)
            return (textract_text, "textract", word_boxes, tables)
        else:
            logger.warning(
                f"Page {page_number}: Failed to render for Textract, "
                "falling back to PyMuPDF"
            )
            return (pymupdf_text, "pymupdf", pymupdf_word_boxes, [])

    # Mode is "auto" - use Textract only if PyMuPDF text OR geometry is insufficient
    text_sufficient = is_text_sufficient(pymupdf_text)
    has_geometry = len(pymupdf_word_boxes) > 0

    if text_sufficient and has_geometry:
        logger.debug(
            f"Page {page_number}: PyMuPDF text and geometry sufficient "
            f"({len(pymupdf_text)} chars, {len(pymupdf_word_boxes)} words)"
        )
        return (pymupdf_text, "pymupdf", pymupdf_word_boxes, [])

    # PyMuPDF text or geometry insufficient, fall back to Textract
    if not text_sufficient:
        logger.info(
            f"Page {page_number}: PyMuPDF text insufficient "
            f"({len(pymupdf_text)} chars), falling back to Textract"
        )
    else:
        logger.info(
            f"Page {page_number}: PyMuPDF geometry missing "
            f"(0 word boxes), falling back to Textract"
        )
    png_bytes = render_page_to_png_bytes(page)
    if png_bytes:
        textract_text, word_boxes, tables = extract_text_and_words_from_image_bytes(png_bytes)
        # Transform coordinates back to original mediabox space
        page_rotation = page.rotation
        word_boxes = transform_coordinates_from_rotated(word_boxes, page_rotation)
        tables = transform_tables_from_rotated(tables, page_rotation)
        return (textract_text, "textract", word_boxes, tables)
    else:
        logger.warning(
            f"Page {page_number}: Failed to render for Textract, "
            "using PyMuPDF text anyway"
        )
        return (pymupdf_text, "pymupdf", pymupdf_word_boxes, [])


def extract_pdf_pages(
    pdf_path: Path,
) -> List[Tuple[int, str, str, List[Dict], Optional[List[TableStructure]]]]:
    """
    Extract text, word boxes, and tables from all pages of a PDF.

    Args:
        pdf_path: Path to PDF file

    Returns:
        List of tuples: (page_number, text, text_source, word_boxes, tables)
        - page_number is 1-based
        - word_boxes is a list of dicts with keys: text, left, top, width, height
        - tables is a list of TableStructure objects (empty for PyMuPDF pages)
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    results = []

    try:
        doc = fitz.open(pdf_path)

        for page_index in range(len(doc)):
            page_number = page_index + 1  # 1-based
            page = doc[page_index]

            text, text_source, word_boxes, tables = extract_page_with_fallback(page, page_number)

            results.append((page_number, text, text_source, word_boxes, tables))

        doc.close()

    except Exception as e:
        logger.error(f"Failed to process PDF {pdf_path}: {e}")
        raise

    return results


def get_pdf_page_count(pdf_path: Path) -> int:
    """Get the page count of a PDF."""
    try:
        doc = fitz.open(pdf_path)
        count = len(doc)
        doc.close()
        return count
    except Exception as e:
        logger.error(f"Failed to get page count for {pdf_path}: {e}")
        return 0
