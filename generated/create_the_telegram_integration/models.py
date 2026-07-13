"""Data model for intake items coming from Telegram (and future channels).

An IntakeItem is the canonical representation of a piece of raw input
captured from an intake channel before it is triaged, enriched, or
processed by downstream Job-Star services.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional


class ContentType(str, Enum):
    """The kind of raw content captured."""

    TEXT = "text"
    VOICE = "voice"


class IntakeStatus(str, Enum):
    """Lifecycle status of an intake item."""

    NEW = "new"
    PROCESSING = "processing"
    PROCESSED = "processed"
    FAILED = "failed"


@dataclass
class IntakeItem:
    """A single piece of intake from a channel (e.g. Telegram).

    Attributes:
        id: Unique identifier (UUID4 hex string).
        source: The intake channel that produced this item (e.g. "telegram").
        content_type: Whether the raw content is text or voice.
        raw_content: The raw payload. For text, the message text.
            For voice, the file path or file_id of the voice recording.
        transcript: Transcribed text for voice items (None for text items
            until/if transcribed).
        telegram_message_id: The Telegram message ID for traceability.
        telegram_user_id: The Telegram user ID of the sender.
        telegram_chat_id: The Telegram chat ID where the message arrived.
        timestamp: ISO-8601 UTC timestamp when the item was created.
        status: Current lifecycle status.
        metadata: Free-form dict for extra channel-specific data.
    """

    source: str = "telegram"
    content_type: ContentType = ContentType.TEXT
    raw_content: str = ""
    transcript: Optional[str] = None
    telegram_message_id: Optional[int] = None
    telegram_user_id: Optional[int] = None
    telegram_chat_id: Optional[int] = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    status: IntakeStatus = IntakeStatus.NEW
    metadata: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)

    # ------------------------------------------------------------------ #
    # Serialization helpers
    # ------------------------------------------------------------------ #
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a plain dict suitable for JSON / SQLite storage."""
        d = asdict(self)
        # Store enums as their string values for portability
        d["content_type"] = self.content_type.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "IntakeItem":
        """Reconstruct an IntakeItem from a stored dict."""
        # Tolerate missing fields
        content_type = data.get("content_type", ContentType.TEXT.value)
        status = data.get("status", IntakeStatus.NEW.value)
        # Coerce string values back to enums
        if isinstance(content_type, str):
            content_type = ContentType(content_type)
        if isinstance(status, str):
            status = IntakeStatus(status)
        return cls(
            id=data.get("id", uuid.uuid4().hex),
            source=data.get("source", "telegram"),
            content_type=content_type,
            raw_content=data.get("raw_content", ""),
            transcript=data.get("transcript"),
            telegram_message_id=data.get("telegram_message_id"),
            telegram_user_id=data.get("telegram_user_id"),
            telegram_chat_id=data.get("telegram_chat_id"),
            timestamp=data.get(
                "timestamp", datetime.now(timezone.utc).isoformat()
            ),
            status=status,
            metadata=data.get("metadata", {}),
        )

    # ------------------------------------------------------------------ #
    # Convenience constructors
    # ------------------------------------------------------------------ #
    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        telegram_message_id: Optional[int] = None,
        telegram_user_id: Optional[int] = None,
        telegram_chat_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "IntakeItem":
        """Create a text-based intake item."""
        return cls(
            source="telegram",
            content_type=ContentType.TEXT,
            raw_content=text,
            transcript=text,  # text is its own transcript
            telegram_message_id=telegram_message_id,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            metadata=metadata or {},
        )

    @classmethod
    def from_voice(
        cls,
        voice_file_id: str,
        *,
        telegram_message_id: Optional[int] = None,
        telegram_user_id: Optional[int] = None,
        telegram_chat_id: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "IntakeItem":
        """Create a voice-based intake item (transcript filled later)."""
        return cls(
            source="telegram",
            content_type=ContentType.VOICE,
            raw_content=voice_file_id,
            transcript=None,
            telegram_message_id=telegram_message_id,
            telegram_user_id=telegram_user_id,
            telegram_chat_id=telegram_chat_id,
            metadata=metadata or {},
        )
