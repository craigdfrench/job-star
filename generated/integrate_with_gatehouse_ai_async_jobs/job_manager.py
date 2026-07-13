"""Job submission and tracking for Job-Star.

This module wraps the gatehouse-ai async job client and maintains an
in-memory registry of submitted jobs and their states. Job-Star acts as the
intelligent client that decides what to execute, when, and how; the
:class:`JobManager` is the thin execution boundary that actually talks to
gatehouse-ai.

The gatehouse-ai client is represented by the :class:`GatehouseClient`
protocol, so any client implementing the three required async methods can be
plugged in. This keeps the bootstrap resilient while the concrete gatehouse
interface stabilizes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums and data structures
# ---------------------------------------------------------------------------


class JobState(str, Enum):
    """Lifecycle states for a job, as understood by Job-Star.

    These mirror the states gatehouse-ai reports, normalized to a single
    vocabulary. ``UNKNOWN`` is used when gatehouse returns something we don't
    recognize, so callers can decide how to react.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

    @property
    def is_terminal(self) -> bool:
        """True if the job will not transition to any further state."""
        return self in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED)


# Sentinel set used by list_active() and wait_for().
TERMINAL_STATES = frozenset(
    {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}
)


@dataclass
class JobStatus:
    """A snapshot of a job's status at a point in time.

    ``raw`` preserves the original payload returned by gatehouse-ai so callers
    that need provider-specific fields can still get at them.
    """

    job_id: str
    state: JobState
    raw: Dict[str, Any] = field(default_factory=dict)
    fetched_at: float = field(default_factory=time.time)

    @property
    def is_terminal(self) -> bool:
        return self.state.is_terminal


@dataclass
class JobRecord:
    """In-memory registry entry for a submitted job.

    ``local_metadata`` is whatever Job-Star decided to attach at submission
    time (e.g. the reasoning trace that led to submitting the job). It is
    opaque to the manager and preserved for later introspection.
    """

    job_id: str
    submitted_at: float
    local_metadata: Dict[str, Any] = field(default_factory=dict)
    last_status: Optional[JobStatus] = None
    spec: Dict[str, Any] = field(default_factory=dict)

    @property
    def state(self) -> JobState:
        """Best-known state, defaulting to PENDING if never refreshed."""
        if self.last_status is None:
            return JobState.PENDING
        return self.last_status.state

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


# ---------------------------------------------------------------------------
# Gatehouse client protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class GatehouseClient(Protocol):
    """Structural protocol for the gatehouse-ai async job client.

    Any object exposing these three async methods can be used. The exact
    argument/return shapes are intentionally permissive (``Dict[str, Any]``)
    because the concrete gatehouse interface is still being finalized.
    """

    async def submit_job(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        """Submit a job spec and return a dict containing at least ``job_id``."""
        ...

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Return the current status payload for ``job_id``."""
        ...

    async def list_jobs(self, *args: Any, **kwargs: Any) -> List[Dict[str, Any]]:
        """Return a list of job status payloads."""
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_state(raw_state: Any) -> JobState:
    """Map a gatehouse-ai state value onto :class:`JobState`.

    Accepts strings (case-insensitive) or members of ``JobState``. Unknown
    values collapse to :attr:`JobState.UNKNOWN` rather than raising, so a
    transient mismatch in the upstream vocabulary never crashes tracking.
    """
    if isinstance(raw_state, JobState):
        return raw_state
    if isinstance(raw_state, str):
        normalized = raw_state.strip().lower()
        try:
            return JobState(normalized)
        except ValueError:
            logger.warning("Unrecognized job state from gatehouse: %r", raw_state)
            return JobState.UNKNOWN
    logger.warning("Non-string job state from gatehouse: %r", raw_state)
    return JobState.UNKNOWN


def _extract_job_id(submit_response: Dict[str, Any]) -> str:
    """Pull a job id out of a gatehouse submit response.

    Tries common key names so we're tolerant of small upstream changes.
    """
    for key in ("job_id", "id", "jobId"):
        value = submit_response.get(key)
        if value is not None:
            return str(value)
    raise ValueError(
        "gatehouse submit response did not contain a job id; "
        f"got keys: {sorted(submit_response)}"
    )


def _parse_status(job_id: str, raw: Dict[str, Any]) -> JobStatus:
    """Build a :class:`JobStatus` from a raw gatehouse status payload."""
    state = _normalize_state(raw.get("state") or raw.get("status"))
    return JobStatus(job_id=job_id, state=state, raw=raw)


# ---------------------------------------------------------------------------
# JobManager
# ---------------------------------------------------------------------------


class JobManager:
    """Wraps the gatehouse-ai async job client and tracks submitted jobs.

    Responsibilities:

    * Submit jobs to gatehouse and register them locally.
    * Track the state of submitted jobs, refreshing from gatehouse on demand.
    * Wait (asynchronously) for a job to reach a terminal state.
    * List jobs that are still active (non-terminal).

    The registry is in-memory and single-process; it is not persisted. That
    is acceptable for the bootstrap phase — a persistent registry is a
    later step.
    """

    def __init__(
        self,
        client: GatehouseClient,
        *,
        poll_interval: float = 1.0,
        poll_max_interval: float = 30.0,
        poll_backoff_factor: float = 1.5,
    ) -> None:
        self._client = client
        self._registry: Dict[str, JobRecord] = {}
        self._lock = asyncio.Lock()

        # Polling configuration for wait_for().
        self.poll_interval = poll_interval
        self.poll_max_interval = poll_max_interval
        self.poll_backoff_factor = poll_backoff_factor

    # -- submission -------------------------------------------------------

    async def submit(
        self,
        spec: Dict[str, Any],
        *,
        local_metadata: Optional[Dict[str, Any]] = None,
    ) -> JobRecord:
        """Submit a job spec to gatehouse and register it locally.

        Args:
            spec: The job specification accepted by the gatehouse client.
            local_metadata: Optional Job-Star-side metadata to attach to the
                registry record. Opaque to the manager; useful for storing
                the reasoning that led to the submission.

        Returns:
            The :class:`JobRecord` for the submitted job.

        Raises:
            ValueError: If the gatehouse response lacks a job id.
            Exception: Any error raised by the gatehouse client propagates;
                no partial registry entry is left behind on failure.
        """
        logger.debug("Submitting job spec to gatehouse: %s", spec)
        response = await self._client.submit_job(spec)
        job_id = _extract_job_id(response)
        record = JobRecord(
            job_id=job_id,
            submitted_at=time.time(),
            local_metadata=dict(local_metadata or {}),
            spec=dict(spec),
            last_status=_parse_status(job_id, response),
        )
        async with self._lock:
            self._registry[job_id] = record
        logger.info("Submitted job %s", job_id)
        return record

    # -- tracking ---------------------------------------------------------

    async def track(
        self,
        job_id: str,
        *,
        refresh: bool = True,
    ) -> JobStatus:
        """Return the status of a job.

        Args:
            job_id: The gatehouse job id.
            refresh: If True (default), fetch fresh status from gatehouse and
                update the registry. If False, return the last-known status
                without a network call.

        Returns:
            The :class:`JobStatus` for the job.

        Raises:
            KeyError: If ``job_id`` is not in the local registry.
        """
        async with self._lock:
            record = self._registry.get(job_id)
        if record is None:
            raise KeyError(f"Unknown job_id: {job_id!r}")

        if not refresh and record.last_status is not None:
            return record.last_status

        # If the job is already terminal, there's no point refreshing.
        if record.is_terminal and record.last_status is not None and not refresh:
            return record.last_status

        status = await self._fetch_status(job_id)
        async with self._lock:
            # Re-read in case it was removed concurrently.
            record = self._registry.get(job_id)
            if record is not None:
                record.last_status = status
        return status

    async def _fetch_status(self, job_id: str) -> JobStatus:
        raw = await self._client.get_job_status(job_id)
        return _parse_status(job_id, raw)

    # -- waiting ----------------------------------------------------------

    async def wait_for(
        self,
        job_id: str,
        *,
        timeout: Optional[float] = None,
        on_update: Optional[Callable[[JobStatus], Awaitable[None]]] = None,
    ) -> JobStatus:
        """Block until ``job_id`` reaches a terminal state, then return it.

        Polls gatehouse with exponential backoff (capped at
        ``poll_max_interval``). The initial interval is ``poll_interval`` and
        each subsequent interval is multiplied by ``poll_backoff_factor``.

        Args:
            job_id: The gatehouse job id.
            timeout: Maximum seconds to wait. None means wait forever.
            on_update: Optional async callback invoked whenever the status
                changes (including the initial fetch). Useful for streaming
                updates to a UI or log.

        Returns:
            The final :class:`JobStatus`.

        Raises:
            KeyError: If ``job_id`` is not in the registry.
            asyncio.TimeoutError: If ``timeout`` elapses before a terminal
                state is reached.
        """
        async with self._lock:
            record = self._registry.get(job_id)
        if record is None:
            raise KeyError(f"Unknown job_id: {job_id!r}")

        # If already terminal, short-circuit.
        if record.is_terminal and record.last_status is not None:
            return record.last_status

        deadline: Optional[float] = None
        if timeout is not None:
            deadline = time.monotonic() + timeout

        interval = self.poll_interval
        last_state: Optional[JobState] = None

        while True:
            status = await self._fetch_status(job_id)
            async with self._lock:
                record = self._registry.get(job_id)
                if record is not None:
                    record.last_status = status

            if status.state != last_state:
                last_state = status.state
                logger.info("Job %s -> %s", job_id, status.state)
                if on_update is not None:
                    await on_update(status)

            if status.is_terminal:
                return status

            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError(
                        f"Timed out waiting for job {job_id!r} after {timeout}s"
                    )
                sleep_for = min(interval, remaining)
            else:
                sleep_for = interval

            await asyncio.sleep(sleep_for)

            # Back off for the next iteration.
            interval = min(interval * self.poll_backoff_factor, self.poll_max_interval)

    # -- listing ----------------------------------------------------------

    async def list_active(self) -> List[JobRecord]:
        """Return registry entries for jobs that are not yet terminal.

        "Active" means the last-known state is not one of the terminal
        states (succeeded, failed, cancelled). This uses cached status only
        and does not refresh from gatehouse, so it's cheap to call often.
        If you need fresh data, call :meth:`track` on the specific jobs first.
        """
        async with self._lock:
            records = list(self._registry.values())
        return [r for r in records if not r.is_terminal]

    async def list_all(self) -> List[JobRecord]:
        """Return all registry entries, terminal or not."""
        async with self._lock:
            return list(self._registry.values())

    # -- registry access --------------------------------------------------

    def get_record(self, job_id: str) -> Optional[JobRecord]:
        """Synchronously return the cached record, or None if unknown.

        This does not touch the network and does not refresh. It is safe to
        call from synchronous code paths that just want a peek at the
        registry.
        """
        return self._registry.get(job_id)

    async def forget(self, job_id: str) -> None:
        """Remove a job from the in-memory registry.

        This does NOT cancel the job on gatehouse; it only drops our local
        record. Useful for pruning completed jobs to keep the registry small.
        """
        async with self._lock:
            self._registry.pop(job_id, None)

    async def __aenter__(self) -> "JobManager":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        # Nothing to close for now; the gatehouse client owns its own
        # lifecycle. Hook retained for future cleanup (e.g. flushing a
        # persistent store).
        return None
