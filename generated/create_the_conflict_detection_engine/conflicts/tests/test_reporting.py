"""Tests for conflict scoring and reporting."""

import pytest

from jobstar.conflict.base import ConflictResult
from jobstar.conflict.scoring import (
    ConflictScore,
    Confidence,
    Severity,
    score_conflict,
)
from jobstar.conflict.report import build_report, ScoredConflict
from jobstar.conflict.reporting import generate_conflict_report, report_to_markdown


def _make_result(ctype: str, metadata=None) -> ConflictResult:
    return ConflictResult(
        conflict_type=ctype,
        goal_a_id="g-001",
        goal_b_id="g-002",
        description=f"Test {ctype} conflict",
        metadata=metadata or {},
    )


class TestScoring:
    def test_duplicate_default(self):
        score = score_conflict(_make_result("duplicate"))
        assert score.severity == Severity.MEDIUM
        assert score.confidence == Confidence.HIGH
        assert 0.0 < score.composite <= 1.0

    def test_duplicate_exact_id_is_certain(self):
        score = score_conflict(_make_result("duplicate", {"match_method": "exact_id"}))
        assert score.confidence == Confidence.CERTAIN
        assert score.severity == Severity.HIGH

    def test_contradiction_default_critical(self):
        score = score_conflict(_make_result("contradiction"))
        assert score.severity == Severity.CRITICAL

    def test_contradiction_negation_bumps_confidence(self):
        score = score_conflict(_make_result("contradiction", {"negation_detected": True}))
        assert score.confidence == Confidence.HIGH

    def test_resource_zero_capacity_critical(self):
        score = score_conflict(_make_result("resource", {"available_capacity": 0}))
        assert score.severity == Severity.CRITICAL

    def test_tension_repeated_bumps_confidence(self):
        score = score_conflict(_make_result("tension", {"occurrence_count": 5}))
        assert score.confidence == Confidence.MEDIUM

    def test_composite_is_product(self):
        score = ConflictScore.compute(Severity.CRITICAL, Confidence.CERTAIN)
        assert score.composite == 1.0

    def test_unknown_type_defaults_low(self):
        score = score_conflict(_make_result("unknown_type"))
        assert score.severity == Severity.LOW
        assert score.confidence == Confidence.LOW


class TestReport:
    def test_empty_report(self):
        report = build_report([])
        assert report.total_conflicts == 0
        assert report.top_conflicts == []

    def test_report_counts(self):
        results = [
            _make_result("duplicate"),
            _make_result("contradiction"),
            _make_result("tension"),
        ]
        report = build_report(results)
        assert report.total_conflicts == 3
        assert report.by_type["duplicate"] == 1
        assert report.by_type["contradiction"] == 1
        assert report.by_type["tension"] == 1

    def test_top_conflicts_sorted_by_composite(self):
        results = [
            _make_result("tension"),
            _make_result("contradiction"),
            _make_result("duplicate"),
        ]
        report = build_report(results, top_n=2)
        assert len(report.top_conflicts) == 2
        # Contradiction (critical) should be first
        assert report.top_conflicts[0]["conflict"]["conflict_type"] == "contradiction"

    def test_summary_mentions_counts(self):
        report = build_report([_make_result("contradiction")])
        assert "1 conflict" in report.summary
        assert "critical" in report.summary.lower()

    def test_markdown_output(self):
        report = generate_conflict_report([_make_result("duplicate")])
        md = report_to_markdown(report)
        assert "# Conflict Report" in md
        assert "duplicate" in md


class TestFacade:
    def test_generate_report_returns_report(self):
        report = generate_conflict_report([_make_result("duplicate")], context={"goal_count": 5})
        assert report.total_conflicts == 1
        assert "5 goal" in report.summary
