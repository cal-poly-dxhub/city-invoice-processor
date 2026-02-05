"""Bedrock vision-based page analysis."""

import base64
import json
import logging
from typing import Dict, Any
import boto3
from botocore.exceptions import ClientError
from invoice_recon.config import Config

logger = logging.getLogger(__name__)


def create_bedrock_client():
    """Create a Bedrock Runtime client with proper configuration."""
    profile_name = Config.AWS_PROFILE if Config.AWS_PROFILE else None
    session = boto3.Session(
        region_name=Config.AWS_REGION,
        profile_name=profile_name
    )
    return session.client("bedrock-runtime")


def detect_table_page(image_bytes: bytes) -> bool:
    """
    Use Bedrock vision to detect if a page is primarily a table.

    Args:
        image_bytes: PNG image bytes of the page

    Returns:
        True if the page contains primarily tabular data, False otherwise
    """
    if not Config.TABLE_DETECTION_ENABLED:
        return False

    client = create_bedrock_client()

    # Encode image to base64
    image_base64 = base64.b64encode(image_bytes).decode("utf-8")

    # Construct vision prompt
    system_prompt = """You are analyzing document pages to detect if they contain primarily tabular data.
A page should be classified as a "table page" if:
- It contains structured rows and columns of data (like a spreadsheet)
- Most of the content is organized in a grid/table format
- It has headers and multiple data rows

A page should NOT be classified as a "table page" if:
- It's mostly prose text with occasional small tables
- It's a form with labeled fields (not tabular data)
- It's an invoice/receipt with line items (these are usually better handled as regular text)

Respond with a JSON object containing:
{
  "is_table": true/false,
  "confidence": "high"/"medium"/"low",
  "reasoning": "brief explanation"
}"""

    user_message = {
        "role": "user",
        "content": [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": image_base64
                }
            },
            {
                "type": "text",
                "text": "Analyze this page and determine if it primarily contains tabular data."
            }
        ]
    }

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 500,
        "temperature": 0.0,
        "system": system_prompt,
        "messages": [user_message]
    }

    try:
        response = client.invoke_model(
            modelId=Config.BEDROCK_MODEL_ID,
            body=json.dumps(request_body)
        )

        response_body = json.loads(response["body"].read())
        content = response_body.get("content", [])

        if not content:
            logger.warning("Empty response from Bedrock vision")
            return False

        text_response = content[0].get("text", "")

        # Parse JSON response
        try:
            result = json.loads(text_response)
            is_table = result.get("is_table", False)
            confidence = result.get("confidence", "unknown")
            reasoning = result.get("reasoning", "")

            logger.info(
                f"Table detection: is_table={is_table}, "
                f"confidence={confidence}, reasoning={reasoning}"
            )

            return is_table

        except json.JSONDecodeError:
            logger.warning(f"Failed to parse Bedrock vision response as JSON: {text_response}")
            return False

    except ClientError as e:
        logger.error(f"Bedrock vision API error: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error in table detection: {e}")
        return False
