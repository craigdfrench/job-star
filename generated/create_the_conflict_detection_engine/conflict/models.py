"""
Data models for conflict detection results.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from job_star.conflict.types import ConflictStatus, ConflictType, Severity


@dataclass
class Conflict:
    """A single detected conflict between two or more goals."""

    id: str = field(default_factory=lambda: str(uuid4()))
    conflict_type: ConflictType = ConflictType.TENSION
    severity: Severity = Severity.LOW
    goal_ids: tuple[str, ...] = ()
    description: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    suggested_resolution: Optional[str] = None
    status: ConflictStatus = ConflictStatus.DETECTED
    detected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    domain: Optional[str] = None  # The domain where the conflict was detected

    def acknowledge(self) -> None:
        self.status = ConflictStatus.ACKNOWLEDGED

    def resolve(self) -> None:
        self.status = ConflictStatus.RESOLVED

    def ignore(self) -> None:
        self.status = ConflictStatus.IGNORED

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conflict_type": self.conflict_type.value,
            "severity": self.severity.value,
            "goal_ids": list(self.goal_ids),
            "description": self.description,
            "evidence": self.evidence,
            "suggested_resolution": self.suggested_resolution,
            "status": self.status.value,
            "detected_at": self.detected_at,
            "domain": self.domain,
        }


@dataclass
class ConflictReport:
    """A collection of conflicts detected in a single analysis pass."""

    conflicts: list[Conflict] = field(default_factory=list)
    analyzed_goal_count: int = 0
    analysis_timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def has_blocking(self) -> bool:
        return any(c.severity == Severity.BLOCKING for c in self.conflicts)

    @property
    def has_high(self) -> bool:
        return any(c.severity == Severity.HIGH for c in self.conflicts)

    def by_type(self, conflict_type: ConflictType) -> list[Conflict]:
        return [c for c in self.conflicts if c.conflict_type == conflict_type]

    def by_severity(self, severity: Severity) -> list[Conflict]:
        return [c for c in self.conflicts if c.severity == severity]

    def sorted_by_severity(self) -> list[Conflict]:
        order = {
            Severity.BLOCKING: 0,
            Severity.HIGH: 1,
            Severity.MEDIUM: 2,
            Severity.LOW: 3,
            Severity.INFO: 4,
        }
        return sorted(self.conflicts, key=lambda c: order.get(c.severity, 99))

    def to_dict(self) -> dict[str, Any]:
        return {
            "conflicts": [c.to_dict() for c in self.conflicts],
            "analyzed_goal_count": self.analyzed_goal_count,
            "analysis_timestamp": self.analysis_timestamp,
        }
