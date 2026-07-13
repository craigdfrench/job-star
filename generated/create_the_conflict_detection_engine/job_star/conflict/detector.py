"""
Main conflict detection orchestrator.

Coordinates all four detection strategies, manages pairwise comparison,
and optionally uses an LLM client for semantic analysis.
"""

from __future__ import annotations

from itertools import combinations
from typing import Callable, Optional

from .strategies import (
    ContradictionDetector,
    DuplicateDetector,
    ResourceConflictDetector,
    TensionDetector,
)
from .types import ConflictReport, ConflictType, GoalSnapshot


# Type alias for an optional LLM-based semantic analysis function.
# It takes (goal_a, goal_b, conflict_type) and returns a float score 0-1.
SemanticAnalyzer = Callable[[GoalSnapshot, GoalSnapshot, ConflictType], Optional[float]]


class ConflictDetector:
    """
    Main conflict detection engine.

    Usage:
        detector = ConflictDetector()
        reports = detector.detect_all(goals)
        for report in reports:
            print(f"[{report.severity.value}] {report.title}")
    """

    def __init__(
        self,
        semantic_analyzer: Optional[SemanticAnalyzer] = None,
        resource_budgets: Optional[dict[str, float]] = None,
    ):
        """
        Args:
            semantic_analyzer: Optional function that provides LLM-based semantic
                scores for conflict analysis. If None, only heuristic detection is used.
            resource_budgets: Override default resource budgets for resource conflict detection.
        """
        self.semantic_analyzer = semantic_analyzer
        self.duplicate_detector = DuplicateDetector()
        self.contradiction_detector = ContradictionDetector()
        self.resource_detector = ResourceConflictDetector(resource_budgets)
        self.tension_detector = TensionDetector()

    def detect_pair(
        self, a: GoalSnapshot, b: GoalSnapshot, all_goals: Optional[list[GoalSnapshot]] = None
    ) -> list[ConflictReport]:
        """
        Run all four conflict detectors on a single pair of goals.

        Returns multiple reports if multiple conflict types are detected.
        """
        reports: list[ConflictReport] = []

        # Get semantic scores if analyzer is available
        sem_dup = None
        sem_contra = None
        sem_tension = None
        if self.semantic_analyzer:
            sem_dup = self.semantic_analyzer(a, b, ConflictType.DUPLICATE)
            sem_contra = self.semantic_analyzer(a, b, ConflictType.CONTRADICTION)
            sem_tension = self.semantic_analyzer(a, b, ConflictType.TENSION)

        # Run each detector
        dup = self.duplicate_detector.detect(a, b, semantic_similarity=sem_dup)
        if dup:
            reports.append(dup)

        contra = self.contradiction_detector.detect(a, b, semantic_contradiction=sem_contra)
        if contra:
            reports.append(contra)

        resource = self.resource_detector.detect(a, b, all_goals=all_goals)
        if resource:
            reports.append(resource)

        tension = self.tension_detector.detect(a, b, semantic_tension=sem_tension)
        if tension:
            reports.append(tension)

        return reports

    def detect_all(self, goals: list[GoalSnapshot]) -> list[ConflictReport]:
        """
        Detect all conflicts across all pairs of goals.

        Args:
            goals: List of goal snapshots to analyze.

        Returns:
            List of conflict reports, sorted by severity (highest first).
        """
        all_reports: list[ConflictReport] = []

        for a, b in combinations(goals, 2):
            pair_reports = self.detect_pair(a, b, all_goals=goals)
            all_reports.extend(pair_reports)

        # Sort by severity (critical first), then by confidence
        severity_order = {
            "critical": 0,
            "high": 1,
            "medium": 2,
            "low": 3,
            "info": 4,
        }
        all_reports.sort(
            key=lambda r: (severity_order.get(r.severity.value, 5), -r.aggregate_confidence)
        )

        return all_reports

    def detect_for_goal(
        self, target: GoalSnapshot, all_goals: list[GoalSnapshot]
    ) -> list[ConflictReport]:
        """
        Detect conflicts involving a specific goal against all others.

        Useful when a new goal is added and you want to check it against
        existing goals without re-scanning everything.
        """
        reports: list[ConflictReport] = []
        for other in all_goals:
            if other.id == target.id:
                continue
            pair_reports = self.detect_pair(target, other, all_goals=all_goals)
            reports.extend(pair_reports)

        severity_order = {
            "critical": 0,
            "high": 1,
            "medium": 2,
            "low": 3,
            "info": 4,
        }
        reports.sort(
            key=lambda r: (severity_order.get(r.severity.value, 5), -r.aggregate_confidence)
        )
        return reports
