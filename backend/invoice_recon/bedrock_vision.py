"""Bedrock vision-based page analysis."""

import base64
import json
import logging
from typing import Dict, Any
import boto3
from botocore.exceptions import ClientError
from invoice_recon.config import Config

logger = logging.getLogger(__name__)


def _extract_json_from_response(response_text: str) -> Dict[str, Any]:
    """
    Extract JSON from response text, handling markdown fences.

    Args:
        response_text: Raw response text

    Returns:
        Parsed JSON dict
    """
    text = response_text.strip()

    # Remove markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first line (```json or ```)
        lines = lines[1:]
        # Remove last line if it's ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Find first { or [ and last } or ]
    start_brace = text.find("{")
    start_bracket = text.find("[")

    if start_brace == -1 and start_bracket == -1:
        raise ValueError("No JSON object or array found in response")

    if start_brace == -1:
        start = start_bracket
        end_char = "]"
    elif start_bracket == -1:
        start = start_brace
        end_char = "}"
    else:
        start = min(start_brace, start_bracket)
        end_char = "}" if start == start_brace else "]"

    end = text.rfind(end_char)
    if end == -1:
        raise ValueError(f"No matching {end_char} found in response")

    json_text = text[start : end + 1]
    return json.loads(json_text)


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

    try:
        # Use the Converse API which is model-agnostic (works with both
        # Anthropic Claude and Amazon Nova models)
        response = client.converse(
            modelId=Config.BEDROCK_VISION_MODEL_ID,
            system=[{"text": system_prompt}],
            messages=[{
                "role": "user",
                "content": [
                    {
                        "image": {
                            "format": "png",
                            "source": {"bytes": image_bytes},
                        }
                    },
                    {
                        "text": "Analyze this page and determine if it primarily contains tabular data."
                    },
                ],
            }],
            inferenceConfig={
                "maxTokens": 500,
                "temperature": 0.0,
            },
        )

        content = response.get("output", {}).get("message", {}).get("content", [])

        if not content:
            logger.warning("Empty response from Bedrock vision")
            return False

        text_response = content[0].get("text", "")

        # Parse JSON response (handles markdown fences)
        try:
            result = _extract_json_from_response(text_response)
            is_table = result.get("is_table", False)
            confidence = result.get("confidence", "unknown")
            reasoning = result.get("reasoning", "")

            logger.info(
                f"Table detection: is_table={is_table}, "
                f"confidence={confidence}, reasoning={reasoning}"
            )

            return is_table

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Failed to parse Bedrock vision response as JSON: {e}")
            logger.debug(f"Response text: {text_response[:200]}")
            return False

    except ClientError as e:
        logger.error(f"Bedrock vision API error: {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error in table detection: {e}")
        return False
