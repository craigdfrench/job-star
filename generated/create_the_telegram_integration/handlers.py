"""Telegram update handlers.

Each handler:
  1. Checks the allowlist.
  2. Normalizes the update into an IntakeItem.
  3. Enqueues it.
  4. Replies with a lightweight confirmation.

Handlers are transport-agnostic: they receive a `telegram.Update` and a
`Bot`-like context. The runner (polling or webhook) is responsible for
routing updates here.
"""
from __future__ import annotations

import logging
from typing import Any

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from services.telegram_bot.config import TelegramBotConfig
from services.telegram_bot.intake_queue import IntakeQueue
from services.telegram_bot.models import IntakeItem

log = logging.getLogger(__name__)


def _make_intake_item(update: Update) -> IntakeItem:
    msg = update.message or update.edited_message
    item = IntakeItem(
        source="telegram",
        channel_chat_id=msg.chat_id,
        sender_user_id=msg.from_user.id,
        message_id=msg.message_id,
    )
    if msg.voice:
        item.voice_file_id = msg.voice.file_id
    elif msg.text:
        item.raw_text = msg.text
    return item


def _allowed(update: Update, config: TelegramBotConfig) -> bool:
    msg = update.message or update.edited_message
    return msg is not None and msg.chat_id in config.allowed_chat_ids


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: TelegramBotConfig = context.bot_data["config"]
    if not _allowed(update, config):
        return
    await update.message.reply_text(
        "Job-Star intake ready. Send text or a voice note."
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: TelegramBotConfig = context.bot_data["config"]
    queue: IntakeQueue = context.bot_data["queue"]
    if not _allowed(update, config):
        log.warning("Ignored text from disallowed chat %s", update.message.chat_id)
        return
    item = _make_intake_item(update)
    await queue.enqueue(item)
    await update.message.reply_text("Captured.", quote=False)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    config: TelegramBotConfig = context.bot_data["config"]
    queue: IntakeQueue = context.bot_data["queue"]
    if not _allowed(update, config):
        log.warning("Ignored voice from disallowed chat %s", update.message.chat_id)
        return
    item = _make_intake_item(update)
    await queue.enqueue(item)
    await update.message.reply_text("Voice received — transcribing…", quote=False)


def register_handlers(app: Application, config: TelegramBotConfig, queue: IntakeQueue) -> None:
    app.bot_data["config"] = config
    app.bot_data["queue"] = queue
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))


// --- DUPLICATE BLOCK ---

"""Message handlers for the Telegram intake bot.

Every handler is wrapped by the AuthGuard so unauthorized users are rejected
before any intake processing occurs.
"""
from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from services.telegram_bot.auth import AuthGuard, build_authorized_handler
from services.telegram_bot.config import TelegramBotConfig
from services.telegram_bot.intake_queue import IntakeQueue
from services.telegram_bot.transcribe import transcribe_voice

logger = logging.getLogger(__name__)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — greet the user and explain how to use the bot."""
    user = update.effective_user
    name = user.first_name if user else "there"
    await update.message.reply_text(
        f"Hi {name}! 👋\n\n"
        "I'm your job-search intake assistant. Send me:\n"
        "• Text notes about jobs, leads, or reflections\n"
        "• Voice memos (I'll transcribe them)\n\n"
        "Everything gets queued for triage. Use /help for more."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/help — show available commands."""
    await update.message.reply_text(
        "Commands:\n"
        "/start — greeting\n"
        "/help — this message\n"
        "/status — show pending intake count\n\n"
        "Otherwise, just send text or voice notes."
    )


async def cmd_status(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    queue: IntakeQueue,
) -> None:
    """/status — report how many items are pending in the intake queue."""
    count = queue.pending_count()
    await update.message.reply_text(
        f"📋 Intake queue: {count} item(s) pending triage."
    )


async def handle_text(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    queue: IntakeQueue,
) -> None:
    """Handle a plain-text message: enqueue it for triage."""
    text = update.message.text
    user = update.effective_user
    await queue.enqueue(
        source="telegram",
        kind="text",
        user_id=user.id if user else None,
        chat_id=update.effective_chat.id,
        content=text,
    )
    await update.message.reply_text("✅ Saved to your intake queue.")


async def handle_voice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    queue: IntakeQueue,
) -> None:
    """Handle a voice message: download, transcribe, enqueue."""
    voice = update.message.voice
    user = update.effective_user
    # Download the voice file
    file = await context.bot.get_file(voice.file_id)
    local_path = f"data/voice_{update.update_id}.ogg"
    await file.download_to_drive(local_path)
    # Transcribe
    try:
        transcript = transcribe_voice(local_path, model_name="base")
    except Exception as exc:  # noqa: BLE001
        logger.error("Transcription failed: %s", exc)
        await update.message.reply_text(
            "⚠️ Couldn't transcribe that voice note. Please try again or send text."
        )
        return
    await queue.enqueue(
        source="telegram",
        kind="voice",
        user_id=user.id if user else None,
        chat_id=update.effective_chat.id,
        content=transcript,
        meta={"audio_path": local_path, "duration_s": voice.duration},
    )
    await update.message.reply_text(
        f"✅ Transcribed and saved:\n\n{transcript[:200]}"
    )


def build_application(config: TelegramBotConfig) -> Application:
    """Build the Telegram Application with auth guards wired in."""
    guard = config.make_auth_guard()
    queue = IntakeQueue(config.intake_queue_path)

    if not guard.allowed_user_ids:
        logger.warning(
            "Starting bot with EMPTY allowlist — all users will be rejected. "
            "Set ALLOWED_USER_IDS to allow yourself."
        )
    else:
        logger.info(
            "Auth guard active. Allowed user IDs: %s",
            sorted(guard.allowed_user_ids),
        )

    app = (
        ApplicationBuilder()
        .token(config.bot_token)
        .build()
    )

    # Wrap each handler with the auth guard.
    app.add_handler(CommandHandler("start", build_authorized_handler(guard, cmd_start)))
    app.add_handler(CommandHandler("help", build_authorized_handler(guard, cmd_help)))
    app.add_handler(
        CommandHandler(
            "status",
            build_authorized_handler(guard, lambda u, c: cmd_status(u, c, queue)),
        )
    )
    app.add_handler(
        MessageHandler(
            filters.VOICE,
            build_authorized_handler(guard, lambda u, c: handle_voice(u, c, queue)),
        )
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            build_authorized_handler(guard, lambda u, c: handle_text(u, c, queue)),
        )
    )

    return app


// --- DUPLICATE BLOCK ---

"""Telegram update handlers and dispatcher wiring."""
from __future__ import annotations

import logging
from typing import Any, Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .config import BotConfig
from .intake_queue import IntakeQueue
from .models import IntakeSource
from .text_handler import TextIntakeHandler

logger = logging.getLogger(__name__)


class Handlers:
    """Bundles handler callables with shared dependencies."""

    def __init__(self, config: BotConfig, queue: IntakeQueue) -> None:
        self.config = config
        self.queue = queue
        self.text = TextIntakeHandler(config=config, queue=queue)

    # ------------------------------------------------------------------ #
    # Commands
    # ------------------------------------------------------------------ #
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or not update.effective_user:
            return
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                "👋 Hi! I'm Job-Star's intake bot.\n\n"
                "Send me anything — text, voice notes, links — and I'll capture it "
                "for triage. No formatting required."
            ),
        )

    async def help_(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat:
            return
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                "Just send a message. Text or voice. I'll capture it.\n\n"
                "/start — intro\n/help — this message"
            ),
        )

    # ------------------------------------------------------------------ #
    # Text messages
    # ------------------------------------------------------------------ #
    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_chat or not update.effective_user or not update.message or not update.message.text:
            return

        user_id = update.effective_user.id
        chat_id = update.effective_chat.id

        # Authorization gate (defined in a previous step).
        if not self.config.is_authorized(user_id):
            logger.warning("Unauthorized user_id=%s attempted intake", user_id)
            await context.bot.send_message(
                chat_id=chat_id,
                text="🚫 You're not authorized to use this bot.",
            )
            return

        result = self.text.handle_text(
            user_id=user_id,
            chat_id=chat_id,
            message_id=update.message.message_id,
            text=update.message.text,
            username=update.effective_user.username,
            raw=update.message.to_dict() if hasattr(update.message, "to_dict") else None,
        )

        if result.confirmation:
            await context.bot.send_message(chat_id=chat_id, text=result.confirmation)

    # ------------------------------------------------------------------ #
    # Registration
    # ------------------------------------------------------------------ #
    def register(self, application: Application) -> None:
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("help", self.help_))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text)
        )


def build_application(config: BotConfig, queue: IntakeQueue) -> Application:
    """Construct a Telegram Application with handlers wired up."""
    application = (
        Application.builder()
        .token(config.bot_token)
        .build()
    )
    handlers = Handlers(config=config, queue=queue)
    handlers.register(application)
    return application


// --- DUPLICATE BLOCK ---

"""Telegram bot handlers registration.

Exposes a function to build the Application with all handlers registered.
"""
from __future__ import annotations

import logging

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from .config import get_config
from .intake_queue import IntakeQueue
from .voice_handler import handle_voice_message

logger = logging.getLogger(__name__)


async def _cmd_start(update, context) -> None:
    await update.message.reply_text(
        "Job-Star intake bot ready. Send me text or voice notes — "
        "I'll capture them for triage."
    )


async def _cmd_keep(update, context) -> None:
    """Mark the most recent pending-review item as accepted."""
    queue: IntakeQueue = context.bot_data["intake_queue"]
    user_id = str(update.message.from_user.id)
    item = await queue.latest_pending_for_user(user_id)
    if not item:
        await update.message.reply_text("Nothing pending to keep.")
        return
    await queue.mark_accepted(item.id)
    await update.message.reply_text(f"✅ Kept: \"{item.raw_text[:80]}\"")


async def _handle_text(update, context) -> None:
    """Text intake handler (from previous step)."""
    from .handlers import handle_text_message  # lazy to avoid cycle if needed
    await handle_text_message(update, context)


def build_application() -> Application:
    cfg = get_config()
    queue = IntakeQueue(storage_path=cfg.intake_storage_path)

    app = (
        ApplicationBuilder()
        .token(cfg.bot_token)
        .build()
    )

    app.bot_data["intake_queue"] = queue

    app.add_handler(CommandHandler("start", _cmd_start))
    app.add_handler(CommandHandler("keep", _cmd_keep))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice_message))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_text))

    return app


// --- DUPLICATE BLOCK ---

"""Telegram message handlers for text and voice intake.

These handlers process incoming text and voice messages,
create IntakeItem objects, and enqueue them for downstream processing.
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram import Update
from telegram.ext import (
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters,
)

from .intake_queue import IntakeQueue
from .models import IntakeItem, IntakeSource, IntakeStatus
from .transcribe import transcribe_voice

logger = logging.getLogger(__name__)


def _check_authorized(update: Update, config) -> bool:
    """Check if the user is in the allowed list."""
    if not update.effective_user:
        return False
    user_id = update.effective_user.id
    if not config.allowed_user_ids:
        return True  # No restriction if not configured
    return user_id in config.allowed_user_ids


async def handle_text_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle incoming text messages — create intake item."""
    if not update.effective_message or not update.effective_user:
        return

    config = context.bot_data.get("config")
    if config and not _check_authorized(update, config):
        logger.warning("Unauthorized user: %s", update.effective_user.id)
        await update.effective_message.reply_text(
            "⛔ You are not authorized to use this bot."
        )
        return

    text = update.effective_message.text
    user_id = str(update.effective_user.id)

    logger.info("Text intake from user=%s: %s", user_id, text[:50])

    queue: Optional[IntakeQueue] = context.bot_data.get("intake_queue")
    if queue is None:
        logger.error("intake_queue not found in bot_data")
        return

    item = IntakeItem(
        user_id=user_id,
        content=text,
        source=IntakeSource.TEXT,
        status=IntakeStatus.NEW,
    )
    await queue.enqueue(item)

    await update.effective_message.reply_text(
        f"✅ Captured! Use /inbox to review your recent items."
    )


async def handle_voice_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle incoming voice messages — transcribe and create intake item."""
    if not update.effective_message or not update.effective_user:
        return

    config = context.bot_data.get("config")
    if config and not _check_authorized(update, config):
        logger.warning("Unauthorized user: %s", update.effective_user.id)
        await update.effective_message.reply_text(
            "⛔ You are not authorized to use this bot."
        )
        return

    voice = update.effective_message.voice
    if not voice:
        return

    user_id = str(update.effective_user.id)
    logger.info("Voice intake from user=%s, duration=%ss", user_id, voice.duration)

    # Acknowledge receipt
    await update.effective_message.reply_text("🎙 Transcribing your voice message...")

    # Download and transcribe
    try:
        file = await context.bot.get_file(voice.file_id)
        audio_bytes = await file.download_as_bytearray()

        transcription = await transcribe_voice(bytes(audio_bytes))

        if not transcription:
            await update.effective_message.reply_text(
                "⚠️ Could not transcribe the voice message. Please try again."
            )
            return
    except Exception:
        logger.exception("Voice transcription failed for user=%s", user_id)
        await update.effective_message.reply_text(
            "⚠️ Transcription failed. Please try sending a text message."
        )
        return

    # Create and enqueue intake item
    queue: Optional[IntakeQueue] = context.bot_data.get("intake_queue")
    if queue is None:
        logger.error("intake_queue not found in bot_data")
        return

    item = IntakeItem(
        user_id=user_id,
        content=transcription,
        source=IntakeSource.VOICE,
        status=IntakeStatus.NEW,
        raw_file_id=voice.file_id,
    )
    await queue.enqueue(item)

    await update.effective_message.reply_text(
        f"✅ Transcribed: \"{transcription[:100]}\"\n\nUse /inbox to review."
    )


def get_message_handlers() -> list[MessageHandler]:
    """Return message handlers for registration with the dispatcher."""
    return [
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message),
        MessageHandler(filters.VOICE, handle_voice_message),
    ]


// --- DUPLICATE BLOCK ---

"""Message handlers for the Telegram intake bot."""
from __future__ import annotations

from typing import Optional

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from .config import get_config
from .intake_queue import IntakeQueue
from .logger import get_logger
from .models import IntakeItem, IntakeSource
from .transcribe import transcribe_voice

log = get_logger("handlers")

# Module-level queue (initialized on first use)
_queue: Optional[IntakeQueue] = None
_health = None  # Will be set by register_handlers


def _get_queue() -> IntakeQueue:
    global _queue
    if _queue is None:
        _queue = IntakeQueue()
    return _queue


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    await update.message.reply_text(
        "👋 Hi! I'm Job-Star's intake bot.\n\n"
        "Send me text or voice messages and I'll capture them as intake items.\n"
        "Use /list to see recent items, /help for commands."
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await update.message.reply_text(
        "Commands:\n"
        "  /start — Welcome message\n"
        "  /help  — This help\n"
        "  /list  — Show recent intake items\n"
        "  /status — Bot health status\n\n"
        "Just send any text or voice message to create an intake item."
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /list — show recent intake items."""
    items = _get_queue().recent(limit=10)
    if not items:
        await update.message.reply_text("No intake items yet.")
        return
    lines = [f"• {item.summary()}" for item in items]
    await update.message.reply_text("Recent intake items:\n" + "\n".join(lines))


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status — basic health info."""
    if _health:
        await update.message.reply_text(
            f"✅ Bot is running.\nMessages processed: {_health._messages_processed}"
        )
    else:
        await update.message.reply_text("✅ Bot is running.")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming text messages — create an intake item."""
    config = get_config()
    user_id = update.effective_user.id if update.effective_user else 0

    if config.allowed_user_ids and user_id not in config.allowed_user_ids:
        log.warning("Unauthorized user %s attempted to send message", user_id)
        return

    text = update.message.text or ""
    log.info("Text intake from user %s: %s", user_id, text[:80])

    item = IntakeItem(
        source=IntakeSource.TEXT,
        content=text,
        telegram_user_id=user_id,
        telegram_message_id=update.message.message_id,
    )
    _get_queue().add(item)

    if _health:
        _health.record_message()

    await update.message.reply_text(f"✅ Captured: {item.summary()}")


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming voice messages — transcribe and create intake item."""
    config = get_config()
    user_id = update.effective_user.id if update.effective_user else 0

    if config.allowed_user_ids and user_id not in config.allowed_user_ids:
        log.warning("Unauthorized user %s attempted to send voice", user_id)
        return

    voice = update.message.voice
    if not voice:
        return

    log.info("Voice intake from user %s, duration=%ss", user_id, voice.duration)

    await update.message.reply_text("🎙️ Transcribing...")

    try:
        file = await context.bot.get_file(voice.file_id)
        audio_bytes = await file.download_as_bytearray()
        transcript = await transcribe_voice(bytes(audio_bytes))
    except Exception as exc:
        log.error("Voice transcription failed: %s", exc)
        await update.message.reply_text("❌ Could not transcribe voice message.")
        return

    log.info("Transcript: %s", transcript[:80])

    item = IntakeItem(
        source=IntakeSource.VOICE,
        content=transcript,
        telegram_user_id=user_id,
        telegram_message_id=update.message.message_id,
    )
    _get_queue().add(item)

    if _health:
        _health.record_message()

    await update.message.reply_text(f"✅ Captured: {item.summary()}")


def register_handlers(app: Application, health=None) -> None:
    """Register all command and message handlers with the application."""
    global _health
    _health = health

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))

    log.info("Handlers registered.")
