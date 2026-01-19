"""SQLite-based index store for caching extraction results."""

import hashlib
import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from invoice_recon.models import DocumentRef, PageRecord

logger = logging.getLogger(__name__)


class IndexStore:
    """SQLite-based cache for document and page data."""

    def __init__(self, db_path: Path):
        """
        Initialize the index store.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Documents table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                doc_id TEXT PRIMARY KEY,
                budget_item TEXT NOT NULL,
                path TEXT NOT NULL,
                file_sha256 TEXT NOT NULL,
                page_count INTEGER NOT NULL
            )
        """)

        # Pages table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                doc_id TEXT NOT NULL,
                page_number INTEGER NOT NULL,
                text_source TEXT NOT NULL,
                text_sha256 TEXT NOT NULL,
                text TEXT NOT NULL,
                entities_json TEXT NOT NULL,
                entities_sha256 TEXT NOT NULL,
                PRIMARY KEY (doc_id, page_number)
            )
        """)

        conn.commit()
        conn.close()

    def get_document(self, doc_id: str) -> Optional[DocumentRef]:
        """Get document metadata by doc_id."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT doc_id, budget_item, path, file_sha256, page_count "
            "FROM documents WHERE doc_id = ?",
            (doc_id,),
        )

        row = cursor.fetchone()
        conn.close()

        if row:
            return DocumentRef(
                doc_id=row[0],
                budget_item=row[1],
                path=row[2],
                file_sha256=row[3],
                page_count=row[4],
            )

        return None

    def upsert_document(self, doc_ref: DocumentRef) -> None:
        """Insert or update document metadata."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO documents
            (doc_id, budget_item, path, file_sha256, page_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                doc_ref.doc_id,
                doc_ref.budget_item,
                doc_ref.path,
                doc_ref.file_sha256,
                doc_ref.page_count,
            ),
        )

        conn.commit()
        conn.close()

    def get_page(self, doc_id: str, page_number: int) -> Optional[PageRecord]:
        """Get page data by doc_id and page_number."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT doc_id, page_number, text_source, text, entities_json
            FROM pages
            WHERE doc_id = ? AND page_number = ?
            """,
            (doc_id, page_number),
        )

        row = cursor.fetchone()
        conn.close()

        if row:
            entities = json.loads(row[4])
            return PageRecord(
                doc_id=row[0],
                page_number=row[1],
                text_source=row[2],
                text=row[3],
                entities=entities,
            )

        return None

    def get_page_text_hash(self, doc_id: str, page_number: int) -> Optional[str]:
        """Get the text hash for a page (for cache invalidation)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT text_sha256 FROM pages WHERE doc_id = ? AND page_number = ?",
            (doc_id, page_number),
        )

        row = cursor.fetchone()
        conn.close()

        return row[0] if row else None

    def upsert_page(
        self,
        doc_id: str,
        page_number: int,
        text_source: str,
        text: str,
        entities: Dict[str, Any],
    ) -> None:
        """Insert or update page data."""
        text_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        entities_json = json.dumps(entities, sort_keys=True)
        entities_sha256 = hashlib.sha256(entities_json.encode("utf-8")).hexdigest()

        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT OR REPLACE INTO pages
            (doc_id, page_number, text_source, text_sha256, text, entities_json, entities_sha256)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                doc_id,
                page_number,
                text_source,
                text_sha256,
                text,
                entities_json,
                entities_sha256,
            ),
        )

        conn.commit()
        conn.close()

    def get_all_documents(self) -> List[DocumentRef]:
        """Get all documents in the index."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT doc_id, budget_item, path, file_sha256, page_count FROM documents"
        )

        rows = cursor.fetchall()
        conn.close()

        return [
            DocumentRef(
                doc_id=row[0],
                budget_item=row[1],
                path=row[2],
                file_sha256=row[3],
                page_count=row[4],
            )
            for row in rows
        ]

    def get_all_pages_for_document(self, doc_id: str) -> List[PageRecord]:
        """Get all pages for a document."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT doc_id, page_number, text_source, text, entities_json
            FROM pages
            WHERE doc_id = ?
            ORDER BY page_number
            """,
            (doc_id,),
        )

        rows = cursor.fetchall()
        conn.close()

        pages = []
        for row in rows:
            entities = json.loads(row[4])
            pages.append(
                PageRecord(
                    doc_id=row[0],
                    page_number=row[1],
                    text_source=row[2],
                    text=row[3],
                    entities=entities,
                )
            )

        return pages

    def should_reextract_document(
        self, doc_id: str, current_sha256: str
    ) -> bool:
        """
        Check if a document should be re-extracted.

        Returns True if:
        - Document not in index
        - File hash has changed
        """
        doc = self.get_document(doc_id)
        if not doc:
            return True

        return doc.file_sha256 != current_sha256

    def should_reextract_entities(
        self, doc_id: str, page_number: int, text_sha256: str
    ) -> bool:
        """
        Check if entities should be re-extracted for a page.

        Returns True if:
        - Page not in index
        - Text hash has changed
        """
        cached_hash = self.get_page_text_hash(doc_id, page_number)
        if not cached_hash:
            return True

        return cached_hash != text_sha256
