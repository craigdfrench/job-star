"""Telegram intake bot for Job-Star.

Provides a zero-friction mobile intake channel (voice + text) that
normalizes messages into IntakeItem objects and enqueues them for
downstream processing. See
docs/architecture/telegram-integration.md for the full ADR.
"""

from services.telegram_bot.models import IntakeItem

__all__ = ["IntakeItem"]


// --- DUPLICATE BLOCK ---

"""Job-Star Telegram bot package.

Provides a Telegram intake channel for zero-friction mobile input
(voice and text). Captured messages are normalized into IntakeItem
records and persisted via IntakeStore for downstream triage.
"""

from .intake_item import IntakeItem, IntakeStatus, ContentType
from .intake_store import IntakeStore

__all__ = [
    "IntakeItem",
    "IntakeStatus",
    "ContentType",
    "IntakeStore",
]
