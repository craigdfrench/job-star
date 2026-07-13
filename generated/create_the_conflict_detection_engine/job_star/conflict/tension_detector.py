"""
Tension detection engine for Job-Star.

Analyzes pairs of goals to detect subtle tensions — friction that isn't
a direct contradiction or resource conflict but still creates drag when
both goals are pursued simultaneously.

This module is designed to work with the broader conflict detection
engine, handling the tension-specific detection while duplicate,
contradiction, and resource conflict detection are handled by sibling
modules.
"""

from itertools import combinations
from typing import Iterable

from .tension_patterns import GoalProxy, TENSION_PATTERNS
from .tension_types import TensionResult, TensionSignal, TensionSeverity


class TensionDetector:
    """Detects tensions between goals.
    
    Usage:
        detector = TensionDetector()
        results = detector.detect_all(goals)
        for result in results:
            if result.is_actionable:
                print(result.summary())
    
    Or for a specific pair:
        result = detector.detect_pair(goal_a, goal_b)
    """

    def __init__(self, min_severity: TensionSeverity = TensionSeverity.LOW):
        """
        Args:
            min_severity: Minimum severity to include in results.
                Signals below this are filtered out.
        """
        self.min_severity = min_severity
        self.patterns = TENSION_PATTERNS

    def detect_pair(self, goal_a: GoalProxy, goal_b: GoalProxy) -> TensionResult:
        """Detect all tension signals between a specific pair of goals.
        
        Args:
            goal_a: First goal proxy
            goal_b: Second goal proxy
            
        Returns:
            TensionResult with all detected signals above min_severity
        """
        signals = []

        for pattern in self.patterns:
            try:
                signal = pattern(goal_a, goal_b)
            except Exception as e:
                # Log but don't fail — one broken pattern shouldn't
                # prevent other patterns from running
                signal = None
                # In production: logger.warning(f"Pattern {pattern.__name__} failed: {e}")

            if signal and signal.severity.value >= self.min_severity.value:
                signals.append(signal)

        return TensionResult(
            goal_a_id=goal_a.id,
            goal_b_id=goal_b.id,
            signals=signals,
        )

    def detect_all(self, goals: Iterable[GoalProxy]) -> list[TensionResult]:
        """Detect tensions across all goal pairs.
        
        Args:
            goals: Iterable of GoalProxy objects
            
        Returns:
            List of TensionResults, only including pairs with at least
            one signal above min_severity. Sorted by max severity descending.
        """
        goal_list = list(goals)
        results = []

        for a, b in combinations(goal_list, 2):
            result = self.detect_pair(a, b)
            if result.signals:  # Only include if we found something
                results.append(result)

        # Sort by severity (highest first), then by confidence
        results.sort(
            key=lambda r: (r.max_severity.value, r.combined_confidence),
            reverse=True,
        )
        return results

    def detect_for_goal(
        self, target: GoalProxy, others: Iterable[GoalProxy]
    ) -> list[TensionResult]:
        """Detect tensions between a specific goal and all others.
        
        Useful when a new goal is added and you want to check it against
        existing goals without re-scanning everything.
        
        Args:
            target: The goal to check
            others: Other goals to check against
            
        Returns:
            List of TensionResults between target and each other goal
        """
        results = []
        for other in others:
            if other.id == target.id:
                continue
            result = self.detect_pair(target, other)
            if result.signals:
                results.append(result)

        results.sort(
            key=lambda r: (r.max_severity.value, r.combined_confidence),
            reverse=True,
        )
        return results
