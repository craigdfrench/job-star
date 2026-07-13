"""
Conflict detection engine.

Orchestrates all detectors, deduplicates results, and produces
a ConflictReport. Designed for cross-domain awareness — all goals
from all domains are analyzed together.
"""

from typing import Any, Optional

from job_star.conflict.detectors.contradiction import ContradictionDetector
from job_star.conflict.detectors.duplicate import DuplicateDetector
from job_star.conflict.detectors.resource import ResourceDetector
from job_star.conflict.detectors.tension import TensionDetector
from job_star.conflict.models import Conflict, ConflictReport
from job_star.conflict.types import ConflictType, Severity
from job_star.goal.models import Goal


class ConflictEngine:
    """
    Main conflict detection engine.

    Runs all registered detectors against the full set of goals
    and aggregates results into a ConflictReport.
    """

    def __init__(
        self,
        detectors: Optional[list] = None,
        enable_cross_domain: bool = True,
    ) -> None:
        """
        Initialize the engine with detectors.

        Args:
            detectors: Custom list of detectors. If None, uses defaults.
            enable_cross_domain: If True, analyze goals across all domains
                                 together. If False, only compare goals
                                 within the same domain.
        """
        if detectors is None:
            detectors = [
                DuplicateDetector(),
                ContradictionDetector(),
                ResourceDetector(),
                TensionDetector(),
            ]
        self.detectors = detectors
        self.enable_cross_domain = enable_cross_domain

    def analyze(self, goals: list[Goal]) -> ConflictReport:
        """
        Analyze all goals for conflicts.

        Args:
            goals: All active goals to analyze.

        Returns:
            A ConflictReport containing all detected conflicts.
        """
        if not goals:
            return ConflictReport(conflicts=[], analyzed_goal_count=0)

        all_conflicts: list[Conflict] = []

        if self.enable_cross_domain:
            # Analyze all goals together for cross-domain awareness
            for detector in self.detectors:
                all_conflicts.extend(detector.detect(goals))
        else:
            # Group by domain and analyze each group separately
            domain_groups: dict[str, list[Goal]] = {}
            for goal in goals:
                domain = getattr(goal, "domain", None) or "unassigned"
                domain_groups.setdefault(domain, []).append(goal)
            for domain_goals in domain_groups.values():
                for detector in self.detectors:
                    all_conflicts.extend(detector.detect(domain_goals))

        # Deduplicate conflicts
        all_conflicts = self._deduplicate(all_conflicts)

        return ConflictReport(
            conflicts=all_conflicts,
            analyzed_goal_count=len(goals),
        )

    def analyze_pair(self, goal_a: Goal, goal_b: Goal) -> list[Conflict]:
        """
        Analyze a specific pair of goals for conflicts.

        Useful for checking conflicts when a new goal is added.

        Args:
            goal_a: First goal.
            goal_b: Second goal.

        Returns:
            List of conflicts between these two goals.
        """
        report = self.analyze([goal_a, goal_b])
        return report.conflicts

    def _deduplicate(self, conflicts: list[Conflict]) -> list[Conflict]:
        """Remove duplicate conflicts (same type, same goals)."""
        seen: set[tuple] = set()
        unique: list[Conflict] = []
        for conflict in conflicts:
            # Sort goal_ids so order doesn't matter
            key = (
                conflict.conflict_type,
                tuple(sorted(conflict.goal_ids)),
                conflict.description,
            )
            if key not in seen:
                seen.add(key)
                unique.append(conflict)
        return unique
