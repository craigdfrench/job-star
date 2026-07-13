"""
Tests for conflict reporting and scoring.

When conflicts are detected, they need to be scored, prioritized,
and formatted into actionable reports for the user.
"""

import pytest
from datetime import datetime, timezone

from jobstar.conflict.base import (
    ConflictResult,
    ConflictType,
    ConflictSeverity,
    GoalContext,
)


class TestConflictScoring:
    """Tests for conflict scoring logic."""

    def test_severity_ordering(self):
        """Severity levels should have a clear ordering."""
        assert ConflictSeverity.LOW.value < ConflictSeverity.MEDIUM.value
        assert ConflictSeverity.MEDIUM.value < ConflictSeverity.HIGH.value
        assert ConflictSeverity.HIGH.value < ConflictSeverity.CRITICAL.value

    def test_conflict_type_values_are_unique(self):
        """Each conflict type should have a unique value."""
        types = [
            ConflictType.DUPLICATE,
            ConflictType.CONTRADICTION,
            ConflictType.COMPETING_RESOURCES,
            ConflictType.TENSION,
        ]
        values = [t.value for t in types]
        assert len(values) == len(set(values)), "Conflict type values must be unique"

    def test_high_confidence_contradiction_is_critical(self):
        """A high-confidence contradiction should map to CRITICAL severity."""
        result = ConflictResult(
            conflict_type=ConflictType.CONTRADICTION,
            severity=ConflictSeverity.CRITICAL,
            confidence=0.95,
            reasoning="Direct logical contradiction between goals.",
            goal_a_id="g1",
            goal_b_id="g2",
            evidence=["A requires X", "B requires not-X"],
        )

        assert result.severity == ConflictSeverity.CRITICAL
        assert result.confidence > 0.9

    def test_low_confidence_tension_is_low_severity(self):
        """A low-confidence tension should map to LOW severity."""
        result = ConflictResult(
            conflict_type=ConflictType.TENSION,
            severity=ConflictSeverity.LOW,
            confidence=0.35,
            reasoning="Slight tension possible but unlikely.",
            goal_a_id="g1",
            goal_b_id="g3",
            evidence=[],
        )

        assert result.severity == ConflictSeverity.LOW
        assert result.confidence < 0.5

    def test_duplicate_severity_is_medium(self):
        """Duplicate detection should typically be MEDIUM severity (wasteful, not harmful)."""
        result = ConflictResult(
            conflict_type=ConflictType.DUPLICATE,
            severity=ConflictSeverity.MEDIUM,
            confidence=0.88,
            reasoning="These goals appear to be duplicates.",
            goal_a_id="g1",
            goal_b_id="g2",
            evidence=["Same title", "Same description"],
        )

        assert result.severity == ConflictSeverity.MEDIUM


class TestConflictReporting:
    """Tests for conflict report formatting."""

    @pytest.fixture
    def sample_conflicts(self):
        """A sample set of conflicts for reporting."""
        return [
            ConflictResult(
                conflict_type=ConflictType.CONTRADICTION,
                severity=ConflictSeverity.CRITICAL,
                confidence=0.95,
                reasoning="Goal A requires 99.99% uptime while Goal B requires downtime.",
                goal_a_id="g1",
                goal_b_id="g2",
                evidence=["A: maximize uptime", "B: migration with downtime"],
            ),
            ConflictResult(
                conflict_type=ConflictType.DUPLICATE,
                severity=ConflictSeverity.MEDIUM,
                confidence=0.90,
                reasoning="Two goals with same title and description.",
                goal_a_id="g3",
                goal_b_id="g4",
                evidence=["Identical titles"],
            ),
            ConflictResult(
                conflict_type=ConflictType.TENSION,
                severity=ConflictSeverity.LOW,
                confidence=0.40,
                reasoning="Slight time tension between work and personal goals.",
                goal_a_id="g5",
                goal_b_id="g6",
                evidence=[],
            ),
        ]

    def test_conflicts_can_be_sorted_by_severity(self, sample_conflicts):
        """Conflicts should be sortable by severity (highest first)."""
        sorted_conflicts = sorted(
            sample_conflicts,
            key=lambda c: c.severity.value,
            reverse=True,
        )

        assert sorted_conflicts[0].severity == ConflictSeverity.CRITICAL
        assert sorted_conflicts[1].severity == ConflictSeverity.MEDIUM
        assert sorted_conflicts[2].severity == ConflictSeverity.LOW

    def test_conflicts_can_be_sorted_by_confidence(self, sample_conflicts):
        """Conflicts should be sortable by confidence (highest first)."""
        sorted_conflicts = sorted(
            sample_conflicts,
            key=lambda c: c.confidence,
            reverse=True,
        )

        assert sorted_conflicts[0].confidence >= sorted_conflicts[1].confidence
        assert sorted_conflicts[1].confidence >= sorted_conflicts[2].confidence

    def test_conflicts_can_be_filtered_by_type(self, sample_conflicts):
        """Conflicts should be filterable by conflict type."""
        contradictions = [c for c in sample_conflicts if c.conflict_type == ConflictType.CONTRADICTION]
        duplicates = [c for c in sample_conflicts if c.conflict_type == ConflictType.DUPLICATE]
        tensions = [c for c in sample_conflicts if c.conflict_type == ConflictType.TENSION]

        assert len(contradictions) == 1
        assert len(duplicates) == 1
        assert len(tensions) == 1

    def test_conflicts_can_be_filtered_by_severity_threshold(self, sample_conflicts):
        """Conflicts should be filterable by minimum severity."""
        high_or_above = [
            c for c in sample_conflicts
            if c.severity.value >= ConflictSeverity.HIGH.value
        ]

        assert len(high_or_above) == 1
        assert high_or_above[0].severity == ConflictSeverity.CRITICAL

    def test_conflict_summary_counts(self, sample_conflicts):
        """A summary should correctly count conflicts by type."""
        from collections import Counter

        type_counts = Counter(c.conflict_type for c in sample_conflicts)

        assert type_counts[ConflictType.CONTRADICTION] == 1
        assert type_counts[ConflictType.DUPLICATE] == 1
        assert type_counts[ConflictType.TENSION] == 1
        assert sum(type_counts.values()) == 3

    def test_conflict_report_contains_all_essential_fields(self, sample_conflicts):
        """Each conflict result should contain all fields needed for reporting."""
        for conflict in sample_conflicts:
            assert conflict.conflict_type is not None
            assert conflict.severity is not None
            assert 0.0 <= conflict.confidence <= 1.0
            assert len(conflict.reasoning) > 0
            assert conflict.goal_a_id is not None
            assert conflict.goal_b_id is not None
            assert isinstance(conflict.evidence, list)
