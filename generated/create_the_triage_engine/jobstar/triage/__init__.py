"""Job-Star Triage Engine.

Classifies incoming intake requests by domain, urgency, and type,
and checks for duplicates against the goal registry.
"""
from .models import IntakeRequest, TriageResult, Classification
from .engine import TriageEngine

__all__ = [
    "IntakeRequest",
    "TriageResult",
    "Classification",
    "TriageEngine",
]


// --- DUPLICATE BLOCK ---

"""Data models for the triage engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from enum import Enum


class Urgency(str, Enum):
    NOW = "now"        # blocking, do immediately
    SOON = "soon"      # this week / high priority
    LATER = "later"    # backlog, scheduled eventually
    SOMEDAY = "someday"  # nice-to-have, no commitment


class RequestType(str, Enum):
    BUG = "bug"
    FEATURE = "feature"
    TASK = "task"
    QUESTION = "question"
    REFACTOR = "refactor"
    OPS = "ops"
    UNKNOWN = "unknown"


class Domain(str, Enum):
    META = "meta"          # Job-Star working on itself
    INFRA = "infra"        # infrastructure, deployment, CI/CD
    DEV = "dev"            # software development tasks
    RESEARCH = "research"  # investigation, learning, exploration
    PERSONAL = "personal"  # personal productivity, life tasks
    UNKNOWN = "unknown"


@dataclass
class IntakeRequest:
    """A raw incoming request awaiting triage."""
    id: str
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = "manual"          # e.g. "email", "slack", "manual"
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: dict = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        """Combined text used for classification."""
        parts = [self.title]
        if self.description:
            parts.append(self.description)
        if self.tags:
            parts.append(" ".join(self.tags))
        return "\n".join(parts)


@dataclass
class Classification:
    """A single classification label with a confidence score."""
    label: str
    confidence: float  # 0.0 - 1.0

    def __post_init__(self):
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0,1], got {self.confidence}"
            )


@dataclass
class GoalRef:
    """A lightweight reference to an existing goal for dedup."""
    id: str
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    status: str = "open"


@dataclass
class DuplicateMatch:
    """Result of a duplicate check against one goal."""
    goal_id: str
    similarity: float
    reason: str  # human-readable explanation


@dataclass
class TriageResult:
    """Full output of triaging an IntakeRequest."""
    request_id: str
    domain: Classification
    urgency: Classification
    type: Classification
    suggested_tags: list[str] = field(default_factory=list)
    duplicate_of: Optional[DuplicateMatch] = None
    notes: list[str] = field(default_factory=list)
    triaged_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of is not None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "domain": {"label": self.domain.label,
                       "confidence": self.domain.confidence},
            "urgency": {"label": self.urgency.label,
                        "confidence": self.urgency.confidence},
            "type": {"label": self.type.label,
                     "confidence": self.type.confidence},
            "suggested_tags": self.suggested_tags,
            "duplicate_of": (
                {
                    "goal_id": self.duplicate_of.goal_id,
                    "similarity": self.duplicate_of.similarity,
                    "reason": self.duplicate_of.reason,
                }
                if self.duplicate_of else None
            ),
            "notes": self.notes,
            "triaged_at": self.triaged_at.isoformat(),
        }


// --- DUPLICATE BLOCK ---

from job_star.triage.duplicate_checker import (
    DuplicateChecker,
    DuplicateReport,
    DuplicateCandidate,
    compute_source_hash,
    extract_keywords,
)
from job_star.triage.goal_registry import GoalRegistry, GoalRecord

__all__ = [
    "DuplicateChecker",
    "DuplicateReport",
    "DuplicateCandidate",
    "GoalRegistry",
    "GoalRecord",
    "compute_source_hash",
    "extract_keywords",
]


// --- DUPLICATE BLOCK ---

"""Job-Star Triage Engine.

Classifies incoming intake requests by domain, urgency, and type,
and checks for duplicates against the goal registry.
"""

from .classifier import Classifier
from .engine import TriageEngine
from .models import (
    ClassificationResult,
    Domain,
    DuplicateStatus,
    IntakeRequest,
    RequestType,
    Urgency,
)
from .registry import (
    DuplicateDetector,
    DuplicateResult,
    GoalRegistry,
    InMemoryGoalRegistry,
    RegistryGoal,
)

__all__ = [
    "Classifier",
    "TriageEngine",
    "ClassificationResult",
    "Domain",
    "DuplicateStatus",
    "IntakeRequest",
    "RequestType",
    "Urgency",
    "DuplicateDetector",
    "DuplicateResult",
    "GoalRegistry",
    "InMemoryGoalRegistry",
    "RegistryGoal",
]


// --- DUPLICATE BLOCK ---

"""Triage engine package for Job-Star."""

from job_star.triage.goal_registry import Goal, GoalRegistry
from job_star.triage.duplicate_detector import (
    DuplicateCandidate,
    DuplicateDetector,
    DuplicateReport,
)

__all__ = [
    "Goal",
    "GoalRegistry",
    "DuplicateCandidate",
    "DuplicateDetector",
    "DuplicateReport",
]


// --- DUPLICATE BLOCK ---

"""Tests for the duplicate detector."""

import os
import tempfile
from pathlib import Path

import pytest

from job_star.triage.goal_registry import Goal, GoalRegistry
from job_star.triage.duplicate_detector import DuplicateDetector


@pytest.fixture
def registry(tmp_path):
    return GoalRegistry(path=tmp_path / "registry.json")


@pytest.fixture
def detector(registry):
    return DuplicateDetector(registry=registry)


def _make_goal(registry, title, description="", domain="meta", keywords=None):
    g = Goal(
        id=registry.new_id(),
        title=title,
        description=description,
        domain=domain,
        urgency="soon",
        goal_type="build",
        keywords=keywords or [],
    )
    registry.add(g)
    return g


def test_no_duplicates_empty_registry(detector):
    report = detector.check(title="Build a new dashboard")
    assert not report.is_duplicate
    assert report.confidence == 0.0


def test_exact_title_match(detector, registry):
    _make_goal(registry, "Build the triage engine", "Classify intake requests")
    detector.refresh()
    report = detector.check(title="Build the triage engine")
    assert report.is_duplicate
    assert report.confidence == 1.0
    assert report.method == "exact"
    assert report.best_candidate is not None


def test_exact_match_is_case_insensitive(detector, registry):
    _make_goal(registry, "Build the Triage Engine")
    detector.refresh()
    report = detector.check(title="build the triage engine")
    assert report.is_duplicate


def test_keyword_overlap_duplicate(detector, registry):
    _make_goal(
        registry,
        "Refactor authentication module",
        description="Rewrite the auth login flow",
        keywords=["auth", "login", "refactor", "module"],
    )
    detector.refresh()
    report = detector.check(
        title="Refactor auth login module",
        description="Rewrite authentication flow",
    )
    assert report.is_duplicate
    assert report.best_candidate is not None


def test_no_false_positive_on_unrelated(detector, registry):
    _make_goal(registry, "Build the triage engine", "Classify intake requests")
    detector.refresh()
    report = detector.check(
        title="Deploy Kubernetes cluster",
        description="Set up production k8s infrastructure",
    )
    assert not report.is_duplicate


def test_domain_filtering(detector, registry):
    _make_goal(registry, "Build dashboard", domain="frontend")
    detector.refresh()
    # same title but different domain should not match
    report = detector.check(title="Build dashboard", domain="backend")
    assert not report.is_duplicate
    # same domain should match
    report = detector.check(title="Build dashboard", domain="frontend")
    assert report.is_duplicate


def test_tfidf_fuzzy_match(detector, registry):
    _make_goal(
        registry,
        "Implement duplicate detection for intake requests",
        description="Detect duplicate goals using semantic similarity",
    )
    detector.refresh()
    report = detector.check(
        title="Add duplicate detection to intake",
        description="Find duplicate requests via similarity matching",
    )
    assert report.is_duplicate
    assert "tfidf_sim" in " ".join(report.best_candidate.reasons)


def test_refresh_after_new_goal(detector, registry):
    report = detector.check(title="Build a logger")
    assert not report.is_duplicate
    _make_goal(registry, "Build a logger", "Structured logging service")
    detector.refresh()
    report = detector.check(title="Build a logger")
    assert report.is_duplicate


def test_inactive_goals_ignored(detector, registry):
    g = _make_goal(registry, "Build a logger", "Structured logging")
    g.status = "inactive"
    registry.add(g)
    detector.refresh()
    report = detector.check(title="Build a logger")
    assert not report.is_duplicate


// --- DUPLICATE BLOCK ---

"""Job-Star triage engine."""

from .models import (
    ClassificationResult,
    Domain,
    DuplicateMatch,
    GoalRegistryEntry,
    IntakeRequest,
    RequestType,
    Urgency,
)
from .engine import triage, triage_batch

__all__ = [
    "ClassificationResult",
    "Domain",
    "DuplicateMatch",
    "GoalRegistryEntry",
    "IntakeRequest",
    "RequestType",
    "Urgency",
    "triage",
    "triage_batch",
]


// --- DUPLICATE BLOCK ---

"""
Job-Star Triage Engine
======================

Classifies incoming intake requests by domain, urgency, and type.
Checks for duplicates against the goal registry.

Public API:
    from triage import (
        IntakeRequest,
        ClassificationResult,
        GoalRegistryEntry,
        Domain,
        Urgency,
        RequestType,
    )
"""

from triage.models import (
    ClassificationResult,
    Domain,
    GoalRegistryEntry,
    IntakeRequest,
    RequestType,
    Urgency,
)

__all__ = [
    "IntakeRequest",
    "ClassificationResult",
    "GoalRegistryEntry",
    "Domain",
    "Urgency",
    "RequestType",
]

__version__ = "0.1.0"


// --- DUPLICATE BLOCK ---

"""Job-Star Triage Engine.

Classifies incoming intake requests by domain, urgency, and type.
Checks for duplicates against the goal registry.
"""

from triage.models import IntakeRequest, ClassificationResult
from triage.classifier import classify, check_duplicate

__all__ = [
    "IntakeRequest",
    "ClassificationResult",
    "classify",
    "check_duplicate",
]


// --- DUPLICATE BLOCK ---

"""Tests for the triage classifier.

Run with: python -m pytest triage/test_classifier.py -v
Or standalone: python triage/test_classifier.py
"""

from triage.models import IntakeRequest
from triage.classifier import classify, check_duplicate


def _make(title: str, body: str = "") -> IntakeRequest:
    return IntakeRequest(id="test-1", title=title, body=body)


def test_engineering_bug_urgent():
    req = _make("Production API is down — urgent hotfix needed", "Getting 500 errors on all endpoints")
    result = classify(req)
    assert result.domain == "engineering"
    assert result.urgency == "now"
    assert result.type == "bug"
    print(f"✓ test_engineering_bug_urgent: domain={result.domain}, urgency={result.urgency}, type={result.type}")


def test_product_feature_soon():
    req = _make("Add export-to-PDF feature for reports", "Should be able to download reports as PDF this week")
    result = classify(req)
    assert result.domain == "product"
    assert result.urgency == "soon"
    assert result.type == "feature"
    print(f"✓ test_product_feature_soon: domain={result.domain}, urgency={result.urgency}, type={result.type}")


def test_personal_later():
    req = _make("Research good hiking trails for vacation", "Nice to have, no rush — someday")
    result = classify(req)
    assert result.domain == "personal"
    assert result.urgency == "later"
    print(f"✓ test_personal_later: domain={result.domain}, urgency={result.urgency}, type={result.type}")


def test_meta_bootstrap():
    req = _make("Build the Job-Star triage classifier", "Bootstrap the system's own intake pipeline")
    result = classify(req)
    assert result.domain == "meta"
    assert result.type == "task"
    print(f"✓ test_meta_bootstrap: domain={result.domain}, urgency={result.urgency}, type={result.type}")


def test_question_type():
    req = _make("How do I configure the CI pipeline?")
    result = classify(req)
    assert result.type == "question"
    print(f"✓ test_question_type: type={result.type}")


def test_decision_type():
    req = _make("Should we use Postgres or DynamoDB for the goal registry?")
    result = classify(req)
    assert result.type == "decision"
    print(f"✓ test_decision_type: type={result.type}")


def test_research_type():
    req = _make("Investigate and benchmark vector databases for semantic search")
    result = classify(req)
    assert result.type == "research"
    assert result.domain == "research"
    print(f"✓ test_research_type: type={result.type}, domain={result.domain}")


def test_duplicate_detection():
    req = _make("Fix the login page bug", "")
    registry = [
        {"id": "goal-001", "title": "Fix the login page bug"},
        {"id": "goal-002", "title": "Add dark mode feature"},
    ]
    is_dup, dup_of = check_duplicate(req, registry)
    assert is_dup is True
    assert dup_of == "goal-001"
    print(f"✓ test_duplicate_detection: is_dup={is_dup}, dup_of={dup_of}")


def test_no_duplicate():
    req = _make("Set up Kubernetes cluster", "")
    registry = [
        {"id": "goal-001", "title": "Fix the login page bug"},
    ]
    is_dup, dup_of = check_duplicate(req, registry)
    assert is_dup is False
    print(f"✓ test_no_duplicate: is_dup={is_dup}")


def test_duplicate_via_classify():
    req = _make("Fix the login page bug", "It's broken and urgent")
    registry = [
        {"id": "goal-001", "title": "Fix the login page bug"},
    ]
    result = classify(req, goal_registry=registry)
    assert result.is_duplicate is True
    assert result.duplicate_of == "goal-001"
    print(f"✓ test_duplicate_via_classify: is_duplicate={result.is_duplicate}, duplicate_of={result.duplicate_of}")


def test_defaults_on_empty():
    req = _make("hello", "")
    result = classify(req)
    assert result.domain == "meta"
    assert result.urgency == "soon"
    assert result.type == "task"
    print(f"✓ test_defaults_on_empty: domain={result.domain}, urgency={result.urgency}, type={result.type}")


def test_confidence_scores_populated():
    req = _make("Urgent: production database is down, need hotfix immediately")
    result = classify(req)
    assert result.domain_scores["engineering"] > 0
    assert result.urgency_scores["now"] > 0
    assert result.type_scores["bug"] > 0
    assert result.urgency_confidence > 0
    print(f"✓ test_confidence_scores: urgency_conf={result.urgency_confidence}, overall={result.overall_confidence:.3f}")


if __name__ == "__main__":
    test_engineering_bug_urgent()
    test_product_feature_soon()
    test_personal_later()
    test_meta_bootstrap()
    test_question_type()
    test_decision_type()
    test_research_type()
    test_duplicate_detection()
    test_no_duplicate()
    test_duplicate_via_classify()
    test_defaults_on_empty()
    test_confidence_scores_populated()
    print("\n✅ All tests passed.")


// --- DUPLICATE BLOCK ---

"""Job-Star triage subsystem.

Classifies incoming intake requests by domain, urgency, and type,
and checks for duplicates against the goal registry.
"""
from triage.models import (
    Classification,
    Domain,
    DuplicateMatch,
    RequestType,
    TriageResult,
    Urgency,
)
from triage.engine import triage_request

__all__ = [
    "triage_request",
    "TriageResult",
    "Classification",
    "DuplicateMatch",
    "Domain",
    "Urgency",
    "RequestType",
]


// --- DUPLICATE BLOCK ---

"""Job-Star Triage Engine.

Classifies incoming intake requests by domain, urgency, and type,
and checks for duplicates against the goal registry.
"""
from .models import IntakeRequest, TriageResult, Classification
from .engine import TriageEngine

__all__ = [
    "IntakeRequest",
    "TriageResult",
    "Classification",
    "TriageEngine",
]


// --- DUPLICATE BLOCK ---

"""Data models for the triage engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from enum import Enum


class Urgency(str, Enum):
    NOW = "now"        # blocking, do immediately
    SOON = "soon"      # this week / high priority
    LATER = "later"    # backlog, scheduled eventually
    SOMEDAY = "someday"  # nice-to-have, no commitment


class RequestType(str, Enum):
    BUG = "bug"
    FEATURE = "feature"
    TASK = "task"
    QUESTION = "question"
    REFACTOR = "refactor"
    OPS = "ops"
    UNKNOWN = "unknown"


class Domain(str, Enum):
    META = "meta"          # Job-Star working on itself
    INFRA = "infra"        # infrastructure, deployment, CI/CD
    DEV = "dev"            # software development tasks
    RESEARCH = "research"  # investigation, learning, exploration
    PERSONAL = "personal"  # personal productivity, life tasks
    UNKNOWN = "unknown"


@dataclass
class IntakeRequest:
    """A raw incoming request awaiting triage."""
    id: str
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = "manual"          # e.g. "email", "slack", "manual"
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    metadata: dict = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        """Combined text used for classification."""
        parts = [self.title]
        if self.description:
            parts.append(self.description)
        if self.tags:
            parts.append(" ".join(self.tags))
        return "\n".join(parts)


@dataclass
class Classification:
    """A single classification label with a confidence score."""
    label: str
    confidence: float  # 0.0 - 1.0

    def __post_init__(self):
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0,1], got {self.confidence}"
            )


@dataclass
class GoalRef:
    """A lightweight reference to an existing goal for dedup."""
    id: str
    title: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    status: str = "open"


@dataclass
class DuplicateMatch:
    """Result of a duplicate check against one goal."""
    goal_id: str
    similarity: float
    reason: str  # human-readable explanation


@dataclass
class TriageResult:
    """Full output of triaging an IntakeRequest."""
    request_id: str
    domain: Classification
    urgency: Classification
    type: Classification
    suggested_tags: list[str] = field(default_factory=list)
    duplicate_of: Optional[DuplicateMatch] = None
    notes: list[str] = field(default_factory=list)
    triaged_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def is_duplicate(self) -> bool:
        return self.duplicate_of is not None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "domain": {"label": self.domain.label,
                       "confidence": self.domain.confidence},
            "urgency": {"label": self.urgency.label,
                        "confidence": self.urgency.confidence},
            "type": {"label": self.type.label,
                     "confidence": self.type.confidence},
            "suggested_tags": self.suggested_tags,
            "duplicate_of": (
                {
                    "goal_id": self.duplicate_of.goal_id,
                    "similarity": self.duplicate_of.similarity,
                    "reason": self.duplicate_of.reason,
                }
                if self.duplicate_of else None
            ),
            "notes": self.notes,
            "triaged_at": self.triaged_at.isoformat(),
        }


// --- DUPLICATE BLOCK ---

from job_star.triage.duplicate_checker import (
    DuplicateChecker,
    DuplicateReport,
    DuplicateCandidate,
    compute_source_hash,
    extract_keywords,
)
from job_star.triage.goal_registry import GoalRegistry, GoalRecord

__all__ = [
    "DuplicateChecker",
    "DuplicateReport",
    "DuplicateCandidate",
    "GoalRegistry",
    "GoalRecord",
    "compute_source_hash",
    "extract_keywords",
]


// --- DUPLICATE BLOCK ---

"""Job-Star Triage Engine.

Classifies incoming intake requests by domain, urgency, and type,
and checks for duplicates against the goal registry.
"""

from .classifier import Classifier
from .engine import TriageEngine
from .models import (
    ClassificationResult,
    Domain,
    DuplicateStatus,
    IntakeRequest,
    RequestType,
    Urgency,
)
from .registry import (
    DuplicateDetector,
    DuplicateResult,
    GoalRegistry,
    InMemoryGoalRegistry,
    RegistryGoal,
)

__all__ = [
    "Classifier",
    "TriageEngine",
    "ClassificationResult",
    "Domain",
    "DuplicateStatus",
    "IntakeRequest",
    "RequestType",
    "Urgency",
    "DuplicateDetector",
    "DuplicateResult",
    "GoalRegistry",
    "InMemoryGoalRegistry",
    "RegistryGoal",
]


// --- DUPLICATE BLOCK ---

"""Triage engine package for Job-Star."""

from job_star.triage.goal_registry import Goal, GoalRegistry
from job_star.triage.duplicate_detector import (
    DuplicateCandidate,
    DuplicateDetector,
    DuplicateReport,
)

__all__ = [
    "Goal",
    "GoalRegistry",
    "DuplicateCandidate",
    "DuplicateDetector",
    "DuplicateReport",
]


// --- DUPLICATE BLOCK ---

"""Tests for the duplicate detector."""

import os
import tempfile
from pathlib import Path

import pytest

from job_star.triage.goal_registry import Goal, GoalRegistry
from job_star.triage.duplicate_detector import DuplicateDetector


@pytest.fixture
def registry(tmp_path):
    return GoalRegistry(path=tmp_path / "registry.json")


@pytest.fixture
def detector(registry):
    return DuplicateDetector(registry=registry)


def _make_goal(registry, title, description="", domain="meta", keywords=None):
    g = Goal(
        id=registry.new_id(),
        title=title,
        description=description,
        domain=domain,
        urgency="soon",
        goal_type="build",
        keywords=keywords or [],
    )
    registry.add(g)
    return g


def test_no_duplicates_empty_registry(detector):
    report = detector.check(title="Build a new dashboard")
    assert not report.is_duplicate
    assert report.confidence == 0.0


def test_exact_title_match(detector, registry):
    _make_goal(registry, "Build the triage engine", "Classify intake requests")
    detector.refresh()
    report = detector.check(title="Build the triage engine")
    assert report.is_duplicate
    assert report.confidence == 1.0
    assert report.method == "exact"
    assert report.best_candidate is not None


def test_exact_match_is_case_insensitive(detector, registry):
    _make_goal(registry, "Build the Triage Engine")
    detector.refresh()
    report = detector.check(title="build the triage engine")
    assert report.is_duplicate


def test_keyword_overlap_duplicate(detector, registry):
    _make_goal(
        registry,
        "Refactor authentication module",
        description="Rewrite the auth login flow",
        keywords=["auth", "login", "refactor", "module"],
    )
    detector.refresh()
    report = detector.check(
        title="Refactor auth login module",
        description="Rewrite authentication flow",
    )
    assert report.is_duplicate
    assert report.best_candidate is not None


def test_no_false_positive_on_unrelated(detector, registry):
    _make_goal(registry, "Build the triage engine", "Classify intake requests")
    detector.refresh()
    report = detector.check(
        title="Deploy Kubernetes cluster",
        description="Set up production k8s infrastructure",
    )
    assert not report.is_duplicate


def test_domain_filtering(detector, registry):
    _make_goal(registry, "Build dashboard", domain="frontend")
    detector.refresh()
    # same title but different domain should not match
    report = detector.check(title="Build dashboard", domain="backend")
    assert not report.is_duplicate
    # same domain should match
    report = detector.check(title="Build dashboard", domain="frontend")
    assert report.is_duplicate


def test_tfidf_fuzzy_match(detector, registry):
    _make_goal(
        registry,
        "Implement duplicate detection for intake requests",
        description="Detect duplicate goals using semantic similarity",
    )
    detector.refresh()
    report = detector.check(
        title="Add duplicate detection to intake",
        description="Find duplicate requests via similarity matching",
    )
    assert report.is_duplicate
    assert "tfidf_sim" in " ".join(report.best_candidate.reasons)


def test_refresh_after_new_goal(detector, registry):
    report = detector.check(title="Build a logger")
    assert not report.is_duplicate
    _make_goal(registry, "Build a logger", "Structured logging service")
    detector.refresh()
    report = detector.check(title="Build a logger")
    assert report.is_duplicate


def test_inactive_goals_ignored(detector, registry):
    g = _make_goal(registry, "Build a logger", "Structured logging")
    g.status = "inactive"
    registry.add(g)
    detector.refresh()
    report = detector.check(title="Build a logger")
    assert not report.is_duplicate


// --- DUPLICATE BLOCK ---

"""Job-Star triage engine."""

from .models import (
    ClassificationResult,
    Domain,
    DuplicateMatch,
    GoalRegistryEntry,
    IntakeRequest,
    RequestType,
    Urgency,
)
from .engine import triage, triage_batch

__all__ = [
    "ClassificationResult",
    "Domain",
    "DuplicateMatch",
    "GoalRegistryEntry",
    "IntakeRequest",
    "RequestType",
    "Urgency",
    "triage",
    "triage_batch",
]


// --- DUPLICATE BLOCK ---

"""
Job-Star Triage Engine
======================

Classifies incoming intake requests by domain, urgency, and type.
Checks for duplicates against the goal registry.

Public API:
    from triage import (
        IntakeRequest,
        ClassificationResult,
        GoalRegistryEntry,
        Domain,
        Urgency,
        RequestType,
    )
"""

from triage.models import (
    ClassificationResult,
    Domain,
    GoalRegistryEntry,
    IntakeRequest,
    RequestType,
    Urgency,
)

__all__ = [
    "IntakeRequest",
    "ClassificationResult",
    "GoalRegistryEntry",
    "Domain",
    "Urgency",
    "RequestType",
]

__version__ = "0.1.0"


// --- DUPLICATE BLOCK ---

"""Job-Star Triage Engine.

Classifies incoming intake requests by domain, urgency, and type.
Checks for duplicates against the goal registry.
"""

from triage.models import IntakeRequest, ClassificationResult
from triage.classifier import classify, check_duplicate

__all__ = [
    "IntakeRequest",
    "ClassificationResult",
    "classify",
    "check_duplicate",
]


// --- DUPLICATE BLOCK ---

"""Tests for the triage classifier.

Run with: python -m pytest triage/test_classifier.py -v
Or standalone: python triage/test_classifier.py
"""

from triage.models import IntakeRequest
from triage.classifier import classify, check_duplicate


def _make(title: str, body: str = "") -> IntakeRequest:
    return IntakeRequest(id="test-1", title=title, body=body)


def test_engineering_bug_urgent():
    req = _make("Production API is down — urgent hotfix needed", "Getting 500 errors on all endpoints")
    result = classify(req)
    assert result.domain == "engineering"
    assert result.urgency == "now"
    assert result.type == "bug"
    print(f"✓ test_engineering_bug_urgent: domain={result.domain}, urgency={result.urgency}, type={result.type}")


def test_product_feature_soon():
    req = _make("Add export-to-PDF feature for reports", "Should be able to download reports as PDF this week")
    result = classify(req)
    assert result.domain == "product"
    assert result.urgency == "soon"
    assert result.type == "feature"
    print(f"✓ test_product_feature_soon: domain={result.domain}, urgency={result.urgency}, type={result.type}")


def test_personal_later():
    req = _make("Research good hiking trails for vacation", "Nice to have, no rush — someday")
    result = classify(req)
    assert result.domain == "personal"
    assert result.urgency == "later"
    print(f"✓ test_personal_later: domain={result.domain}, urgency={result.urgency}, type={result.type}")


def test_meta_bootstrap():
    req = _make("Build the Job-Star triage classifier", "Bootstrap the system's own intake pipeline")
    result = classify(req)
    assert result.domain == "meta"
    assert result.type == "task"
    print(f"✓ test_meta_bootstrap: domain={result.domain}, urgency={result.urgency}, type={result.type}")


def test_question_type():
    req = _make("How do I configure the CI pipeline?")
    result = classify(req)
    assert result.type == "question"
    print(f"✓ test_question_type: type={result.type}")


def test_decision_type():
    req = _make("Should we use Postgres or DynamoDB for the goal registry?")
    result = classify(req)
    assert result.type == "decision"
    print(f"✓ test_decision_type: type={result.type}")


def test_research_type():
    req = _make("Investigate and benchmark vector databases for semantic search")
    result = classify(req)
    assert result.type == "research"
    assert result.domain == "research"
    print(f"✓ test_research_type: type={result.type}, domain={result.domain}")


def test_duplicate_detection():
    req = _make("Fix the login page bug", "")
    registry = [
        {"id": "goal-001", "title": "Fix the login page bug"},
        {"id": "goal-002", "title": "Add dark mode feature"},
    ]
    is_dup, dup_of = check_duplicate(req, registry)
    assert is_dup is True
    assert dup_of == "goal-001"
    print(f"✓ test_duplicate_detection: is_dup={is_dup}, dup_of={dup_of}")


def test_no_duplicate():
    req = _make("Set up Kubernetes cluster", "")
    registry = [
        {"id": "goal-001", "title": "Fix the login page bug"},
    ]
    is_dup, dup_of = check_duplicate(req, registry)
    assert is_dup is False
    print(f"✓ test_no_duplicate: is_dup={is_dup}")


def test_duplicate_via_classify():
    req = _make("Fix the login page bug", "It's broken and urgent")
    registry = [
        {"id": "goal-001", "title": "Fix the login page bug"},
    ]
    result = classify(req, goal_registry=registry)
    assert result.is_duplicate is True
    assert result.duplicate_of == "goal-001"
    print(f"✓ test_duplicate_via_classify: is_duplicate={result.is_duplicate}, duplicate_of={result.duplicate_of}")


def test_defaults_on_empty():
    req = _make("hello", "")
    result = classify(req)
    assert result.domain == "meta"
    assert result.urgency == "soon"
    assert result.type == "task"
    print(f"✓ test_defaults_on_empty: domain={result.domain}, urgency={result.urgency}, type={result.type}")


def test_confidence_scores_populated():
    req = _make("Urgent: production database is down, need hotfix immediately")
    result = classify(req)
    assert result.domain_scores["engineering"] > 0
    assert result.urgency_scores["now"] > 0
    assert result.type_scores["bug"] > 0
    assert result.urgency_confidence > 0
    print(f"✓ test_confidence_scores: urgency_conf={result.urgency_confidence}, overall={result.overall_confidence:.3f}")


if __name__ == "__main__":
    test_engineering_bug_urgent()
    test_product_feature_soon()
    test_personal_later()
    test_meta_bootstrap()
    test_question_type()
    test_decision_type()
    test_research_type()
    test_duplicate_detection()
    test_no_duplicate()
    test_duplicate_via_classify()
    test_defaults_on_empty()
    test_confidence_scores_populated()
    print("\n✅ All tests passed.")


// --- DUPLICATE BLOCK ---

"""Job-Star triage subsystem.

Classifies incoming intake requests by domain, urgency, and type,
and checks for duplicates against the goal registry.
"""
from triage.models import (
    Classification,
    Domain,
    DuplicateMatch,
    RequestType,
    TriageResult,
    Urgency,
)
from triage.engine import triage_request

__all__ = [
    "triage_request",
    "TriageResult",
    "Classification",
    "DuplicateMatch",
    "Domain",
    "Urgency",
    "RequestType",
]
