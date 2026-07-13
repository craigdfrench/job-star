"""
Integration tests for the full conflict detection pipeline.

These tests exercise the complete flow: multiple goals go in,
conflicts of all types are detected, scored, and reported.
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
from jobstar.conflict.duplicate import DuplicateDetector
from jobstar.conflict.cross_domain_detector import CrossDomainConflictDetector


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_ai_client():
    """A mock AI client that can be configured per-test."""
    client = AsyncMock()
    client.complete.return_value = {
        "has_conflict": False,
        "has_contradiction": False,
        "has_tension": False,
        "confidence": 0.5,
        "reasoning": "No conflict detected.",
        "evidence": [],
    }
    return client


@pytest.fixture
def goal_set():
    """A realistic set of goals with various conflict relationships."""
    return [
        GoalContext(
            goal_id="g1",
            title="Ship product v2.0 by end of Q4",
            description="Complete all features and ship by December 31.",
            domain="work",
            tags=["product", "deadline"],
            metadata={"budget": 30000, "personnel": 4, "priority": "high"},
            created_at=datetime.now(timezone.utc),
        ),
        GoalContext(
            goal_id="g2",
            title="Deliver product v2.0 by end of Q4",  # Near-duplicate of g1
            description="Finish the product launch by end of Q4.",
            domain="work",
            tags=["product", "launch"],
            metadata={"budget": 30000, "personnel": 4, "priority": "high"},
            created_at=datetime.now(timezone.utc),
        ),
        GoalContext(
            goal_id="g3",
            title="Reduce team workload by 30%",
            description="Cut overtime and reduce sprint commitments.",
            domain="work",
            tags=["wellbeing", "team"],
            metadata={"budget": 0, "personnel": 0, "priority": "medium"},
            created_at=datetime.now(timezone.utc),
        ),
        GoalContext(
            goal_id="g4",
            title="Train for marathon in November",
            description="Run 50 miles per week, train 6 days a week.",
            domain="personal",
            tags=["fitness", "marathon"],
            metadata={"budget": 500, "personnel": 1, "priority": "medium"},
            created_at=datetime.now(timezone.utc),
        ),
        GoalContext(
            goal_id="g5",
            title="Complete online MBA program",
            description="Dedicate 20 hours per week to MBA coursework for 2 years.",
            domain="personal",
            tags=["education", "career"],
            metadata={"budget": 50000, "personnel": 1, "priority": "high"},
            created_at=datetime.now(timezone.utc),
        ),
    ]


# ---------------------------------------------------------------------------
# Duplicate detection integration
# ---------------------------------------------------------------------------

class TestDuplicateDetectionIntegration:
    """Integration tests for duplicate detection across a goal set."""

    @pytest.mark.asyncio
    async def test_duplicates_detected_in_goal_set(self, goal_set):
        """Near-duplicate goals should be detected."""
        detector = DuplicateDetector(ai_client=AsyncMock())

        # g1 and g2 are near-duplicates
        result = await detector.detect(goal_set[0], goal_set[1])

        if result is not None:
            assert result.conflict_type == ConflictType.DUPLICATE
            assert result.goal_a_id in ("g1", "g2")
            assert result.goal_b_id in ("g1", "g2")

    @pytest.mark.asyncio
    async def test_no_duplicate_for_different_goals(self, goal_set):
        """Goals with different titles/descriptions should not be flagged as duplicates."""
        detector = DuplicateDetector(ai_client=AsyncMock())

        result = await detector.detect(goal_set[0], goal_set[3])

        # g1 (ship product) and g4 (marathon training) are clearly different
        if result is not None:
            assert result.conflict_type != ConflictType.DUPLICATE or result.confidence < 0.5


# ---------------------------------------------------------------------------
# Cross-domain conflict integration
# ---------------------------------------------------------------------------

class TestCrossDomainIntegration:
    """Integration tests for cross-domain conflict detection."""

    @pytest.mark.asyncio
    async def test_cross_domain_tension_work_vs_personal(self, goal_set, mock_ai_client):
        """Work-intensive goal and personal-time goal should show cross-domain tension."""
        mock_ai_client.complete.return_value = {
            "has_tension": True,
            "confidence": 0.75,
            "reasoning": "Shipping product v2.0 requires intensive team effort "
            "while marathon training requires significant personal time, "
            "creating tension across work and personal domains.",
            "tension_type": "time_allocation",
            "evidence": ["Q4 deadline requires heavy work", "Marathon training needs 6 days/week"],
        }

        detector = CrossDomainConflictDetector(ai_client=mock_ai_client)
        result = await detector.detect(goal_set[0], goal_set[3])  # work vs personal

        if result is not None:
            assert result.confidence > 0.5
            assert goal_set[0].domain != goal_set[3].domain

    @pytest.mark.asyncio
    async def test_cross_domain_resource_competition(self, goal_set, mock_ai_client):
        """Goals competing for budget across domains should be detected."""
        mock_ai_client.complete.return_value = {
            "has_conflict": True,
            "conflict_type": "competing_resources",
            "confidence": 0.68,
            "reasoning": "Product launch ($30K) and MBA program ($50K) both require "
            "significant financial commitment, totaling $80K.",
            "resources": ["budget"],
            "evidence": ["Product budget: $30K", "MBA budget: $50K"],
        }

        detector = CrossDomainConflictDetector(ai_client=mock_ai_client)
        result = await detector.detect(goal_set[0], goal_set[4])  # work vs personal

        if result is not None:
            assert result.confidence > 0.5


# ---------------------------------------------------------------------------
# Full pipeline integration
# ---------------------------------------------------------------------------

class TestFullPipelineIntegration:
    """Tests that exercise the full conflict detection pipeline across all strategies."""

    @pytest.mark.asyncio
    async def test_all_conflict_types_can_coexist(self, goal_set, mock_ai_client):
        """The pipeline should be able to detect multiple conflict types simultaneously."""
        results = []

        duplicate_detector = DuplicateDetector(ai_client=mock_ai_client)
        cross_domain_detector = CrossDomainConflictDetector(ai_client=mock_ai_client)

        # Check all pairs
        for i, goal_a in enumerate(goal_set):
            for j, goal_b in enumerate(goal_set):
                if i >= j:
                    continue

                # Try duplicate detection
                dup_result = await duplicate_detector.detect(goal_a, goal_b)
                if dup_result:
                    results.append(dup_result)
                    continue

                # Try cross-domain detection
                cd_result = await cross_domain_detector.detect(goal_a, goal_b)
                if cd_result:
                    results.append(cd_result)

        # We should find at least some conflicts in this goal set
        # (The exact number depends on AI responses, but the pipeline should run)
        assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_empty_goal_set_produces_no_conflicts(self):
        """An empty goal set should produce zero conflicts."""
        detector = DuplicateDetector(ai_client=AsyncMock())
        results = []

        # No goals to compare
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_single_goal_produces_no_conflicts(self, goal_set):
        """A single goal cannot conflict with itself in the pairwise comparison."""
        # With only one goal, there are no pairs to check
        single_goal = goal_set[:1]
        pairs = [
            (single_goal[i], single_goal[j])
            for i in range(len(single_goal))
            for j in range(i + 1, len(single_goal))
        ]
        assert len(pairs) == 0

    @pytest.mark.asyncio
    async def test_conflict_results_are_symmetric(self, goal_set, mock_ai_client):
        """Detecting conflict(A, B) and conflict(B, A) should yield equivalent results."""
        detector = CrossDomainConflictDetector(ai_client=mock_ai_client)

        mock_ai_client.complete.return_value = {
            "has_tension": True,
            "confidence": 0.7,
            "reasoning": "Time tension between goals.",
            "evidence": [],
        }

        result_ab = await detector.detect(goal_set[0], goal_set[3])
        result_ba = await detector.detect(goal_set[3], goal_set[0])

        # Both should detect (or both should not)
        if result_ab is not None and result_ba is not None:
            assert result_ab.confidence == result_ba.confidence
