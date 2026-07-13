"""Schema for the context gatherer.

This module defines the data contract between the context gatherer agent
and the downstream triage agent. The gatherer takes an IntakeRequest and
produces a ContextBundle containing related files, git history, and recent
errors relevant to the request.

All gathered context fields are optional: a bundle with only the original
request populated is a valid (if unhelpful) result, signaling that the
gatherer found nothing relevant.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field


class RequestKind(str, Enum):
    """High-level category of an intake request.

    The gatherer uses this to tune which sources it probes (e.g., a bug
    request triggers an error-log scan; a refactor request triggers a
    broader file search).
    """

    BUG = "bug"
    FEATURE = "feature"
    REFACTOR = "refactor"
    QUESTION = "question"
    CHORE = "chore"
    UNKNOWN = "unknown"


class IntakeRequest(BaseModel):
    """The raw request as it enters the system, before any enrichment.

    This is the gatherer's input. It is intentionally minimal: just enough
    to identify what the user wants and where to look.
    """

    id: str = Field(..., description="Stable identifier for the request.")
    title: str = Field(..., description="Short human-readable summary.")
    description: str = Field(
        ..., description="Full request body. May be long and unstructured."
    )
    kind: RequestKind = Field(
        default=RequestKind.UNKNOWN,
        description="Coarse category. UNKNOWN if not yet classified.",
    )
    repo_path: Optional[Path] = Field(
        default=None,
        description="Local filesystem path to the repo, if known. "
        "None means the gatherer cannot inspect files or git.",
    )
    requested_by: Optional[str] = Field(
        default=None, description="User or system that submitted the request."
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the request was received.",
    )
    hints: list[str] = Field(
        default_factory=list,
        description="Optional explicit hints from the submitter, e.g. "
        "['auth module', 'login flow']. The gatherer treats these as "
        "high-signal search seeds.",
    )

    model_config = {"use_enum_values": True}


class FileMatch(BaseModel):
    """A file the gatherer believes is relevant to the request.

    Full file contents are intentionally not stored here to keep bundles
    small. The triage agent can request specific files by path if it
    needs the full text.
    """

    path: Path = Field(..., description="Repo-relative or absolute path to the file.")
    reason: str = Field(
        ..., description="Why this file was selected, e.g. 'keyword match: login'."
    )
    score: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Relevance score in [0,1]. Higher is more relevant. "
        "The gatherer's ranking heuristic; not a calibrated probability.",
    )
    snippet: Optional[str] = Field(
        default=None,
        description="Optional short excerpt (<=500 chars) showing the most "
        "relevant region of the file. Useful for triage without a full read.",
    )
    last_modified: Optional[datetime] = Field(
        default=None, description="mtime of the file, if available."
    )
    language: Optional[str] = Field(
        default=None,
        description="Detected language/file type, e.g. 'python', 'typescript'. "
        "Used by triage to pick the right tooling.",
    )


class GitHistory(BaseModel):
    """A slice of recent git activity relevant to the request.

    The gatherer runs targeted `git log` / `git blame` queries seeded by
    the request's keywords and matched files. This is not a full repo
    history dump.
    """

    commit_hash: str = Field(..., description="Full or short commit SHA.")
    author: Optional[str] = Field(default=None, description="Commit author name.")
    authored_at: Optional[datetime] = Field(
        default=None, description="When the change was authored."
    )
    summary: str = Field(..., description="First line of the commit message.")
    files_changed: list[Path] = Field(
        default_factory=list,
        description="Files touched by this commit. Useful for blast-radius "
        "analysis during triage.",
    )
    relevance: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="How relevant this commit is to the request, per the "
        "gatherer's heuristic.",
    )
    touches_matched_files: bool = Field(
        default=False,
        description="True if this commit modified any FileMatch in the bundle. "
        "High-signal: the request is about code that recently churned.",
    )


class ErrorEntry(BaseModel):
    """A recent error the gatherer surfaced as possibly related.

    Sources include log files, CI artifacts, and in-memory error buffers
    the gatherer has access to. Errors are best-effort and time-bounded.
    """

    source: str = Field(
        ..., description="Where the error came from, e.g. 'logs/app.log', 'ci:build-42'."
    )
    message: str = Field(..., description="The error message text.")
    timestamp: Optional[datetime] = Field(
        default=None, description="When the error occurred, if known."
    )
    stack_trace: Optional[str] = Field(
        default=None,
        description="Optional stack trace excerpt. Truncated to keep the "
        "bundle small; triage can fetch the full trace if needed.",
    )
    file: Optional[Path] = Field(
        default=None,
        description="File implicated by the error, if the source names one.",
    )
    line: Optional[int] = Field(
        default=None, description="Line number implicated, if known."
    )
    score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Relevance to the request, per the gatherer's heuristic.",
    )


class GathererStats(BaseModel):
    """Instrumentation about the gatherer run itself.

    Useful for debugging why a bundle is thin or for cost/latency tracking.
    """

    started_at: datetime
    finished_at: datetime
    files_considered: int = Field(
        default=0, description="Total files scanned before filtering/ranking."
    )
    git_commits_scanned: int = Field(
        default=0, description="Total commits examined in the relevant window."
    )
    errors_scanned: int = Field(
        default=0, description="Total error entries examined before filtering."
    )
    sources_tried: list[str] = Field(
        default_factory=list,
        description="Names of context sources the gatherer attempted, e.g. "
        "['filesystem', 'git', 'logs']. Useful when a source is missing.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal issues encountered, e.g. 'git not installed'.",
    )

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


class ContextBundle(BaseModel):
    """The full output of the context gatherer.

    This is what the triage agent consumes. The original request is
    echoed back so triage doesn't need to re-fetch it, and every
    enriched field is optional so a sparse bundle is still valid.
    """

    request: IntakeRequest = Field(
        ..., description="The original request, echoed for triage convenience."
    )
    files: list[FileMatch] = Field(
        default_factory=list,
        description="Ranked list of files relevant to the request. "
        "Sorted by score descending; the gatherer caps the count to "
        "keep the bundle bounded.",
    )
    git_history: list[GitHistory] = Field(
        default_factory=list,
        description="Recent commits relevant to the request, most relevant first.",
    )
    errors: list[ErrorEntry] = Field(
        default_factory=list,
        description="Recent errors possibly related to the request, ranked.",
    )
    stats: Optional[GathererStats] = Field(
        default=None,
        description="Instrumentation for this gatherer run. May be None if "
        "the bundle was constructed by hand or by a stub gatherer.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Free-form notes from the gatherer, e.g. 'no git repo found; "
        "file matches are keyword-only'.",
    )

    def is_empty(self) -> bool:
        """True if the gatherer found no enriched context at all.

        A bundle is never truly empty (it always has the request), but
        this signals triage that it's working with no supporting evidence.
        """
        return not (self.files or self.git_history or self.errors)

    def top_files(self, n: int = 5) -> list[FileMatch]:
        """Return the n highest-scoring files."""
        return sorted(self.files, key=lambda f: f.score, reverse=True)[:n]

    def top_errors(self, n: int = 3) -> list[ErrorEntry]:
        """Return the n highest-scoring errors."""
        return sorted(self.errors, key=lambda e: e.score, reverse=True)[:n]
