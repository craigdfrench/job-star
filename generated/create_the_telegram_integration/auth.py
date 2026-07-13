"""Authorization middleware for the Telegram intake bot.

This module enforces a strict allowlist of Telegram user IDs. Since the bot
is an intake channel for personal data (job search notes, voice memos with
PII, etc.), unauthorized access must be rejected and logged.

Usage:
    from services.telegram_bot.auth import AuthGuard

    guard = AuthGuard(config)
    if not guard.is_authorized(update):
        return  # handler already sent rejection message
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from telegram import Update
from telegram.ext import ContextTypes, ApplicationBuilder

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UnauthorizedAttempt:
    """Record of an unauthorized access attempt, for logging/audit."""
    user_id: int
    username: Optional[str]
    first_name: Optional[str]
    chat_id: int
    timestamp: str
    message_preview: str


class AuthGuard:
    """Checks incoming updates against an allowlist of Telegram user IDs.

    The allowlist is sourced from configuration (env var ``ALLOWED_USER_IDS``,
    a comma-separated list of integers). If the allowlist is empty, the guard
    operates in *fail-closed* mode and rejects everyone — this is intentional
    to avoid accidentally exposing the intake channel.
    """

    def __init__(self, allowed_user_ids: Iterable[int]) -> None:
        self._allowed: frozenset[int] = frozenset(int(uid) for uid in allowed_user_ids)
        self._rejection_message = (
            "🚫 Sorry, you're not authorized to use this bot.\n\n"
            "This is a private job-search intake assistant. "
            "If you believe this is an error, contact the owner."
        )

    # ------------------------------------------------------------------ #
    # Core check
    # ------------------------------------------------------------------ #
    def is_authorized(self, update: Update) -> bool:
        """Return True if the update's sender is on the allowlist."""
        user = update.effective_user
        if user is None:
            # No user context (e.g., channel posts) — reject by default.
            return False
        if not self._allowed:
            # Fail-closed: no allowlist configured means nobody is allowed.
            logger.warning(
                "AuthGuard reject: allowlist is empty (fail-closed mode). "
                "user_id=%s username=%s",
                user.id, user.username,
            )
            return False
        return user.id in self._allowed

    def extract_attempt(self, update: Update) -> UnauthorizedAttempt:
        """Build an audit record from an unauthorized update."""
        user = update.effective_user
        chat = update.effective_chat
        text = ""
        if update.message and update.message.text:
            text = update.message.text[:80]
        elif update.message and update.message.voice:
            text = f"[voice message, duration={update.message.voice.duration}s]"
        return UnauthorizedAttempt(
            user_id=user.id if user else -1,
            username=user.username if user else None,
            first_name=user.first_name if user else None,
            chat_id=chat.id if chat else -1,
            timestamp=datetime.now(timezone.utc).isoformat(),
            message_preview=text,
        )

    def log_unauthorized(self, attempt: UnauthorizedAttempt) -> None:
        """Log an unauthorized access attempt for audit purposes."""
        logger.warning(
            "Unauthorized access attempt: user_id=%s username=%s name=%s "
            "chat_id=%s preview=%r at=%s",
            attempt.user_id,
            attempt.username,
            attempt.first_name,
            attempt.chat_id,
            attempt.message_preview,
            attempt.timestamp,
        )

    @property
    def rejection_message(self) -> str:
        return self._rejection_message

    @property
    def allowed_user_ids(self) -> frozenset[int]:
        return self._allowed


# ---------------------------------------------------------------------- #
# python-telegram-bot middleware integration
# ---------------------------------------------------------------------- #
async def auth_middleware(
    guard: AuthGuard,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    """Pre-handler check. Returns True if the request should proceed.

    When unauthorized, sends the rejection message and logs the attempt.
    Callers should ``return`` early when this returns False.
    """
    if guard.is_authorized(update):
        return True

    attempt = guard.extract_attempt(update)
    guard.log_unauthorized(attempt)

    # Send a friendly rejection if we have a chat to reply to.
    if update.effective_chat is not None:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=guard.rejection_message,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to send rejection message: %s", exc)
    return False


def build_authorized_handler(guard: AuthGuard, handler_fn: Any) -> Any:
    """Wrap a handler so it only runs for authorized users.

    ``handler_fn`` must be an async callable ``(update, context)``.
    Returns a new async callable with the same signature.
    """

    async def _guarded(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        ok = await auth_middleware(guard, update, context)
        if not ok:
            return
        await handler_fn(update, context)

    _guarded.__name__ = f"authorized_{getattr(handler_fn, '__name__', 'handler')}"
    _guarded.__doc__ = handler_fn.__doc__
    return _guarded
