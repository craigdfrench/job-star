"""
Conflict detectors for Job-Star.

Each detector implements the ConflictDetector protocol and focuses on
one type of conflict. The engine orchestrates them.
"""

from typing import Protocol

from job_star.conflict.models import Conflict
from job_star.goal.models import Goal


class ConflictDetector(Protocol):
    """Protocol that all conflict detectors must implement."""

    @property
    def name(self) -> str:
        """Human-readable name of this detector."""
        ...

    def detect(self, goals: list[Goal]) -> list[Conflict]:
        """
        Analyze a list of goals and return any conflicts found.

        Args:
            goals: All active goals to analyze (cross-domain).

        Returns:
            A list of Conflict objects. May be empty.
        """
        ...
