"""Tests for the triage engine."""
import pytest

from job_star.triage import IntakeRequest, TriageEngine
from job_star.triage.models import GoalRef


# Minimal inline config for fast tests
TEST_CONFIG = {
    "domains": {
        "meta": {"keywords": ["job-star", "bootstrap", "triage"], "weight": 2.0},
        "infra": {"keywords": ["deploy", "server", "docker"], "weight": 1.5},
        "dev": {"keywords": ["bug", "feature", "api", "fix"], "weight": 1.0},
    },
    "urgency": {
        "now": {"keywords": ["urgent", "blocker", "critical"], "weight": 3.0},
        "soon": {"keywords": ["soon", "this week", "priority"], "weight": 2.0},
        "later": {"keywords": ["later", "backlog"], "weight": 1.0},
        "someday": {"keywords": ["someday", "nice to have"], "weight": 1.0},
    },
    "types": {
        "bug": {"keywords": ["bug", "crash", "broken", "error"], "weight": 2.0},
        "feature": {"keywords": ["feature", "add", "implement"], "weight": 1.5},
        "task": {"keywords": ["task", "do", "update", "configure"], "weight": 1.0},
        "question": {"keywords": ["how do i", "what is", "?"], "weight": 1.5},
    },
    "duplicate": {
        "similarity_threshold": 0.6,
        "high_confidence_threshold": 0.8,
        "min_title_length": 5,
        "stopwords": ["the", "a", "is", "to", "of", "and", "for"],
    },
    "tagging": {
        "max_tags": 3,
        "min_keyword_length": 4,
        "tag_map": {"docker": "docker", "api": "api"},
    },
}


@pytest.fixture
def engine():
    return TriageEngine.from_dict(TEST_CONFIG)


def test_classifies_meta_domain(engine):
    req = IntakeRequest(
        id="1",
        title="Build Job-Star triage engine",
        description="Bootstrap the triage system for job-star itself.",
    )
    result = engine.triage(req)
    assert result.domain.label == "meta"


def test_classifies_urgency_now(engine):
    req = IntakeRequest(
        id="2",
        title="Critical blocker: server is down",
        description="Urgent — production outage, fix immediately.",
    )
    result = engine.triage(req)
    assert result.urgency.label == "now"


def test_classifies_type_bug(engine):
    req = IntakeRequest(
        id="3",
        title="Fix crash in API endpoint",
        description="The endpoint throws an error when called.",
    )
    result = engine.triage(req)
    assert result.type.label == "bug"


def test_detects_duplicate(engine):
    req = IntakeRequest(
        id="4",
        title="Build Job-Star triage engine",
        description="Bootstrap the triage system.",
    )
    goals = [
        GoalRef(id="g1", title="Build Job-Star triage engine",
                description="Bootstrap the triage system."),
    ]
    result = engine.triage(req, goals=goals)
    assert result.is_duplicate
    assert result.duplicate_of.goal_id == "g1"
    assert result.duplicate_of.similarity >= 0.6


def test_no_false_duplicate(engine):
    req = IntakeRequest(
        id="5",
        title="Refactor authentication module",
        description="Clean up the auth code.",
    )
    goals = [
        GoalRef(id="g2", title="Deploy docker containers to k8s",
                description="Set up CI/CD pipeline."),
    ]
    result = engine.triage(req, goals=goals)
    assert not result.is_duplicate


def test_suggests_tags(engine):
    req = IntakeRequest(
        id="6",
        title="Add docker deployment for the api",
        description="Containerize the api service.",
    )
    result = engine.triage(req)
    assert "docker" in result.suggested_tags
    assert "api" in result.suggested_tags


def test_to_dict_roundtrip(engine):
    req = IntakeRequest(id="7", title="Fix bug in api")
    result = engine.triage(req)
    d = result.to_dict()
    assert d["request_id"] == "7"
    assert "domain" in d
    assert "urgency" in d
    assert "type" in d


// --- DUPLICATE BLOCK ---

"""Tests for the triage engine."""

import pytest

from triage import (
    Domain,
    DuplicateStatus,
    InMemoryGoalRegistry,
    IntakeRequest,
    RegistryGoal,
    RequestType,
    TriageEngine,
    Urgency,
)


@pytest.fixture
def engine():
    """Fresh triage engine with empty registry."""
    return TriageEngine()


@pytest.fixture
def engine_with_goals():
    """Engine with pre-existing goals for duplicate testing."""
    registry = InMemoryGoalRegistry(goals=[
        RegistryGoal(
            id="G-001",
            title="Fix login crash on empty password",
            description="The login endpoint crashes when password is empty",
            domain=Domain.CODE,
            urgency=Urgency.NOW,
        ),
        RegistryGoal(
            id="G-002",
            title="Add dark mode to UI",
            description="Implement dark mode theme for the frontend",
            domain=Domain.UI,
            urgency=Urgency.LATER,
        ),
    ])
    return TriageEngine(registry=registry)


class TestDomainClassification:
    def test_meta_domain(self, engine):
        req = IntakeRequest(
            id="R-1",
            title="Update Job-Star triage workflow",
            description="Need to adjust the intake process for new sources",
        )
        result = engine.triage(req)
        assert result.domain == Domain.META

    def test_code_domain(self, engine):
        req = IntakeRequest(
            id="R-2",
            title="Fix bug in parser function",
            description="The parse method throws an exception on bad input",
        )
        result = engine.triage(req)
        assert result.domain == Domain.CODE

    def test_devops_domain(self, engine):
        req = IntakeRequest(
            id="R-3",
            title="Set up CI/CD pipeline",
            description="Configure Docker deployment for staging",
        )
        result = engine.triage(req)
        assert result.domain == Domain.DEVOPS

    def test_security_domain(self, engine):
        req = IntakeRequest(
            id="R-4",
            title="Fix SQL injection vulnerability",
            description="User input is not sanitized, security risk",
        )
        result = engine.triage(req)
        assert result.domain == Domain.SECURITY

    def test_docs_domain(self, engine):
        req = IntakeRequest(
            id="R-5",
            title="Write API documentation",
            description="Need README and guide for the new endpoints",
        )
        result = engine.triage(req)
        assert result.domain == Domain.DOCS


class TestUrgencyClassification:
    def test_now_urgency(self, engine):
        req = IntakeRequest(
            id="R-6",
            title="Production down - critical outage",
            description="This is a blocker, everything is broken",
        )
        result = engine.triage(req)
        assert result.urgency == Urgency.NOW

    def test_soon_urgency(self, engine):
        req = IntakeRequest(
            id="R-7",
            title="Add feature for next sprint",
            description="This is important and should be done soon",
        )
        result = engine.triage(req)
        assert result.urgency == Urgency.SOON

    def test_later_urgency(self, engine):
        req = IntakeRequest(
            id="R-8",
            title="Nice to have: add tooltips",
            description="Eventually we want this in the backlog",
        )
        result = engine.triage(req)
        assert result.urgency == Urgency.LATER

    def test_default_urgency_is_soon(self, engine):
        req = IntakeRequest(
            id="R-9",
            title="Something needs doing",
            description="There's a thing to handle",
        )
        result = engine.triage(req)
        assert result.urgency == Urgency.SOON


class TestTypeClassification:
    def test_bug_type(self, engine):
        req = IntakeRequest(
            id="R-10",
            title="Crash on startup",
            description="The app fails with an unexpected error",
        )
        result = engine.triage(req)
        assert result.request_type == RequestType.BUG

    def test_feature_type(self, engine):
        req = IntakeRequest(
            id="R-11",
            title="Add support for CSV export",
            description="Implement new capability to export data",
        )
        result = engine.triage(req)
        assert result.request_type == RequestType.FEATURE

    def test_refactor_type(self, engine):
        req = IntakeRequest(
            id="R-12",
            title="Refactor authentication module",
            description="Clean up and simplify the auth code, reduce tech debt",
        )
        result = engine.triage(req)
        assert result.request_type == RequestType.REFACTOR

    def test_question_type(self, engine):
        req = IntakeRequest(
            id="R-13",
            title="How do I configure the pipeline?",
            description="I'm confused about the setup, can someone explain?",
        )
        result = engine.triage(req)
        assert result.request_type == RequestType.QUESTION


class TestDuplicateDetection:
    def test_unique_request(self, engine_with_goals):
        req = IntakeRequest(
            id="R-14",
            title="Completely different feature",
            description="Something about astronomy and telescopes",
        )
        result = engine_with_goals.triage(req)
        assert result.duplicate_status == DuplicateStatus.UNIQUE

    def test_duplicate_detection(self, engine_with_goals):
        req = IntakeRequest(
            id="R-15",
            title="Fix login crash on empty password",
            description="The login endpoint crashes when password is empty",
        )
        result = engine_with_goals.triage(req)
        assert result.duplicate_status == DuplicateStatus.DUPLICATE
        assert result.duplicate_of == "G-001"

    def test_related_detection(self, engine_with_goals):
        req = IntakeRequest(
            id="R-16",
            title="Fix login error handling",
            description="Login should handle empty password gracefully",
        )
        result = engine_with_goals.triage(req)
        # Should be at least related to G-001
        assert result.duplicate_status in (
            DuplicateStatus.RELATED, DuplicateStatus.DUPLICATE
        )
        assert "G-001" in result.related_goals or result.duplicate_of == "G-001"


class TestConfidence:
    def test_high_confidence_with_strong_signals(self, engine):
        req = IntakeRequest(
            id="R-17",
            title="Critical security vulnerability - SQL injection",
            description="Production is down due to this exploit, blocker",
        )
        result = engine.triage(req)
        assert result.confidence > 0.5

    def test_low_confidence_with_no_signals(self, engine):
        req = IntakeRequest(
            id="R-18",
            title="A thing",
            description="Stuff",
        )
        result = engine.triage(req)
        assert result.confidence < 0.5


class TestBatchAndReport:
    def test_batch_triage(self, engine):
        requests = [
            IntakeRequest(id="R-19", title="Fix bug", description="crash error"),
            IntakeRequest(id="R-20", title="Add docs", description="readme guide"),
        ]
        results = engine.triage_batch(requests)
        assert len(results) == 2
        assert results[0].request_id == "R-19"
        assert results[1].request_id == "R-20"

    def test_report_format(self, engine):
        req = IntakeRequest(
            id="R-21",
            title="Fix critical bug in parser",
            description="The parser crashes on bad input, blocker",
        )
        result, report = engine.triage_and_report(req)
        assert "Triage Report" in report
        assert "Domain:" in report
        assert "Urgency:" in report
        assert "Confidence:" in report


// --- DUPLICATE BLOCK ---

"""Unit tests for the Job-Star triage engine."""

import pytest
from uuid import uuid4

from jobstar.triage import (
    Domain,
    DuplicateChecker,
    InMemoryRegistry,
    IntakeRequest,
    RequestType,
    TriageEngine,
    Urgency,
    classify,
)
from jobstar.triage.models import GoalRegistryEntry
from datetime import datetime, timezone


class TestClassifier:
    def test_classifies_software_bug(self):
        req = IntakeRequest(
            title="Fix crash in API endpoint when parsing JSON",
            description="The code throws an exception when input is malformed.",
        )
        result = classify(req)
        assert result.domain == Domain.SOFTWARE
        assert result.type == RequestType.BUG
        assert result.confidence > 0.0

    def test_classifies_infra_urgent(self):
        req = IntakeRequest(
            title="Production server is down — urgent outage",
            description="AWS EC2 instance not responding, need ASAP.",
        )
        result = classify(req)
        assert result.domain == Domain.INFRA
        assert result.urgency == Urgency.CRITICAL

    def test_classifies_docs_task(self):
        req = IntakeRequest(
            title="Update README documentation with new setup guide",
            description="Add a tutorial for the markdown wiki.",
        )
        result = classify(req)
        assert result.domain == Domain.DOCS

    def test_hint_overrides_when_no_keywords(self):
        req = IntakeRequest(
            title="Something vague",
            description="Not much to go on",
            hint_domain=Domain.RESEARCH,
            hint_urgency=Urgency.SOON,
            hint_type=RequestType.GOAL,
        )
        result = classify(req)
        assert result.domain == Domain.RESEARCH
        assert result.urgency == Urgency.SOON
        assert result.type == RequestType.GOAL

    def test_unknown_when_no_signals(self):
        req = IntakeRequest(title="xyz", description="abc def")
        result = classify(req)
        assert result.domain == Domain.UNKNOWN
        assert result.urgency == Urgency.UNKNOWN
        assert result.type == RequestType.UNKNOWN


class TestDuplicateChecker:
    @pytest.fixture
    def registry_with_goal(self):
        registry = InMemoryRegistry()
        goal = GoalRegistryEntry(
            id=uuid4(),
            title="Fix crash in API endpoint when parsing JSON",
            description="The code throws an exception when input is malformed.",
            domain=Domain.SOFTWARE,
            urgency=Urgency.SOON,
            type=RequestType.BUG,
            tags=["api", "json", "crash"],
            status="open",
            created_at=datetime.now(timezone.utc),
        )
        registry.goals[goal.id] = goal
        return registry

    async def test_detects_duplicate(self, registry_with_goal):
        checker = DuplicateChecker(backend=registry_with_goal)
        req = IntakeRequest(
            title="Fix crash in API endpoint when parsing JSON input",
            description="The code throws an exception when input is malformed.",
            tags=["api", "json", "crash"],
        )
        matches = await checker.check(req)
        assert len(matches) >= 1
        assert matches[0].similarity_score > 0.5

    async def test_no_duplicate_for_unrelated(self, registry_with_goal):
        checker = DuplicateChecker(backend=registry_with_goal)
        req = IntakeRequest(
            title="Set up monitoring dashboard for analytics",
            description="Create a Grafana dashboard for data pipeline metrics.",
            tags=["grafana", "monitoring"],
        )
        matches = await checker.check(req)
        assert len(matches) == 0


class TestTriageEngine:
    async def test_full_triage_new_request(self):
        registry = InMemoryRegistry()
        checker = DuplicateChecker(backend=registry)
        engine = TriageEngine(duplicate_checker=checker)

        req = IntakeRequest(
            title="Add feature to export data as CSV",
            description="Implement CSV export for the analytics dashboard.",
        )
        result = await engine.triage(req)
        assert result.is_duplicate is False
        assert "New" in result.recommended_action
        assert result.classification.domain == Domain.DATA

    async def test_full_triage_duplicate(self):
        registry = InMemoryRegistry()
        goal = GoalRegistryEntry(
            id=uuid4(),
            title="Add feature to export data as CSV",
            description="Implement CSV export for the analytics dashboard.",
            domain=Domain.DATA,
            urgency=Urgency.NORMAL,
            type=RequestType.FEATURE,
            tags=["export", "csv", "data"],
            status="open",
            created_at=datetime.now(timezone.utc),
        )
        registry.goals[goal.id] = goal

        checker = DuplicateChecker(backend=registry)
        engine = TriageEngine(duplicate_checker=checker)

        req = IntakeRequest(
            title="Add feature to export data as CSV",
            description="Implement CSV export for the analytics dashboard.",
            tags=["export", "csv", "data"],
        )
        result = await engine.triage(req)
        assert result.is_duplicate is True
        assert "duplicate" in result.recommended_action.lower()


// --- DUPLICATE BLOCK ---

"""Tests for the triage engine."""
import pytest

from job_star.triage import IntakeRequest, TriageEngine
from job_star.triage.models import GoalRef


# Minimal inline config for fast tests
TEST_CONFIG = {
    "domains": {
        "meta": {"keywords": ["job-star", "bootstrap", "triage"], "weight": 2.0},
        "infra": {"keywords": ["deploy", "server", "docker"], "weight": 1.5},
        "dev": {"keywords": ["bug", "feature", "api", "fix"], "weight": 1.0},
    },
    "urgency": {
        "now": {"keywords": ["urgent", "blocker", "critical"], "weight": 3.0},
        "soon": {"keywords": ["soon", "this week", "priority"], "weight": 2.0},
        "later": {"keywords": ["later", "backlog"], "weight": 1.0},
        "someday": {"keywords": ["someday", "nice to have"], "weight": 1.0},
    },
    "types": {
        "bug": {"keywords": ["bug", "crash", "broken", "error"], "weight": 2.0},
        "feature": {"keywords": ["feature", "add", "implement"], "weight": 1.5},
        "task": {"keywords": ["task", "do", "update", "configure"], "weight": 1.0},
        "question": {"keywords": ["how do i", "what is", "?"], "weight": 1.5},
    },
    "duplicate": {
        "similarity_threshold": 0.6,
        "high_confidence_threshold": 0.8,
        "min_title_length": 5,
        "stopwords": ["the", "a", "is", "to", "of", "and", "for"],
    },
    "tagging": {
        "max_tags": 3,
        "min_keyword_length": 4,
        "tag_map": {"docker": "docker", "api": "api"},
    },
}


@pytest.fixture
def engine():
    return TriageEngine.from_dict(TEST_CONFIG)


def test_classifies_meta_domain(engine):
    req = IntakeRequest(
        id="1",
        title="Build Job-Star triage engine",
        description="Bootstrap the triage system for job-star itself.",
    )
    result = engine.triage(req)
    assert result.domain.label == "meta"


def test_classifies_urgency_now(engine):
    req = IntakeRequest(
        id="2",
        title="Critical blocker: server is down",
        description="Urgent — production outage, fix immediately.",
    )
    result = engine.triage(req)
    assert result.urgency.label == "now"


def test_classifies_type_bug(engine):
    req = IntakeRequest(
        id="3",
        title="Fix crash in API endpoint",
        description="The endpoint throws an error when called.",
    )
    result = engine.triage(req)
    assert result.type.label == "bug"


def test_detects_duplicate(engine):
    req = IntakeRequest(
        id="4",
        title="Build Job-Star triage engine",
        description="Bootstrap the triage system.",
    )
    goals = [
        GoalRef(id="g1", title="Build Job-Star triage engine",
                description="Bootstrap the triage system."),
    ]
    result = engine.triage(req, goals=goals)
    assert result.is_duplicate
    assert result.duplicate_of.goal_id == "g1"
    assert result.duplicate_of.similarity >= 0.6


def test_no_false_duplicate(engine):
    req = IntakeRequest(
        id="5",
        title="Refactor authentication module",
        description="Clean up the auth code.",
    )
    goals = [
        GoalRef(id="g2", title="Deploy docker containers to k8s",
                description="Set up CI/CD pipeline."),
    ]
    result = engine.triage(req, goals=goals)
    assert not result.is_duplicate


def test_suggests_tags(engine):
    req = IntakeRequest(
        id="6",
        title="Add docker deployment for the api",
        description="Containerize the api service.",
    )
    result = engine.triage(req)
    assert "docker" in result.suggested_tags
    assert "api" in result.suggested_tags


def test_to_dict_roundtrip(engine):
    req = IntakeRequest(id="7", title="Fix bug in api")
    result = engine.triage(req)
    d = result.to_dict()
    assert d["request_id"] == "7"
    assert "domain" in d
    assert "urgency" in d
    assert "type" in d


// --- DUPLICATE BLOCK ---

"""Tests for the triage engine."""

import pytest

from triage import (
    Domain,
    DuplicateStatus,
    InMemoryGoalRegistry,
    IntakeRequest,
    RegistryGoal,
    RequestType,
    TriageEngine,
    Urgency,
)


@pytest.fixture
def engine():
    """Fresh triage engine with empty registry."""
    return TriageEngine()


@pytest.fixture
def engine_with_goals():
    """Engine with pre-existing goals for duplicate testing."""
    registry = InMemoryGoalRegistry(goals=[
        RegistryGoal(
            id="G-001",
            title="Fix login crash on empty password",
            description="The login endpoint crashes when password is empty",
            domain=Domain.CODE,
            urgency=Urgency.NOW,
        ),
        RegistryGoal(
            id="G-002",
            title="Add dark mode to UI",
            description="Implement dark mode theme for the frontend",
            domain=Domain.UI,
            urgency=Urgency.LATER,
        ),
    ])
    return TriageEngine(registry=registry)


class TestDomainClassification:
    def test_meta_domain(self, engine):
        req = IntakeRequest(
            id="R-1",
            title="Update Job-Star triage workflow",
            description="Need to adjust the intake process for new sources",
        )
        result = engine.triage(req)
        assert result.domain == Domain.META

    def test_code_domain(self, engine):
        req = IntakeRequest(
            id="R-2",
            title="Fix bug in parser function",
            description="The parse method throws an exception on bad input",
        )
        result = engine.triage(req)
        assert result.domain == Domain.CODE

    def test_devops_domain(self, engine):
        req = IntakeRequest(
            id="R-3",
            title="Set up CI/CD pipeline",
            description="Configure Docker deployment for staging",
        )
        result = engine.triage(req)
        assert result.domain == Domain.DEVOPS

    def test_security_domain(self, engine):
        req = IntakeRequest(
            id="R-4",
            title="Fix SQL injection vulnerability",
            description="User input is not sanitized, security risk",
        )
        result = engine.triage(req)
        assert result.domain == Domain.SECURITY

    def test_docs_domain(self, engine):
        req = IntakeRequest(
            id="R-5",
            title="Write API documentation",
            description="Need README and guide for the new endpoints",
        )
        result = engine.triage(req)
        assert result.domain == Domain.DOCS


class TestUrgencyClassification:
    def test_now_urgency(self, engine):
        req = IntakeRequest(
            id="R-6",
            title="Production down - critical outage",
            description="This is a blocker, everything is broken",
        )
        result = engine.triage(req)
        assert result.urgency == Urgency.NOW

    def test_soon_urgency(self, engine):
        req = IntakeRequest(
            id="R-7",
            title="Add feature for next sprint",
            description="This is important and should be done soon",
        )
        result = engine.triage(req)
        assert result.urgency == Urgency.SOON

    def test_later_urgency(self, engine):
        req = IntakeRequest(
            id="R-8",
            title="Nice to have: add tooltips",
            description="Eventually we want this in the backlog",
        )
        result = engine.triage(req)
        assert result.urgency == Urgency.LATER

    def test_default_urgency_is_soon(self, engine):
        req = IntakeRequest(
            id="R-9",
            title="Something needs doing",
            description="There's a thing to handle",
        )
        result = engine.triage(req)
        assert result.urgency == Urgency.SOON


class TestTypeClassification:
    def test_bug_type(self, engine):
        req = IntakeRequest(
            id="R-10",
            title="Crash on startup",
            description="The app fails with an unexpected error",
        )
        result = engine.triage(req)
        assert result.request_type == RequestType.BUG

    def test_feature_type(self, engine):
        req = IntakeRequest(
            id="R-11",
            title="Add support for CSV export",
            description="Implement new capability to export data",
        )
        result = engine.triage(req)
        assert result.request_type == RequestType.FEATURE

    def test_refactor_type(self, engine):
        req = IntakeRequest(
            id="R-12",
            title="Refactor authentication module",
            description="Clean up and simplify the auth code, reduce tech debt",
        )
        result = engine.triage(req)
        assert result.request_type == RequestType.REFACTOR

    def test_question_type(self, engine):
        req = IntakeRequest(
            id="R-13",
            title="How do I configure the pipeline?",
            description="I'm confused about the setup, can someone explain?",
        )
        result = engine.triage(req)
        assert result.request_type == RequestType.QUESTION


class TestDuplicateDetection:
    def test_unique_request(self, engine_with_goals):
        req = IntakeRequest(
            id="R-14",
            title="Completely different feature",
            description="Something about astronomy and telescopes",
        )
        result = engine_with_goals.triage(req)
        assert result.duplicate_status == DuplicateStatus.UNIQUE

    def test_duplicate_detection(self, engine_with_goals):
        req = IntakeRequest(
            id="R-15",
            title="Fix login crash on empty password",
            description="The login endpoint crashes when password is empty",
        )
        result = engine_with_goals.triage(req)
        assert result.duplicate_status == DuplicateStatus.DUPLICATE
        assert result.duplicate_of == "G-001"

    def test_related_detection(self, engine_with_goals):
        req = IntakeRequest(
            id="R-16",
            title="Fix login error handling",
            description="Login should handle empty password gracefully",
        )
        result = engine_with_goals.triage(req)
        # Should be at least related to G-001
        assert result.duplicate_status in (
            DuplicateStatus.RELATED, DuplicateStatus.DUPLICATE
        )
        assert "G-001" in result.related_goals or result.duplicate_of == "G-001"


class TestConfidence:
    def test_high_confidence_with_strong_signals(self, engine):
        req = IntakeRequest(
            id="R-17",
            title="Critical security vulnerability - SQL injection",
            description="Production is down due to this exploit, blocker",
        )
        result = engine.triage(req)
        assert result.confidence > 0.5

    def test_low_confidence_with_no_signals(self, engine):
        req = IntakeRequest(
            id="R-18",
            title="A thing",
            description="Stuff",
        )
        result = engine.triage(req)
        assert result.confidence < 0.5


class TestBatchAndReport:
    def test_batch_triage(self, engine):
        requests = [
            IntakeRequest(id="R-19", title="Fix bug", description="crash error"),
            IntakeRequest(id="R-20", title="Add docs", description="readme guide"),
        ]
        results = engine.triage_batch(requests)
        assert len(results) == 2
        assert results[0].request_id == "R-19"
        assert results[1].request_id == "R-20"

    def test_report_format(self, engine):
        req = IntakeRequest(
            id="R-21",
            title="Fix critical bug in parser",
            description="The parser crashes on bad input, blocker",
        )
        result, report = engine.triage_and_report(req)
        assert "Triage Report" in report
        assert "Domain:" in report
        assert "Urgency:" in report
        assert "Confidence:" in report


// --- DUPLICATE BLOCK ---

"""Unit tests for the Job-Star triage engine."""

import pytest
from uuid import uuid4

from jobstar.triage import (
    Domain,
    DuplicateChecker,
    InMemoryRegistry,
    IntakeRequest,
    RequestType,
    TriageEngine,
    Urgency,
    classify,
)
from jobstar.triage.models import GoalRegistryEntry
from datetime import datetime, timezone


class TestClassifier:
    def test_classifies_software_bug(self):
        req = IntakeRequest(
            title="Fix crash in API endpoint when parsing JSON",
            description="The code throws an exception when input is malformed.",
        )
        result = classify(req)
        assert result.domain == Domain.SOFTWARE
        assert result.type == RequestType.BUG
        assert result.confidence > 0.0

    def test_classifies_infra_urgent(self):
        req = IntakeRequest(
            title="Production server is down — urgent outage",
            description="AWS EC2 instance not responding, need ASAP.",
        )
        result = classify(req)
        assert result.domain == Domain.INFRA
        assert result.urgency == Urgency.CRITICAL

    def test_classifies_docs_task(self):
        req = IntakeRequest(
            title="Update README documentation with new setup guide",
            description="Add a tutorial for the markdown wiki.",
        )
        result = classify(req)
        assert result.domain == Domain.DOCS

    def test_hint_overrides_when_no_keywords(self):
        req = IntakeRequest(
            title="Something vague",
            description="Not much to go on",
            hint_domain=Domain.RESEARCH,
            hint_urgency=Urgency.SOON,
            hint_type=RequestType.GOAL,
        )
        result = classify(req)
        assert result.domain == Domain.RESEARCH
        assert result.urgency == Urgency.SOON
        assert result.type == RequestType.GOAL

    def test_unknown_when_no_signals(self):
        req = IntakeRequest(title="xyz", description="abc def")
        result = classify(req)
        assert result.domain == Domain.UNKNOWN
        assert result.urgency == Urgency.UNKNOWN
        assert result.type == RequestType.UNKNOWN


class TestDuplicateChecker:
    @pytest.fixture
    def registry_with_goal(self):
        registry = InMemoryRegistry()
        goal = GoalRegistryEntry(
            id=uuid4(),
            title="Fix crash in API endpoint when parsing JSON",
            description="The code throws an exception when input is malformed.",
            domain=Domain.SOFTWARE,
            urgency=Urgency.SOON,
            type=RequestType.BUG,
            tags=["api", "json", "crash"],
            status="open",
            created_at=datetime.now(timezone.utc),
        )
        registry.goals[goal.id] = goal
        return registry

    async def test_detects_duplicate(self, registry_with_goal):
        checker = DuplicateChecker(backend=registry_with_goal)
        req = IntakeRequest(
            title="Fix crash in API endpoint when parsing JSON input",
            description="The code throws an exception when input is malformed.",
            tags=["api", "json", "crash"],
        )
        matches = await checker.check(req)
        assert len(matches) >= 1
        assert matches[0].similarity_score > 0.5

    async def test_no_duplicate_for_unrelated(self, registry_with_goal):
        checker = DuplicateChecker(backend=registry_with_goal)
        req = IntakeRequest(
            title="Set up monitoring dashboard for analytics",
            description="Create a Grafana dashboard for data pipeline metrics.",
            tags=["grafana", "monitoring"],
        )
        matches = await checker.check(req)
        assert len(matches) == 0


class TestTriageEngine:
    async def test_full_triage_new_request(self):
        registry = InMemoryRegistry()
        checker = DuplicateChecker(backend=registry)
        engine = TriageEngine(duplicate_checker=checker)

        req = IntakeRequest(
            title="Add feature to export data as CSV",
            description="Implement CSV export for the analytics dashboard.",
        )
        result = await engine.triage(req)
        assert result.is_duplicate is False
        assert "New" in result.recommended_action
        assert result.classification.domain == Domain.DATA

    async def test_full_triage_duplicate(self):
        registry = InMemoryRegistry()
        goal = GoalRegistryEntry(
            id=uuid4(),
            title="Add feature to export data as CSV",
            description="Implement CSV export for the analytics dashboard.",
            domain=Domain.DATA,
            urgency=Urgency.NORMAL,
            type=RequestType.FEATURE,
            tags=["export", "csv", "data"],
            status="open",
            created_at=datetime.now(timezone.utc),
        )
        registry.goals[goal.id] = goal

        checker = DuplicateChecker(backend=registry)
        engine = TriageEngine(duplicate_checker=checker)

        req = IntakeRequest(
            title="Add feature to export data as CSV",
            description="Implement CSV export for the analytics dashboard.",
            tags=["export", "csv", "data"],
        )
        result = await engine.triage(req)
        assert result.is_duplicate is True
        assert "duplicate" in result.recommended_action.lower()
