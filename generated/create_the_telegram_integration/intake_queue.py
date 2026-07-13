"""Pluggable intake queue.

Bootstrap uses an in-memory asyncio queue. The interface is deliberately
minimal so we can swap in Redis / SQS later without touching handlers.
"""
from __future__ import annotations

import asyncio
from typing import Protocol

from services.telegram_bot.models import IntakeItem


class IntakeQueue(Protocol):
    async def enqueue(self, item: IntakeItem) -> None: ...
    async def dequeue(self) -> IntakeItem: ...


class InMemoryIntakeQueue:
    def __init__(self) -> None:
        self._q: asyncio.Queue[IntakeItem] = asyncio.Queue()

    async def enqueue(self, item: IntakeItem) -> None:
        await self._q.put(item)

    async def dequeue(self) -> IntakeItem:
        return await self._q.get()


def make_queue(backend: str) -> IntakeQueue:
    if backend == "memory":
        return InMemoryIntakeQueue()
    raise NotImplementedError(f"Queue backend '{backend}' not yet supported.")


// --- DUPLICATE BLOCK ---

"""Intake queue for storing and retrieving intake items.

Provides an async interface for enqueueing items from Telegram
and dequeuing them for downstream triage/processing.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from .models import IntakeItem, IntakeSource, IntakeStatus

logger = logging.getLogger(__name__)


class IntakeQueue:
    """Async-safe queue for intake items.

    In production this would back onto a database (SQLite/Postgres).
    For bootstrap, we use an in-memory list protected by a lock,
    with an optional SQLite persistence layer.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._items: list[IntakeItem] = []
        self._lock = asyncio.Lock()
        self._db_path = db_path
        # If db_path provided, load existing items on init
        if db_path:
            self._load_from_db()

    def _load_from_db(self) -> None:
        """Load items from SQLite if available."""
        import sqlite3

        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM intake_items ORDER BY created_at DESC LIMIT 100"
            )
            for row in cursor:
                self._items.append(
                    IntakeItem(
                        id=row["id"],
                        user_id=row["user_id"],
                        content=row["content"],
                        source=IntakeSource(row["source"]),
                        status=IntakeStatus(row["status"]),
                        created_at=datetime.fromisoformat(row["created_at"]),
                        raw_file_id=row.get("raw_file_id"),
                    )
                )
            conn.close()
            logger.info("Loaded %d items from %s", len(self._items), self._db_path)
        except Exception:
            logger.warning("Could not load from db (first run?): %s", self._db_path)

    async def enqueue(self, item: IntakeItem) -> None:
        """Add an item to the queue."""
        async with self._lock:
            self._items.append(item)
            if self._db_path:
                self._persist_item(item)
        logger.info("Enqueued intake item %s for user %s", item.id, item.user_id)

    def _persist_item(self, item: IntakeItem) -> None:
        """Persist a single item to SQLite."""
        import sqlite3

        self._ensure_table()
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            """INSERT OR REPLACE INTO intake_items
               (id, user_id, content, source, status, created_at, raw_file_id)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                item.id,
                item.user_id,
                item.content,
                item.source.value,
                item.status.value,
                item.created_at.isoformat(),
                item.raw_file_id,
            ),
        )
        conn.commit()
        conn.close()

    def _ensure_table(self) -> None:
        import sqlite3

        conn = sqlite3.connect(self._db_path)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS intake_items (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                content TEXT,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                raw_file_id TEXT
            )"""
        )
        conn.commit()
        conn.close()

    async def dequeue(self) -> Optional[IntakeItem]:
        """Get the next unprocessed item (oldest first)."""
        async with self._lock:
            for item in self._items:
                if item.status == IntakeStatus.NEW:
                    return item
        return None

    async def mark_processed(self, item_id: str) -> None:
        """Mark an item as processed."""
        async with self._lock:
            for item in self._items:
                if item.id == item_id:
                    item.status = IntakeStatus.PROCESSED
                    if self._db_path:
                        self._update_status_db(item_id, IntakeStatus.PROCESSED)
                    break

    def _update_status_db(self, item_id: str, status: IntakeStatus) -> None:
        import sqlite3

        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "UPDATE intake_items SET status = ? WHERE id = ?",
            (status.value, item_id),
        )
        conn.commit()
        conn.close()

    async def get_recent_for_user(
        self,
        user_id: str,
        limit: int = 5,
        unprocessed_only: bool = True,
    ) -> list[IntakeItem]:
        """Get recent items for a specific user.

        Args:
            user_id: Telegram user ID as string.
            limit: Maximum number of items to return.
            unprocessed_only: If True, only return items with NEW status.

        Returns:
            List of IntakeItem, most recent first.
        """
        async with self._lock:
            filtered = [
                item
                for item in self._items
                if item.user_id == user_id
                and (
                    not unprocessed_only
                    or item.status == IntakeStatus.NEW
                )
            ]
            # Sort by created_at descending (most recent first)
            filtered.sort(
                key=lambda x: x.created_at or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            return filtered[:limit]
