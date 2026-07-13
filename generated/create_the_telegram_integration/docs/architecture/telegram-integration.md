# ADR: Telegram Integration Architecture

**Status:** Proposed
**Date:** 2025
**Domain:** meta
**Step:** Define Telegram Integration Architecture

## Context

Job-Star needs a zero-friction mobile intake channel. Telegram is chosen because:
- Native voice notes (low-friction capture of thoughts on the go)
- Text input with markdown support
- Cross-platform mobile clients
- Mature bot API, no auth plumbing required (Telegram handles identity)
- Free, no usage fees

The bot must accept both voice and text, normalize them into a common
`IntakeItem` shape, and hand them off to downstream processing
(transcription, classification, storage) without coupling to those
systems.

## Decision

### 1. Tech stack: Python + `python-telegram-bot`

- Aligns with the Python core of Job-Star.
- `python-telegram-bot` (v20+, async) is the most mature Python wrapper,
  supports both long polling and webhooks with the same handler code.
- Async-first, which matters once we add Whisper transcription calls.

### 2. Transport: Long polling for bootstrap, webhook-ready

**Bootstrap:** Long polling (`Application.run_polling()`).
- No public ingress / TLS / reverse proxy required.
- Works locally and behind NAT.
- Sufficient for a single-user personal system.

**Production path (deferred):** Switch to webhooks via FastAPI mount.
- The `IntakeHandler` class is transport-agnostic; only the runner
  (`run_polling.py` vs `run_webhook.py`) changes.
- Webhook mode will be enabled when we deploy to a host with a public
  endpoint and need lower latency / multiple workers.

### 3. Service structure


// --- DUPLICATE BLOCK ---

pip install -r services/telegram_bot/requirements.txt


// --- DUPLICATE BLOCK ---

cp .env.example .env
# set TELEGRAM_BOT_TOKEN and OPENAI_API_KEY


// --- DUPLICATE BLOCK ---

python -m services.telegram_bot.run_polling
# or
python -m services.telegram_bot.bot


// --- DUPLICATE BLOCK ---

from services.telegram_bot.bot import TelegramBot
from services.telegram_bot.config import TelegramBotConfig

bot = TelegramBot(TelegramBotConfig.from_env())
bot.run()


// --- DUPLICATE BLOCK ---

"""Job-Star Telegram intake service."""

from .bot import TelegramBot
from .config import TelegramBotConfig
from .handlers import Handlers
from .intake_queue import IntakeQueue
from .models import IntakeItem, IntakeType

__all__ = [
    "TelegramBot",
    "TelegramBotConfig",
    "Handlers",
    "IntakeQueue",
    "IntakeItem",
    "IntakeType",
]


// --- DUPLICATE BLOCK ---

## Voice Message Intake

Flow:
1. User sends a voice note to the bot.
2. `voice_handler.handle_voice_message` downloads the OGG/Opus file from Telegram.
3. The file is passed to `transcribe.transcribe_audio`, which selects a backend:
   - `WhisperAPIBackend` if `OPENAI_API_KEY` is set (preferred).
   - `LocalWhisperBackend` if `WHISPER_LOCAL_MODEL` is set (offline fallback).
4. The transcribed text becomes an `IntakeItem` with `source=VOICE` and
   `status=PENDING_REVIEW`.
5. The bot replies with the transcription so the user can verify accuracy.
   - `/keep` accepts the most recent pending item.
   - A follow-up text message can be used to correct it (future: edit flow).

Error handling:
- Download failure → user-facing error, no item created.
- Unsupported format → user-facing error listing supported formats.
- Transcription failure → user-facing error, no item created.
- Empty transcription → user-facing prompt to retry.

Temp audio files are written to a per-message temp dir and deleted after
processing.
