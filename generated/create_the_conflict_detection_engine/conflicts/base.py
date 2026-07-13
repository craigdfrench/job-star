"""
Base classes and interfaces for conflict detection.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from datetime import datetime


class ConflictType(Enum):
    """Types of conflicts that can be detected between goals."""
    DUPLICATE = "duplicate"
    CONTRADICTION = "contradiction"
    COMPETING_RESOURCE = "competing_resource"
    TENSION = "tension"


class ConflictSeverity(Enum):
    """Severity levels for detected conflicts."""
    INFO = "info"          # Worth noting, no action required
    LOW = "low"            # Minor overlap, monitor
    MEDIUM = "medium"      # Likely needs attention
    HIGH = "high"          # Action recommended
    CRITICAL = "critical"  # Immediate action required


@dataclass
class Goal:
    """Minimal goal representation for conflict detection.

    This is a lightweight interface — the full Goal model in Job-Star
    can be adapted to this shape, or a mapper can convert.
    """
    id: str
    title: str
    description: str
    domain: str = "general"
    urgency: str = "idle-opportunistic"
    steps: list[str] = field(default_factory=list)
    resources: list[str] = field(default_factory=list)
    expected_outputs: list[str] = field(default_factory=list)
    created_at: Optional[datetime] = None
    tags: list[str] = field(default_factory=list)


@dataclass
class ConflictResult:
    """Base result from any conflict detection check."""
    conflict_type: ConflictType
    severity: ConflictSeverity
    goal_a_id: str
    goal_b_id: str
    confidence: float  # 0.0 to 1.0
    explanation: str
    suggested_action: str = ""
    signals: dict = field(default_factory=dict)  # breakdown of contributing signals


// --- DUPLICATE BLOCK ---

# Future implementation
def _text_similarity(a: str, b: str) -> float:
    emb_a = model.encode(a)
    emb_b = model.encode(b)
    return cosine_similarity(emb_a, emb_b)


// --- DUPLICATE BLOCK ---

"""
Base types for the Job-Star conflict detection engine.

This file defines the shared interface used by all detectors:
duplicate, contradiction, competing-resource, and tension.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Protocol

from jobstar.model.resource import ResourceDemand


class ConflictKind(Enum):
    DUPLICATE = "duplicate"
    CONTRADICTION = "contradiction"
    COMPETING_RESOURCE = "competing_resource"
    TENSION = "tension"


class ConflictSeverity(Enum):
    NEGLIGIBLE = "negligible"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Conflict:
    kind: ConflictKind
    severity: ConflictSeverity
    goal_ids: list[str]
    title: str
    description: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    suggested_resolutions: list[str] = field(default_factory=list)
    detected_at: datetime = field(default_factory=datetime.utcnow)


class GoalRef(Protocol):
    """Minimal interface a goal must expose for conflict detection."""
    id: str
    domain: str

    def effective_start(self) -> datetime: ...
    def effective_deadline(self) -> datetime: ...
    def resource_demands(self) -> list[ResourceDemand]: ...


class Detector(Protocol):
    def detect(self, goals: Iterable[GoalRef]) -> list[Conflict]: ...
