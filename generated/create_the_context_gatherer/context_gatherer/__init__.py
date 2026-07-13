"""Job-Star context gatherer.

Examines intake requests and gathers related files, git history, and
recent errors before triage.
"""

from context_gatherer.models import IntakeRequest, Signal, Severity
from context_gatherer.parser import parse_intake

__all__ = ["IntakeRequest", "Signal", "Severity", "parse_intake"]


// --- DUPLICATE BLOCK ---

"""Data models for the context gatherer."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Severity(str, Enum):
    """Triage severity levels, ordered from most to least urgent."""

    BLOCKER = "blocker"
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"

    @classmethod
    def from_hint(cls, hint: Optional[str]) -> "Severity":
        """Map a free-text severity hint to a known level.

        Falls back to UNKNOWN when no confident mapping exists.
        """
        if not hint:
            return cls.UNKNOWN
        h = hint.strip().lower()
        mapping = {
            "blocker": cls.BLOCKER,
            "p0": cls.BLOCKER,
            "sev0": cls.BLOCKER,
            "sev-0": cls.BLOCKER,
            "critical": cls.CRITICAL,
            "p1": cls.CRITICAL,
            "sev1": cls.CRITICAL,
            "sev-1": cls.CRITICAL,
            "urgent": cls.CRITICAL,
            "high": cls.HIGH,
            "p2": cls.HIGH,
            "sev2": cls.HIGH,
            "sev-2": cls.HIGH,
            "medium": cls.MEDIUM,
            "normal": cls.MEDIUM,
            "p3": cls.MEDIUM,
            "sev3": cls.MEDIUM,
            "sev-3": cls.MEDIUM,
            "low": cls.LOW,
            "minor": cls.LOW,
            "p4": cls.LOW,
            "sev4": cls.LOW,
            "sev-4": cls.LOW,
            "trivial": cls.LOW,
        }
        return mapping.get(h, cls.UNKNOWN)


@dataclass(frozen=True)
class Signal:
    """A single extracted searchable signal.

    Attributes:
        value: The extracted string (e.g. a file path, error message).
        kind: Category of the signal (file_path, component, error, keyword).
        confidence: 0.0–1.0 estimate of how reliable this signal is.
        source: Short description of where/how it was found.
    """

    value: str
    kind: str
    confidence: float
    source: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {self.confidence}")
        if not self.value.strip():
            raise ValueError("signal value must be non-empty")
        if not self.kind.strip():
            raise ValueError("signal kind must be non-empty")


@dataclass(frozen=True)
class IntakeRequest:
    """Structured representation of a raw intake request.

    Produced by :func:`context_gatherer.parser.parse_intake`.

    Attributes:
        raw_text: The original, unmodified intake text.
        file_paths: Signals pointing at file paths.
        components: Signals naming components/modules/classes.
        errors: Signals describing error messages or stack traces.
        keywords: General search keywords.
        severity: Best-effort severity derived from hints.
        severity_hint: The raw text fragment that produced the severity.
    """

    raw_text: str
    file_paths: tuple[Signal, ...] = field(default_factory=tuple)
    components: tuple[Signal, ...] = field(default_factory=tuple)
    errors: tuple[Signal, ...] = field(default_factory=tuple)
    keywords: tuple[Signal, ...] = field(default_factory=tuple)
    severity: Severity = Severity.UNKNOWN
    severity_hint: Optional[str] = None

    @property
    def all_signals(self) -> tuple[Signal, ...]:
        """All signals, ordered by confidence descending."""
        combined = (
            *self.file_paths,
            *self.components,
            *self.errors,
            *self.keywords,
        )
        return tuple(sorted(combined, key=lambda s: s.confidence, reverse=True))

    def is_empty(self) -> bool:
        """True when no usable signals were extracted."""
        return not (
            self.file_paths or self.components or self.errors or self.keywords
        )


// --- DUPLICATE BLOCK ---

from .git_collector import (
    CommitInfo,
    CommitStat,
    GitCollector,
    GitCollectorConfig,
    GitHistory,
    collect_git_history,
)

__all__ = [
    "CommitInfo",
    "CommitStat",
    "GitCollector",
    "GitCollectorConfig",
    "GitHistory",
    "collect_git_history",
]
