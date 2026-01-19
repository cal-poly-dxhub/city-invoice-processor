"""PDF text extraction with fallback to Textract."""

import hashlib
import logging
from pathlib import Path
from typing import List, Tuple
import fitz  # PyMuPDF
from invoice_recon.config import Config
from invoice_recon.textract_text import extract_text_from_image_bytes

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
) -> Tuple[str, str]:
    """
    Extract text from a page with Textract fallback.

    Args:
        page: PyMuPDF page object
        page_number: Page number (1-based)

    Returns:
        Tuple of (extracted_text, text_source)
        text_source is "pymupdf" or "textract"
    """
    # Try PyMuPDF first
    pymupdf_text = extract_page_text_pymupdf(page)

    # Check mode
    if Config.TEXTRACT_MODE == "never":
        return (pymupdf_text, "pymupdf")

    if Config.TEXTRACT_MODE == "always":
        # Always use Textract
        logger.info(f"Page {page_number}: TEXTRACT_MODE=always, using Textract")
        png_bytes = render_page_to_png_bytes(page)
        if png_bytes:
            textract_text = extract_text_from_image_bytes(png_bytes)
            return (textract_text, "textract")
        else:
            logger.warning(
                f"Page {page_number}: Failed to render for Textract, "
                "falling back to PyMuPDF"
            )
            return (pymupdf_text, "pymupdf")

    # Mode is "auto" - use Textract only if PyMuPDF text is insufficient
    if is_text_sufficient(pymupdf_text):
        logger.debug(
            f"Page {page_number}: PyMuPDF text sufficient "
            f"({len(pymupdf_text)} chars)"
        )
        return (pymupdf_text, "pymupdf")

    # PyMuPDF text insufficient, fall back to Textract
    logger.info(
        f"Page {page_number}: PyMuPDF text insufficient "
        f"({len(pymupdf_text)} chars), falling back to Textract"
    )
    png_bytes = render_page_to_png_bytes(page)
    if png_bytes:
        textract_text = extract_text_from_image_bytes(png_bytes)
        return (textract_text, "textract")
    else:
        logger.warning(
            f"Page {page_number}: Failed to render for Textract, "
            "using PyMuPDF text anyway"
        )
        return (pymupdf_text, "pymupdf")


def extract_pdf_pages(
    pdf_path: Path,
) -> List[Tuple[int, str, str]]:
    """
    Extract text from all pages of a PDF.

    Args:
        pdf_path: Path to PDF file

    Returns:
        List of tuples: (page_number, text, text_source)
        page_number is 1-based
    """
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    results = []

    try:
        doc = fitz.open(pdf_path)

        for page_index in range(len(doc)):
            page_number = page_index + 1  # 1-based
            page = doc[page_index]

            text, text_source = extract_page_with_fallback(page, page_number)

            results.append((page_number, text, text_source))

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
