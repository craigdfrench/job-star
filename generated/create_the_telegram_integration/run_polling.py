"""Thin CLI entrypoint that starts the Telegram bot in long-polling mode.

Delegates to :mod:`services.telegram_bot.bot` so there is a single source of
truth for the polling loop and shutdown handling.

Usage:
    python -m services.telegram_bot.run_polling
"""

from __future__ import annotations

from services.telegram_bot.bot import run

if __name__ == "__main__":
    run()


// --- DUPLICATE BLOCK ---

"""Entry point for running the Telegram bot in polling mode.

Usage:
    python -m services.telegram_bot.run_polling

Set environment variables via .env or shell:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_ALLOWED_USER_IDS (comma-separated)
"""

from __future__ import annotations

import asyncio
import logging
import sys

from telegram.ext import ApplicationBuilder

from .config import BotConfig
from .intake_queue import IntakeQueue
from .handlers import get_message_handlers
from .commands import get_command_handlers

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def build_application(config: BotConfig):
    """Build and configure the Telegram Application."""
    application = (
        ApplicationBuilder()
        .token(config.bot_token)
        .build()
    )

    # Create shared intake queue
    queue = IntakeQueue(db_path=config.db_path)

    # Store shared resources in bot_data for handlers to access
    application.bot_data["intake_queue"] = queue
    application.bot_data["config"] = config

    # Register command handlers (/help, /inbox, /recent, /start)
    for handler in get_command_handlers():
        application.add_handler(handler)
        logger.info("Registered command handler: %s", handler.command)

    # Register message handlers (text, voice)
    for handler in get_message_handlers():
        application.add_handler(handler)
        logger.info("Registered message handler")

    return application


async def main() -> None:
    """Run the bot in polling mode."""
    config = BotConfig.from_env()

    logger.info("Starting Telegram bot in polling mode...")
    logger.info("Allowed users: %s", config.allowed_user_ids)

    application = build_application(config)

    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    logger.info("Bot is running. Press Ctrl+C to stop.")

    try:
        # Keep running until interrupted
        stop_event = asyncio.Event()
        await stop_event.wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down...")
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()
        logger.info("Bot stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)


// --- DUPLICATE BLOCK ---

"""Logging configuration for the Telegram bot service.

Provides a configured logger with structured output suitable for debugging
and monitoring the intake channel.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import Optional


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(
    level: Optional[str] = None,
    log_file: Optional[str] = None,
    max_bytes: int = 5 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """Configure and return the root logger for the telegram bot service.

    Args:
        level: Logging level string (DEBUG, INFO, WARNING, ERROR).
               Defaults to env var TELEGRAM_BOT_LOG_LEVEL or INFO.
        log_file: Optional path to a log file. If provided, logs are written
                  here with rotation. Defaults to env var TELEGRAM_BOT_LOG_FILE
                  or None (stdout only).
        max_bytes: Max size of each log file before rotation.
        backup_count: Number of rotated log files to keep.

    Returns:
        Configured root logger for 'services.telegram_bot'.
    """
    level = level or os.getenv("TELEGRAM_BOT_LOG_LEVEL", "INFO")
    log_file = log_file or os.getenv("TELEGRAM_BOT_LOG_FILE")

    numeric_level = getattr(logging, level.upper(), logging.INFO)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # Configure the package-level logger
    logger = logging.getLogger("services.telegram_bot")
    logger.setLevel(numeric_level)
    logger.handlers.clear()
    logger.propagate = False

    # Console handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(numeric_level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Optional file handler with rotation
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """Get a child logger under the telegram_bot namespace.

    Args:
        name: Submodule name (e.g., 'handlers', 'transcribe').

    Returns:
        Logger instance named 'services.telegram_bot.<name>'.
    """
    return logging.getLogger(f"services.telegram_bot.{name}")


// --- DUPLICATE BLOCK ---

"""Run the Telegram bot in long-polling mode.

This is the entry point for the persistent bot process.
"""
from __future__ import annotations

import signal
import sys

from telegram.ext import ApplicationBuilder

from .config import get_config
from .handlers import register_handlers
from .health import HealthCheck
from .logger import configure_logging, get_logger

log = get_logger("run_polling")


def main() -> None:
    """Start the bot in polling mode."""
    configure_logging()
    log.info("Initializing Telegram bot...")

    config = get_config()
    log.info("Allowed users: %s", config.allowed_user_ids or "ALL")

    app = ApplicationBuilder().token(config.bot_token).build()

    # Health check heartbeat
    health = HealthCheck(interval_seconds=30.0)
    register_handlers(app, health)

    # Graceful shutdown
    def shutdown(signum, frame) -> None:  # type: ignore
        log.info("Received signal %s, shutting down...", signum)
        health.maybe_write(force=True)
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    log.info("Starting polling loop. Press Ctrl+C to stop.")
    health.maybe_write(force=True)
    app.run_polling()


if __name__ == "__main__":
    main()


// --- DUPLICATE BLOCK ---

# tests/telegram/__init__.py
"""Test suite for the Telegram intake bot."""
