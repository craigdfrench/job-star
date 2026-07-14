"""Unified data models for Job-Star.

These map directly to the Postgres schema (sql/schema.sql). All components
share these models — no more duplicate definitions.
"""

from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


def _parse_jsonb(val: Any) -> Any:
    """Parse JSONB values from asyncpg (may be str or already parsed)."""
    if val is None:
        return None
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, ValueError):
            return {}
    return val


def _parse_array(val: Any) -> list:
    """Parse array values from asyncpg."""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, ValueError):
            return []
    return list(val)


# ============================================================================
# Enums — controlled vocabulary matching the DB schema
# ============================================================================

class Domain(str, Enum):
    CODING = "coding"
    PERSONAL = "personal"
    INFRA = "infra"
    META = "meta"


class Urgency(str, Enum):
    IMPERATIVE = "imperative"
    SOON = "soon"
    IDLE_OPPORTUNISTIC = "idle-opportunistic"
    TIMED = "timed"


class GoalStatus(str, Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    ABANDONED = "abandoned"


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class ConflictType(str, Enum):
    DUPLICATE = "duplicate"
    CONTRADICTORY = "contradictory"
    COMPETING_RESOURCE = "competing_resource"
    TENSION = "tension"


class ConflictResolution(str, Enum):
    UNRESOLVED = "unresolved"
    AUTO_MERGED = "auto_merged"
    USER_DECIDED = "user_decided"
    DISMISSED = "dismissed"


# ============================================================================
# Core models — map to database tables
# ============================================================================

@dataclass
class Goal:
    """A goal in the registry. Maps to the `goals` table."""
    id: str = field(default_factory=lambda: str(uuid4()))
    parent_id: Optional[str] = None
    title: str = ""
    description: Optional[str] = None
    domain: Domain = Domain.CODING
    status: GoalStatus = GoalStatus.ACTIVE
    urgency: Urgency = Urgency.SOON
    progress: float = 0.0
    blockers: list[str] = field(default_factory=list)
    deadline: Optional[datetime] = None
    source: str = "intake"
    expert: Optional[str] = None  # expert agent that owns this goal (NULL = generic)
    requested_by: Optional[str] = None  # who requested this goal (for multi-user/family)
    vikunja_task_id: Optional[int] = None  # Vikunja task ID if synced from Vikunja
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_row(cls, row: dict) -> "Goal":
        """Create from a database row (dict)."""
        return cls(
            id=str(row["id"]),
            parent_id=str(row["parent_id"]) if row.get("parent_id") else None,
            title=row["title"],
            description=row.get("description"),
            domain=Domain(row.get("domain", "coding")),
            status=GoalStatus(row.get("status", "active")),
            urgency=Urgency(row.get("urgency", "soon")),
            progress=float(row.get("progress", 0.0)),
            blockers=_parse_array(row.get("blockers", [])),
            deadline=row.get("deadline"),
            source=row.get("source", "intake"),
            expert=row.get("expert"),
            requested_by=row.get("requested_by"),
            vikunja_task_id=row.get("vikunja_task_id"),
            metadata=_parse_jsonb(row.get("metadata", {})) or {},
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


@dataclass
class Step:
    """A step within a goal. Maps to the `goal_steps` table."""
    id: str = field(default_factory=lambda: str(uuid4()))
    goal_id: str = ""
    title: str = ""
    description: Optional[str] = None
    status: StepStatus = StepStatus.PENDING
    order_index: int = 0
    depends_on: list[str] = field(default_factory=list)  # step IDs this depends on (DAG)
    result: Optional[dict[str, Any]] = None
    model: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost: float = 0.0
    attempted_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_row(cls, row: dict) -> "Step":
        return cls(
            id=str(row["id"]),
            goal_id=str(row["goal_id"]),
            title=row["title"],
            description=row.get("description"),
            status=StepStatus(row.get("status", "pending")),
            order_index=row.get("order_index", 0),
            depends_on=[str(d) for d in _parse_array(row.get("depends_on", []))],
            result=_parse_jsonb(row.get("result")),
            model=row.get("model"),
            input_tokens=row.get("input_tokens"),
            output_tokens=row.get("output_tokens"),
            cost=float(row.get("cost", 0.0)),
            attempted_at=row.get("attempted_at"),
            completed_at=row.get("completed_at"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )


@dataclass
class Conflict:
    """A conflict between two goals. Maps to `goal_conflicts` table."""
    id: str = field(default_factory=lambda: str(uuid4()))
    goal_a_id: str = ""
    goal_b_id: str = ""
    conflict_type: ConflictType = ConflictType.DUPLICATE
    description: Optional[str] = None
    resolution: ConflictResolution = ConflictResolution.UNRESOLVED
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime] = None


# ============================================================================
# Intake models — what comes in before it becomes a goal
# ============================================================================

@dataclass
class IntakeRequest:
    """Raw incoming request before triage."""
    title: str
    description: str = ""
    source: str = "manual"  # manual, web, telegram, voice, api
    urgency_override: Optional[Urgency] = None  # user can specify
    domain_override: Optional[Domain] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return f"{self.title} {self.description}".strip().lower()


@dataclass
class TriageResult:
    """Output of the triage engine."""
    domain: Domain
    urgency: Urgency
    request_type: str  # bug, feature, refactor, question, chore, etc.
    confidence: float = 0.0
    is_duplicate: bool = False
    duplicate_of: Optional[str] = None  # goal ID if duplicate
    duplicate_confidence: float = 0.0
    keywords: list[str] = field(default_factory=list)
    rationale: str = ""
    expert: Optional[str] = None  # expert agent that should own this goal


@dataclass
class RoutingDecision:
    """Output of the router — which model to use."""
    model: str
    provider: str
    reason: str
    estimated_cost: float = 0.0
    complexity: str = "moderate"  # trivial, simple, moderate, complex


@dataclass
class ExecutionResult:
    """Output of executing a step with an AI model."""
    content: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    success: bool = True
    error: Optional[str] = None
    # Gatehouse-provided metadata from usage.x_gatehouse (dev server).
    # Contains cost_class, routing_advice, quota_windows, retail_value, etc.
    x_gatehouse: dict[str, Any] = field(default_factory=dict)