"""Job-Star scheduler: queue work, retry after quota holds, defer to idle time.

The scheduler is the integration point between the router/supervisor and the
gateway monitor. It answers:
- When should a job run?
- What model should it use?
- If the preferred model is unavailable, should we defer or fall back?

Usage:
    scheduler = Scheduler(gateway_monitor)
    await scheduler.schedule(ScheduledJob(...))
    # Later, in a loop
    ready_jobs = await scheduler.pop_ready()
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .gatehouse.monitor import GatewayMonitor


class JobStatus(str, Enum):
    PENDING = "pending"
    DEFERRED = "deferred"
    READY = "ready"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ScheduledJob:
    """A unit of work scheduled for execution."""
    goal_id: str
    step_id: str
    title: str
    preferred_model: str
    required_capability: str | None = None
    prefer_free: bool = False
    max_retries: int = 3
    priority: int = 0  # higher = more important

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    status: JobStatus = JobStatus.PENDING
    deferred_until: float = 0.0
    attempts: int = 0
    fallback_model: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return self.status == JobStatus.READY or (self.status == JobStatus.DEFERRED and time.time() >= self.deferred_until)


class Scheduler:
    """Schedules and dispatches jobs with gateway-aware retry/fallback logic.

    The scheduler is intentionally in-memory for the bootstrap. Later it can
    be backed by the Postgres database.
    """

    def __init__(
        self,
        gateway_monitor: GatewayMonitor | None = None,
        default_defer_seconds: float = 3 * 60 * 60,
        allow_fallback: bool = True,
    ):
        self.monitor = gateway_monitor or GatewayMonitor()
        self.default_defer_seconds = default_defer_seconds
        self.allow_fallback = allow_fallback
        self._jobs: list[ScheduledJob] = []

    async def schedule(self, job: ScheduledJob) -> ScheduledJob:
        """Add a job to the schedule and determine initial readiness."""
        await self._resolve_model(job)
        self._jobs.append(job)
        return job

    async def _resolve_model(self, job: ScheduledJob) -> None:
        """Determine if the job can run now, needs a fallback, or must be deferred."""
        await self.monitor.refresh()

        if self.monitor.is_available(job.preferred_model):
            job.status = JobStatus.READY
            job.fallback_model = None
            return

        # Try fallback if allowed
        if self.allow_fallback:
            fallback = self.monitor.pick_fallback(
                job.preferred_model,
                required_capability=job.required_capability,
                prefer_free=job.prefer_free,
            )
            if fallback:
                job.fallback_model = fallback
                job.status = JobStatus.READY
                return

        # Defer until the preferred model's quota hold expires
        wait_seconds = self.monitor.time_until_available(job.preferred_model)
        job.deferred_until = time.time() + (wait_seconds or self.default_defer_seconds)
        job.status = JobStatus.DEFERRED

    async def pop_ready(self, limit: int | None = None) -> list[ScheduledJob]:
        """Get jobs that are ready to run, optionally limited by count."""
        # Re-evaluate deferred jobs
        await self._reevaluate()

        # Sort by priority descending, then deferred_until ascending
        ready = [j for j in self._jobs if j.status == JobStatus.READY]
        ready.sort(key=lambda j: (-j.priority, j.deferred_until))

        if limit is not None:
            ready = ready[:limit]

        for j in ready:
            j.status = JobStatus.RUNNING
            j.attempts += 1

        return ready

    async def _reevaluate(self) -> None:
        """Re-evaluate deferred jobs to see if their models are available yet."""
        await self.monitor.refresh()
        for job in self._jobs:
            if job.status == JobStatus.DEFERRED and time.time() >= job.deferred_until:
                await self._resolve_model(job)

    def record_success(self, job: ScheduledJob, model: str, tokens: int = 0) -> None:
        """Mark a job as completed and update model state."""
        job.status = JobStatus.COMPLETED
        self.monitor.record_success(model, tokens)

    async def record_failure(self, job: ScheduledJob, model: str, error: str) -> None:
        """Mark a job as failed and either retry or defer."""
        self.monitor.record_failure(model, error)

        if job.attempts >= job.max_retries:
            job.status = JobStatus.FAILED
            return

        # Try to resolve model again (may pick a fallback or defer after quota hold)
        await self._resolve_model(job)

    def list_jobs(self, status: JobStatus | None = None) -> list[ScheduledJob]:
        """Return all jobs, optionally filtered by status."""
        if status is None:
            return list(self._jobs)
        return [j for j in self._jobs if j.status == status]

    def seconds_until_next_ready(self) -> float:
        """Return the number of seconds until the next deferred job is ready (0 if ready now)."""
        ready = [j for j in self._jobs if j.status == JobStatus.READY]
        if ready:
            return 0.0

        deferred = [j.deferred_until for j in self._jobs if j.status == JobStatus.DEFERRED]
        if not deferred:
            return float("inf")

        return max(0.0, min(deferred) - time.time())