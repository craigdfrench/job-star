"""End-to-end tests for the triage engine.

The TriageEngine combines classification and duplicate detection into a
single pipeline. These tests verify the integrated behavior using the
sample goal registry fixture.
"""
import json
from pathlib import Path

import pytest

from job_star.triage.engine import TriageEngine, TriageResult


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def registry_path():
    return FIXTURES_DIR / "sample_registry.json"


@pytest.fixture
def registry_data():
    with open(FIXTURES_DIR / "sample_registry.json") as f:
        return json.load(f)["goals"]


@pytest.fixture
def engine(registry_path):
    """TriageEngine initialized with the sample registry."""
    return TriageEngine(registry_path=registry_path)


# ---------------------------------------------------------------------------
# End-to-end triage
# ---------------------------------------------------------------------------

class TestEngineTriage:
    """Verify the engine produces complete, correct TriageResults."""

    def test_new_request_classified_and_not_duplicate(self, engine):
        """A genuinely new request should be classified and marked unique."""
        request = {
            "title": "Implement real-time collaboration in the document editor",
            "summary": "Multiple users editing simultaneously with live cursor presence.",
        }
        result = engine.triage(request)

        assert isinstance(result, TriageResult)
        assert result.classification.domain == "web"
        assert result.classification.type == "feature"
        assert result.classification.urgency in {"now", "soon", "whenever"}
        assert result.duplicate.is_duplicate is False
        assert result.duplicate.matched_goal_id is None
        assert result.action == "create"

    def test_duplicate_request_flagged_and_routed(self, engine):
        """A duplicate should be detected and routed for review/merge."""
        request = {
            "title": "Fix login page crash on Safari",
            "summary": "Safari users see a blank page after clicking login.",
        }
        result = engine.triage(request)

        assert result.duplicate.is_duplicate is True
        assert result.duplicate.matched_goal_id == "G-0001"
        assert result.action == "review_duplicate"

    def test_urgent_new_bug_routed_correctly(self, engine):
        """An urgent new production bug should get urgency='now' and action='create'."""
        request = {
            "title": "URGENT: payment webhook is failing and dropping transactions",
            "summary": "The Stripe webhook endpoint is returning 500s and we're losing payment confirmations in production.",
        }
        result = engine.triage(request)

        assert result.classification.urgency == "now"
        assert result.classification.domain == "backend"
        assert result.classification.type == "bug"
        assert result.duplicate.is_duplicate is False
        assert result.action == "create"

    def test_low_priority_meta_task(self, engine):
        """A low-priority documentation request should be classified as meta/whenever."""
        request = {
            "title": "Whenever you have time, write a contributing guide for the repo",
            "summary": "Add a CONTRIBUTING.md with setup instructions and PR conventions.",
        }
        result = engine.triage(request)

        assert result.classification.domain == "meta"
        assert result.classification.urgency == "whenever"
        assert result.classification.type == "task"
        assert result.action == "create"

    def test_near_duplicate_security_request(self, engine):
        """A heavily overlapping security request should be routed for review."""
        request = {
            "title": "Security review of OAuth token storage and session handling",
            "summary": "Audit the authentication flow for vulnerabilities in token handling and refresh logic.",
            "tags": ["security", "auth", "audit"],
        }
        result = engine.triage(request)

        assert result.duplicate.is_duplicate is True
        assert result.duplicate.matched_goal_id == "G-0008"
        assert result.action == "review_duplicate"


# ---------------------------------------------------------------------------
# Engine robustness
# ---------------------------------------------------------------------------

class TestEngineRobustness:
    """Verify the engine handles edge cases gracefully."""

    def test_empty_request_raises(self, engine):
        with pytest.raises((ValueError, TypeError)):
            engine.triage({})

    def test_whitespace_only_request_raises(self, engine):
        with pytest.raises((ValueError, TypeError)):
            engine.triage({"title": "   ", "summary": "\n\t"})

    def test_request_with_only_title(self, engine):
        """A request with a title but no summary should still be triaged."""
        request = {"title": "Add export to CSV feature"}
        result = engine.triage(request)
        assert isinstance(result, TriageResult)
        assert result.classification.type == "feature"

    def test_engine_loads_registry_from_path(self, registry_path):
        """The engine should accept a path and load the registry."""
        engine = TriageEngine(registry_path=registry_path)
        # Internal registry should be populated.
        assert len(engine.registry) > 0

    def test_engine_accepts_registry_list_directly(self, registry_data):
        """The engine should also accept a pre-loaded registry list."""
        engine = TriageEngine(registry=registry_data)
        result = engine.triage({
            "title": "Fix login page crash on Safari",
            "summary": "Blank page on Safari after login.",
        })
        assert result.duplicate.is_duplicate is True

    def test_triage_result_has_all_fields(self, engine):
        """TriageResult must expose classification, duplicate, and action."""
        result = engine.triage({
            "title": "Add dark mode to settings",
            "summary": "Theme toggle stored in localStorage.",
        })
        assert hasattr(result, "classification")
        assert hasattr(result, "duplicate")
        assert hasattr(result, "action")
        assert result.action in {"create", "review_duplicate"}

    def test_idempotent_triage(self, engine):
        """Triaging the same request twice yields consistent results."""
        request = {
            "title": "Upgrade PostgreSQL from 14 to 16",
            "summary": "Plan database major version upgrade with backup and rollback.",
        }
        r1 = engine.triage(request)
        r2 = engine.triage(request)
        assert r1.classification.domain == r2.classification.domain
        assert r1.duplicate.is_duplicate == r2.duplicate.is_duplicate
        assert r1.action == r2.action

    def test_engine_with_empty_registry(self):
        """An engine with no existing goals should classify everything as new."""
        engine = TriageEngine(registry=[])
        result = engine.triage({
            "title": "Fix login page crash on Safari",
            "summary": "Blank page after login.",
        })
        assert result.duplicate.is_duplicate is False
        assert result.action == "create"
