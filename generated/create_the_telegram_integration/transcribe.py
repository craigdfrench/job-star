"""Voice transcription via Whisper.

Bootstrap uses the local `whisper` package with the 'tiny' model for
speed. The function is async-safe by running inference in a thread
executor so the polling loop / event loop is not blocked.
"""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Optional

log = logging.getLogger(__name__)

_whisper_model = None  # lazily loaded


def _get_whisper_model(model_name: str):
    global _whisper_model
    if _whisper_model is None:
        import whisper  # heavy import, deferred
        _whisper_model = whisper.load_model(model_name)
    return _whisper_model


def _transcribe_sync(audio_path: str, model_name: str) -> Optional[str]:
    try:
        model = _get_whisper_model(model_name)
        result = model.transcribe(audio_path)
        text = result.get("text", "").strip()
        return text or None
    except Exception as e:  # noqa: BLE001
        log.error("Whisper transcription failed: %s", e)
        return None


async def transcribe_audio(audio_path: str, model_name: str = "tiny") -> Optional[str]:
    """Run Whisper transcription off the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None, functools.partial(_transcribe_sync, audio_path, model_name)
    )
