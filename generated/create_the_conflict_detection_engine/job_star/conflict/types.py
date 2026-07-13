"""
Conflict type definitions and data structures for Job-Star's conflict detection engine.

Defines the four conflict types and the structured output format for detected conflicts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4


class ConflictType(str, Enum):
    """The four categories of conflicts Job-Star detects between goals."""

    DUPLICATE = "duplicate"
    """Two goals that are essentially the same — redundant effort or double-counting."""

    CONTRADICTION = "contradiction"
    """Two goals that directly oppose each other — achieving one prevents the other."""

    COMPETING_RESOURCE = "competing_resource"
    """Two goals that need the same limited resource (time, money, attention, energy)."""

    TENSION = "tension"
    """Two goals that create friction or trade-offs — not impossible to combine,
    but doing both is harder than doing either alone."""


class ConflictSeverity(str, Enum):
    """How serious a detected conflict is."""

    INFO = "info"
    """Noted for awareness, no action needed (e.g., near-duplicate that's intentional)."""

    LOW = "low"
    """Minor friction; worth noting but unlikely to derail either goal."""

    MEDIUM = "medium"
    """Meaningful trade-off; the user should be aware and may want to adjust."""

    HIGH = "high"
    """Significant conflict; achieving both goals is difficult without explicit planning."""

    CRITICAL = "critical"
    """Direct contradiction or severe resource starvation; one goal will likely fail."""


@dataclass
class ConflictEvidence:
    """Evidence supporting a detected conflict."""

    source: str
    """Where the evidence came from: 'semantic', 'resource', 'temporal', 'domain', 'heuristic'."""

    description: str
    """Human-readable explanation of what was detected."""

    confidence: float
    """0.0 to 1.0 — how confident the detector is in this piece of evidence."""

    metadata: dict = field(default_factory=dict)
    """Additional structured data (e.g., shared resource names, overlap scores)."""


@dataclass
class ConflictReport:
    """A single detected conflict between two (or more) goals."""

    id: UUID = field(default_factory=uuid4)
    conflict_type: ConflictType = ConflictType.TENSION
    severity: ConflictSeverity = ConflictSeverity.LOW
    goal_ids: list[str] = field(default_factory=list)
    """IDs of the goals involved in this conflict (usually 2)."""

    title: str = ""
    """Short human-readable label for the conflict."""

    description: str = ""
    """Full explanation of the conflict."""

    evidence: list[ConflictEvidence] = field(default_factory=list)
    """Supporting evidence from detection strategies."""

    recommendation: str = ""
    """Suggested action: merge, prioritize, sequence, resource-allocate, or accept."""

    detected_at: datetime = field(default_factory=datetime.now)
    detector_version: str = "0.1.0"

    @property
    def aggregate_confidence(self) -> float:
        """Average confidence across all evidence pieces."""
        if not self.evidence:
            return 0.0
        return sum(e.confidence for e in self.evidence) / len(self.evidence)

    def to_dict(self) -> dict:
        return {
            "id": str(self.id),
            "conflict_type": self.conflict_type.value,
            "severity": self.severity.value,
            "goal_ids": self.goal_ids,
            "title": self.title,
            "description": self.description,
            "evidence": [
                {
                    "source": e.source,
                    "description": e.description,
                    "confidence": e.confidence,
                    "metadata": e.metadata,
                }
                for e in self.evidence
            ],
            "recommendation": self.recommendation,
            "detected_at": self.detected_at.isoformat(),
            "detector_version": self.detector_version,
            "aggregate_confidence": self.aggregate_confidence,
        }


@dataclass
class GoalSnapshot:
    """A minimal representation of a goal for conflict analysis.

    The conflict engine works with snapshots rather than full goal objects
    so it can operate on goals from any storage backend.
    """

    id: str
    title: str
    description: str = ""
    domain: str = ""
    """e.g., 'work', 'health', 'personal', 'learning', 'financial'."""

    resources: dict[str, float] = field(default_factory=dict)
    """Named resources this goal consumes, with estimated amounts.
    e.g., {'time_hours_week': 10, 'money_monthly': 500, 'focus_score': 7}"""

    deadline: Optional[datetime] = None
    tags: list[str] = field(default_factory=list)
    priority: int = 3
    """1 (highest) to 5 (lowest)."""

    success_criteria: str = ""
    """What 'done' looks like — helps detect contradictions."""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "domain": self.domain,
            "resources": self.resources,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "tags": self.tags,
            "priority": self.priority,
            "success_criteria": self.success_criteria,
        }
