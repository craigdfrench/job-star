"""Transcription service wrapper.

Supports OpenAI Whisper API (preferred) and local whisper fallback.
Designed to be swapped/mocked for testing.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

logger = logging.getLogger(__name__)


class TranscriptionError(Exception):
    """Raised when transcription fails."""


class TranscriptionBackend(Protocol):
    def transcribe(self, audio_path: Path, *, language: Optional[str] = None) -> str:
        ...


@dataclass
class WhisperAPIBackend:
    """Calls OpenAI Whisper API (audio.transcriptions.create)."""
    api_key: str
    model: str = "whisper-1"
    timeout: float = 60.0

    def transcribe(self, audio_path: Path, *, language: Optional[str] = None) -> str:
        try:
            from openai import OpenAI
        except ImportError as e:
            raise TranscriptionError("openai package not installed") from e

        client = OpenAI(api_key=self.api_key, timeout=self.timeout)
        try:
            with open(audio_path, "rb") as audio_file:
                kwargs = {"model": self.model, "file": audio_file}
                if language:
                    kwargs["language"] = language
                result = client.audio.transcriptions.create(**kwargs)
                # result may be a str or an object with .text
                text = result.text if hasattr(result, "text") else str(result)
                return text.strip()
        except Exception as e:
            logger.exception("Whisper API transcription failed")
            raise TranscriptionError(f"Whisper API failed: {e}") from e


@dataclass
class LocalWhisperBackend:
    """Uses local openai-whisper package for offline transcription."""
    model_name: str = "base"

    def transcribe(self, audio_path: Path, *, language: Optional[str] = None) -> str:
        try:
            import whisper
        except ImportError as e:
            raise TranscriptionError("whisper package not installed") from e

        try:
            model = whisper.load_model(self.model_name)
            kwargs = {}
            if language:
                kwargs["language"] = language
            result = model.transcribe(str(audio_path), **kwargs)
            text = result.get("text", "").strip()
            return text
        except Exception as e:
            logger.exception("Local whisper transcription failed")
            raise TranscriptionError(f"Local whisper failed: {e}") from e


def get_transcription_backend() -> TranscriptionBackend:
    """Factory: prefer Whisper API if key present, else local whisper."""
    api_key = os.getenv("OPENAI_API_KEY")
    if api_key:
        model = os.getenv("WHISPER_API_MODEL", "whisper-1")
        return WhisperAPIBackend(api_key=api_key, model=model)

    if os.getenv("WHISPER_LOCAL_MODEL"):
        return LocalWhisperBackend(model_name=os.getenv("WHISPER_LOCAL_MODEL", "base"))

    raise TranscriptionError(
        "No transcription backend configured. Set OPENAI_API_KEY or install local whisper."
    )


def transcribe_audio(audio_path: Path, *, language: Optional[str] = None) -> str:
    """Convenience function using the default backend."""
    backend = get_transcription_backend()
    return backend.transcribe(audio_path, language=language)


// --- DUPLICATE BLOCK ---

"""Voice message intake handler.

Receives voice messages, downloads the audio file from Telegram,
transcribes it, creates an intake item, and sends a confirmation
with the transcription back to the user for verification.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

from telegram import Update, Voice
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from .intake_queue import IntakeQueue
from .models import IntakeItem, IntakeSource, IntakeStatus
from .transcribe import TranscriptionError, transcribe_audio

logger = logging.getLogger(__name__)

# Telegram voice messages are OGG Opus. Whisper handles ogg/opus fine.
SUPPORTED_FORMATS = {"ogg", "oga", "mp3", "m4a", "wav", "webm"}

CONFIRM_TEMPLATE = (
    "🎙️ Voice intake captured.\n\n"
    "Transcription:\n\"{text}\"\n\n"
    "Reply with corrections, or send /keep to accept as-is."
)


async def _download_voice(
    voice: Voice, context: ContextTypes.DEFAULT_TYPE, dest_dir: Path
) -> Path:
    """Download the voice file from Telegram to dest_dir. Returns local path."""
    file = await context.bot.get_file(voice.file_id)
    # Preserve extension based on mime_path if available, default to .ogg
    ext = ".ogg"
    if voice.mime_type and "mpeg" in voice.mime_type:
        ext = ".mp3"
    elif voice.mime_type and "m4a" in voice.mime_type:
        ext = ".m4a"

    dest = dest_dir / f"voice_{voice.file_unique_id}{ext}"
    await file.download_to_drive(custom_path=str(dest))
    return dest


def _extension_ok(path: Path) -> bool:
    return path.suffix.lower().lstrip(".") in SUPPORTED_FORMATS


async def handle_voice_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Process an incoming voice message into an intake item."""
    if not update.message or not update.message.voice:
        return

    message = update.message
    user = message.from_user
    if not user:
        return

    chat_id = message.chat_id
    voice = message.voice

    logger.info(
        "Voice message received user_id=%s chat_id=%s duration=%ss",
        user.id, chat_id, voice.duration,
    )

    # Acknowledge with typing/upload action so user knows it's being processed
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    queue: IntakeQueue = context.bot_data["intake_queue"]

    tmp_dir = Path(tempfile.mkdtemp(prefix="jobstar_voice_"))
    audio_path: Optional[Path] = None

    try:
        # 1. Download
        try:
            audio_path = await _download_voice(voice, context, tmp_dir)
        except Exception as e:
            logger.exception("Failed to download voice file")
            await message.reply_text(
                "⚠️ I couldn't download your voice message. Please try again."
            )
            return

        # 2. Format check
        if not _extension_ok(audio_path):
            logger.warning("Unsupported audio format: %s", audio_path.suffix)
            await message.reply_text(
                f"⚠️ Unsupported audio format ({audio_path.suffix}). "
                f"Supported: {', '.join(sorted(SUPPORTED_FORMATS))}."
            )
            return

        # 3. Transcribe
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        try:
            text = transcribe_audio(audio_path)
        except TranscriptionError as e:
            logger.exception("Transcription failed")
            await message.reply_text(
                "⚠️ I couldn't transcribe your voice message. "
                "Please try sending text instead.\n\nError: " + str(e)
            )
            return

        if not text:
            await message.reply_text(
                "⚠️ Transcription came back empty. Was the message silent? "
                "Please try again."
            )
            return

        # 4. Create intake item
        item = IntakeItem(
            source=IntakeSource.VOICE,
            raw_text=text,
            user_id=str(user.id),
            chat_id=str(chat_id),
            message_id=str(message.message_id),
            status=IntakeStatus.PENDING_REVIEW,
            metadata={
                "voice_duration_sec": voice.duration,
                "voice_file_id": voice.file_id,
                "voice_file_unique_id": voice.file_unique_id,
                "transcription_backend": os.getenv("TRANSCRIPTION_BACKEND", "auto"),
            },
        )
        item_id = await queue.enqueue(item)
        logger.info("Voice intake enqueued item_id=%s", item_id)

        # 5. Confirmation with transcription for verification
        await message.reply_text(
            CONFIRM_TEMPLATE.format(text=text),
            reply_to_message_id=message.message_id,
        )

    except Exception as e:
        logger.exception("Unexpected error handling voice message")
        await message.reply_text(
            "⚠️ Something went wrong processing your voice message. "
            "Please try again or send text."
        )
    finally:
        # Cleanup temp audio file
        if audio_path and audio_path.exists():
            try:
                audio_path.unlink()
            except OSError:
                logger.warning("Could not delete temp file %s", audio_path)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass
