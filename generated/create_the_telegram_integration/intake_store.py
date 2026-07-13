"""Storage layer for IntakeItem records.

Uses SQLite as the bootstrap persistence backend. The interface is
designed so the backend can be swapped (e.g. to Postgres, a document
store, or an ORM) without changing call sites.

The store is intentionally simple: it provides CRUD operations plus
a couple of query helpers (list by status, fetch newest). Concurrency
is handled at the SQLite level with a per-process lock; for the
bootstrap single-bot deployment this is sufficient.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, List, Optional

from .intake_item import IntakeItem, IntakeStatus

# Default location for the bootstrap database.
_DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "intake.db"


class IntakeStore:
    """SQLite-backed store for IntakeItem records."""

    def __init__(self, db_path: Optional[os.PathLike] = None) -> None:
        self.db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_schema()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS intake_items (
                    id                   TEXT PRIMARY KEY,
                    source               TEXT NOT NULL,
                    content_type         TEXT NOT NULL,
                    raw_content          TEXT NOT NULL,
                    transcript           TEXT,
                    telegram_message_id  INTEGER,
                    telegram_user_id     INTEGER,
                    telegram_chat_id     INTEGER,
                    timestamp            TEXT NOT NULL,
                    status               TEXT NOT NULL,
                    metadata             TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_intake_status "
                "ON intake_items(status)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_intake_user "
                "ON intake_items(telegram_user_id)"
            )

    # ------------------------------------------------------------------ #
    # CRUD operations
    # ------------------------------------------------------------------ #
    def create(self, item: IntakeItem) -> IntakeItem:
        """Insert a new intake item. Returns the item (with generated id)."""
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO intake_items (
                    id, source, content_type, raw_content, transcript,
                    telegram_message_id, telegram_user_id, telegram_chat_id,
                    timestamp, status, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.id,
                    item.source,
                    item.content_type.value,
                    item.raw_content,
                    item.transcript,
                    item.telegram_message_id,
                    item.telegram_user_id,
                    item.telegram_chat_id,
                    item.timestamp,
                    item.status.value,
                    json.dumps(item.metadata),
                ),
            )
        return item

    def get(self, item_id: str) -> Optional[IntakeItem]:
        """Fetch a single intake item by id, or None if not found."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM intake_items WHERE id = ?", (item_id,)
            ).fetchone()
        return self._row_to_item(row) if row else None

    def update(self, item: IntakeItem) -> IntakeItem:
        """Update an existing intake item (matched by id)."""
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE intake_items SET
                    source = ?,
                    content_type = ?,
                    raw_content = ?,
                    transcript = ?,
                    telegram_message_id = ?,
                    telegram_user_id = ?,
                    telegram_chat_id = ?,
                    timestamp = ?,
                    status = ?,
                    metadata = ?
                WHERE id = ?
                """,
                (
                    item.source,
                    item.content_type.value,
                    item.raw_content,
                    item.transcript,
                    item.telegram_message_id,
                    item.telegram_user_id,
                    item.telegram_chat_id,
                    item.timestamp,
                    item.status.value,
                    json.dumps(item.metadata),
                    item.id,
                ),
            )
        return item

    def delete(self, item_id: str) -> bool:
        """Delete an intake item by id. Returns True if a row was removed."""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM intake_items WHERE id = ?", (item_id,)
            )
        return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    # Query helpers
    # ------------------------------------------------------------------ #
    def list_by_status(
        self, status: IntakeStatus, limit: int = 100
    ) -> List[IntakeItem]:
        """Return items matching a status, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM intake_items WHERE status = ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (status.value, limit),
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def list_new(self, limit: int = 100) -> List[IntakeItem]:
        """Convenience: return all items with status NEW."""
        return self.list_by_status(IntakeStatus.NEW, limit=limit)

    def list_all(self, limit: int = 100) -> List[IntakeItem]:
        """Return all items, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM intake_items ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_item(r) for r in rows]

    def count(self) -> int:
        """Total number of stored items."""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM intake_items").fetchone()
        return int(row["n"]) if row else 0

    def mark_status(self, item_id: str, status: IntakeStatus) -> bool:
        """Update only the status of an item. Returns True if updated."""
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE intake_items SET status = ? WHERE id = ?",
                (status.value, item_id),
            )
        return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    # Conversion
    # ------------------------------------------------------------------ #
    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> IntakeItem:
        data = dict(row)
        # Deserialize metadata JSON
        meta_raw = data.get("metadata") or "{}"
        try:
            data["metadata"] = json.loads(meta_raw)
        except (json.JSONDecodeError, TypeError):
            data["metadata"] = {}
        return IntakeItem.from_dict(data)
