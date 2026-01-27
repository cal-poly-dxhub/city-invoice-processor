"""AWS Bedrock entity extraction."""

import json
import logging
import time
from typing import Any, Dict, List
import boto3
from botocore.exceptions import ClientError
from invoice_recon.config import Config

logger = logging.getLogger(__name__)


def create_bedrock_client():
    """Create a Bedrock Runtime client with proper configuration."""
    # Always pass profile_name to override AWS_PROFILE env var
    # Use Config.AWS_PROFILE if set, otherwise None (uses default credentials)
    profile_name = Config.AWS_PROFILE if Config.AWS_PROFILE else None

    session = boto3.Session(
        region_name=Config.AWS_REGION,
        profile_name=profile_name
    )
    return session.client("bedrock-runtime")


def invoke_bedrock_messages(
    system_prompt: str,
    messages: List[Dict[str, Any]],
    max_tokens: int = 4096,
    temperature: float = 0.0,
    max_retries: int = 5,
) -> str:
    """
    Invoke Bedrock Messages API with exponential backoff on throttling.

    Args:
        system_prompt: System prompt
        messages: List of message dicts
        max_tokens: Maximum tokens to generate
        temperature: Temperature for sampling
        max_retries: Maximum number of retries on throttling

    Returns:
        Response text content
    """
    client = create_bedrock_client()

    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system_prompt,
        "messages": messages,
    }

    for attempt in range(max_retries):
        try:
            response = client.invoke_model(
                modelId=Config.BEDROCK_MODEL_ID,
                body=json.dumps(request_body),
            )

            response_body = json.loads(response["body"].read())
            content = response_body.get("content", [])

            # Extract text from content blocks
            text_parts = []
            for block in content:
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))

            return "".join(text_parts)

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")

            if error_code == "ThrottlingException":
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + (0.1 * attempt)
                    logger.warning(
                        f"Throttled by Bedrock, retrying in {wait_time:.1f}s "
                        f"(attempt {attempt + 1}/{max_retries})"
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    logger.error("Max retries exceeded for Bedrock throttling")
                    raise
            else:
                logger.error(f"Bedrock API error: {e}")
                raise

    return ""


def extract_json_from_response(response_text: str) -> Dict[str, Any]:
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


def get_safe_default_entities(page_number: int) -> Dict[str, Any]:
    """Get safe default entities structure when extraction fails."""
    return {
        "page_number": page_number,
        "doc_type": "unknown",
        "people": [],
        "organizations": [],
        "periods": [],
        "dates": [],
        "amounts": [],
        "keywords": [],
    }


def extract_entities(page_text: str, budget_item: str, page_number: int) -> Dict[str, Any]:
    """
    Extract entities from page text using Bedrock.

    Args:
        page_text: Extracted text from page
        budget_item: Budget item category
        page_number: Page number (1-based)

    Returns:
        Entities dict with schema:
        {
            "page_number": int,
            "doc_type": str,
            "people": [{"full_name": str, "first_name": str, "last_name": str}],
            "organizations": [str],
            "periods": [str],
            "dates": [str],
            "amounts": [{"raw": str, "value": float|None, "currency": str, "context": str}],
            "keywords": [str]
        }
    """
    system_prompt = """You are an expert at extracting structured information from document text.

CRITICAL RULES:
- ONLY extract information that is explicitly present in the provided text
- DO NOT hallucinate, infer, or guess any information
- Return ONLY valid JSON, no explanatory text
- If a field has no data, return empty array or "unknown" for doc_type
- Do not include coordinates or bounding boxes

Your task is to extract entities and classify the document type."""

    user_prompt = f"""Extract entities from this page text.

Budget Item: {budget_item}
Page Number: {page_number}

Page Text:
{page_text[:6000]}

Return JSON with this exact schema:
{{
  "page_number": {page_number},
  "doc_type": "timecard|paystub|bank_statement|utility_bill|invoice|receipt|other|unknown",
  "people": [{{"full_name": "...", "first_name": "...", "last_name": "..."}}],
  "organizations": ["..."],
  "periods": ["..."],
  "dates": ["..."],
  "amounts": [{{"raw":"...", "value": <number|null>, "currency":"USD|...", "context":"..."}}],
  "keywords": ["..."]
}}

ONLY extract what is present in the text. Return valid JSON only."""

    messages = [{"role": "user", "content": user_prompt}]

    try:
        response_text = invoke_bedrock_messages(
            system_prompt=system_prompt,
            messages=messages,
            max_tokens=4096,
            temperature=0.0,
        )

        # Try to extract JSON
        entities = extract_json_from_response(response_text)

        # Ensure page_number is set
        entities["page_number"] = page_number

        return entities

    except (json.JSONDecodeError, ValueError) as e:
        logger.warning(f"Failed to parse JSON from Bedrock response: {e}")

        # Try repair prompt
        try:
            repair_prompt = f"""The previous response was not valid JSON. Please return ONLY valid JSON with the required schema.

Original text to extract from:
{page_text[:4000]}

Return this exact schema as valid JSON:
{{
  "page_number": {page_number},
  "doc_type": "timecard|paystub|bank_statement|utility_bill|invoice|receipt|other|unknown",
  "people": [{{"full_name": "...", "first_name": "...", "last_name": "..."}}],
  "organizations": ["..."],
  "periods": ["..."],
  "dates": ["..."],
  "amounts": [{{"raw":"...", "value": <number|null>, "currency":"USD", "context":"..."}}],
  "keywords": ["..."]
}}"""

            messages_repair = [
                {"role": "user", "content": user_prompt},
                {"role": "assistant", "content": response_text},
                {"role": "user", "content": repair_prompt},
            ]

            repair_response = invoke_bedrock_messages(
                system_prompt=system_prompt,
                messages=messages_repair,
                max_tokens=4096,
                temperature=0.0,
            )

            entities = extract_json_from_response(repair_response)
            entities["page_number"] = page_number
            return entities

        except Exception as repair_error:
            logger.error(f"Repair attempt also failed: {repair_error}")
            return get_safe_default_entities(page_number)

    except Exception as e:
        logger.error(f"Unexpected error in extract_entities: {e}")
        return get_safe_default_entities(page_number)
