"""AWS Textract text extraction."""

import logging
from typing import Dict, List, Optional, Tuple
import boto3
from botocore.exceptions import ClientError
from invoice_recon.config import Config
from invoice_recon.table_parser import parse_textract_tables, TableStructure

logger = logging.getLogger(__name__)


def create_textract_client():
    """Create a Textract client with proper configuration."""
    # Always pass profile_name to override AWS_PROFILE env var
    # Use Config.AWS_PROFILE if set, otherwise None (uses default credentials)
    profile_name = Config.AWS_PROFILE if Config.AWS_PROFILE else None

    session = boto3.Session(
        region_name=Config.AWS_REGION,
        profile_name=profile_name
    )
    return session.client("textract")


def extract_text_from_image_bytes(image_bytes: bytes) -> str:
    """
    Extract text from image bytes using AWS Textract.

    Args:
        image_bytes: PNG image bytes

    Returns:
        Extracted text (lines joined with newlines)
    """
    text, _, _ = extract_text_and_words_from_image_bytes(image_bytes)
    return text


def extract_text_and_words_from_image_bytes(image_bytes: bytes) -> Tuple[str, List[Dict], List[TableStructure]]:
    """
    Extract text, word-level bounding boxes, and table structures from image bytes using AWS Textract.

    Args:
        image_bytes: PNG image bytes

    Returns:
        Tuple of (text, word_boxes, tables) where:
        - text: Extracted text (lines joined with newlines)
        - word_boxes: List of dicts with keys: text, left, top, width, height
        - tables: List of TableStructure objects
    """
    client = create_textract_client()

    try:
        response = client.analyze_document(
            Document={"Bytes": image_bytes},
            FeatureTypes=["TABLES", "FORMS"],
        )
    except ClientError as e:
        logger.error(f"Textract API error: {e}")
        return ("", [], [])

    blocks = response.get("Blocks", [])

    # Extract LINE blocks for text
    lines = []
    for block in blocks:
        if block.get("BlockType") == "LINE":
            text = block.get("Text", "").strip()
            if text:
                lines.append(text)

            # Stop after max lines to limit prompt size
            if len(lines) >= Config.TEXTRACT_MAX_LINES:
                logger.info(
                    f"Reached TEXTRACT_MAX_LINES ({Config.TEXTRACT_MAX_LINES}), "
                    "stopping extraction"
                )
                break

    # Extract WORD blocks with bounding boxes
    word_boxes = []
    for block in blocks:
        if block.get("BlockType") == "WORD":
            text = block.get("Text", "").strip()
            if not text:
                continue

            geometry = block.get("Geometry", {})
            bbox = geometry.get("BoundingBox", {})

            # Textract bounding boxes are normalized (0-1)
            word_boxes.append({
                "text": text,
                "left": bbox.get("Left", 0),
                "top": bbox.get("Top", 0),
                "width": bbox.get("Width", 0),
                "height": bbox.get("Height", 0),
            })

    # Parse table structures
    tables = parse_textract_tables(blocks)

    return ("\n".join(lines), word_boxes, tables)
