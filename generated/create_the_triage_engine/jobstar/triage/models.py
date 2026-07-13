"""Data models for the triage engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class Domain(str, Enum):
    """The knowledge/work domain of a request."""
    META = "meta"          # Job-Star system itself, process, planning
    CODE = "code"          # Source code, implementation, logic
    DOCS = "docs"          # Documentation, README, guides
    DEVOPS = "devops"      # CI/CD, deployment, infrastructure
    RESEARCH = "research"  # Investigation, prototyping, exploration
    SECURITY = "security"  # Security vulnerabilities, hardening
    DATA = "data"          # Data models, migrations, schemas
    UI = "ui"             # Frontend, UX, visual
    UNKNOWN = "unknown"


class Urgency(str, Enum):
    """How soon the request needs attention."""
    NOW = "now"    # Blocking, production down, security issue
    SOON = "soon"  # Should be addressed in current cycle
    LATER = "later"  # Backlog, nice-to-have, planning


class RequestType(str, Enum):
    """The nature of the request."""
    BUG = "bug"
    FEATURE = "feature"
    REFACTOR = "refactor"
    QUESTION = "question"
    DOCS = "docs"
    CHORE = "chore"
    SECURITY = "security"
    UNKNOWN = "unknown"


class DuplicateStatus(str, Enum):
    """Result of duplicate check."""
    UNIQUE = "unique"
    DUPLICATE = "duplicate"
    RELATED = "related"  # Similar but not exact match


@dataclass
class IntakeRequest:
    """Raw incoming request before classification."""
    id: str
    title: str
    description: str
    source: str = "manual"  # manual, github, slack, email
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        """Combined text for classification."""
        parts = [self.title, self.description]
        parts.extend(self.tags)
        return " ".join(p for p in parts if p).lower()


@dataclass
class ClassificationResult:
    """Output of the classification engine."""
    request_id: str
    domain: Domain
    urgency: Urgency
    request_type: RequestType
    confidence: float  # 0.0 to 1.0
    duplicate_status: DuplicateStatus
    duplicate_of: Optional[str] = None  # Goal ID if duplicate
    related_goals: list[str] = field(default_factory=list)
    matched_signals: list[str] = field(default_factory=list)
    classified_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "domain": self.domain.value,
            "urgency": self.urgency.value,
            "request_type": self.request_type.value,
            "confidence": self.confidence,
            "duplicate_status": self.duplicate_status.value,
            "duplicate_of": self.duplicate_of,
            "related_goals": self.related_goals,
            "matched_signals": self.matched_signals,
            "classified_at": self.classified_at.isoformat(),
        }


// --- DUPLICATE BLOCK ---

"""Data models for the Job-Star triage engine."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Domain(str, Enum):
    META = "meta"
    BACKEND = "backend"
    FRONTEND = "frontend"
    INFRA = "infra"
    DATA = "data"
    DOCS = "docs"
    SECURITY = "security"
    UNKNOWN = "unknown"


class Urgency(str, Enum):
    NOW = "now"          # blocking, needs immediate attention
    SOON = "soon"        # should be done in current cycle
    LATER = "later"      # queued for near-term
    EVENTUALLY = "eventually"  # backlog / nice-to-have


class RequestType(str, Enum):
    BUG = "bug"
    FEATURE = "feature"
    REFACTOR = "refactor"
    QUESTION = "question"
    CHORE = "chore"
    UNKNOWN = "unknown"


class IntakeRequest(BaseModel):
    """Raw incoming request before triage."""
    id: UUID = Field(default_factory=uuid4)
    title: str
    description: str = ""
    source: str = "manual"  # e.g. "manual", "slack", "github-issue"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GoalRegistryEntry(BaseModel):
    """An entry in the goal registry — a known, tracked goal."""
    id: UUID
    title: str
    description: str = ""
    domain: Domain = Domain.UNKNOWN
    created_at: datetime


class DuplicateMatch(BaseModel):
    """Result of a duplicate check against the registry."""
    is_duplicate: bool
    matched_goal_id: Optional[UUID] = None
    similarity_score: float = 0.0
    reason: str = ""


class ClassificationResult(BaseModel):
    """Full triage output for a single IntakeRequest."""
    request_id: UUID
    domain: Domain
    urgency: Urgency
    request_type: RequestType
    duplicate: DuplicateMatch
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str = ""
    triaged_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


// --- DUPLICATE BLOCK ---

"""
Data models and schemas for the Job-Star triage engine.

All models use Pydantic v2 for validation and serialization.
Enums define the controlled vocabulary for classification.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums — Controlled vocabulary for classification
# ---------------------------------------------------------------------------


class Domain(str, Enum):
    """The technical domain a request belongs to."""

    META = "meta"          # Job-Star system itself / process / tooling
    BACKEND = "backend"    # Server-side logic, APIs, data processing
    FRONTEND = "frontend"  # UI, client-side, user experience
    INFRA = "infra"        # Deployment, CI/CD, hosting, networking
    DATA = "data"          # Databases, migrations, data pipelines
    DOCS = "docs"          # Documentation, guides, references
    DEVX = "devx"          # Developer experience, tooling, workflows
    SECURITY = "security"  # Auth, permissions, vulnerabilities
    UNKNOWN = "unknown"    # Could not classify confidently


class Urgency(str, Enum):
    """When a request needs to be acted upon."""

    NOW = "now"            # Blocking / critical — work on immediately
    SOON = "soon"          # Important — work on in current cycle
    LATER = "later"        # Useful — schedule for near-term
    EVENTUALLY = "eventually"  # Nice-to-have — backlog / someday


class RequestType(str, Enum):
    """The nature of the request."""

    BUG = "bug"            # Something is broken
    FEATURE = "feature"    # New capability needed
    REFACTOR = "refactor"  # Improve existing code without behavior change
    QUESTION = "question"  # Needs an answer / investigation
    DOCS = "docs"          # Documentation work
    CHORE = "chore"        # Maintenance, dependency updates, cleanup


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class IntakeRequest(BaseModel):
    """
    A raw, unclassified request coming into the system.

    This is the input to the triage engine. It represents anything
    that needs to be triaged: a bug report, a feature idea, a question,
    a TODO discovered in code, etc.
    """

    id: str = Field(
        default_factory=lambda: f"req-{uuid4().hex[:12]}",
        description="Unique identifier for this intake request.",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Short summary of the request.",
    )
    description: str = Field(
        default="",
        max_length=10000,
        description="Detailed description or context.",
    )
    source: str = Field(
        default="manual",
        max_length=100,
        description="Where this request originated (e.g. 'manual', 'github-issue', 'slack').",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the request was submitted.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional extra context (labels, links, author, etc.).",
    )

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be blank or whitespace-only")
        return v.strip()


class GoalRegistryEntry(BaseModel):
    """
    An existing goal in the registry, used for duplicate detection.

    The triage engine compares incoming IntakeRequests against
    a collection of these entries to find potential duplicates.
    """

    goal_id: str = Field(
        ...,
        description="Unique identifier for the goal.",
    )
    title: str = Field(
        ...,
        min_length=1,
        description="Goal title.",
    )
    description: str = Field(
        default="",
        description="Goal description.",
    )
    domain: Domain = Field(
        default=Domain.UNKNOWN,
        description="Classified domain.",
    )
    urgency: Urgency = Field(
        default=Urgency.LATER,
        description="Classified urgency.",
    )
    request_type: RequestType = Field(
        default=RequestType.FEATURE,
        description="Classified request type.",
    )
    status: str = Field(
        default="open",
        description="Lifecycle status: open, in_progress, completed, archived.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the goal was created.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Freeform tags for categorization.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Extracted keywords used for duplicate matching.",
    )

    @field_validator("status")
    @classmethod
    def status_must_be_known(cls, v: str) -> str:
        allowed = {"open", "in_progress", "completed", "archived", "blocked"}
        v_lower = v.lower().strip()
        if v_lower not in allowed:
            raise ValueError(f"status must be one of {allowed}, got '{v}'")
        return v_lower


class ClassificationResult(BaseModel):
    """
    The output of the triage engine for a single IntakeRequest.

    Contains the classification (domain, urgency, type), a confidence
    score, duplicate detection results, and a human-readable rationale.
    """

    request_id: str = Field(
        ...,
        description="ID of the IntakeRequest this result corresponds to.",
    )
    domain: Domain = Field(
        ...,
        description="Classified domain.",
    )
    urgency: Urgency = Field(
        ...,
        description="Classified urgency.",
    )
    request_type: RequestType = Field(
        ...,
        description="Classified request type.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score for the classification (0.0 to 1.0).",
    )
    is_duplicate: bool = Field(
        default=False,
        description="Whether this request appears to duplicate an existing goal.",
    )
    duplicate_of: Optional[str] = Field(
        default=None,
        description="Goal ID this request duplicates, if any.",
    )
    duplicate_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence that this is a duplicate (0.0 to 1.0).",
    )
    rationale: str = Field(
        default="",
        description="Human-readable explanation of the classification.",
    )
    suggested_goal_id: Optional[str] = Field(
        default=None,
        description="Suggested ID for a new goal if not a duplicate.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Keywords extracted from the request, used for matching.",
    )
    classified_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the classification was produced.",
    )

    def to_summary(self) -> str:
        """Return a one-line human-readable summary of the classification."""
        dup_info = ""
        if self.is_duplicate and self.duplicate_of:
            dup_info = f" [DUPLICATE of {self.duplicate_of}]"
        return (
            f"[{self.domain.value}/{self.urgency.value}/{self.request_type.value}] "
            f"conf={self.confidence:.2f}{dup_info}"
        )


// --- DUPLICATE BLOCK ---

"""Data models for the triage engine.

These are intentionally simple dataclasses so the classifier
can be tested and evolved independently of storage concerns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class IntakeRequest:
    """A raw incoming request before classification."""

    id: str
    title: str
    body: str = ""
    source: str = "manual"  # e.g. "manual", "email", "slack", "api"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    requester: Optional[str] = None  # who asked, if known

    @property
    def text(self) -> str:
        """Combined text used for classification."""
        return f"{self.title}\n{self.body}".strip().lower()


@dataclass
class ClassificationResult:
    """The output of classifying an IntakeRequest."""

    domain: str
    urgency: str
    type: str

    # Scores per category, useful for debugging and confidence estimation
    domain_scores: dict[str, float] = field(default_factory=dict)
    urgency_scores: dict[str, float] = field(default_factory=dict)
    type_scores: dict[str, float] = field(default_factory=dict)

    # Confidence in [0, 1] — rough heuristic based on score dominance
    domain_confidence: float = 0.0
    urgency_confidence: float = 0.0
    type_confidence: float = 0.0

    # Whether this request looks like a duplicate of an existing goal
    is_duplicate: bool = False
    duplicate_of: Optional[str] = None  # goal id if duplicate

    classified_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    classifier_version: str = "rule-based-v1"

    @property
    def overall_confidence(self) -> float:
        return (self.domain_confidence + self.urgency_confidence + self.type_confidence) / 3.0


// --- DUPLICATE BLOCK ---

"""
Data models for the triage engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List


class Domain(str, Enum):
    META = "meta"
    ENGINEERING = "engineering"
    RESEARCH = "research"
    WRITING = "writing"
    OPS = "ops"
    PERSONAL = "personal"
    UNKNOWN = "unknown"


class Urgency(str, Enum):
    NOW = "now"
    SOON = "soon"
    LATER = "later"
    BACKLOG = "backlog"


class RequestType(str, Enum):
    GOAL = "goal"
    TASK = "task"
    QUESTION = "question"
    FIX = "fix"
    REFACTOR = "refactor"
    RESEARCH = "research"
    UNKNOWN = "unknown"


@dataclass
class IntakeRequest:
    """A raw incoming request before triage."""
    id: str
    raw_text: str
    source: str = "unknown"  # e.g. "slack", "email", "manual"
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict = field(default_factory=dict)


@dataclass
class GoalRegistryEntry:
    """An existing entry in the goal registry to check against."""
    goal_id: str
    title: str
    description: str = ""
    domain: Domain = Domain.UNKNOWN
    urgency: Urgency = Urgency.BACKLOG
    tags: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class DuplicateCheck:
    """Result of a duplicate detection check.

    Attributes:
        is_duplicate: True if a near-match was found above threshold.
        matched_goal_id: The goal_id of the matched entry, or None.
        similarity_score: Similarity score in [0.0, 1.0].
        candidates: List of (goal_id, score) tuples for near-matches,
            sorted descending, for debugging / review.
        method: Which similarity method was used ("tfidf" or "token_overlap").
    """
    is_duplicate: bool
    matched_goal_id: Optional[str]
    similarity_score: float
    candidates: List[tuple] = field(default_factory=list)
    method: str = "token_overlap"


// --- DUPLICATE BLOCK ---

"""Data models for the triage subsystem."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class Domain(str, Enum):
    """High-level knowledge domains for intake classification."""
    META = "meta"          # Job-Star self-improvement / bootstrap
    DEV = "dev"            # Software development tasks
    RESEARCH = "research"  # Investigation / analysis
    WRITING = "writing"    # Documentation / content
    OPS = "ops"            # Operations / maintenance
    UNKNOWN = "unknown"


class Urgency(str, Enum):
    """Priority levels for intake requests."""
    NOW = "now"      # Blocking, needs immediate attention
    SOON = "soon"    # Important, should be picked up next
    LATER = "later"  # Can wait / backlog
    BACKLOG = "backlog"  # Nice-to-have


class RequestType(str, Enum):
    """What kind of work this request represents."""
    BUG = "bug"
    FEATURE = "feature"
    REFACTOR = "refactor"
    QUESTION = "question"
    DOCS = "docs"
    RESEARCH = "research"
    CHORE = "chore"
    UNKNOWN = "unknown"


@dataclass
class Classification:
    """Result of classifying a single intake text."""
    domain: Domain
    urgency: Urgency
    request_type: RequestType
    confidence: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class DuplicateMatch:
    """A potential duplicate found in the goal registry."""
    goal_id: str
    title: str
    similarity: float
    reason: str  # human-readable explanation of why it's a duplicate


@dataclass
class TriageResult:
    """Combined output of the triage pipeline.

    Contains classification results, duplicate detection results,
    and metadata about the triage run.
    """
    text: str
    classification: Classification
    duplicates: list[DuplicateMatch] = field(default_factory=list)
    is_duplicate: bool = False
    triaged_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    suggestion: str = ""

    def summary(self) -> str:
        """Return a human-readable one-line summary."""
        dup_note = f" [DUPLICATE of {self.duplicates[0].goal_id}]" if self.is_duplicate else ""
        return (
            f"domain={self.classification.domain.value} "
            f"urgency={self.classification.urgency.value} "
            f"type={self.classification.request_type.value} "
            f"confidence={self.classification.confidence:.2f}"
            f"{dup_note}"
        )


// --- DUPLICATE BLOCK ---

"""Data models for the triage engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class Domain(str, Enum):
    """The knowledge/work domain of a request."""
    META = "meta"          # Job-Star system itself, process, planning
    CODE = "code"          # Source code, implementation, logic
    DOCS = "docs"          # Documentation, README, guides
    DEVOPS = "devops"      # CI/CD, deployment, infrastructure
    RESEARCH = "research"  # Investigation, prototyping, exploration
    SECURITY = "security"  # Security vulnerabilities, hardening
    DATA = "data"          # Data models, migrations, schemas
    UI = "ui"             # Frontend, UX, visual
    UNKNOWN = "unknown"


class Urgency(str, Enum):
    """How soon the request needs attention."""
    NOW = "now"    # Blocking, production down, security issue
    SOON = "soon"  # Should be addressed in current cycle
    LATER = "later"  # Backlog, nice-to-have, planning


class RequestType(str, Enum):
    """The nature of the request."""
    BUG = "bug"
    FEATURE = "feature"
    REFACTOR = "refactor"
    QUESTION = "question"
    DOCS = "docs"
    CHORE = "chore"
    SECURITY = "security"
    UNKNOWN = "unknown"


class DuplicateStatus(str, Enum):
    """Result of duplicate check."""
    UNIQUE = "unique"
    DUPLICATE = "duplicate"
    RELATED = "related"  # Similar but not exact match


@dataclass
class IntakeRequest:
    """Raw incoming request before classification."""
    id: str
    title: str
    description: str
    source: str = "manual"  # manual, github, slack, email
    tags: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        """Combined text for classification."""
        parts = [self.title, self.description]
        parts.extend(self.tags)
        return " ".join(p for p in parts if p).lower()


@dataclass
class ClassificationResult:
    """Output of the classification engine."""
    request_id: str
    domain: Domain
    urgency: Urgency
    request_type: RequestType
    confidence: float  # 0.0 to 1.0
    duplicate_status: DuplicateStatus
    duplicate_of: Optional[str] = None  # Goal ID if duplicate
    related_goals: list[str] = field(default_factory=list)
    matched_signals: list[str] = field(default_factory=list)
    classified_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "domain": self.domain.value,
            "urgency": self.urgency.value,
            "request_type": self.request_type.value,
            "confidence": self.confidence,
            "duplicate_status": self.duplicate_status.value,
            "duplicate_of": self.duplicate_of,
            "related_goals": self.related_goals,
            "matched_signals": self.matched_signals,
            "classified_at": self.classified_at.isoformat(),
        }


// --- DUPLICATE BLOCK ---

"""Data models for the Job-Star triage engine."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class Domain(str, Enum):
    META = "meta"
    BACKEND = "backend"
    FRONTEND = "frontend"
    INFRA = "infra"
    DATA = "data"
    DOCS = "docs"
    SECURITY = "security"
    UNKNOWN = "unknown"


class Urgency(str, Enum):
    NOW = "now"          # blocking, needs immediate attention
    SOON = "soon"        # should be done in current cycle
    LATER = "later"      # queued for near-term
    EVENTUALLY = "eventually"  # backlog / nice-to-have


class RequestType(str, Enum):
    BUG = "bug"
    FEATURE = "feature"
    REFACTOR = "refactor"
    QUESTION = "question"
    CHORE = "chore"
    UNKNOWN = "unknown"


class IntakeRequest(BaseModel):
    """Raw incoming request before triage."""
    id: UUID = Field(default_factory=uuid4)
    title: str
    description: str = ""
    source: str = "manual"  # e.g. "manual", "slack", "github-issue"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GoalRegistryEntry(BaseModel):
    """An entry in the goal registry — a known, tracked goal."""
    id: UUID
    title: str
    description: str = ""
    domain: Domain = Domain.UNKNOWN
    created_at: datetime


class DuplicateMatch(BaseModel):
    """Result of a duplicate check against the registry."""
    is_duplicate: bool
    matched_goal_id: Optional[UUID] = None
    similarity_score: float = 0.0
    reason: str = ""


class ClassificationResult(BaseModel):
    """Full triage output for a single IntakeRequest."""
    request_id: UUID
    domain: Domain
    urgency: Urgency
    request_type: RequestType
    duplicate: DuplicateMatch
    confidence: float = Field(ge=0.0, le=1.0)
    notes: str = ""
    triaged_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


// --- DUPLICATE BLOCK ---

"""
Data models and schemas for the Job-Star triage engine.

All models use Pydantic v2 for validation and serialization.
Enums define the controlled vocabulary for classification.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums — Controlled vocabulary for classification
# ---------------------------------------------------------------------------


class Domain(str, Enum):
    """The technical domain a request belongs to."""

    META = "meta"          # Job-Star system itself / process / tooling
    BACKEND = "backend"    # Server-side logic, APIs, data processing
    FRONTEND = "frontend"  # UI, client-side, user experience
    INFRA = "infra"        # Deployment, CI/CD, hosting, networking
    DATA = "data"          # Databases, migrations, data pipelines
    DOCS = "docs"          # Documentation, guides, references
    DEVX = "devx"          # Developer experience, tooling, workflows
    SECURITY = "security"  # Auth, permissions, vulnerabilities
    UNKNOWN = "unknown"    # Could not classify confidently


class Urgency(str, Enum):
    """When a request needs to be acted upon."""

    NOW = "now"            # Blocking / critical — work on immediately
    SOON = "soon"          # Important — work on in current cycle
    LATER = "later"        # Useful — schedule for near-term
    EVENTUALLY = "eventually"  # Nice-to-have — backlog / someday


class RequestType(str, Enum):
    """The nature of the request."""

    BUG = "bug"            # Something is broken
    FEATURE = "feature"    # New capability needed
    REFACTOR = "refactor"  # Improve existing code without behavior change
    QUESTION = "question"  # Needs an answer / investigation
    DOCS = "docs"          # Documentation work
    CHORE = "chore"        # Maintenance, dependency updates, cleanup


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class IntakeRequest(BaseModel):
    """
    A raw, unclassified request coming into the system.

    This is the input to the triage engine. It represents anything
    that needs to be triaged: a bug report, a feature idea, a question,
    a TODO discovered in code, etc.
    """

    id: str = Field(
        default_factory=lambda: f"req-{uuid4().hex[:12]}",
        description="Unique identifier for this intake request.",
    )
    title: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Short summary of the request.",
    )
    description: str = Field(
        default="",
        max_length=10000,
        description="Detailed description or context.",
    )
    source: str = Field(
        default="manual",
        max_length=100,
        description="Where this request originated (e.g. 'manual', 'github-issue', 'slack').",
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the request was submitted.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional extra context (labels, links, author, etc.).",
    )

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be blank or whitespace-only")
        return v.strip()


class GoalRegistryEntry(BaseModel):
    """
    An existing goal in the registry, used for duplicate detection.

    The triage engine compares incoming IntakeRequests against
    a collection of these entries to find potential duplicates.
    """

    goal_id: str = Field(
        ...,
        description="Unique identifier for the goal.",
    )
    title: str = Field(
        ...,
        min_length=1,
        description="Goal title.",
    )
    description: str = Field(
        default="",
        description="Goal description.",
    )
    domain: Domain = Field(
        default=Domain.UNKNOWN,
        description="Classified domain.",
    )
    urgency: Urgency = Field(
        default=Urgency.LATER,
        description="Classified urgency.",
    )
    request_type: RequestType = Field(
        default=RequestType.FEATURE,
        description="Classified request type.",
    )
    status: str = Field(
        default="open",
        description="Lifecycle status: open, in_progress, completed, archived.",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the goal was created.",
    )
    tags: list[str] = Field(
        default_factory=list,
        description="Freeform tags for categorization.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Extracted keywords used for duplicate matching.",
    )

    @field_validator("status")
    @classmethod
    def status_must_be_known(cls, v: str) -> str:
        allowed = {"open", "in_progress", "completed", "archived", "blocked"}
        v_lower = v.lower().strip()
        if v_lower not in allowed:
            raise ValueError(f"status must be one of {allowed}, got '{v}'")
        return v_lower


class ClassificationResult(BaseModel):
    """
    The output of the triage engine for a single IntakeRequest.

    Contains the classification (domain, urgency, type), a confidence
    score, duplicate detection results, and a human-readable rationale.
    """

    request_id: str = Field(
        ...,
        description="ID of the IntakeRequest this result corresponds to.",
    )
    domain: Domain = Field(
        ...,
        description="Classified domain.",
    )
    urgency: Urgency = Field(
        ...,
        description="Classified urgency.",
    )
    request_type: RequestType = Field(
        ...,
        description="Classified request type.",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score for the classification (0.0 to 1.0).",
    )
    is_duplicate: bool = Field(
        default=False,
        description="Whether this request appears to duplicate an existing goal.",
    )
    duplicate_of: Optional[str] = Field(
        default=None,
        description="Goal ID this request duplicates, if any.",
    )
    duplicate_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence that this is a duplicate (0.0 to 1.0).",
    )
    rationale: str = Field(
        default="",
        description="Human-readable explanation of the classification.",
    )
    suggested_goal_id: Optional[str] = Field(
        default=None,
        description="Suggested ID for a new goal if not a duplicate.",
    )
    keywords: list[str] = Field(
        default_factory=list,
        description="Keywords extracted from the request, used for matching.",
    )
    classified_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the classification was produced.",
    )

    def to_summary(self) -> str:
        """Return a one-line human-readable summary of the classification."""
        dup_info = ""
        if self.is_duplicate and self.duplicate_of:
            dup_info = f" [DUPLICATE of {self.duplicate_of}]"
        return (
            f"[{self.domain.value}/{self.urgency.value}/{self.request_type.value}] "
            f"conf={self.confidence:.2f}{dup_info}"
        )


// --- DUPLICATE BLOCK ---

"""Data models for the triage engine.

These are intentionally simple dataclasses so the classifier
can be tested and evolved independently of storage concerns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class IntakeRequest:
    """A raw incoming request before classification."""

    id: str
    title: str
    body: str = ""
    source: str = "manual"  # e.g. "manual", "email", "slack", "api"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    requester: Optional[str] = None  # who asked, if known

    @property
    def text(self) -> str:
        """Combined text used for classification."""
        return f"{self.title}\n{self.body}".strip().lower()


@dataclass
class ClassificationResult:
    """The output of classifying an IntakeRequest."""

    domain: str
    urgency: str
    type: str

    # Scores per category, useful for debugging and confidence estimation
    domain_scores: dict[str, float] = field(default_factory=dict)
    urgency_scores: dict[str, float] = field(default_factory=dict)
    type_scores: dict[str, float] = field(default_factory=dict)

    # Confidence in [0, 1] — rough heuristic based on score dominance
    domain_confidence: float = 0.0
    urgency_confidence: float = 0.0
    type_confidence: float = 0.0

    # Whether this request looks like a duplicate of an existing goal
    is_duplicate: bool = False
    duplicate_of: Optional[str] = None  # goal id if duplicate

    classified_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    classifier_version: str = "rule-based-v1"

    @property
    def overall_confidence(self) -> float:
        return (self.domain_confidence + self.urgency_confidence + self.type_confidence) / 3.0


// --- DUPLICATE BLOCK ---

"""
Data models for the triage engine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List


class Domain(str, Enum):
    META = "meta"
    ENGINEERING = "engineering"
    RESEARCH = "research"
    WRITING = "writing"
    OPS = "ops"
    PERSONAL = "personal"
    UNKNOWN = "unknown"


class Urgency(str, Enum):
    NOW = "now"
    SOON = "soon"
    LATER = "later"
    BACKLOG = "backlog"


class RequestType(str, Enum):
    GOAL = "goal"
    TASK = "task"
    QUESTION = "question"
    FIX = "fix"
    REFACTOR = "refactor"
    RESEARCH = "research"
    UNKNOWN = "unknown"


@dataclass
class IntakeRequest:
    """A raw incoming request before triage."""
    id: str
    raw_text: str
    source: str = "unknown"  # e.g. "slack", "email", "manual"
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict = field(default_factory=dict)


@dataclass
class GoalRegistryEntry:
    """An existing entry in the goal registry to check against."""
    goal_id: str
    title: str
    description: str = ""
    domain: Domain = Domain.UNKNOWN
    urgency: Urgency = Urgency.BACKLOG
    tags: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class DuplicateCheck:
    """Result of a duplicate detection check.

    Attributes:
        is_duplicate: True if a near-match was found above threshold.
        matched_goal_id: The goal_id of the matched entry, or None.
        similarity_score: Similarity score in [0.0, 1.0].
        candidates: List of (goal_id, score) tuples for near-matches,
            sorted descending, for debugging / review.
        method: Which similarity method was used ("tfidf" or "token_overlap").
    """
    is_duplicate: bool
    matched_goal_id: Optional[str]
    similarity_score: float
    candidates: List[tuple] = field(default_factory=list)
    method: str = "token_overlap"


// --- DUPLICATE BLOCK ---

"""Data models for the triage subsystem."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class Domain(str, Enum):
    """High-level knowledge domains for intake classification."""
    META = "meta"          # Job-Star self-improvement / bootstrap
    DEV = "dev"            # Software development tasks
    RESEARCH = "research"  # Investigation / analysis
    WRITING = "writing"    # Documentation / content
    OPS = "ops"            # Operations / maintenance
    UNKNOWN = "unknown"


class Urgency(str, Enum):
    """Priority levels for intake requests."""
    NOW = "now"      # Blocking, needs immediate attention
    SOON = "soon"    # Important, should be picked up next
    LATER = "later"  # Can wait / backlog
    BACKLOG = "backlog"  # Nice-to-have


class RequestType(str, Enum):
    """What kind of work this request represents."""
    BUG = "bug"
    FEATURE = "feature"
    REFACTOR = "refactor"
    QUESTION = "question"
    DOCS = "docs"
    RESEARCH = "research"
    CHORE = "chore"
    UNKNOWN = "unknown"


@dataclass
class Classification:
    """Result of classifying a single intake text."""
    domain: Domain
    urgency: Urgency
    request_type: RequestType
    confidence: float = 0.0
    matched_keywords: list[str] = field(default_factory=list)


@dataclass
class DuplicateMatch:
    """A potential duplicate found in the goal registry."""
    goal_id: str
    title: str
    similarity: float
    reason: str  # human-readable explanation of why it's a duplicate


@dataclass
class TriageResult:
    """Combined output of the triage pipeline.

    Contains classification results, duplicate detection results,
    and metadata about the triage run.
    """
    text: str
    classification: Classification
    duplicates: list[DuplicateMatch] = field(default_factory=list)
    is_duplicate: bool = False
    triaged_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    suggestion: str = ""

    def summary(self) -> str:
        """Return a human-readable one-line summary."""
        dup_note = f" [DUPLICATE of {self.duplicates[0].goal_id}]" if self.is_duplicate else ""
        return (
            f"domain={self.classification.domain.value} "
            f"urgency={self.classification.urgency.value} "
            f"type={self.classification.request_type.value} "
            f"confidence={self.classification.confidence:.2f}"
            f"{dup_note}"
        )
