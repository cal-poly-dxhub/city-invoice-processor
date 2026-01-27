"""Tests for Bedrock entity extraction payload building and JSON parsing."""

import json
import pytest
from invoice_recon.bedrock_entities import (
    extract_json_from_response,
    get_safe_default_entities,
)


def test_extract_json_from_response_plain():
    """Test extracting plain JSON."""
    response = '{"key": "value", "number": 123}'
    result = extract_json_from_response(response)
    assert result == {"key": "value", "number": 123}


def test_extract_json_from_response_with_markdown():
    """Test extracting JSON wrapped in markdown code fence."""
    response = """```json
{
  "page_number": 1,
  "doc_type": "timecard",
  "people": [{"full_name": "John Doe", "first_name": "John", "last_name": "Doe"}]
}
```"""
    result = extract_json_from_response(response)
    assert result["page_number"] == 1
    assert result["doc_type"] == "timecard"
    assert len(result["people"]) == 1


def test_extract_json_from_response_with_plain_markdown():
    """Test extracting JSON wrapped in plain markdown fence."""
    response = """```
{
  "page_number": 2,
  "doc_type": "paystub"
}
```"""
    result = extract_json_from_response(response)
    assert result["page_number"] == 2
    assert result["doc_type"] == "paystub"


def test_extract_json_from_response_with_extra_text():
    """Test extracting JSON when there's extra text around it."""
    response = """Here is the extracted data:
{
  "page_number": 3,
  "keywords": ["invoice", "payment"]
}
Hope this helps!"""
    result = extract_json_from_response(response)
    assert result["page_number"] == 3
    assert result["keywords"] == ["invoice", "payment"]


def test_extract_json_from_response_array():
    """Test extracting JSON array."""
    response = '[{"id": 1}, {"id": 2}]'
    result = extract_json_from_response(response)
    assert result == [{"id": 1}, {"id": 2}]


def test_extract_json_from_response_invalid():
    """Test that invalid JSON raises ValueError."""
    response = "This is not JSON at all"
    with pytest.raises(ValueError, match="No JSON object or array found"):
        extract_json_from_response(response)


def test_extract_json_from_response_incomplete():
    """Test that incomplete JSON raises error."""
    response = '{"incomplete": "json"'
    with pytest.raises(json.JSONDecodeError):
        extract_json_from_response(response)


def test_get_safe_default_entities():
    """Test safe default entities structure."""
    default = get_safe_default_entities(5)

    assert default["page_number"] == 5
    assert default["doc_type"] == "unknown"
    assert default["people"] == []
    assert default["organizations"] == []
    assert default["periods"] == []
    assert default["dates"] == []
    assert default["amounts"] == []
    assert default["keywords"] == []
