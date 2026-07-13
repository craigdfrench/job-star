"""Resource availability checker for the Job-Star idle loop.

Samples current system resources (CPU utilization, available memory,
available disk, active job count) and decides whether the system has
enough spare capacity to accept an idle-opportunistic task.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Tuple

import psutil

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ResourceSnapshot:
    """A point-in-time sample of system resources.

    All byte values are in bytes; cpu_percent is 0-100; active_jobs is an int.
    """

    cpu_percent: float
    available_memory_bytes: int
    total_memory_bytes: int
    available_disk_bytes: int
    active_jobs: int
    load_avg_1min: Optional[float] = None  # None on platforms without getloadavg

    @property
    def available_memory_mb(self) -> float:
        return self.available_memory_bytes / (1024.0 * 1024.0)

    @property
    def available_disk_gb(self) -> float:
        return self.available_disk_bytes / (1024.0 ** 3)

    def as_dict(self) -> dict:
        return {
            "cpu_percent": round(self.cpu_percent, 2),
            "available_memory_bytes": self.available_memory_bytes,
            "available_memory_mb": round(self.available_memory_mb, 2),
            "total_memory_bytes": self.total_memory_bytes,
            "available_disk_bytes": self.available_disk_bytes,
            "available_disk_gb": round(self.available_disk_gb, 3),
            "active_jobs": self.active_jobs,
            "load_avg_1min": self.load_avg_1min,
        }


@dataclass
class ResourceCheckResult:
    """Detailed result of a resource check, including per-metric pass/fail."""

    ok: bool
    snapshot: ResourceSnapshot
    reasons: list = field(default_factory=list)  # human-readable failure reasons
    failed_metrics: list = field(default_factory=list)  # metric names that failed

    def __bool__(self) -> bool:
        return self.ok


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_THRESHOLDS: dict = {
    # Reject an idle task if CPU utilization is at or above this (percent).
    "max_cpu_percent": 75.0,
    # Reject if available memory falls below this (bytes).
    "min_available_memory_bytes": 512 * 1024 * 1024,  # 512 MiB
    # Reject if available disk falls below this (bytes).
    "min_available_disk_bytes": 1 * 1024 ** 3,        # 1 GiB
    # Reject if the number of active Job-Star jobs is at or above this.
    "max_active_jobs": 2,
    # Seconds to sample CPU utilization. Higher = more accurate, slower.
    "cpu_sample_interval": 0.5,
    # Filesystem path to check for disk availability.
    "disk_path": "/",
}

# Threshold keys that map to snapshot fields for reporting.
_METRIC_LABELS = {
    "max_cpu_percent": "cpu_percent",
    "min_available_memory_bytes": "available_memory_bytes",
    "min_available_disk_bytes": "available_disk_bytes",
    "max_active_jobs": "active_jobs",
}


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def _default_active_job_counter() -> int:
    """Fallback active-job counter: count child processes of this process.

    The real Job-Star job registry should be injected via `job_counter`
    for accurate counts; this is a safe default when none is provided.
    """
    try:
        return len(psutil.Process().children(recursive=True))
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0


def sample_resources(
    cpu_sample_interval: float = 0.5,
    disk_path: str = "/",
    job_counter: Optional[Callable[[], int]] = None,
) -> ResourceSnapshot:
    """Take a single snapshot of current system resources.

    Parameters
    ----------
    cpu_sample_interval : float
        Seconds to block while sampling CPU utilization.
    disk_path : str
        Filesystem path to query for free disk space.
    job_counter : callable, optional
        Function returning the current number of active Job-Star jobs.
        Defaults to counting child processes.
    """
    cpu_percent = psutil.cpu_percent(interval=cpu_sample_interval)

    vm = psutil.virtual_memory()
    available_memory = int(vm.available)
    total_memory = int(vm.total)

    try:
        du = psutil.disk_usage(disk_path)
        available_disk = int(du.free)
    except (OSError, PermissionError) as exc:
        log.warning("disk_usage(%r) failed: %s; reporting 0 available", disk_path, exc)
        available_disk = 0

    counter = job_counter or _default_active_job_counter
    try:
        active_jobs = int(counter())
    except Exception as exc:
        log.warning("job_counter() raised: %s; reporting 0 active jobs", exc)
        active_jobs = 0

    load_avg_1min: Optional[float]
    try:
        load_avg_1min = os.getloadavg()[0]
    except (AttributeError, OSError):
        # Windows and some containers lack getloadavg.
        load_avg_1min = None

    return ResourceSnapshot(
        cpu_percent=cpu_percent,
        available_memory_bytes=available_memory,
        total_memory_bytes=total_memory,
        available_disk_bytes=available_disk,
        active_jobs=active_jobs,
        load_avg_1min=load_avg_1min,
    )


# ---------------------------------------------------------------------------
# Threshold evaluation
# ---------------------------------------------------------------------------

def _evaluate(
    snapshot: ResourceSnapshot, thresholds: Mapping[str, Any]
) -> ResourceCheckResult:
    """Compare a snapshot against thresholds and produce a detailed result."""
    reasons: list = []
    failed: list = []

    max_cpu = float(thresholds.get("max_cpu_percent", DEFAULT_THRESHOLDS["max_cpu_percent"]))
    if snapshot.cpu_percent >= max_cpu:
        failed.append("cpu_percent")
        reasons.append(
            f"cpu_percent {snapshot.cpu_percent:.1f}% >= max {max_cpu:.1f}%"
        )

    min_mem = int(thresholds.get("min_available_memory_bytes",
                                 DEFAULT_THRESHOLDS["min_available_memory_bytes"]))
    if snapshot.available_memory_bytes < min_mem:
        failed.append("available_memory_bytes")
        reasons.append(
            f"available memory {snapshot.available_memory_mb:.1f} MiB < "
            f"min {min_mem / 1024 / 1024:.1f} MiB"
        )

    min_disk = int(thresholds.get("min_available_disk_bytes",
                                  DEFAULT_THRESHOLDS["min_available_disk_bytes"]))
    if snapshot.available_disk_bytes < min_disk:
        failed.append("available_disk_bytes")
        reasons.append(
            f"available disk {snapshot.available_disk_gb:.3f} GiB < "
            f"min {min_disk / 1024 ** 3:.3f} GiB"
        )

    max_jobs = int(thresholds.get("max_active_jobs", DEFAULT_THRESHOLDS["max_active_jobs"]))
    if snapshot.active_jobs >= max_jobs:
        failed.append("active_jobs")
        reasons.append(
            f"active jobs {snapshot.active_jobs} >= max {max_jobs}"
        )

    ok = not failed
    return ResourceCheckResult(
        ok=ok, snapshot=snapshot, reasons=reasons, failed_metrics=failed
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_resources(
    thresholds: Optional[Mapping[str, Any]] = None,
    job_counter: Optional[Callable[[], int]] = None,
) -> Tuple[bool, ResourceSnapshot]:
    """Check whether the system has capacity for an idle-opportunistic task.

    Parameters
    ----------
    thresholds : mapping, optional
        Threshold configuration. Missing keys fall back to
        :data:`DEFAULT_THRESHOLDS`. Recognized keys:
          - ``max_cpu_percent`` (float)
          - ``min_available_memory_bytes`` (int)
          - ``min_available_disk_bytes`` (int)
          - ``max_active_jobs`` (int)
          - ``cpu_sample_interval`` (float)
          - ``disk_path`` (str)
    job_counter : callable, optional
        Returns the current active Job-Star job count.

    Returns
    -------
    (ok, snapshot) : (bool, ResourceSnapshot)
        ``ok`` is True when every resource metric is within its threshold.
        ``snapshot`` is always returned so callers can log/diagnose.
    """
    t = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        t.update(thresholds)

    snapshot = sample_resources(
        cpu_sample_interval=float(t.get("cpu_sample_interval", 0.5)),
        disk_path=str(t.get("disk_path", "/")),
        job_counter=job_counter,
    )

    result = _evaluate(snapshot, t)

    if result.ok:
        log.debug(
            "resource check OK: cpu=%.1f%% mem_avail=%.1fMiB disk_avail=%.3fGiB jobs=%d",
            snapshot.cpu_percent,
            snapshot.available_memory_mb,
            snapshot.available_disk_gb,
            snapshot.active_jobs,
        )
    else:
        log.info(
            "resource check FAIL (%s): %s",
            ", ".join(result.failed_metrics) or "unknown",
            "; ".join(result.reasons),
        )

    return result.ok, snapshot


def check_resources_detailed(
    thresholds: Optional[Mapping[str, Any]] = None,
    job_counter: Optional[Callable[[], int]] = None,
) -> ResourceCheckResult:
    """Like :func:`check_resources` but returns a full :class:`ResourceCheckResult`
    with per-metric failure reasons. Useful for diagnostics and UI.
    """
    t = dict(DEFAULT_THRESHOLDS)
    if thresholds:
        t.update(thresholds)

    snapshot = sample_resources(
        cpu_sample_interval=float(t.get("cpu_sample_interval", 0.5)),
        disk_path=str(t.get("disk_path", "/")),
        job_counter=job_counter,
    )
    return _evaluate(snapshot, t)


__all__ = [
    "ResourceSnapshot",
    "ResourceCheckResult",
    "DEFAULT_THRESHOLDS",
    "sample_resources",
    "check_resources",
    "check_resources_detailed",
]
