"""Tests for the triage engine integration."""

import uuid
from datetime import datetime, timezone

from job_star.triage.engine import triage, triage_batch
from job_star.triage.models import (
    Domain,
    GoalRegistryEntry,
    IntakeRequest,
    RequestType,
    Urgency,
)


def _make_entry(title: str, description: str = "") -> GoalRegistryEntry:
    return GoalRegistryEntry(
        id=uuid.uuid4(),
        title=title,
        description=description,
        created_at=datetime.now(timezone.utc),
    )


class TestTriage:
    def test_full_classification(self):
        req = IntakeRequest(
            title="URGENT: API endpoint is broken",
            description="The server crashes with a traceback. This is blocking.",
        )
        result = triage(req)
        assert result.request_id == req.id
        assert result.domain == Domain.BACKEND
        assert result.urgency == Urgency.NOW
        assert result.request_type == RequestType.BUG
        assert result.duplicate.is_duplicate is False
        assert 0.0 <= result.confidence <= 1.0

    def test_duplicate_detected(self):
        registry = [_make_entry("Fix login bug", "Login page crashes on submit")]
        req = IntakeRequest(title="Fix login bug", description="Login page crashes on submit")
        result = triage(req, registry)
        assert result.duplicate.is_duplicate is True
        assert result.duplicate.matched_goal_id == registry[0].id
        assert "duplicate" in result.notes.lower()

    def test_no_registry(self):
        req = IntakeRequest(title="Add dark mode", description="Want a theme toggle")
        result = triage(req)  # no registry passed
        assert result.duplicate.is_duplicate is False
        assert result.duplicate.reason == "No similar goals found in registry"

    def test_low_confidence_notes(self):
        req = IntakeRequest(title="Thing", description="Stuff")
        result = triage(req)
        # With no keyword matches, notes should mention low confidence
        assert "low" in result.notes.lower() or "defaulted" in result.notes.lower()

    def test_meta_domain(self):
        req = IntakeRequest(
            title="Improve Job-Star triage",
            description="The agent workflow needs better bootstrap logic",
        )
        result = triage(req)
        assert result.domain == Domain.META

    def test_empty_request(self):
        req = IntakeRequest(title="", description="")
        result = triage(req)
        assert result.domain == Domain.UNKNOWN
        assert result.urgency == Urgency.SOON  # default
        assert result.request_type == RequestType.UNKNOWN
        assert result.confidence == 0.0


class TestTriageBatch:
    def test_multiple_requests(self):
        requests = [
            IntakeRequest(title="Fix API bug", description="Server error"),
            IntakeRequest(title="Add CSS button", description="Frontend component"),
            IntakeRequest(title="Update Docker deploy", description="CI pipeline"),
        ]
        results = triage_batch(requests)
        assert len(results) == 3
        assert results[0].domain == Domain.BACKEND
        assert results[1].domain == Domain.FRONTEND
        assert results[2].domain == Domain.INFRA

    def test_empty_batch(self):
        results = triage_batch([])
        assert results == []

    def test_batch_with_registry(self):
        registry = [_make_entry("Fix API bug", "Server error")]
        requests = [
            IntakeRequest(title="Fix API bug", description="Server error"),
            IntakeRequest(title="Add new feature", description="Something new"),
        ]
        results = triage_batch(requests, registry)
        assert results[0].duplicate.is_duplicate is True
        assert results[1].duplicate.is_duplicate is False

    def test_preserves_order(self):
        titles = ["Bug one", "Bug two", "Bug three"]
        requests = [IntakeRequest(title=t) for t in titles]
        results = triage_batch(requests)
        assert [r.request_id for r in results] == [r.id for r in requests]
