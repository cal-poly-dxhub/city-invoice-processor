"""Tests for PDF extraction with Textract fallback."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from invoice_recon.pdf_extract import (
    is_text_sufficient,
    extract_page_with_fallback,
)
from invoice_recon.config import Config


def test_is_text_sufficient():
    """Test text sufficiency check."""
    # Save original value
    original_min_chars = Config.TEXT_MIN_CHARS

    try:
        Config.TEXT_MIN_CHARS = 40

        # Sufficient text
        assert is_text_sufficient("a" * 40) is True
        assert is_text_sufficient("a" * 100) is True

        # Insufficient text
        assert is_text_sufficient("") is False
        assert is_text_sufficient("   ") is False
        assert is_text_sufficient("short") is False
        assert is_text_sufficient("a" * 39) is False

    finally:
        Config.TEXT_MIN_CHARS = original_min_chars


@patch("invoice_recon.pdf_extract.extract_text_from_image_bytes")
@patch("invoice_recon.pdf_extract.render_page_to_png_bytes")
@patch("invoice_recon.pdf_extract.extract_page_text_pymupdf")
def test_extract_page_with_fallback_auto_mode_sufficient(
    mock_pymupdf, mock_render, mock_textract
):
    """Test auto mode with sufficient PyMuPDF text (no Textract fallback)."""
    # Save original config
    original_mode = Config.TEXTRACT_MODE
    original_min_chars = Config.TEXT_MIN_CHARS

    try:
        Config.TEXTRACT_MODE = "auto"
        Config.TEXT_MIN_CHARS = 40

        # Mock PyMuPDF to return sufficient text
        mock_pymupdf.return_value = "a" * 100

        page_mock = Mock()
        text, source = extract_page_with_fallback(page_mock, 1)

        assert text == "a" * 100
        assert source == "pymupdf"

        # PyMuPDF should be called
        mock_pymupdf.assert_called_once()

        # Textract should NOT be called
        mock_render.assert_not_called()
        mock_textract.assert_not_called()

    finally:
        Config.TEXTRACT_MODE = original_mode
        Config.TEXT_MIN_CHARS = original_min_chars


@patch("invoice_recon.pdf_extract.extract_text_from_image_bytes")
@patch("invoice_recon.pdf_extract.render_page_to_png_bytes")
@patch("invoice_recon.pdf_extract.extract_page_text_pymupdf")
def test_extract_page_with_fallback_auto_mode_insufficient(
    mock_pymupdf, mock_render, mock_textract
):
    """Test auto mode with insufficient PyMuPDF text (Textract fallback)."""
    # Save original config
    original_mode = Config.TEXTRACT_MODE
    original_min_chars = Config.TEXT_MIN_CHARS

    try:
        Config.TEXTRACT_MODE = "auto"
        Config.TEXT_MIN_CHARS = 40

        # Mock PyMuPDF to return insufficient text
        mock_pymupdf.return_value = "short"

        # Mock render and Textract
        mock_render.return_value = b"fake_png_bytes"
        mock_textract.return_value = "Textract extracted text is much longer"

        page_mock = Mock()
        text, source = extract_page_with_fallback(page_mock, 1)

        assert text == "Textract extracted text is much longer"
        assert source == "textract"

        # PyMuPDF should be called first
        mock_pymupdf.assert_called_once()

        # Textract should be called
        mock_render.assert_called_once()
        mock_textract.assert_called_once_with(b"fake_png_bytes")

    finally:
        Config.TEXTRACT_MODE = original_mode
        Config.TEXT_MIN_CHARS = original_min_chars


@patch("invoice_recon.pdf_extract.extract_text_from_image_bytes")
@patch("invoice_recon.pdf_extract.render_page_to_png_bytes")
@patch("invoice_recon.pdf_extract.extract_page_text_pymupdf")
def test_extract_page_with_fallback_never_mode(
    mock_pymupdf, mock_render, mock_textract
):
    """Test never mode (no Textract even if insufficient)."""
    # Save original config
    original_mode = Config.TEXTRACT_MODE

    try:
        Config.TEXTRACT_MODE = "never"

        # Mock PyMuPDF to return insufficient text
        mock_pymupdf.return_value = "short"

        page_mock = Mock()
        text, source = extract_page_with_fallback(page_mock, 1)

        assert text == "short"
        assert source == "pymupdf"

        # PyMuPDF should be called
        mock_pymupdf.assert_called_once()

        # Textract should NOT be called
        mock_render.assert_not_called()
        mock_textract.assert_not_called()

    finally:
        Config.TEXTRACT_MODE = original_mode


@patch("invoice_recon.pdf_extract.extract_text_from_image_bytes")
@patch("invoice_recon.pdf_extract.render_page_to_png_bytes")
@patch("invoice_recon.pdf_extract.extract_page_text_pymupdf")
def test_extract_page_with_fallback_always_mode(
    mock_pymupdf, mock_render, mock_textract
):
    """Test always mode (always use Textract)."""
    # Save original config
    original_mode = Config.TEXTRACT_MODE

    try:
        Config.TEXTRACT_MODE = "always"

        # Mock PyMuPDF (might be called but ignored)
        mock_pymupdf.return_value = "PyMuPDF text"

        # Mock render and Textract
        mock_render.return_value = b"fake_png_bytes"
        mock_textract.return_value = "Textract text"

        page_mock = Mock()
        text, source = extract_page_with_fallback(page_mock, 1)

        assert text == "Textract text"
        assert source == "textract"

        # Textract should be called
        mock_render.assert_called_once()
        mock_textract.assert_called_once_with(b"fake_png_bytes")

    finally:
        Config.TEXTRACT_MODE = original_mode
