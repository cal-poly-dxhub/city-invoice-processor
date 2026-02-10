"""Tests for PDF extraction with Textract fallback (two-phase approach)."""

import pytest
from unittest.mock import Mock, patch
from invoice_recon.pdf_extract import (
    is_text_sufficient,
    prepare_page_data,
    resolve_page_extraction,
)
from invoice_recon.config import Config


def test_is_text_sufficient():
    """Test text sufficiency check."""
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


@patch("invoice_recon.pdf_extract.render_page_to_png_bytes")
@patch("invoice_recon.pdf_extract.extract_page_text_and_words_pymupdf")
def test_prepare_auto_mode_sufficient(mock_pymupdf_words, mock_render):
    """Test auto mode with sufficient PyMuPDF text — no API needed."""
    original_mode = Config.TEXTRACT_MODE
    original_min_chars = Config.TEXT_MIN_CHARS
    original_table = Config.TABLE_DETECTION_ENABLED

    try:
        Config.TEXTRACT_MODE = "auto"
        Config.TEXT_MIN_CHARS = 40
        Config.TABLE_DETECTION_ENABLED = False

        sufficient_text = "a" * 100
        word_box = {"text": "word", "left": 0, "top": 0, "width": 0.1, "height": 0.02}
        mock_pymupdf_words.return_value = (sufficient_text, [word_box])

        page_mock = Mock()
        page_mock.rotation = 0
        result = prepare_page_data(page_mock, 1)

        assert result["needs_api"] is False
        assert result["pymupdf_text"] == sufficient_text
        assert result["text_sufficient"] is True

        mock_pymupdf_words.assert_called_once()
        mock_render.assert_not_called()

    finally:
        Config.TEXTRACT_MODE = original_mode
        Config.TEXT_MIN_CHARS = original_min_chars
        Config.TABLE_DETECTION_ENABLED = original_table


@patch("invoice_recon.pdf_extract.extract_text_and_words_from_image_bytes")
@patch("invoice_recon.pdf_extract.render_page_to_png_bytes")
@patch("invoice_recon.pdf_extract.extract_page_text_and_words_pymupdf")
def test_prepare_and_resolve_auto_mode_insufficient(
    mock_pymupdf_words, mock_render, mock_textract
):
    """Test auto mode with insufficient PyMuPDF text — Textract fallback."""
    original_mode = Config.TEXTRACT_MODE
    original_min_chars = Config.TEXT_MIN_CHARS
    original_table = Config.TABLE_DETECTION_ENABLED

    try:
        Config.TEXTRACT_MODE = "auto"
        Config.TEXT_MIN_CHARS = 40
        Config.TABLE_DETECTION_ENABLED = False

        mock_pymupdf_words.return_value = ("short", [])
        mock_render.return_value = b"fake_png_bytes"
        textract_word_box = {"text": "word", "left": 0, "top": 0, "width": 0.1, "height": 0.02}
        mock_textract.return_value = (
            "Textract extracted text is much longer",
            [textract_word_box],
            [],
        )

        page_mock = Mock()
        page_mock.rotation = 0
        page_data = prepare_page_data(page_mock, 1)

        assert page_data["needs_api"] is True
        assert page_data["png_bytes"] == b"fake_png_bytes"

        text, source, word_boxes, tables = resolve_page_extraction(page_data)

        assert text == "Textract extracted text is much longer"
        assert source == "textract"

        mock_pymupdf_words.assert_called_once()
        mock_render.assert_called_once()
        mock_textract.assert_called_once_with(b"fake_png_bytes")

    finally:
        Config.TEXTRACT_MODE = original_mode
        Config.TEXT_MIN_CHARS = original_min_chars
        Config.TABLE_DETECTION_ENABLED = original_table


@patch("invoice_recon.pdf_extract.render_page_to_png_bytes")
@patch("invoice_recon.pdf_extract.extract_page_text_and_words_pymupdf")
def test_prepare_never_mode(mock_pymupdf_words, mock_render):
    """Test never mode — no API calls even if insufficient."""
    original_mode = Config.TEXTRACT_MODE
    original_table = Config.TABLE_DETECTION_ENABLED

    try:
        Config.TEXTRACT_MODE = "never"
        Config.TABLE_DETECTION_ENABLED = False

        mock_pymupdf_words.return_value = ("short", [])

        page_mock = Mock()
        page_mock.rotation = 0
        result = prepare_page_data(page_mock, 1)

        assert result["needs_api"] is False
        assert result["pymupdf_text"] == "short"

        mock_pymupdf_words.assert_called_once()
        mock_render.assert_not_called()

    finally:
        Config.TEXTRACT_MODE = original_mode
        Config.TABLE_DETECTION_ENABLED = original_table


@patch("invoice_recon.pdf_extract.extract_text_and_words_from_image_bytes")
@patch("invoice_recon.pdf_extract.render_page_to_png_bytes")
@patch("invoice_recon.pdf_extract.extract_page_text_and_words_pymupdf")
def test_prepare_and_resolve_always_mode(
    mock_pymupdf_words, mock_render, mock_textract
):
    """Test always mode — always uses Textract."""
    original_mode = Config.TEXTRACT_MODE
    original_table = Config.TABLE_DETECTION_ENABLED

    try:
        Config.TEXTRACT_MODE = "always"
        Config.TABLE_DETECTION_ENABLED = False

        word_box = {"text": "word", "left": 0, "top": 0, "width": 0.1, "height": 0.02}
        mock_pymupdf_words.return_value = ("PyMuPDF text", [word_box])
        mock_render.return_value = b"fake_png_bytes"
        mock_textract.return_value = (
            "Textract text",
            [word_box],
            [],
        )

        page_mock = Mock()
        page_mock.rotation = 0
        page_data = prepare_page_data(page_mock, 1)

        assert page_data["needs_api"] is True

        text, source, word_boxes, tables = resolve_page_extraction(page_data)

        assert text == "Textract text"
        assert source == "textract"

        mock_render.assert_called_once()
        mock_textract.assert_called_once_with(b"fake_png_bytes")

    finally:
        Config.TEXTRACT_MODE = original_mode
        Config.TABLE_DETECTION_ENABLED = original_table
