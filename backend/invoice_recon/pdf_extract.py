"""PDF text extraction with fallback to Textract."""

import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import fitz  # PyMuPDF
from invoice_recon.config import Config
from invoice_recon.textract_text import extract_text_from_image_bytes, extract_text_and_words_from_image_bytes
from invoice_recon.bedrock_vision import detect_table_page
from invoice_recon.table_parser import TableStructure

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


def extract_page_with_fallback(
    page, page_number: int
) -> Tuple[str, str, List[Dict], Optional[List[TableStructure]]]:
    """
    Extract text, word boxes, and tables from a page with Textract fallback.

    Strategy:
    0. If TABLE_DETECTION_ENABLED, use Bedrock vision to detect table pages
       - If detected as table, skip PyMuPDF and use Textract directly
    1. Try PyMuPDF first for text + geometry (fast, free)
    2. If text is sufficient, use PyMuPDF geometry
    3. If text insufficient, fall back to Textract (slow, paid)

    Args:
        page: PyMuPDF page object
        page_number: Page number (1-based)

    Returns:
        Tuple of (extracted_text, text_source, word_boxes, tables)
        - text_source is "pymupdf" or "textract" or "textract_table"
        - word_boxes is a list of dicts with keys: text, left, top, width, height
          (normalized 0-1 coordinates)
        - tables is a list of TableStructure objects (empty for PyMuPDF)
    """
    # Step 0: Check if table detection is enabled and detect tables
    if Config.TABLE_DETECTION_ENABLED and Config.TEXTRACT_MODE != "never":
        logger.debug(f"Page {page_number}: Running table detection")
        png_bytes = render_page_to_png_bytes(page)
        if png_bytes:
            is_table = detect_table_page(png_bytes)
            if is_table:
                logger.info(
                    f"Page {page_number}: Detected as table page, using Textract directly"
                )
                textract_text, word_boxes, tables = extract_text_and_words_from_image_bytes(png_bytes)
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
