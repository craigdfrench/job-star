"""Job-Star Telegram intake bot.

Wraps the Telegram Bot API (long-polling or webhook) and routes incoming
messages (text / voice / photo) through the intake pipeline:

    update -> handlers -> intake_queue -> (triage / storage)

The bot is intentionally framework-light: it uses `python-telegram-bot` v20+
(async) so it slots cleanly into the rest of the Job-Star async services.
"""

from __future__ import annotations

import logging
from typing import Optional

from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from .config import TelegramBotConfig
from .handlers import Handlers
from .intake_queue import IntakeQueue

log = logging.getLogger(__name__)


class TelegramBot:
    """Runnable Telegram intake bot.

    Parameters
    ----------
    config:
        Resolved configuration (token, allowed user ids, etc.).
    queue:
        Shared intake queue used to forward parsed updates downstream.
    """

    def __init__(
        self,
        config: TelegramBotConfig,
        queue: Optional[IntakeQueue] = None,
    ) -> None:
        self.config = config
        self.queue = queue or IntakeQueue()
        self.handlers = Handlers(config=config, queue=self.queue)
        self._app: Optional[Application] = None

    # ------------------------------------------------------------------ build

    def build(self) -> Application:
        """Construct (but do not start) the Telegram Application."""
        if not self.config.bot_token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN is not set; cannot build Telegram bot."
            )

        self._app = (
            ApplicationBuilder()
            .token(self.config.bot_token)
            .concurrent_updates(True)
            .build()
        )

        # Commands
        self._app.add_handler(CommandHandler("start", self.handlers.on_start))
        self._app.add_handler(CommandHandler("help", self.handlers.on_help))
        self._app.add_handler(CommandHandler("status", self.handlers.on_status))

        # Intake: voice notes, text, and photos (whiteboards / screenshots)
        self._app.add_handler(
            MessageHandler(filters.VOICE, self.handlers.on_voice)
        )
        self._app.add_handler(
            MessageHandler(filters.AUDIO, self.handlers.on_audio)
        )
        self._app.add_handler(
            MessageHandler(filters.PHOTO, self.handlers.on_photo)
        )
        self._app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.handlers.on_text)
        )

        return self._app

    # ------------------------------------------------------------------ run

    async def run_polling(self) -> None:
        """Start the bot in long-polling mode (dev / single-instance)."""
        app = self.build()
        log.info("Starting Telegram bot in polling mode")
        await app.initialize()
        await app.start()
        await app.updater.start_polling(drop_pending_updates=True)
        # Keep running until stopped externally
        try:
            await app.updater.stop()  # pragma: no cover - normally unreachable
        finally:
            await app.stop()
            await app.shutdown()

    def run(self) -> None:
        """Synchronous entry point — builds and runs the bot via polling."""
        import asyncio

        asyncio.run(self.run_polling())


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = TelegramBotConfig.from_env()
    TelegramBot(config=cfg).run()


if __name__ == "__main__":
    main()


// --- DUPLICATE BLOCK ---

"""Base handler that logs incoming messages without processing them.

At this bootstrap stage we only want to prove the pipe works end-to-end:
Telegram -> our process -> structured log line. Real intake logic
(transcription, queueing, triage) is wired in later steps on top of this.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Optional

from telegram import Update
from telegram.ext import Application, BaseHandler, ContextTypes

logger = logging.getLogger("jobstar.telegram.intake")


def _summarize_update(update: Update) -> dict[str, Any]:
    """Reduce an Update to a small, log-safe summary.

    We avoid dumping the entire payload (which can be large for voice/media)
    and instead capture the fields we'll need for downstream intake routing.
    """
    msg = update.effective_message
    user = update.effective_user
    chat = update.effective_chat

    summary: dict[str, Any] = {
        "update_id": update.update_id,
        "ts": time.time(),
    }

    if user is not None:
        summary["user"] = {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "is_bot": user.is_bot,
        }

    if chat is not None:
        summary["chat"] = {"id": chat.id, "type": chat.type}

    if msg is not None:
        summary["message"] = {
            "id": msg.message_id,
            "date": msg.date.isoformat() if msg.date else None,
            "has_text": bool(msg.text),
            "has_voice": bool(getattr(msg, "voice", None)),
            "has_audio": bool(getattr(msg, "audio", None)),
            "has_photo": bool(getattr(msg, "photo", None)),
            "has_document": bool(getattr(msg, "document", None)),
            "text_len": len(msg.text) if msg.text else 0,
        }
        # Keep a short text preview for debugging; never log full PII payloads.
        if msg.text:
            summary["message"]["text_preview"] = msg.text[:80]

    return summary


def log_incoming(update: Update, stage: str = "received") -> None:
    """Emit a structured log line for an incoming update."""
    logger.info(
        "telegram.update.%s",
        stage,
        extra={"telegram": _summarize_update(update)},
    )


class BaseLoggingHandler(BaseHandler):
    """Handler base that logs every matching update and then no-ops.

    Subclasses can override :meth:`process` to add real behavior while
    inheriting the logging + error handling here.
    """

    def __init__(
        self,
        handler_type: type[BaseHandler],
        callback: Optional[Callable[[Update, ContextTypes.DEFAULT_TYPE], Awaitable[Any]]] = None,
        **handler_kwargs: Any,
    ) -> None:
        self._callback = callback
        self._inner = handler_type(self._wrapped, **handler_kwargs)
        super().__init__(self._wrapped, block=handler_kwargs.get("block", True))

    async def _wrapped(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        log_incoming(update, stage="received")
        try:
            if self._callback is not None:
                await self._callback(update, context)
        except Exception:
            logger.exception("telegram.handler.error", extra={"update_id": update.update_id})
            # Swallow at this stage so the polling loop stays alive.
            return
        log_incoming(update, stage="acknowledged")

    def check_update(self, update: object) -> bool:
        # Delegate to the wrapped handler's matching logic.
        return self._inner.check_update(update)  # type: ignore[arg-type]

    def register(self, application: Application, group: int = 0) -> None:
        # Register the inner handler so PTB routes correctly.
        self._inner.register(application, group)
