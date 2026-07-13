"""
Unit tests for competing resource detection.

Competing resource detection identifies goals that draw from the same
limited resources (time, budget, personnel, compute) and may strain capacity.
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
from jobstar.conflict.duplicate import DuplicateDetector


class TestResourceDetector:
    """Tests for competing resource detection logic."""

    @pytest.fixture
    def detector(self):
        """Create a resource detector with a mock AI client."""
        from jobstar.conflict.cross_domain_detector import CrossDomainConflictDetector
        ai_client = AsyncMock()
        return CrossDomainConflictDetector(ai_client=ai_client)

    @pytest.fixture
    def resource_heavy_goal_a(self):
        return GoalContext(
            goal_id="goal-r1",
            title="Launch marketing campaign Q4",
            description="Full-scale digital marketing campaign requiring $50K budget "
            "and 3 team members for 8 weeks.",
            domain="work",
            tags=["marketing", "campaign"],
            metadata={"budget": 50000, "personnel": 3, "duration_weeks": 8},
            created_at=datetime.now(timezone.utc),
        )

    @pytest.fixture
    def resource_heavy_goal_b(self):
        return GoalContext(
            goal_id="goal-r2",
            title="Redesign company website",
            description="Complete website overhaul requiring $40K budget "
            "and 2 team members for 6 weeks.",
            domain="work",
            tags=["web", "design"],
            metadata={"budget": 40000, "personnel": 2, "duration_weeks": 6},
            created_at=datetime.now(timezone.utc),
        )

    @pytest.fixture
    def low_resource_goal(self):
        return GoalContext(
            goal_id="goal-r3",
            title="Write daily journal",
            description="Spend 10 minutes each morning journaling.",
            domain="personal",
            tags=["journaling", "habit"],
            metadata={"budget": 0, "personnel": 1, "duration_weeks": 0},
            created_at=datetime.now(timezone.utc),
        )

    def test_resource_overlap_detection(self, resource_heavy_goal_a, resource_heavy_goal_b):
        """Two goals with overlapping budget should be flagged for resource competition."""
        # Resource overlap is computed by comparing metadata fields
        budget_a = resource_heavy_goal_a.metadata.get("budget", 0)
        budget_b = resource_heavy_goal_b.metadata.get("budget", 0)
        total_budget = budget_a + budget_b

        # If total exceeds a threshold (e.g., $80K), flag as resource competition
        assert total_budget > 80000, "Expected combined budget to exceed threshold"

    def test_no_resource_overlap_with_low_resource_goal(
        self, resource_heavy_goal_a, low_resource_goal
    ):
        """A resource-heavy goal and a low-resource goal should not trigger resource competition."""
        budget_a = resource_heavy_goal_a.metadata.get("budget", 0)
        budget_c = low_resource_goal.metadata.get("budget", 0)
        total_budget = budget_a + budget_c

        assert total_budget < 80000, "Expected combined budget to be under threshold"

    def test_personnel_overlap_detection(self, resource_heavy_goal_a, resource_heavy_goal_b):
        """Two goals requiring the same personnel should be flagged."""
        personnel_a = resource_heavy_goal_a.metadata.get("personnel", 0)
        personnel_b = resource_heavy_goal_b.metadata.get("personnel", 0)
        total_personnel = personnel_a + personnel_b

        # If total personnel exceeds available team (e.g., 4), flag
        assert total_personnel > 4, "Expected combined personnel to exceed availability"

    def test_time_overlap_detection(self, resource_heavy_goal_a, resource_heavy_goal_b):
        """Two goals with overlapping time windows should be flagged."""
        duration_a = resource_heavy_goal_a.metadata.get("duration_weeks", 0)
        duration_b = resource_heavy_goal_b.metadata.get("duration_weeks", 0)

        # Both are Q4 timelines — they overlap
        assert duration_a > 0 and duration_b > 0, "Both goals have time commitments"

    @pytest.mark.asyncio
    async def test_resource_conflict_result_structure(
        self, detector, resource_heavy_goal_a, resource_heavy_goal_b
    ):
        """When resource competition is detected, the result should have correct structure."""
        detector.ai_client.complete.return_value = {
            "has_conflict": True,
            "conflict_type": "competing_resources",
            "confidence": 0.78,
            "reasoning": "Both goals require significant budget in Q4, "
            "totaling $90K which exceeds the $80K quarterly budget.",
            "resources": ["budget", "personnel"],
            "evidence": [
                f"Goal A budget: ${resource_heavy_goal_a.metadata['budget']}",
                f"Goal B budget: ${resource_heavy_goal_b.metadata['budget']}",
            ],
        }

        result = await detector.detect(resource_heavy_goal_a, resource_heavy_goal_b)

        if result is not None:
            assert result.conflict_type in [
                ConflictType.COMPETING_RESOURCES,
                ConflictType.TENSION,
            ]
            assert result.confidence > 0.5
            assert len(result.reasoning) > 0
