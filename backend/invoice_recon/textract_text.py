"""AWS Textract text extraction."""

import logging
from typing import Optional
import boto3
from botocore.exceptions import ClientError
from invoice_recon.config import Config

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
    client = create_textract_client()

    try:
        response = client.analyze_document(
            Document={"Bytes": image_bytes},
            FeatureTypes=["TABLES", "FORMS"],
        )
    except ClientError as e:
        logger.error(f"Textract API error: {e}")
        return ""

    # Extract LINE blocks
    lines = []
    for block in response.get("Blocks", []):
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

    return "\n".join(lines)
