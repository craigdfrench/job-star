"""Tests for triage data models."""

import pytest
from uuid import uuid4

from job_star.triage.models import (
    ClassificationResult,
    Domain,
    GoalRegistryEntry,
    GoalStatus,
    IntakeRequest,
    RequestType,
    Source,
    Urgency,
)


class TestIntakeRequest:
    def test_minimal_request(self):
        req = IntakeRequest(raw_text="Fix the login bug")
        assert req.raw_text == "Fix the login bug"
        assert req.source == Source.HUMAN
        assert req.id is not None
        assert req.received_at is not None

    def test_request_with_context(self):
        req = IntakeRequest(
            raw_text="Research vector DB options",
            source=Source.JOB_STAR,
            context={"priority": "high", "channel": "slack"},
        )
        assert req.source == Source.JOB_STAR
        assert req.context["priority"] == "high"

    def test_empty_text_rejected(self):
        with pytest.raises(Exception):
            IntakeRequest(raw_text="")


class TestClassificationResult:
    def test_basic_classification(self):
        result = ClassificationResult(
            request_id=uuid4(),
            domain=Domain.ENGINEERING,
            urgency=Urgency.SOON,
            request_type=RequestType.FIX,
            confidence=0.85,
            summary="Fix login authentication bug",
        )
        assert result.domain == Domain.ENGINEERING
        assert result.is_duplicate is False
        assert result.duplicate_of is None

    def test_duplicate_detection_fields(self):
        existing_id = uuid4()
        result = ClassificationResult(
            request_id=uuid4(),
            domain=Domain.ENGINEERING,
            urgency=Urgency.SOON,
            request_type=RequestType.FIX,
            confidence=0.9,
            summary="Fix login bug",
            duplicate_of=existing_id,
            duplicate_score=0.92,
            is_duplicate=True,
        )
        assert result.is_duplicate is True
        assert result.duplicate_of == existing_id

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            ClassificationResult(
                request_id=uuid4(),
                domain=Domain.UNKNOWN,
                urgency=Urgency.LATER,
                request_type=RequestType.UNKNOWN,
                confidence=1.5,
                summary="test",
            )


class TestGoalRegistryEntry:
    def test_new_goal_defaults(self):
        goal = GoalRegistryEntry(
            title="Build triage engine",
            description="Create a service that classifies intake requests.",
            domain=Domain.META,
            urgency=Urgency.SOON,
            request_type=RequestType.BUILD,
        )
        assert goal.status == GoalStatus.PROPOSED
        assert goal.tags == []
        assert goal.dependencies == []
        assert goal.parent_goal_id is None

    def test_serialization_roundtrip(self):
        goal = GoalRegistryEntry(
            title="Test goal",
            description="A test.",
            domain=Domain.RESEARCH,
            urgency=Urgency.LATER,
            request_type=RequestType.RESEARCH,
            tags=["test", "vector-db"],
        )
        data = goal.model_dump_json()
        restored = GoalRegistryEntry.model_validate_json(data)
        assert restored.title == goal.title
        assert restored.tags == goal.tags
