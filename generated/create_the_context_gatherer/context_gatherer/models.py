"""
Data models for the Job-Star context gatherer.

These models are shared across collector modules (error_collector,
file_collector, git_collector) and the triage layer that consumes
their output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class IntakePriority(str, Enum):
    """Priority level for an intake request."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ErrorSeverity(str, Enum):
    """Severity level parsed from a log entry."""
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass
class IntakeRequest:
    """
    A user-submitted request describing a problem or task.

    Attributes:
        id: Unique identifier for the intake request.
        title: Short human-readable title.
        description: Full description of the problem.
        keywords: Pre-extracted keywords used for matching against
            logs, files, and git history. These should be lowercased
            and stripped of common stop words by the caller.
        priority: Initial priority assessment (may be refined by triage).
        submitted_at: When the intake was submitted.
        source_files: Optional list of files the user explicitly mentioned.
    """
    id: str
    title: str
    description: str
    keywords: list[str] = field(default_factory=list)
    priority: IntakePriority = IntakePriority.MEDIUM
    submitted_at: Optional[datetime] = None
    source_files: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Normalize keywords to lowercase for case-insensitive matching."""
        self.keywords = [kw.lower().strip() for kw in self.keywords if kw.strip()]


@dataclass
class ErrorEntry:
    """
    A single error log entry extracted from a log file.

    Attributes:
        timestamp: When the error occurred (parsed from log line).
        message: The error message text.
        source_file: Path of the log file this entry came from.
        line_number: Line number within the log file (1-indexed).
        severity: Parsed severity level, if determinable.
        raw_line: The original unmodified log line.
        matched_keywords: Which intake keywords matched this entry.
    """
    timestamp: Optional[datetime]
    message: str
    source_file: Path
    line_number: int
    severity: ErrorSeverity = ErrorSeverity.UNKNOWN
    raw_line: str = ""
    matched_keywords: list[str] = field(default_factory=list)

    def dedup_key(self) -> str:
        """
        A key for deduplication. Two entries with the same message
        and severity are considered duplicates regardless of timestamp
        (though the most recent timestamp is retained).
        """
        # Normalize whitespace in message for dedup
        normalized = " ".join(self.message.split())
        return f"{self.severity.value}:{normalized[:200]}"

    def __str__(self) -> str:
        ts = self.timestamp.isoformat() if self.timestamp else "unknown-time"
        return f"[{ts}] {self.severity.value.upper()} {self.message[:120]}"
