# Makefile — Job-Star build and run targets

.PHONY: run-telegram install-telegram

# --- Telegram Bot Service ---

install-telegram:
	pip install -r services/telegram_bot/requirements.txt 2>/dev/null || \
		pip install python-telegram-bot openai python-dotenv

run-telegram:
	@bash scripts/run_telegram_bot.sh

run-telegram-bg:
	@nohup bash scripts/run_telegram_bot.sh > /dev/null 2>&1 & \
	 echo "Telegram bot started in background (PID: $$!)"


// --- DUPLICATE BLOCK ---

"""Simple health check for the Telegram bot service.

Writes a heartbeat file periodically so external monitors can verify
the bot process is alive and processing messages.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from .logger import get_logger

log = get_logger("health")


class HealthCheck:
    """Writes a heartbeat file at a regular interval.

    The heartbeat file contains a JSON object with:
        - pid: process ID
        - timestamp: epoch seconds
        - messages_processed: count since start
        - last_message_at: epoch seconds of last message, or null
    """

    def __init__(
        self,
        heartbeat_path: Optional[str] = None,
        interval_seconds: float = 30.0,
    ) -> None:
        self.heartbeat_path = Path(
            heartbeat_path or "logs/telegram_bot_heartbeat.json"
        )
        self.interval = interval_seconds
        self._last_write = 0.0
        self._messages_processed = 0
        self._last_message_at: Optional[float] = None

    def record_message(self) -> None:
        """Call when a message is successfully processed."""
        self._messages_processed += 1
        self._last_message_at = time.time()

    def maybe_write(self, force: bool = False) -> None:
        """Write heartbeat if interval has elapsed (or force=True)."""
        now = time.time()
        if not force and (now - self._last_write) < self.interval:
            return

        import os

        data = {
            "pid": os.getpid(),
            "timestamp": now,
            "messages_processed": self._messages_processed,
            "last_message_at": self._last_message_at,
        }
        try:
            self.heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
            self.heartbeat_path.write_text(json.dumps(data, indent=2))
            self._last_write = now
        except OSError as exc:
            log.warning("Failed to write heartbeat: %s", exc)
