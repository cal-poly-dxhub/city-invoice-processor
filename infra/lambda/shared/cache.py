"""DynamoDB cache module — replaces SQLite IndexStore for serverless."""

import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import boto3

logger = logging.getLogger(__name__)

_table = None

TTL_DAYS = 30


def _get_table():
    global _table
    if _table is None:
        dynamodb = boto3.resource("dynamodb")
        _table = dynamodb.Table(os.environ["CACHE_TABLE"])
    return _table


def _ttl_epoch() -> int:
    return int(time.time()) + TTL_DAYS * 86400


# ---- Document-level operations ----

def get_document(doc_id: str) -> Optional[Dict[str, Any]]:
    """Get document metadata. Returns None if not found."""
    resp = _get_table().get_item(Key={"PK": f"DOC#{doc_id}", "SK": "META"})
    return resp.get("Item")


def upsert_document(
    doc_id: str,
    budget_item: str,
    file_sha256: str,
    page_count: int,
) -> None:
    """Insert or update document metadata."""
    _get_table().put_item(
        Item={
            "PK": f"DOC#{doc_id}",
            "SK": "META",
            "budget_item": budget_item,
            "file_sha256": file_sha256,
            "page_count": page_count,
            "ttl": _ttl_epoch(),
        }
    )


def should_reextract_document(doc_id: str, current_sha256: str) -> bool:
    """Check if document needs re-extraction (missing or hash changed)."""
    doc = get_document(doc_id)
    if not doc:
        return True
    return doc.get("file_sha256") != current_sha256


# ---- Page-level operations ----

def get_page(doc_id: str, page_number: int) -> Optional[Dict[str, Any]]:
    """Get cached page data. Returns None if not found."""
    resp = _get_table().get_item(
        Key={"PK": f"PAGE#{doc_id}", "SK": f"#{page_number}"}
    )
    item = resp.get("Item")
    if item and "entities_json" in item:
        item["entities"] = json.loads(item["entities_json"])
    if item and "words_json" in item:
        item["words"] = json.loads(item["words_json"])
    if item and "tables_json" in item:
        item["tables"] = json.loads(item["tables_json"])
    return item


def get_page_text_hash(doc_id: str, page_number: int) -> Optional[str]:
    """Get the text hash for a page (for cache invalidation)."""
    resp = _get_table().get_item(
        Key={"PK": f"PAGE#{doc_id}", "SK": f"#{page_number}"},
        ProjectionExpression="text_sha256",
    )
    item = resp.get("Item")
    return item.get("text_sha256") if item else None


def should_reextract_entities(
    doc_id: str, page_number: int, text_sha256: str
) -> bool:
    """Check if entities need re-extraction (missing or text changed)."""
    cached_hash = get_page_text_hash(doc_id, page_number)
    if not cached_hash:
        return True
    return cached_hash != text_sha256


def upsert_page(
    doc_id: str,
    page_number: int,
    text_source: str,
    text: str,
    entities: Dict[str, Any],
    words: Optional[List[Dict[str, Any]]] = None,
    tables: Optional[List[Any]] = None,
) -> None:
    """Insert or update page data."""
    text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    entities_json = json.dumps(entities, sort_keys=True)
    entities_sha256 = hashlib.sha256(entities_json.encode("utf-8")).hexdigest()
    words_json = json.dumps(words, sort_keys=True) if words else "[]"

    if tables:
        tables_serializable = [
            t.dict() if hasattr(t, "dict") else t for t in tables
        ]
        tables_json = json.dumps(tables_serializable, sort_keys=True)
    else:
        tables_json = None

    item = {
        "PK": f"PAGE#{doc_id}",
        "SK": f"#{page_number}",
        "text_source": text_source,
        "text_sha256": text_sha256,
        "text": text,
        "entities_json": entities_json,
        "entities_sha256": entities_sha256,
        "words_json": words_json,
        "ttl": _ttl_epoch(),
    }
    if tables_json:
        item["tables_json"] = tables_json

    _get_table().put_item(Item=item)


def get_all_pages_for_document(doc_id: str) -> List[Dict[str, Any]]:
    """Get all cached pages for a document, sorted by page number."""
    resp = _get_table().query(
        KeyConditionExpression="PK = :pk AND begins_with(SK, :prefix)",
        ExpressionAttributeValues={":pk": f"PAGE#{doc_id}", ":prefix": "#"},
    )
    pages = []
    for item in resp.get("Items", []):
        page_number = int(item["SK"].lstrip("#"))
        entities = json.loads(item.get("entities_json", "{}"))
        words = json.loads(item.get("words_json", "[]"))
        pages.append({
            "doc_id": doc_id,
            "page_number": page_number,
            "text_source": item.get("text_source", ""),
            "text": item.get("text", ""),
            "entities": entities,
            "words": words,
        })
    pages.sort(key=lambda p: p["page_number"])
    return pages
