"""Logging and observability setup for the Job-Star idle loop.

Provides:
  - setup_idle_logger(): configures the 'jobstar.idle' logger with a
    dedicated file handler and optional console handler.
  - IdleLogAdapter: wraps a logger and emits structured key=value records
    so cycles are fully traceable and greppable.
  - MetricsEmitter: optional sink for numeric metrics (counters/histograms).
    Default implementation is a no-op; users can inject a real emitter
    (Prometheus, statsd, etc.) without modifying the loop.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

class MetricsEmitter:
    """No-op metrics emitter. Subclass or replace to wire a real backend."""

    def cycle_started(self) -> None:
        pass

    def cycle_completed(self, duration_s: float, outcome: str) -> None:
        pass

    def step_executed(self, step_id: str, status: str, duration_s: float) -> None:
        pass

    def resource_snapshot(self, cpu_pct: float, mem_pct: float,
                          disk_pct: float) -> None:
        pass

    def conflict_detected(self, step_id: str, conflict_type: str) -> None:
        pass

    def queue_empty(self) -> None:
        pass


class CallbackMetricsEmitter(MetricsEmitter):
    """Metrics emitter that forwards to user-supplied callback functions.

    Each callback receives a dict of fields. Useful for quick integration
    without subclassing.
    """

    def __init__(
        self,
        on_counter: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_histogram: Optional[Callable[[str, float, Dict[str, Any]], None]] = None,
    ) -> None:
        self._on_counter = on_counter
        self._on_histogram = on_histogram

    def cycle_started(self) -> None:
        if self._on_counter:
            self._on_counter("idle_cycle_started", {})

    def cycle_completed(self, duration_s: float, outcome: str) -> None:
        if self._on_counter:
            self._on_counter("idle_cycle_completed", {"outcome": outcome})
        if self._on_histogram:
            self._on_histogram("idle_cycle_duration_seconds", duration_s,
                               {"outcome": outcome})

    def step_executed(self, step_id: str, status: str, duration_s: float) -> None:
        if self._on_counter:
            self._on_counter("idle_step_executed",
                             {"step_id": step_id, "status": status})
        if self._on_histogram:
            self._on_histogram("idle_step_duration_seconds", duration_s,
                               {"step_id": step_id, "status": status})

    def resource_snapshot(self, cpu_pct: float, mem_pct: float,
                          disk_pct: float) -> None:
        if self._on_histogram:
            self._on_histogram("idle_resource_cpu_pct", cpu_pct, {})
            self._on_histogram("idle_resource_mem_pct", mem_pct, {})
            self._on_histogram("idle_resource_disk_pct", disk_pct, {})

    def conflict_detected(self, step_id: str, conflict_type: str) -> None:
        if self._on_counter:
            self._on_counter("idle_conflict_detected",
                             {"step_id": step_id,
                              "conflict_type": conflict_type})

    def queue_empty(self) -> None:
        if self._on_counter:
            self._on_counter("idle_queue_empty", {})


# ---------------------------------------------------------------------------
# Structured log adapter
# ---------------------------------------------------------------------------

class IdleLogAdapter:
    """Wraps a logger and emits structured records.

    Each log line is prefixed with a fixed component tag and includes
    key=value fields, e.g.:

        2024-01-15T10:23:01 INFO [idle.loop] cycle_start cycle=42
        2024-01-15T10:23:01 INFO [idle.loop] resources cpu=12.3 mem=45.1 ...

    For richer integration, ``log_event`` emits a JSON blob at DEBUG level
    so machine parsers can consume it while humans still get readable INFO.
    """

    COMPONENT = "idle.loop"

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    # -- passthroughs -------------------------------------------------------

    @property
    def logger(self) -> logging.Logger:
        return self._logger

    def debug(self, msg: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, msg, fields)

    def info(self, msg: str, **fields: Any) -> None:
        self._emit(logging.INFO, msg, fields)

    def warning(self, msg: str, **fields: Any) -> None:
        self._emit(logging.WARNING, msg, fields)

    def error(self, msg: str, **fields: Any) -> None:
        self._emit(logging.ERROR, msg, fields)

    def exception(self, msg: str, **fields: Any) -> None:
        self._emit(logging.ERROR, msg, fields, exc_info=True)

    # -- core ---------------------------------------------------------------

    def _emit(self, level: int, msg: str, fields: Dict[str, Any],
              exc_info: bool = False) -> None:
        rendered = self._format(msg, fields)
        self._logger.log(level, rendered, exc_info=exc_info)
        # Also emit a JSON line at DEBUG for machine consumption.
        if level >= logging.INFO and self._logger.isEnabledFor(logging.DEBUG):
            payload = {"component": self.COMPONENT, "event": msg, **fields}
            self._logger.debug("JSON %s", json.dumps(payload, default=str))

    @staticmethod
    def _format(msg: str, fields: Dict[str, Any]) -> str:
        if not fields:
            return msg
        parts = [f"{k}={IdleLogAdapter._fmt_val(v)}" for k, v in fields.items()]
        return f"{msg} " + " ".join(parts)

    @staticmethod
    def _fmt_val(v: Any) -> str:
        if isinstance(v, float):
            return f"{v:.2f}"
        if isinstance(v, bool):
            return "true" if v else "false"
        if v is None:
            return "-"
        s = str(v)
        if " " in s or "=" in s:
            return f'"{s}"'
        return s


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

_IDLE_FORMAT = (
    "%(asctime)s %(levelname)-5s [%(name)s] %(message)s"
)
_IDLE_DATEFMT = "%Y-%m-%dT%H:%M:%S"


def setup_idle_logger(
    log_file: Optional[str] = None,
    level: int = logging.INFO,
    console: bool = False,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """Configure and return the ``jobstar.idle`` logger.

    Parameters
    ----------
    log_file : str, optional
        Path to the dedicated idle-loop log file. If None, defaults to
        ``<cwd>/logs/idle_loop.log`` (directory created if missing).
    level : int
        Root level for the logger (default INFO).
    console : bool
        If True, also emit to stderr (useful for foreground/daemon debug).
    max_bytes : int
        Rotating file handler max size (default 10 MiB).
    backup_count : int
        Number of rotated files to keep.
    """
    logger = logging.getLogger("jobstar.idle")
    logger.setLevel(level)
    # Avoid duplicate handlers on re-setup.
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.propagate = False

    formatter = logging.Formatter(_IDLE_FORMAT, datefmt=_IDLE_DATEFMT)

    # File handler
    if log_file is None:
        log_file = str(Path.cwd() / "logs" / "idle_loop.log")
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        from logging.handlers import RotatingFileHandler
        file_handler: logging.Handler = RotatingFileHandler(
            str(log_path), maxBytes=max_bytes, backupCount=backup_count,
        )
    except Exception:
        # Fallback to plain FileHandler if rotation unavailable.
        file_handler = logging.FileHandler(str(log_path))
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    logger.addHandler(file_handler)

    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        logger.addHandler(console_handler)

    logger.info(
        "idle_logger_initialized log_file=%s level=%s console=%s",
        log_file, logging.getLevelName(level), console,
    )
    return logger


def get_idle_logger() -> logging.Logger:
    """Return the configured ``jobstar.idle`` logger without reconfiguring."""
    return logging.getLogger("jobstar.idle")
