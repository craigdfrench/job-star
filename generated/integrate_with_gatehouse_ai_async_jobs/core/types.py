"""Shared types for Job-Star core components."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import UUID, uuid4


class Priority(enum.IntEnum):
    """Job priority levels. Lower integer = higher priority (heap ordering)."""

    CRITICAL = 0
    HIGH = 10
    NORMAL = 50
    LOW = 80
    BACKGROUND = 100


class ScheduleStrategy(enum.Enum):
    """How a job should be dispatched by the scheduler."""

    IMMEDIATE = "immediate"       # Run as soon as a slot is available
    DEFERRED = "deferred"         # Run at or after a specific time
    BATCHED = "batched"            # Accumulate and flush together
    RATE_LIMITED = "rate_limited" # Respect a token-bucket rate limit


class JobState(enum.Enum):
    """Lifecycle states for a scheduled job."""

    PENDING = "pending"
    READY = "ready"           # Eligible to run, waiting for a concurrency slot
    RUNNING = "running"
    RETRYING = "retrying"     # Scheduled for a retry after backoff
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class JobSpec:
    """Declarative description of a job to be executed.

    The `execute` callable is the bridge to gatehouse-ai's async job interface.
    It must be an async callable that accepts the JobContext and returns a
    JobResult (or raises an exception).
    """

    name: str
    execute: Callable[..., Any]
    priority: Priority = Priority.NORMAL
    strategy: ScheduleStrategy = ScheduleStrategy.IMMEDIATE
    run_at: Optional[datetime] = None       # For DEFERRED
    batch_key: Optional[str] = None         # For BATCHED — group jobs by key
    rate_key: Optional[str] = None         # For RATE_LIMITED — group by limiter
    max_retries: int = 3
    backoff_base: float = 1.0              # seconds; exponential base
    backoff_max: float = 60.0             # cap per-retry delay
    backoff_jitter: float = 0.2           # fraction of delay to randomize
    timeout: Optional[float] = None       # per-execution timeout in seconds
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.strategy is ScheduleStrategy.DEFERRED and self.run_at is None:
            raise ValueError("DEFERRED jobs require run_at")
        if self.strategy is ScheduleStrategy.RATE_LIMITED and self.rate_key is None:
            raise ValueError("RATE_LIMITED jobs require rate_key")


@dataclass
class JobContext:
    """Runtime context passed to the execute callable."""

    job_id: UUID
    spec: JobSpec
    attempt: int
    metadata: dict[str, Any]


@dataclass
class JobResult:
    """Result of executing a job."""

    job_id: UUID
    success: bool
    value: Any = None
    error: Optional[str] = None
    attempt: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @property
    def duration(self) -> Optional[float]:
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).timestamp()
        return None


def utcnow() -> datetime:
    """Timezone-aware UTC now, for testability and consistency."""
    return datetime.now(timezone.utc)


// --- DUPLICATE BLOCK ---

"""
Shared types for Job-Star's execution strategy layer.

These types define the contract between Job-Star's decision layer
and gatehouse-ai's async job interface.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Mapping, Optional, Protocol


class JobType(str, enum.Enum):
    """Kinds of jobs Job-Star can dispatch to gatehouse-ai.

    Each enum member maps to a payload builder in the
    ``payload_builders`` package. Add new members here and create
    a corresponding builder module to extend the system.
    """

    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    DOCUMENTATION = "documentation"
    TESTING = "testing"
    ANALYSIS = "analysis"
    REFACTOR = "refactor"
    RESEARCH = "research"
    MIGRATION = "migration"


@dataclass(frozen=True)
class JobSpec:
    """A job request from Job-Star's decision layer.

    This is the input the Executor receives. It contains everything
    needed to construct a gatehouse-ai job payload: the job type,
    a natural-language objective, relevant file paths, and any
    user-supplied parameters.
    """

    job_type: JobType
    objective: str
    target_files: tuple[str, ...] = ()
    parameters: Mapping[str, Any] = field(default_factory=dict)
    context_files: tuple[str, ...] = ()
    requested_by: str = "job-star"
    priority: int = 5  # 1 (highest) to 10 (lowest); default normal


@dataclass(frozen=True)
class ExecutionParameters:
    """Execution-level parameters attached to every gatehouse job.

    These control *how* the job runs, not *what* it does.
    The Executor derives sensible defaults per JobType and allows
    the caller to override via JobSpec.parameters.
    """

    timeout: timedelta = timedelta(minutes=30)
    max_retries: int = 2
    retry_backoff_seconds: int = 30
    priority: int = 5
    resource_profile: str = "standard"  # "light", "standard", "heavy"
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionContext:
    """Context the job executes within.

    This is passed to gatehouse-ai so the worker knows where to
    operate, what environment variables to set, and what
    permissions the job has.
    """

    working_directory: str
    environment: Mapping[str, str] = field(default_factory=dict)
    permissions: tuple[str, ...] = ("read",)
    git_ref: Optional[str] = None
    workspace_id: Optional[str] = None


@dataclass(frozen=True)
class JobPayload:
    """The fully constructed payload handed to gatehouse-ai.

    This is the Executor's output. It bundles the task-specific
    payload body (from the builder), the execution parameters,
    and the execution context into a single dispatchable unit.
    """

    job_type: JobType
    body: Mapping[str, Any]
    parameters: ExecutionParameters
    context: ExecutionContext
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dispatch_dict(self) -> dict[str, Any]:
        """Serialize to the dict gatehouse-ai expects for job submission."""
        return {
            "type": self.job_type.value,
            "body": dict(self.body),
            "parameters": {
                "timeout_seconds": int(self.parameters.timeout.total_seconds()),
                "max_retries": self.parameters.max_retries,
                "retry_backoff_seconds": self.parameters.retry_backoff_seconds,
                "priority": self.parameters.priority,
                "resource_profile": self.parameters.resource_profile,
                "tags": list(self.parameters.tags),
            },
            "context": {
                "working_directory": self.context.working_directory,
                "environment": dict(self.context.environment),
                "permissions": list(self.context.permissions),
                "git_ref": self.context.git_ref,
                "workspace_id": self.context.workspace_id,
            },
            "metadata": dict(self.metadata),
        }


class PayloadBuilder(Protocol):
    """Protocol every payload builder satisfies.

    A builder takes a JobSpec and returns the task-specific body
    portion of the payload. The Executor wraps this body with
    ExecutionParameters and ExecutionContext.
    """

    def build(self, spec: JobSpec) -> Mapping[str, Any]:
        """Construct the task-specific payload body from a JobSpec."""
        ...

    def default_parameters(self) -> ExecutionParameters:
        """Return default execution parameters for this job type."""
        ...

    def required_permissions(self) -> tuple[str, ...]:
        """Return the permissions this job type needs."""
        ...


// --- DUPLICATE BLOCK ---

"""
Core data structures for Job-Star orchestration.

These types flow through the pipeline:
  Planner → JobProposal → Selector → JobSelection → Scheduler → ScheduledJob → Executor
  JobManager → JobRecord → ResultProcessor → OrchestratorState (feeds back to Planner)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4


class JobStatus(str, Enum):
    """Lifecycle states for a job tracked by Job-Star."""
    PROPOSED = "proposed"       # Planner suggested it
    SELECTED = "selected"       # Selector picked it
    SCHEDULED = "scheduled"     # Scheduler assigned timing
    SUBMITTED = "submitted"     # Sent to gatehouse-ai
    RUNNING = "running"         # gatehouse-ai confirmed execution
    COMPLETED = "completed"     # Finished successfully
    FAILED = "failed"           # Finished with error
    CANCELLED = "cancelled"     # Aborted before/during run
    SKIPPED = "skipped"         # Selector/scheduler decided not to run


class Priority(int, Enum):
    """Job priority levels. Higher = more urgent."""
    LOW = 1
    NORMAL = 5
    HIGH = 8
    CRITICAL = 10


@dataclass
class JobProposal:
    """
    A suggestion from the Planner about something that could be run.

    The Planner examines current state and external signals to propose
    candidate jobs. Not all proposals will be selected or executed.
    """
    id: UUID = field(default_factory=uuid4)
    name: str = ""
    description: str = ""
    job_type: str = ""                  # Maps to a gatehouse-ai job type
    payload: dict[str, Any] = field(default_factory=dict)
    priority: Priority = Priority.NORMAL
    estimated_duration_s: float = 0.0   # Planner's best guess
    tags: list[str] = field(default_factory=list)
    depends_on: list[UUID] = field(default_factory=list)  # Proposal IDs this depends on
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if not self.name:
            self.name = f"{self.job_type}_{self.id.hex[:8]}"


@dataclass
class JobSelection:
    """
    Output of the Selector: which proposal was chosen and why.

    The Selector applies constraints (capacity, dependencies, cost,
    deduplication) to pick the best proposal from a batch.
    """
    proposal: JobProposal
    reason: str = ""                    # Human-readable explanation
    score: float = 0.0                  # Selection score for logging
    alternatives_considered: int = 0


@dataclass
class ScheduledJob:
    """
    Output of the Scheduler: when and how a selected job should run.

    The Scheduler may delay execution, batch it with others, or
    determine it's not ready yet.
    """
    selection: JobSelection
    ready: bool = True                  # Can execute now?
    run_at: Optional[datetime] = None   # When to execute (None = now)
    batch_id: Optional[UUID] = None     # Grouped with other jobs?
    wait_reason: str = ""               # If not ready, why not?


@dataclass
class JobRecord:
    """
    A job as tracked by the JobManager after submission to gatehouse-ai.

    This is the persistent state that flows between orchestrator cycles.
    """
    id: UUID = field(default_factory=uuid4)
    gatehouse_job_id: Optional[str] = None  # ID assigned by gatehouse-ai
    name: str = ""
    job_type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = JobStatus.PROPOSED
    priority: Priority = Priority.NORMAL
    submitted_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[dict[str, Any]] = None
    error: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.SKIPPED)

    @property
    def is_active(self) -> bool:
        return self.status in (JobStatus.SUBMITTED, JobStatus.RUNNING)


@dataclass
class OrchestratorState:
    """
    Snapshot of orchestrator state passed to the Planner each cycle.

    This is what the Planner uses to decide what to propose next.
    """
    active_jobs: list[JobRecord] = field(default_factory=list)
    recent_results: list[JobRecord] = field(default_factory=list)
    cycle_count: int = 0
    last_cycle_at: Optional[datetime] = None
    capacity_used: int = 0
    capacity_limit: int = 10
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def capacity_available(self) -> int:
        return max(0, self.capacity_limit - self.capacity_used)

    @property
    def has_capacity(self) -> bool:
        return self.capacity_available > 0
