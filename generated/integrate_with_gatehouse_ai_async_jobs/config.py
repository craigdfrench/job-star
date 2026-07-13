"""
Job-Star configuration.

Settings are loaded from environment variables (and optionally a `.env`
file) with typed validation via pydantic-settings. Import `get_settings()`
to access a cached singleton; call `reset_settings()` if you need to
force a reload (mainly useful in tests).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from job_star.constants import (
    DEFAULT_AUTH_SCHEME,
    DEFAULT_CONNECT_TIMEOUT_SECONDS,
    DEFAULT_GATEHOUSE_API_VERSION,
    DEFAULT_GATEHOUSE_ENDPOINT,
    DEFAULT_GATEHOUSE_HEALTH_PATH,
    DEFAULT_JOB_COMPLETION_TIMEOUT_SECONDS,
    DEFAULT_LOG_FORMAT,
    DEFAULT_LOG_LEVEL,
    DEFAULT_MAX_CONCURRENT_JOBS,
    DEFAULT_MAX_RETRIES,
    DEFAULT_MAX_WORKER_THREADS,
    DEFAULT_POLL_BACKOFF_FACTOR,
    DEFAULT_POLL_INTERVAL_SECONDS,
    DEFAULT_POLL_MAX_INTERVAL_SECONDS,
    DEFAULT_QUEUE_BATCH_SIZE,
    DEFAULT_READ_TIMEOUT_SECONDS,
    DEFAULT_RETRY_BASE_DELAY_SECONDS,
    DEFAULT_RETRY_MAX_DELAY_SECONDS,
    DEFAULT_SHUTDOWN_GRACE_SECONDS,
    DEFAULT_TOKEN_REFRESH_MARGIN_SECONDS,
    MODE_POLLING,
    VALID_MODES,
)


class GatehouseSettings(BaseSettings):
    """Connection settings for the gatehouse-ai async job service."""

    endpoint: str = Field(
        default=DEFAULT_GATEHOUSE_ENDPOINT,
        description="Base URL of the gatehouse-ai service.",
    )
    api_version: str = Field(
        default=DEFAULT_GATEHOUSE_API_VERSION,
        description="API version path segment appended to endpoint.",
    )
    health_path: str = Field(
        default=DEFAULT_GATEHOUSE_HEALTH_PATH,
        description="Path used for health checks against gatehouse.",
    )

    # Auth ------------------------------------------------------------------
    auth_scheme: str = Field(
        default=DEFAULT_AUTH_SCHEME,
        description="HTTP auth scheme (e.g. 'Bearer', 'Token').",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="Static API key for gatehouse auth (mutually exclusive with token).",
    )
    token: Optional[str] = Field(
        default=None,
        description="Static bearer token for gatehouse auth.",
    )
    token_url: Optional[str] = Field(
        default=None,
        description="OAuth/OIDC token endpoint for refreshing access tokens.",
    )
    client_id: Optional[str] = Field(
        default=None,
        description="OAuth client id used with token_url.",
    )
    client_secret: Optional[str] = Field(
        default=None,
        description="OAuth client secret used with token_url.",
    )
    token_refresh_margin_seconds: float = Field(
        default=DEFAULT_TOKEN_REFRESH_MARGIN_SECONDS,
        description="Refresh access token this many seconds before expiry.",
    )

    # Timeouts --------------------------------------------------------------
    connect_timeout_seconds: float = Field(
        default=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        ge=0.1,
        description="TCP connect timeout for gatehouse requests.",
    )
    read_timeout_seconds: float = Field(
        default=DEFAULT_READ_TIMEOUT_SECONDS,
        ge=0.1,
        description="Read timeout for gatehouse responses.",
    )

    model_config = SettingsConfigDict(
        env_prefix="GATEHOUSE_",
        extra="ignore",
    )


class PollingSettings(BaseSettings):
    """Settings for the polling loop that fetches jobs from gatehouse."""

    enabled: bool = Field(
        default=True,
        description="Whether the polling loop is active.",
    )
    interval_seconds: float = Field(
        default=DEFAULT_POLL_INTERVAL_SECONDS,
        ge=0.1,
        description="Base interval between poll cycles.",
    )
    max_interval_seconds: float = Field(
        default=DEFAULT_POLL_MAX_INTERVAL_SECONDS,
        ge=0.1,
        description="Upper bound for poll interval after backoff.",
    )
    backoff_factor: float = Field(
        default=DEFAULT_POLL_BACKOFF_FACTOR,
        ge=1.0,
        description="Multiplier applied to interval when no jobs are found.",
    )
    batch_size: int = Field(
        default=DEFAULT_QUEUE_BATCH_SIZE,
        ge=1,
        description="Max jobs requested per poll cycle.",
    )

    model_config = SettingsConfigDict(
        env_prefix="POLLING_",
        extra="ignore",
    )


class ConcurrencySettings(BaseSettings):
    """Limits on concurrent execution."""

    max_concurrent_jobs: int = Field(
        default=DEFAULT_MAX_CONCURRENT_JOBS,
        ge=1,
        description="Maximum number of jobs executing in parallel.",
    )
    max_worker_threads: int = Field(
        default=DEFAULT_MAX_WORKER_THREADS,
        ge=1,
        description="Worker thread pool size for the executor.",
    )

    model_config = SettingsConfigDict(
        env_prefix="CONCURRENCY_",
        extra="ignore",
    )


class RetrySettings(BaseSettings):
    """Retry behavior for transient failures."""

    max_retries: int = Field(
        default=DEFAULT_MAX_RETRIES,
        ge=0,
        description="Maximum retry attempts for a failed job.",
    )
    base_delay_seconds: float = Field(
        default=DEFAULT_RETRY_BASE_DELAY_SECONDS,
        ge=0.0,
        description="Initial delay before first retry.",
    )
    max_delay_seconds: float = Field(
        default=DEFAULT_RETRY_MAX_DELAY_SECONDS,
        ge=0.0,
        description="Cap on retry delay (jitter applied up to this).",
    )

    model_config = SettingsConfigDict(
        env_prefix="RETRY_",
        extra="ignore",
    )


class LoggingSettings(BaseSettings):
    """Logging configuration."""

    level: str = Field(
        default=DEFAULT_LOG_LEVEL,
        description="Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).",
    )
    format: str = Field(
        default=DEFAULT_LOG_FORMAT,
        description="logging.Formatter-style format string.",
    )

    model_config = SettingsConfigDict(
        env_prefix="LOG_",
        extra="ignore",
    )


class Settings(BaseSettings):
    """Top-level Job-Star settings.

    Combines all sub-settings and adds process-wide knobs like runtime
    mode, job completion timeout, and shutdown grace period.
    """

    # Runtime behavior -------------------------------------------------------
    mode: str = Field(
        default=MODE_POLLING,
        description=(
            "How Job-Star obtains jobs: 'polling', 'listening', or 'hybrid'."
        ),
    )
    job_completion_timeout_seconds: float = Field(
        default=DEFAULT_JOB_COMPLETION_TIMEOUT_SECONDS,
        ge=1.0,
        description="Hard ceiling on how long to wait for any single job.",
    )
    shutdown_grace_seconds: float = Field(
        default=DEFAULT_SHUTDOWN_GRACE_SECONDS,
        ge=0.0,
        description="Grace period before forcing in-flight jobs to cancel on shutdown.",
    )

    # Sub-configs ------------------------------------------------------------
    gatehouse: GatehouseSettings = Field(default_factory=GatehouseSettings)
    polling: PollingSettings = Field(default_factory=PollingSettings)
    concurrency: ConcurrencySettings = Field(default_factory=ConcurrencySettings)
    retry: RetrySettings = Field(default_factory=RetrySettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="JOB_STAR_",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    # Validators -------------------------------------------------------------
    @field_validator("mode")
    @classmethod
    def _validate_mode(cls, v: str) -> str:
        v_lower = v.lower()
        if v_lower not in VALID_MODES:
            raise ValueError(
                f"mode must be one of {VALID_MODES}, got {v!r}"
            )
        return v_lower

    # Convenience ------------------------------------------------------------
    @property
    def gatehouse_base_url(self) -> str:
        """Full base URL including API version segment."""
        endpoint = self.gatehouse.endpoint.rstrip("/")
        version = self.gatehouse.api_version.strip("/")
        if not version:
            return endpoint
        return f"{endpoint}/{version}"

    def auth_header(self) -> Optional[dict[str, str]]:
        """Build the Authorization header dict, or None if no auth configured."""
        scheme = self.gatehouse.auth_scheme
        if self.gatehouse.token:
            return {"Authorization": f"{scheme} {self.gatehouse.token}"}
        if self.gatehouse.api_key:
            return {"Authorization": f"{scheme} {self.gatehouse.api_key}"}
        return None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return a cached Settings singleton.

    Use this throughout the app so configuration is loaded once and
    consistent. Call `reset_settings()` to invalidate the cache.
    """
    return Settings()


def reset_settings() -> None:
    """Invalidate the cached settings (useful in tests)."""
    get_settings.cache_clear()
