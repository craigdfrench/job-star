"""
Idle Loop Configuration Schema
================================

Defines all tunable parameters for the Job-Star idle loop — the background
process that opportunistically executes queued steps when system resources
permit.

The idle loop runs on a fixed interval (N minutes), checks resource
availability against thresholds, pulls the next eligible step from the
idle-opportunistic queue, verifies no conflicts, executes under supervision,
and updates progress.

This module provides:
    - IdleLoopConfig: dataclass holding all tunable parameters
    - load_idle_loop_config(): factory that merges defaults.yaml with
      environment overrides and an optional user-provided dict
    - ResourceThresholds: nested dataclass for CPU / memory / disk checks
    - RetryPolicy: nested dataclass for retry/backoff behavior
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


# ---------------------------------------------------------------------------
# Nested configuration dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResourceThresholds:
    """
    Minimum available resources required before the idle loop will pull a
    step from the queue.  Values are percentages (0-100) representing the
    *available* amount (i.e. idle CPU, free memory, free disk).
    """
    min_cpu_available_pct: float = 30.0
    min_memory_available_pct: float = 25.0
    min_disk_available_pct: float = 15.0

    def __post_init__(self) -> None:
        for name in ("min_cpu_available_pct",
                     "min_memory_available_pct",
                     "min_disk_available_pct"):
            val = getattr(self, name)
            if not 0.0 <= val <= 100.0:
                raise ValueError(f"{name} must be between 0 and 100, got {val}")


@dataclass(frozen=True)
class RetryPolicy:
    """Retry behavior for steps that fail during idle execution."""
    max_retries: int = 2
    backoff_base_seconds: float = 30.0
    backoff_multiplier: float = 2.0
    backoff_max_seconds: float = 1800.0  # 30 minutes

    def __post_init__(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if self.backoff_base_seconds <= 0:
            raise ValueError("backoff_base_seconds must be positive")
        if self.backoff_multiplier < 1.0:
            raise ValueError("backoff_multiplier must be >= 1.0")


# ---------------------------------------------------------------------------
# Top-level configuration dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IdleLoopConfig:
    """
    Complete configuration for the Job-Star idle loop.

    Attributes:
        check_interval_minutes:
            How often (N) the loop wakes to look for opportunistic work.
        resource_thresholds:
            Minimum available resources required to begin pulling work.
        max_concurrent_idle_jobs:
            Hard ceiling on simultaneous idle-executed steps.
        step_timeout_minutes:
            Per-step wall-clock timeout.  Steps exceeding this are killed
            and marked failed (subject to retry policy).
        retry_policy:
            Retry / backoff behavior for failed idle steps.
        queue_name:
            Logical name of the queue to pull from.
        conflict_check_enabled:
            If True, the loop verifies no conflicting active jobs before
            executing a step.
        heartbeat_interval_seconds:
            How often the supervisor writes a heartbeat for a running step.
        stale_heartbeat_seconds:
            A running step whose heartbeat is older than this is considered
            dead and may be reclaimed.
        enabled:
            Master switch.  If False the loop does nothing.
        log_level:
            Logging level for the idle loop process.
    """

    # --- Timing -----------------------------------------------------------
    check_interval_minutes: float = 5.0

    # --- Resources --------------------------------------------------------
    resource_thresholds: ResourceThresholds = field(
        default_factory=ResourceThresholds
    )

    # --- Concurrency ------------------------------------------------------
    max_concurrent_idle_jobs: int = 2

    # --- Execution --------------------------------------------------------
    step_timeout_minutes: float = 30.0
    retry_policy: RetryPolicy = field(default_factory=RetryPolicy)
    queue_name: str = "idle-opportunistic"
    conflict_check_enabled: bool = True

    # --- Supervision ------------------------------------------------------
    heartbeat_interval_seconds: float = 15.0
    stale_heartbeat_seconds: float = 120.0

    # --- Master control ---------------------------------------------------
    enabled: bool = True
    log_level: str = "INFO"

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def __post_init__(self) -> None:
        if self.check_interval_minutes <= 0:
            raise ValueError("check_interval_minutes must be positive")
        if self.max_concurrent_idle_jobs < 1:
            raise ValueError("max_concurrent_idle_jobs must be >= 1")
        if self.step_timeout_minutes <= 0:
            raise ValueError("step_timeout_minutes must be positive")
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be positive")
        if self.stale_heartbeat_seconds <= self.heartbeat_interval_seconds:
            raise ValueError(
                "stale_heartbeat_seconds must be greater than "
                "heartbeat_interval_seconds"
            )
        if self.log_level not in (
            "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
        ):
            raise ValueError(f"Invalid log_level: {self.log_level}")

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        """Return a plain dict representation (recursively unfolded)."""
        return asdict(self)

    @property
    def check_interval_seconds(self) -> float:
        return self.check_interval_minutes * 60.0

    @property
    def step_timeout_seconds(self) -> float:
        return self.step_timeout_minutes * 60.0


# ---------------------------------------------------------------------------
# Loader / factory
# ---------------------------------------------------------------------------

_DEFAULTS_YAML = Path(__file__).resolve().parent / "defaults.yaml"

# Environment variable prefix for overrides
_ENV_PREFIX = "JOBSTAR_IDLE_LOOP_"

# Mapping of env var suffix -> (dotted path into config, cast function)
_ENV_OVERRIDES: dict[str, tuple[str, type]] = {
    "CHECK_INTERVAL_MINUTES":   ("check_interval_minutes", float),
    "MAX_CONCURRENT_IDLE_JOBS": ("max_concurrent_idle_jobs", int),
    "STEP_TIMEOUT_MINUTES":     ("step_timeout_minutes", float),
    "QUEUE_NAME":               ("queue_name", str),
    "CONFLICT_CHECK_ENABLED":   ("conflict_check_enabled", _to_bool),
    "HEARTBEAT_INTERVAL_SECONDS": ("heartbeat_interval_seconds", float),
    "STALE_HEARTBEAT_SECONDS":  ("stale_heartbeat_seconds", float),
    "ENABLED":                  ("enabled", _to_bool),
    "LOG_LEVEL":                ("log_level", str),
    "MIN_CPU_AVAILABLE_PCT":    ("resource_thresholds.min_cpu_available_pct", float),
    "MIN_MEMORY_AVAILABLE_PCT": ("resource_thresholds.min_memory_available_pct", float),
    "MIN_DISK_AVAILABLE_PCT":   ("resource_thresholds.min_disk_available_pct", float),
    "RETRY_MAX_RETRIES":        ("retry_policy.max_retries", int),
    "RETRY_BACKOFF_BASE_SECONDS": ("retry_policy.backoff_base_seconds", float),
    "RETRY_BACKOFF_MULTIPLIER": ("retry_policy.backoff_multiplier", float),
    "RETRY_BACKOFF_MAX_SECONDS": ("retry_policy.backoff_max_seconds", float),
}


def _to_bool(val: str) -> bool:
    return val.strip().lower() in ("1", "true", "yes", "on")


def _load_yaml_defaults(path: Path = _DEFAULTS_YAML) -> dict[str, Any]:
    """Load the defaults.yaml file.  Returns {} if missing or yaml absent."""
    if yaml is None or not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    # The YAML may contain a top-level "idle_loop" key or be flat.
    if "idle_loop" in data and isinstance(data["idle_loop"], dict):
        return data["idle_loop"]
    return data


def _apply_env_overrides(base: dict[str, Any]) -> dict[str, Any]:
    """Apply environment-variable overrides on top of a config dict."""
    for suffix, (dotted_path, cast) in _ENV_OVERRIDES.items():
        env_key = _ENV_PREFIX + suffix
        raw = os.environ.get(env_key)
        if raw is None:
            continue
        try:
            value = cast(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"Invalid value for {env_key}={raw!r}: {exc}"
            ) from exc
        _set_dotted(base, dotted_path, value)
    return base


def _set_dotted(d: dict[str, Any], path: str, value: Any) -> None:
    keys = path.split(".")
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    d[keys[-1]] = value


def _build_config(merged: dict[str, Any]) -> IdleLoopConfig:
    """Construct an IdleLoopConfig from a merged dict, handling nesting."""
    # Extract nested sections
    rt_data = merged.pop("resource_thresholds", {}) or {}
    rp_data = merged.pop("retry_policy", {}) or {}

    rt = ResourceThresholds(**rt_data)
    rp = RetryPolicy(**rp_data)
    return IdleLoopConfig(resource_thresholds=rt, retry_policy=rp, **merged)


def load_idle_loop_config(
    overrides: Optional[dict[str, Any]] = None,
    *,
    yaml_path: Optional[Path] = None,
) -> IdleLoopConfig:
    """
    Build an IdleLoopConfig by merging (in priority order):

        1. Environment variables  (highest)
        2. ``overrides`` dict argument
        3. ``defaults.yaml`` file  (lowest)

    Parameters
    ----------
    overrides:
        Optional dict of user-supplied overrides.  May contain nested
        ``resource_thresholds`` and ``retry_policy`` sub-dicts.
    yaml_path:
        Optional explicit path to a defaults YAML.  Defaults to the
        packaged ``defaults.yaml`` next to this module.
    """
    path = yaml_path or _DEFAULTS_YAML
    merged: dict[str, Any] = _load_yaml_defaults(path)

    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v

    _apply_env_overrides(merged)
    return _build_config(merged)


# ---------------------------------------------------------------------------
# Module-level singleton (lazy)
# ---------------------------------------------------------------------------

_cached_config: Optional[IdleLoopConfig] = None


def get_idle_loop_config() -> IdleLoopConfig:
    """Return a process-wide cached IdleLoopConfig (lazy singleton)."""
    global _cached_config
    if _cached_config is None:
        _cached_config = load_idle_loop_config()
    return _cached_config


def reset_idle_loop_config() -> None:
    """Clear the cached singleton (mainly for tests)."""
    global _cached_config
    _cached_config = None
