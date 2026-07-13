"""Text message intake handler.

Processes incoming Telegram text messages, creates an IntakeItem,
stores it in the intake queue, and sends a minimal confirmation back
to the user.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from .config import BotConfig
from .models import IntakeItem, IntakeSource, IntakeStatus
from .intake_queue import IntakeQueue

logger = logging.getLogger(__name__)

# Hard limits for intake text.
MAX_TEXT_LENGTH = 4096  # Telegram's own message cap; we may trim earlier.
WARN_TEXT_LENGTH = 2000  # Warn (but still accept) above this length.
TRUNCATE_PREVIEW = 120  # Length of preview echoed in confirmation.

# Messages that are effectively empty after stripping.
_EMPTY_RE = re.compile(r"^[\s\u200b\u200c\u200d\ufeff]*$")


@dataclass
class TextHandlerResult:
    """Outcome of processing a text message."""

    accepted: bool
    item: Optional[IntakeItem] = None
    confirmation: str = ""
    warning: Optional[str] = None


class TextIntakeHandler:
    """Handles plain-text Telegram messages and turns them into IntakeItems."""

    def __init__(
        self,
        config: BotConfig,
        queue: IntakeQueue,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.config = config
        self.queue = queue
        self._now = now

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def handle_text(
        self,
        *,
        user_id: int,
        chat_id: int,
        message_id: int,
        text: str,
        username: Optional[str] = None,
        raw: Optional[dict[str, Any]] = None,
    ) -> TextHandlerResult:
        """Process a single text message.

        Returns a :class:`TextHandlerResult` describing what happened and
        what confirmation text (if any) should be sent back to the user.
        """
        cleaned = self._clean(text)

        if not self._is_meaningful(cleaned):
            logger.info("Rejecting empty text message from user=%s", user_id)
            return TextHandlerResult(
                accepted=False,
                confirmation="⚠️ Empty message — nothing to capture.",
            )

        warning: Optional[str] = None
        if len(cleaned) > WARN_TEXT_LENGTH:
            warning = (
                f"Long message ({len(cleaned)} chars) captured in full; "
                "consider splitting future notes."
            )
            logger.info("Long text intake from user=%s len=%s", user_id, len(cleaned))

        if len(cleaned) > MAX_TEXT_LENGTH:
            # Defensive: Telegram shouldn't deliver longer than this, but
            # be safe if the handler is fed from another source.
            cleaned = cleaned[:MAX_TEXT_LENGTH]
            warning = warning or "Message truncated to maximum length."

        item = IntakeItem(
            id=self._make_id(user_id, message_id),
            source=IntakeSource.TEXT,
            user_id=user_id,
            chat_id=chat_id,
            message_id=message_id,
            username=username,
            raw_text=cleaned,
            created_at=self._now(),
            status=IntakeStatus.NEW,
            meta={"raw": raw} if raw is not None else {},
        )

        self.queue.enqueue(item)
        logger.info("Captured text intake id=%s user=%s len=%s", item.id, user_id, len(cleaned))

        confirmation = self._confirmation(cleaned, warning)
        return TextHandlerResult(accepted=True, item=item, confirmation=confirmation, warning=warning)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _clean(text: str) -> str:
        """Normalize whitespace while preserving internal formatting."""
        # Strip BOM/zero-width chars at the edges, collapse leading/trailing
        # whitespace. We do NOT collapse internal newlines so the user's
        # formatting is preserved in storage.
        text = text.replace("\ufeff", "").replace("\u200b", "")
        return text.strip()

    @staticmethod
    def _is_meaningful(text: str) -> bool:
        if not text:
            return False
        if _EMPTY_RE.match(text):
            return False
        # Require at least one non-whitespace, non-punctuation character.
        return any(ch.isalnum() for ch in text)

    @staticmethod
    def _make_id(user_id: int, message_id: int) -> str:
        return f"tg-text-{user_id}-{message_id}"

    @staticmethod
    def _confirmation(text: str, warning: Optional[str]) -> str:
        preview = text if len(text) <= TRUNCATE_PREVIEW else text[:TRUNCATE_PREVIEW].rstrip() + "…"
        # Single-line preview for the chat bubble.
        preview_one_line = " ".join(preview.split())
        base = f"✅ Captured: {preview_one_line}"
        if warning:
            base += f"\n\nℹ️ {warning}"
        return base
