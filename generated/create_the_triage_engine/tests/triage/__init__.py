# Test package


// --- DUPLICATE BLOCK ---

"""Tests for data models."""

import uuid
from datetime import datetime, timezone

from job_star.triage.models import (
    ClassificationResult,
    Domain,
    DuplicateMatch,
    GoalRegistryEntry,
    IntakeRequest,
    RequestType,
    Urgency,
)


class TestIntakeRequest:
    def test_defaults(self):
        req = IntakeRequest(title="Fix login bug")
        assert req.title == "Fix login bug"
        assert req.description == ""
        assert req.source == "manual"
        assert isinstance(req.id, uuid.UUID)
        assert isinstance(req.created_at, datetime)

    def test_with_description(self):
        req = IntakeRequest(
            title="Add API endpoint",
            description="Need a new endpoint for user profiles",
            source="github-issue",
        )
        assert req.source == "github-issue"
        assert "user profiles" in req.description


class TestEnums:
    def test_domain_values(self):
        assert Domain.META == "meta"
        assert Domain.BACKEND == "backend"
        assert Domain.UNKNOWN == "unknown"

    def test_urgency_values(self):
        assert Urgency.NOW == "now"
        assert Urgency.SOON == "soon"
        assert Urgency.LATER == "later"
        assert Urgency.EVENTUALLY == "eventually"

    def test_request_type_values(self):
        assert RequestType.BUG == "bug"
        assert RequestType.FEATURE == "feature"
        assert RequestType.UNKNOWN == "unknown"


class TestClassificationResult:
    def test_construction(self):
        req = IntakeRequest(title="Test")
        dup = DuplicateMatch(is_duplicate=False, reason="none")
        result = ClassificationResult(
            request_id=req.id,
            domain=Domain.BACKEND,
            urgency=Urgency.SOON,
            request_type=RequestType.BUG,
            duplicate=dup,
            confidence=0.75,
        )
        assert result.domain == Domain.BACKEND
        assert result.urgency == Urgency.SOON
        assert result.request_type == RequestType.BUG
        assert result.confidence == 0.75
        assert result.duplicate.is_duplicate is False

    def test_confidence_bounds(self):
        req = IntakeRequest(title="Test")
        dup = DuplicateMatch(is_duplicate=False)
        # Pydantic should enforce 0.0 <= confidence <= 1.0
        ClassificationResult(
            request_id=req.id,
            domain=Domain.UNKNOWN,
            urgency=Urgency.SOON,
            request_type=RequestType.UNKNOWN,
            duplicate=dup,
            confidence=0.0,
        )
        ClassificationResult(
            request_id=req.id,
            domain=Domain.UNKNOWN,
            urgency=Urgency.SOON,
            request_type=RequestType.UNKNOWN,
            duplicate=dup,
            confidence=1.0,
        )


// --- DUPLICATE BLOCK ---

# Test package


// --- DUPLICATE BLOCK ---

"""Tests for data models."""

import uuid
from datetime import datetime, timezone

from job_star.triage.models import (
    ClassificationResult,
    Domain,
    DuplicateMatch,
    GoalRegistryEntry,
    IntakeRequest,
    RequestType,
    Urgency,
)


class TestIntakeRequest:
    def test_defaults(self):
        req = IntakeRequest(title="Fix login bug")
        assert req.title == "Fix login bug"
        assert req.description == ""
        assert req.source == "manual"
        assert isinstance(req.id, uuid.UUID)
        assert isinstance(req.created_at, datetime)

    def test_with_description(self):
        req = IntakeRequest(
            title="Add API endpoint",
            description="Need a new endpoint for user profiles",
            source="github-issue",
        )
        assert req.source == "github-issue"
        assert "user profiles" in req.description


class TestEnums:
    def test_domain_values(self):
        assert Domain.META == "meta"
        assert Domain.BACKEND == "backend"
        assert Domain.UNKNOWN == "unknown"

    def test_urgency_values(self):
        assert Urgency.NOW == "now"
        assert Urgency.SOON == "soon"
        assert Urgency.LATER == "later"
        assert Urgency.EVENTUALLY == "eventually"

    def test_request_type_values(self):
        assert RequestType.BUG == "bug"
        assert RequestType.FEATURE == "feature"
        assert RequestType.UNKNOWN == "unknown"


class TestClassificationResult:
    def test_construction(self):
        req = IntakeRequest(title="Test")
        dup = DuplicateMatch(is_duplicate=False, reason="none")
        result = ClassificationResult(
            request_id=req.id,
            domain=Domain.BACKEND,
            urgency=Urgency.SOON,
            request_type=RequestType.BUG,
            duplicate=dup,
            confidence=0.75,
        )
        assert result.domain == Domain.BACKEND
        assert result.urgency == Urgency.SOON
        assert result.request_type == RequestType.BUG
        assert result.confidence == 0.75
        assert result.duplicate.is_duplicate is False

    def test_confidence_bounds(self):
        req = IntakeRequest(title="Test")
        dup = DuplicateMatch(is_duplicate=False)
        # Pydantic should enforce 0.0 <= confidence <= 1.0
        ClassificationResult(
            request_id=req.id,
            domain=Domain.UNKNOWN,
            urgency=Urgency.SOON,
            request_type=RequestType.UNKNOWN,
            duplicate=dup,
            confidence=0.0,
        )
        ClassificationResult(
            request_id=req.id,
            domain=Domain.UNKNOWN,
            urgency=Urgency.SOON,
            request_type=RequestType.UNKNOWN,
            duplicate=dup,
            confidence=1.0,
        )
