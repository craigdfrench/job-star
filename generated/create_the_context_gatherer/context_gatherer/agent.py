"""ContextBundle: the structured output of the context gatherer.

Holds everything the triage stage needs to understand a request:
related files, recent git activity, and recent errors/logs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class FileHit:
    """A file deemed relevant to the intake request."""
    path: Path
    score: float  # 0.0–1.0 relevance score
    reason: str   # why this file was selected (keyword match, name match, etc.)
    last_modified: datetime | None = None
    size_bytes: int = 0


@dataclass
class GitCommit:
    """A recent git commit relevant to the request."""
    sha: str
    author: str
    date: datetime
    message: str
    files_changed: list[str] = field(default_factory=list)


@dataclass
class LogError:
    """A recent error or warning extracted from logs."""
    source: Path
    timestamp: datetime | None
    level: str        # ERROR, WARNING, CRITICAL, etc.
    message: str
    context_lines: list[str] = field(default_factory=list)  # surrounding lines


@dataclass
class ContextBundle:
    """Assembled context for a single intake request.

    This is the unit of work handed off to the triage agent.
    """
    raw_intake: str
    repo_root: Path
    keywords: list[str]
    files: list[FileHit] = field(default_factory=list)
    git_commits: list[GitCommit] = field(default_factory=list)
    log_errors: list[LogError] = field(default_factory=list)
    collected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    collector_timings: dict[str, float] = field(default_factory=dict)  # name -> seconds
    notes: list[str] = field(default_factory=list)

    # --- Convenience helpers ---

    @property
    def file_paths(self) -> list[Path]:
        return [f.path for f in self.files]

    @property
    def has_errors(self) -> bool:
        return len(self.log_errors) > 0

    @property
    def recent_commit_shas(self) -> list[str]:
        return [c.sha for c in self.git_commits]

    def summary(self) -> str:
        """Human-readable one-liner for logging/display."""
        return (
            f"ContextBundle(files={len(self.files)}, "
            f"commits={len(self.git_commits)}, "
            f"errors={len(self.log_errors)}, "
            f"keywords={self.keywords[:5]})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict for JSON storage / IPC."""
        return {
            "raw_intake": self.raw_intake,
            "repo_root": str(self.repo_root),
            "keywords": self.keywords,
            "files": [
                {
                    "path": str(f.path),
                    "score": f.score,
                    "reason": f.reason,
                    "last_modified": f.last_modified.isoformat() if f.last_modified else None,
                    "size_bytes": f.size_bytes,
                }
                for f in self.files
            ],
            "git_commits": [
                {
                    "sha": c.sha,
                    "author": c.author,
                    "date": c.date.isoformat(),
                    "message": c.message,
                    "files_changed": c.files_changed,
                }
                for c in self.git_commits
            ],
            "log_errors": [
                {
                    "source": str(e.source),
                    "timestamp": e.timestamp.isoformat() if e.timestamp else None,
                    "level": e.level,
                    "message": e.message,
                    "context_lines": e.context_lines,
                }
                for e in self.log_errors
            ],
            "collected_at": self.collected_at.isoformat(),
            "collector_timings": self.collector_timings,
            "notes": self.notes,
        }
