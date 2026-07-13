"""Environment-driven configuration for the Telegram bot.

All secrets come from environment variables; nothing is hardcoded.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


def _parse_int_list(raw: str) -> List[int]:
    return [int(x.strip()) for x in raw.split(",") if x.strip()]


@dataclass
class TelegramBotConfig:
    bot_token: str
    allowed_chat_ids: List[int]
    whisper_model: str = "tiny"
    # Queue backend: "memory" for bootstrap; future: "redis", "sqs"
    queue_backend: str = "memory"

    @classmethod
    def from_env(cls) -> "TelegramBotConfig":
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN env var is required to start the bot."
            )
        allowed_raw = os.environ.get("TELEGRAM_ALLOWED_CHAT_IDS", "")
        if not allowed_raw:
            raise RuntimeError(
                "TELEGRAM_ALLOWED_CHAT_IDS env var is required "
                "(comma-separated chat ids) for security."
            )
        return cls(
            bot_token=token,
            allowed_chat_ids=_parse_int_list(allowed_raw),
            whisper_model=os.environ.get("TG_BOT_WHISPER_MODEL", "tiny"),
            queue_backend=os.environ.get("TG_BOT_QUEUE_BACKEND", "memory"),
        )


// --- DUPLICATE BLOCK ---

"""Configuration and secrets management for the Telegram intake bot.

Loads configuration from environment variables (with optional .env file via
python-dotenv). Validates required values at startup and fails fast with clear
error messages so misconfiguration is caught immediately rather than at
runtime when a message arrives.

Secrets (bot token, API keys) are NEVER hardcoded and NEVER included in
error messages or logs. Only the presence/absence of a secret is reported.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional, Set


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram bot configuration."""

    bot_token: str
    allowed_user_ids: Set[int] = field(default_factory=set)


@dataclass(frozen=True)
class TranscriptionConfig:
    """Voice transcription configuration.

    provider: 'openai' (default) or 'whisper' (OpenAI Whisper API alias).
    api_key: required when voice messages are enabled.
    model: model identifier passed to the provider.
    """

    provider: str = "openai"
    api_key: Optional[str] = None
    model: str = "whisper-1"
    enabled: bool = False


@dataclass(frozen=True)
class AppConfig:
    """Top-level application configuration."""

    telegram: TelegramConfig
    transcription: TranscriptionConfig
    # Where intake items are queued for downstream processing.
    intake_queue_path: Optional[str] = None
    # Logging level for the bot process.
    log_level: str = "INFO"
    # Environment name (dev/staging/prod) for diagnostics.
    environment: str = "dev"


def _load_dotenv() -> None:
    """Load .env file if python-dotenv is available.

    Optional dependency: if dotenv isn't installed, we simply rely on real
    environment variables. This keeps the bot runnable in environments that
    inject env vars directly (e.g. systemd, Docker, CI).
    """
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        # dotenv is optional; env vars may be set directly.
        pass


def _parse_user_ids(raw: str) -> Set[int]:
    """Parse comma-separated Telegram user IDs into a set of ints.

    Accepts whitespace around items and ignores empty entries.
    Raises ConfigError on any non-integer value.
    """
    ids: Set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.add(int(part))
        except ValueError as exc:
            raise ConfigError(
                f"TELEGRAM_ALLOWED_USER_IDS contains non-integer value: {part!r}"
            ) from exc
    return ids


def _require_env(name: str) -> str:
    """Read a required env var, raising ConfigError if missing/empty.

    The value is returned but never echoed in errors — only the var name.
    """
    value = os.environ.get(name)
    if not value or not value.strip():
        raise ConfigError(
            f"Required environment variable {name} is not set. "
            f"Copy .env.example to .env and fill in the required values."
        )
    return value.strip()


def _optional_env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def load_config() -> AppConfig:
    """Load and validate configuration from the environment.

    Raises:
        ConfigError: if any required value is missing or malformed.
    """
    _load_dotenv()

    # --- Telegram ---
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")

    allowed_raw = _require_env("TELEGRAM_ALLOWED_USER_IDS")
    allowed_user_ids = _parse_user_ids(allowed_raw)
    if not allowed_user_ids:
        raise ConfigError(
            "TELEGRAM_ALLOWED_USER_IDS must contain at least one user ID. "
            "An empty whitelist would reject all messages."
        )

    telegram = TelegramConfig(
        bot_token=bot_token,
        allowed_user_ids=allowed_user_ids,
    )

    # --- Transcription ---
    # Voice transcription is optional. It's enabled only if an API key is
    # present, so the bot can run in text-only mode without a transcription
    # provider.
    whisper_key = (
        _optional_env("WHISPER_API_KEY")
        or _optional_env("OPENAI_API_KEY")
    )
    transcription_provider = _optional_env("TRANSCRIPTION_PROVIDER", "openai") or "openai"
    transcription_model = _optional_env("TRANSCRIPTION_MODEL", "whisper-1") or "whisper-1"

    transcription = TranscriptionConfig(
        provider=transcription_provider,
        api_key=whisper_key,
        model=transcription_model,
        enabled=whisper_key is not None,
    )

    # --- Misc ---
    intake_queue_path = _optional_env("INTAKE_QUEUE_PATH")
    log_level = (_optional_env("LOG_LEVEL", "INFO") or "INFO").upper()
    environment = _optional_env("ENVIRONMENT", "dev") or "dev"

    return AppConfig(
        telegram=telegram,
        transcription=transcription,
        intake_queue_path=intake_queue_path,
        log_level=log_level,
        environment=environment,
    )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Return the cached, validated AppConfig singleton.

    The first call validates and loads; subsequent calls return the same
    instance. Call `reset_config_cache()` in tests if needed.
    """
    return load_config()


def reset_config_cache() -> None:
    """Clear the cached config (primarily for tests)."""
    get_config.cache_clear()


def is_user_allowed(user_id: int) -> bool:
    """Convenience helper: check a Telegram user ID against the whitelist."""
    return user_id in get_config().telegram.allowed_user_ids


__all__ = [
    "AppConfig",
    "TelegramConfig",
    "TranscriptionConfig",
    "ConfigError",
    "load_config",
    "get_config",
    "reset_config_cache",
    "is_user_allowed",
]


// --- DUPLICATE BLOCK ---

"""Configuration and secrets for the Telegram intake bot.

All values are read from environment variables (optionally via a .env file
loaded by the caller). Nothing here is hardcoded or committed as a secret.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional


def _env(name: str, default: str | None = None) -> Optional[str]:
    val = os.environ.get(name)
    return val if val not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class TelegramBotConfig:
    # --- Secrets -----------------------------------------------------------
    telegram_bot_token: str

    # --- Polling -----------------------------------------------------------
    polling_interval: float          # seconds between getUpdates calls
    polling_timeout: int             # long-poll seconds (server-side wait)
    polling_read_timeout: float
    polling_write_timeout: float
    polling_connect_timeout: float
    polling_pool_timeout: float
    drop_pending_updates: bool
    allowed_updates: list[str] = field(default_factory=list)

    # --- Networking (optional) --------------------------------------------
    http_proxy_url: Optional[str] = None

    # --- Downstream intake (used by later steps) --------------------------
    intake_queue_dir: Optional[str] = None
    whisper_model: str = "base"
    log_level: str = "INFO"


def get_config() -> TelegramBotConfig:
    token = _env("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not set. See .env.example."
        )

    return TelegramBotConfig(
        telegram_bot_token=token,
        polling_interval=float(_env_int("TELEGRAM_POLLING_INTERVAL", 1)),
        polling_timeout=_env_int("TELEGRAM_POLLING_TIMEOUT", 30),
        polling_read_timeout=float(_env_int("TELEGRAM_READ_TIMEOUT", 35)),
        polling_write_timeout=float(_env_int("TELEGRAM_WRITE_TIMEOUT", 15)),
        polling_connect_timeout=float(_env_int("TELEGRAM_CONNECT_TIMEOUT", 15)),
        polling_pool_timeout=float(_env_int("TELEGRAM_POOL_TIMEOUT", 1)),
        drop_pending_updates=_env_bool("TELEGRAM_DROP_PENDING_UPDATES", False),
        allowed_updates=_env_list(
            "TELEGRAM_ALLOWED_UPDATES",
            ["message", "edited_message", "channel_post"],
        ),
        http_proxy_url=_env("TELEGRAM_HTTP_PROXY"),
        intake_queue_dir=_env("INTAKE_QUEUE_DIR"),
        whisper_model=_env("WHISPER_MODEL", "base") or "base",
        log_level=_env("LOG_LEVEL", "INFO") or "INFO",
    )


__all__ = ["TelegramBotConfig", "get_config"]


// --- DUPLICATE BLOCK ---

"""Configuration for the Telegram intake bot.

Loads from environment variables. Secrets are never logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List


def _parse_user_ids(raw: str) -> List[int]:
    """Parse a comma-separated list of integers from an env string."""
    if not raw or not raw.strip():
        return []
    ids: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                ids.append(int(part))
            except ValueError:
                raise ValueError(
                    f"ALLOWED_USER_IDS contains non-integer value: {part!r}"
                )
    return ids


@dataclass
class TelegramBotConfig:
    bot_token: str
    allowed_user_ids: List[int] = field(default_factory=list)
    intake_queue_path: str = "data/intake_queue.jsonl"
    whisper_model: str = "base"
    poll_timeout: int = 30

    @classmethod
    def from_env(cls) -> "TelegramBotConfig":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN is not set. "
                "Copy .env.example to .env and fill in your bot token."
            )
        return cls(
            bot_token=token,
            allowed_user_ids=_parse_user_ids(
                os.environ.get("ALLOWED_USER_IDS", "")
            ),
            intake_queue_path=os.environ.get(
                "INTAKE_QUEUE_PATH", "data/intake_queue.jsonl"
            ),
            whisper_model=os.environ.get("WHISPER_MODEL", "base"),
            poll_timeout=int(os.environ.get("POLL_TIMEOUT", "30")),
        )

    def make_auth_guard(self):
        """Convenience: build an AuthGuard from this config."""
        from services.telegram_bot.auth import AuthGuard
        return AuthGuard(self.allowed_user_ids)
