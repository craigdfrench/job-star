"""Shared types for the conflict detection engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class ConflictType(str, Enum):
    DUPLICATE = "duplicate"
    CONTRADICTION = "contradiction"
    COMPETING_RESOURCE = "competing_resource"
    TENSION = "tension"


class ConflictSeverity(str, Enum):
    """How strongly two goals conflict."""
    BLOCKING = "blocking"       # cannot both be achieved
    HIGH = "high"               # very likely mutually exclusive
    MEDIUM = "medium"           # probable contradiction, needs review
    LOW = "low"                 # weak signal, possibly coincidental


@dataclass
class Goal:
    """Minimal goal representation for conflict detection.

    The full Job-Star Goal type is richer; this is the view the
    conflict engine operates on.
    """
    id: str
    title: str
    description: str = ""
    domain: str = "general"
    success_criteria: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    target_state: Optional[str] = None
    metrics: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Conflict:
    """A detected conflict between two goals."""
    type: ConflictType
    goal_a_id: str
    goal_b_id: str
    severity: ConflictSeverity
    confidence: float  # 0.0 - 1.0
    explanation: str
    evidence: list[str] = field(default_factory=list)
    detector: str = ""
    detected_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.value,
            "goal_a_id": self.goal_a_id,
            "goal_b_id": self.goal_b_id,
            "severity": self.severity.value,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "evidence": self.evidence,
            "detector": self.detector,
            "detected_at": self.detected_at.isoformat(),
        }
