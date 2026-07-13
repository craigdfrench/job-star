"""Telegram command handlers for /inbox, /recent, /help.

These commands give the user a feedback loop — they can verify their
intake items are being captured correctly without leaving Telegram.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from .intake_queue import IntakeQueue
from .models import IntakeItem, IntakeStatus

if TYPE_CHECKING:
    from .config import BotConfig

logger = logging.getLogger(__name__)

# How many recent items to show in /inbox
RECENT_ITEM_LIMIT = 5

# Max characters of content to display per item
CONTENT_TRUNCATE = 120


def _truncate(text: str, limit: int = CONTENT_TRUNCATE) -> str:
    """Truncate text to limit, adding ellipsis if cut."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _format_item(item: IntakeItem, index: int) -> str:
    """Format a single intake item for display."""
    # Status emoji
    status_emoji = {
        IntakeStatus.NEW: "🆕",
        IntakeStatus.PROCESSED: "✅",
        IntakeStatus.FAILED: "❌",
        IntakeStatus.ARCHIVED: "📦",
    }.get(item.status, "•")

    # Source indicator
    source_tag = "🎙" if item.source == "voice" else "✍️"

    # Content (truncated)
    content = item.content or "(no content)"
    content_display = _truncate(content)

    # Timestamp (short format)
    time_str = item.created_at.strftime("%m/%d %H:%M") if item.created_at else "?"

    return f"{index}. {status_emoji} {source_tag} {content_display}\n   _{time_str}_"


def _format_inbox(items: list[IntakeItem]) -> str:
    """Format the list of recent items into a message."""
    if not items:
        return (
            "📭 *Your inbox is empty*\n\n"
            "Send me a text or voice message and it'll show up here."
        )

    lines = [f"📥 *Recent Intake Items* ({len(items)} shown)\n"]
    for i, item in enumerate(items, start=1):
        lines.append(_format_item(item, i))

    lines.append("\n_Send a message to add a new item._")
    return "\n".join(lines)


HELP_TEXT = (
    "*Job-Star Bot Commands*\n\n"
    "📝 *Send a text message* — Capture a quick note or task idea\n"
    "🎙 *Send a voice message* — Speak your thought, I'll transcribe it\n\n"
    "*Commands:*\n"
    "/inbox — Show your recent unprocessed items (last 5)\n"
    "/recent — Same as /inbox\n"
    "/help — Show this help message\n"
    "/start — Check if you're authorized\n\n"
    "_Your inputs are queued for triage and review._"
)


async def help_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /help — list available commands."""
    if not update.effective_message:
        return

    logger.debug(
        "help_command from user=%s",
        update.effective_user.id if update.effective_user else "unknown",
    )
    await update.effective_message.reply_text(
        HELP_TEXT, parse_mode="Markdown"
    )


async def inbox_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle /inbox and /recent — show recent unprocessed intake items."""
    if not update.effective_message or not update.effective_user:
        return

    user_id = update.effective_user.id
    logger.debug("inbox_command from user=%s", user_id)

    # Get the queue from context (set up in bot initialization)
    queue: Optional[IntakeQueue] = context.bot_data.get("intake_queue")
    if queue is None:
        logger.error("intake_queue not found in bot_data")
        await update.effective_message.reply_text(
            "⚠️ Internal error: intake queue not available."
        )
        return

    try:
        # Fetch recent items for this user, prioritizing unprocessed
        items = await queue.get_recent_for_user(
            user_id=str(user_id),
            limit=RECENT_ITEM_LIMIT,
            unprocessed_only=True,
        )
    except Exception:
        logger.exception("Failed to fetch recent items for user=%s", user_id)
        await update.effective_message.reply_text(
            "⚠️ Could not retrieve your inbox. Please try again later."
        )
        return

    message_text = _format_inbox(items)
    await update.effective_message.reply_text(
        message_text, parse_mode="Markdown"
    )


def get_command_handlers() -> list[CommandHandler]:
    """Return all command handlers for registration with the dispatcher."""
    return [
        CommandHandler("help", help_command),
        CommandHandler("start", help_command),  # /start also shows help
        CommandHandler("inbox", inbox_command),
        CommandHandler("recent", inbox_command),  # alias
    ]
