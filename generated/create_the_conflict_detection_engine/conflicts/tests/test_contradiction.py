"""
Unit tests for contradiction detection in the conflict detection engine.

Contradiction detection identifies goals that are logically incompatible —
achieving one makes achieving the other impossible.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from jobstar.conflict.base import (
    ConflictResult,
    ConflictType,
    ConflictSeverity,
    GoalContext,
)
from jobstar.conflict.contradiction import ContradictionDetector


class TestContradictionDetector:
    """Tests for the ContradictionDetector strategy."""

    @pytest.fixture
    def detector(self):
        """Create a ContradictionDetector with a mock AI client."""
        ai_client = AsyncMock()
        return ContradictionDetector(ai_client=ai_client)

    @pytest.fixture
    def sample_goal_a(self):
        return GoalContext(
            goal_id="goal-001",
            title="Maximize system uptime to 99.99%",
            description="Ensure the production system has minimal downtime.",
            domain="work",
            tags=["reliability", "production"],
            created_at=datetime.now(timezone.utc),
        )

    @pytest.fixture
    def sample_goal_b(self):
        return GoalContext(
            goal_id="goal-002",
            title="Perform major infrastructure migration",
            description="Migrate all services to new cloud provider with expected downtime.",
            domain="work",
            tags=["migration", "infrastructure"],
            created_at=datetime.now(timezone.utc),
        )

    @pytest.fixture
    def sample_goal_c(self):
        return GoalContext(
            goal_id="goal-003",
            title="Improve code documentation",
            description="Add comprehensive docs to all modules.",
            domain="meta",
            tags=["documentation"],
            created_at=datetime.now(timezone.utc),
        )

    def test_detector_type(self, detector):
        """The detector should report its conflict type as CONTRADICTION."""
        assert detector.conflict_type == ConflictType.CONTRADICTION

    @pytest.mark.asyncio
    async def test_detect_contradiction_found(self, detector, sample_goal_a, sample_goal_b):
        """When AI identifies a contradiction, a ConflictResult is returned."""
        detector.ai_client.complete.return_value = {
            "has_contradiction": True,
            "confidence": 0.85,
            "reasoning": "Maximizing uptime while performing a major migration "
            "with expected downtime are directly contradictory.",
            "evidence": [
                "Goal A requires 99.99% uptime",
                "Goal B explicitly expects downtime during migration",
            ],
        }

        result = await detector.detect(sample_goal_a, sample_goal_b)

        assert result is not None
        assert result.conflict_type == ConflictType.CONTRADICTION
        assert result.severity == ConflictSeverity.HIGH
        assert result.confidence == 0.85
        assert "uptime" in result.reasoning.lower()

    @pytest.mark.asyncio
    async def test_detect_no_contradiction(self, detector, sample_goal_a, sample_goal_c):
        """When goals are compatible, no conflict result is returned."""
        detector.ai_client.complete.return_value = {
            "has_contradiction": False,
            "confidence": 0.92,
            "reasoning": "These goals operate in different areas and do not conflict.",
            "evidence": [],
        }

        result = await detector.detect(sample_goal_a, sample_goal_c)

        assert result is None

    @pytest.mark.asyncio
    async def test_detect_low_confidence_treated_as_none(self, detector, sample_goal_a, sample_goal_b):
        """Low-confidence contradiction detections should not produce results."""
        detector.ai_client.complete.return_value = {
            "has_contradiction": True,
            "confidence": 0.35,
            "reasoning": "Possibly contradictory but uncertain.",
            "evidence": [],
        }

        result = await detector.detect(sample_goal_a, sample_goal_b)

        assert result is None

    @pytest.mark.asyncio
    async def test_detect_ai_error_returns_none(self, detector, sample_goal_a, sample_goal_b):
        """If the AI client raises an error, detection should return None gracefully."""
        detector.ai_client.complete.side_effect = Exception("AI service unavailable")

        result = await detector.detect(sample_goal_a, sample_goal_b)

        assert result is None

    @pytest.mark.asyncio
    async def test_prompt_includes_both_goals(self, detector, sample_goal_a, sample_goal_b):
        """The AI prompt should include information from both goals."""
        detector.ai_client.complete.return_value = {
            "has_contradiction": False,
            "confidence": 0.9,
            "reasoning": "No contradiction.",
            "evidence": [],
        }

        await detector.detect(sample_goal_a, sample_goal_b)

        call_args = detector.ai_client.complete.call_args
        prompt = str(call_args)
        assert sample_goal_a.title in prompt
        assert sample_goal_b.title in prompt
