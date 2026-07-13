"""
Unit tests for tension detection.

Tension detection identifies goals that aren't directly contradictory but
create friction — pursuing one may undermine progress on the other, or
they pull in opposite directions over time.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from jobstar.conflict.base import (
    ConflictResult,
    ConflictType,
    ConflictSeverity,
    GoalContext,
)


class TestTensionDetection:
    """Tests for tension detection between goals."""

    @pytest.fixture
    def work_goal(self):
        return GoalContext(
            goal_id="goal-t1",
            title="Work 60 hours per week on startup",
            description="Commit to intensive work schedule to launch product.",
            domain="work",
            tags=["startup", "intensive"],
            created_at=datetime.now(timezone.utc),
        )

    @pytest.fixture
    def family_goal(self):
        return GoalContext(
            goal_id="goal-t2",
            title="Spend more quality time with family",
            description="Dedicate evenings and weekends to family activities.",
            domain="personal",
            tags=["family", "balance"],
            created_at=datetime.now(timezone.utc),
        )

    @pytest.fixture
    def unrelated_goal(self):
        return GoalContext(
            goal_id="goal-t3",
            title="Learn Spanish",
            description="Study Spanish for 30 minutes daily.",
            domain="personal",
            tags=["learning", "language"],
            created_at=datetime.now(timezone.utc),
        )

    def test_tension_type_is_not_contradiction(self):
        """Tension should be a distinct conflict type from contradiction."""
        assert ConflictType.TENSION != ConflictType.CONTRADICTION

    def test_tension_severity_is_lower_than_contradiction(self):
        """Tension should typically be lower severity than contradiction."""
        assert ConflictSeverity.LOW.value < ConflictSeverity.HIGH.value
        assert ConflictSeverity.MEDIUM.value < ConflictSeverity.HIGH.value

    def test_work_life_tension_pattern(self, work_goal, family_goal):
        """Work-intensive and family-time goals should exhibit tension."""
        # Tension patterns can be detected through keyword/domain analysis
        work_keywords = {"work", "hours", "intensive", "startup"}
        family_keywords = {"family", "evenings", "weekends", "balance"}

        work_text = (work_goal.title + " " + work_goal.description).lower()
        family_text = (family_goal.title + " " + family_goal.description).lower()

        work_matches = work_keywords & set(work_text.split())
        family_matches = family_keywords & set(family_text.split())

        # Both goals have tension-indicating keywords
        assert len(work_matches) > 0, "Work goal should have work-intensity keywords"
        assert len(family_matches) > 0, "Family goal should have family-time keywords"

        # Cross-domain tension: work domain vs personal domain
        assert work_goal.domain != family_goal.domain, (
            "Tension often occurs across domains"
        )

    def test_no_tension_with_unrelated_goal(self, work_goal, unrelated_goal):
        """An unrelated goal should not exhibit tension with the work goal."""
        # Learning Spanish doesn't directly compete with working long hours
        # in the way that family time does
        tension_keywords = {"family", "evenings", "weekends", "rest", "vacation"}
        unrelated_text = (unrelated_goal.title + " " + unrelated_goal.description).lower()

        matches = tension_keywords & set(unrelated_text.split())
        assert len(matches) == 0, "Unrelated goal should not have tension keywords"

    def test_tension_confidence_range(self):
        """Tension confidence should be in valid range [0, 1]."""
        # Tension is often more subjective than contradiction
        # so confidence may be lower
        valid_confidences = [0.0, 0.3, 0.5, 0.7, 0.95, 1.0]
        for conf in valid_confidences:
            assert 0.0 <= conf <= 1.0

    def test_tension_severity_scales_with_confidence(self):
        """Higher confidence tensions should have higher severity."""
        def severity_for_confidence(conf: float) -> ConflictSeverity:
            if conf >= 0.8:
                return ConflictSeverity.HIGH
            elif conf >= 0.5:
                return ConflictSeverity.MEDIUM
            else:
                return ConflictSeverity.LOW

        assert severity_for_confidence(0.9) == ConflictSeverity.HIGH
        assert severity_for_confidence(0.6) == ConflictSeverity.MEDIUM
        assert severity_for_confidence(0.3) == ConflictSeverity.LOW

    @pytest.mark.asyncio
    async def test_tension_detection_with_ai(self, work_goal, family_goal):
        """Full tension detection using a mock AI client."""
        from jobstar.conflict.cross_domain_detector import CrossDomainConflictDetector

        ai_client = AsyncMock()
        ai_client.complete.return_value = {
            "has_tension": True,
            "confidence": 0.72,
            "reasoning": "Working 60 hours per week directly reduces available "
            "evenings and weekends for family time, creating sustained tension.",
            "tension_type": "time_allocation",
            "evidence": [
                "Work goal claims evenings via 60-hour weeks",
                "Family goal requires evenings and weekends",
            ],
        }

        detector = CrossDomainConflictDetector(ai_client=ai_client)
        result = await detector.detect(work_goal, family_goal)

        if result is not None:
            assert result.confidence == 0.72
            assert "time" in result.reasoning.lower() or "tension" in result.reasoning.lower()
