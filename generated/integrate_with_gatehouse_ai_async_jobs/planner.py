"""Shared data models for the Job-Star decision engine.

These structures are intentionally framework-light (plain dataclasses) so the
decision engine can be tested and reasoned about independently of the
gatehouse-ai async transport layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Optional


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    """Lifecycle states a submitted job can be in."""

    PENDING = "pending"      # accepted by gatehouse, not yet running
    RUNNING = "running"      # currently executing
    COMPLETED = "completed"  # finished successfully
    FAILED = "failed"        # finished with an error
    CANCELLED = "cancelled"  # stopped before completion


class Urgency(str, Enum):
    """Coarse urgency bands used by the selector for prioritization."""

    CRITICAL = "critical"   # blocking everything; must run now
    HIGH = "high"            # should run as soon as possible
    SOON = "soon"            # normal priority
    LOW = "low"              # can wait; nice-to-have


@dataclass(frozen=True)
class Goal:
    """A high-level objective Job-Star is trying to achieve.

    Goals are intentionally open-ended. The planner is responsible for
    decomposing a goal into concrete :class:`JobSpec` instances using the
    :class:`~job_star.core.job_types.JobTypeRegistry`.
    """

    id: str
    description: str
    urgency: Urgency = Urgency.SOON
    # Arbitrary structured payload describing the goal (e.g. repo path, target files,
    # test command, acceptance criteria). The planner interprets this per job type.
    context: Mapping[str, Any] = field(default_factory=dict)
    # Optional deadline as an ISO-8601 string; not enforced, only used for scoring.
    deadline: Optional[str] = None


@dataclass(frozen=True)
class JobSpec:
    """A concrete job Job-Star has decided is worth submitting.

    This is the unit the selector emits and that the execution layer hands to
    gatehouse-ai's async job interface.
    """

    # Stable identifier unique within a planning session. Used for dependency
    # tracking before the gatehouse job id is known.
    local_id: str
    job_type: str
    goal_id: str
    urgency: Urgency
    # Structured inputs for the job. Must conform to the JobType's input schema.
    inputs: Mapping[str, Any] = field(default_factory=dict)
    # Local ids of JobSpecs that must complete successfully before this one can run.
    depends_on: tuple[str, ...] = ()
    # Human-readable rationale for why the planner proposed this job. Useful for
    # logging, review, and future learning loops.
    rationale: str = ""


@dataclass(frozen=True)
class ResourceConstraints:
    """Snapshot of what resources are currently available for new jobs."""

    # Maximum concurrent running jobs the execution layer will allow.
    max_concurrent: int = 4
    # Current number of jobs in RUNNING state.
    running_count: int = 0
    # Soft token/cost budget remaining for the session (None = unlimited).
    budget_remaining: Optional[float] = None
    # Job type names currently blocked (e.g. rate-limited or unhealthy).
    blocked_types: frozenset[str] = field(default_factory=frozenset)

    @property
    def has_capacity(self) -> bool:
        return self.running_count < self.max_concurrent


@dataclass
class SystemState:
    """A snapshot of the world the planner/selector reason about.

    The execution layer is responsible for populating this from gatehouse-ai's
    job status responses before each decision cycle.
    """

    # Jobs we know about, keyed by their gatehouse job id (or local_id pre-submit).
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Resource constraints for the upcoming cycle.
    constraints: ResourceConstraints = field(default_factory=ResourceConstraints)
    # Goals currently active in the session.
    active_goals: list[Goal] = field(default_factory=list)
    # Free-form metadata the planner/selector may use (e.g. session id, timestamps).
    metadata: dict[str, Any] = field(default_factory=dict)
    # Timestamp this snapshot was taken.
    captured_at: datetime = field(default_factory=_utcnow)

    # ---- Convenience accessors -------------------------------------------

    def jobs_with_status(self, status: JobStatus) -> list[dict[str, Any]]:
        return [j for j in self.jobs.values() if j.get("status") == status.value]

    @property
    def completed_job_types(self) -> set[str]:
        """Set of job_type names that have at least one completed instance."""
        return {
            j["job_type"]
            for j in self.jobs_with_status(JobStatus.COMPLETED)
            if "job_type" in j
        }

    @property
    def running_job_types(self) -> set[str]:
        return {
            j["job_type"]
            for j in self.jobs_with_status(JobStatus.RUNNING)
            if "job_type" in j
        }

    def completed_local_ids(self) -> set[str]:
        """Local ids of jobs that have completed successfully."""
        return {
            j["local_id"]
            for j in self.jobs_with_status(JobStatus.COMPLETED)
            if "local_id" in j
        }

    def failed_local_ids(self) -> set[str]:
        return {
            j["local_id"]
            for j in self.jobs_with_status(JobStatus.FAILED)
            if "local_id" in j
        }
