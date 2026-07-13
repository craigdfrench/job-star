"""
Competing resource detector.

Detects goals that compete for the same limited resources:
- Budget / financial resources
- Personnel / team members
- Time windows (overlapping deadlines)
- Equipment / infrastructure
"""

from datetime import datetime
from typing import Any

from job_star.conflict.models import Conflict
from job_star.conflict.types import ConflictType, Severity
from job_star.goal.models import Goal


class ResourceDetector:
    """Detects goals competing for the same limited resources."""

    name: str = "resource_detector"

    def __init__(
        self,
        budget_overlap_threshold: float = 0.5,
        deadline_overlap_days: int = 3,
    ) -> None:
        self.budget_overlap_threshold = budget_overlap_threshold
        self.deadline_overlap_days = deadline_overlap_days

    def detect(self, goals: list[Goal]) -> list[Conflict]:
        conflicts: list[Conflict] = []

        conflicts.extend(self._detect_budget_competition(goals))
        conflicts.extend(self._detect_personnel_competition(goals))
        conflicts.extend(self._detect_deadline_competition(goals))
        conflicts.extend(self._detect_equipment_competition(goals))

        return conflicts

    def _detect_budget_competition(self, goals: list[Goal]) -> list[Conflict]:
        """Detect goals competing for budget in the same domain."""
        conflicts: list[Conflict] = []
        for i, goal_a in enumerate(goals):
            budget_a = self._get_attr(goal_a, "budget")
            domain_a = self._get_attr(goal_a, "domain")
            if budget_a is None:
                continue
            for goal_b in goals[i + 1 :]:
                budget_b = self._get_attr(goal_b, "budget")
                domain_b = self._get_attr(goal_b, "domain")
                if budget_b is None:
                    continue
                # Only flag if same domain or no domain specified
                if domain_a and domain_b and domain_a != domain_b:
                    continue
                total = budget_a + budget_b
                # Flag if combined budget is significant
                conflicts.append(
                    Conflict(
                        conflict_type=ConflictType.RESOURCE_COMPETITION,
                        severity=Severity.MEDIUM,
                        goal_ids=(goal_a.id, goal_b.id),
                        description=(
                            f"Goals compete for budget: "
                            f"combined ${total:,.2f}"
                        ),
                        evidence={
                            "resource_type": "budget",
                            "budget_a": budget_a,
                            "budget_b": budget_b,
                            "combined": total,
                            "domain": domain_a or domain_b,
                        },
                        suggested_resolution=(
                            "Review budget allocation. Combined costs may "
                            "exceed available resources."
                        ),
                    )
                )
        return conflicts

    def _detect_personnel_competition(self, goals: list[Goal]) -> list[Conflict]:
        """Detect goals requiring the same personnel."""
        conflicts: list[Conflict] = []
        for i, goal_a in enumerate(goals):
            assignees_a = set(self._get_attr(goal_a, "assignees", []) or [])
            if not assignees_a:
                continue
            for goal_b in goals[i + 1 :]:
                assignees_b = set(self._get_attr(goal_b, "assignees", []) or [])
                if not assignees_b:
                    continue
                overlap = assignees_a & assignees_b
                if overlap:
                    conflicts.append(
                        Conflict(
                            conflict_type=ConflictType.RESOURCE_COMPETITION,
                            severity=Severity.MEDIUM,
                            goal_ids=(goal_a.id, goal_b.id),
                            description=(
                                f"Goals share {len(overlap)} assignee(s): "
                                f"{', '.join(sorted(overlap))}"
                            ),
                            evidence={
                                "resource_type": "personnel",
                                "shared_assignees": sorted(overlap),
                                "assignees_a": sorted(assignees_a),
                                "assignees_b": sorted(assignees_b),
                            },
                            suggested_resolution=(
                                "Shared personnel may be over-allocated. "
                                "Consider staggering timelines or reassigning."
                            ),
                        )
                    )
        return conflicts

    def _detect_deadline_competition(self, goals: list[Goal]) -> list[Conflict]:
        """Detect goals with overlapping deadlines."""
        conflicts: list[Conflict] = []
        for i, goal_a in enumerate(goals):
            deadline_a = self._get_attr(goal_a, "deadline")
            if not deadline_a:
                continue
            for goal_b in goals[i + 1 :]:
                deadline_b = self._get_attr(goal_b, "deadline")
                if not deadline_b:
                    continue
                try:
                    if isinstance(deadline_a, str):
                        da = datetime.fromisoformat(deadline_a)
                    else:
                        da = deadline_a
                    if isinstance(deadline_b, str):
                        db = datetime.fromisoformat(deadline_b)
                    else:
                        db = deadline_b
                except (ValueError, TypeError):
                    continue
                diff_days = abs((da - db).days)
                if diff_days <= self.deadline_overlap_days:
                    conflicts.append(
                        Conflict(
                            conflict_type=ConflictType.RESOURCE_COMPETITION,
                            severity=Severity.HIGH if diff_days == 0 else Severity.MEDIUM,
                            goal_ids=(goal_a.id, goal_b.id),
                            description=(
                                f"Goals have deadlines within {diff_days} "
                                f"day(s) of each other"
                            ),
                            evidence={
                                "resource_type": "time",
                                "deadline_a": str(da),
                                "deadline_b": str(db),
                                "days_apart": diff_days,
                            },
                            suggested_resolution=(
                                "Deadlines are very close. Consider "
                                "staggering or prioritizing one over the other."
                            ),
                        )
                    )
        return conflicts

    def _detect_equipment_competition(self, goals: list[Goal]) -> list[Conflict]:
        """Detect goals requiring the same equipment/infrastructure."""
        conflicts: list[Conflict] = []
        for i, goal_a in enumerate(goals):
            equipment_a = set(self._get_attr(goal_a, "required_resources", []) or [])
            if not equipment_a:
                continue
            for goal_b in goals[i + 1 :]:
                equipment_b = set(self._get_attr(goal_b, "required_resources", []) or [])
                if not equipment_b:
                    continue
                overlap = equipment_a & equipment_b
                if overlap:
                    conflicts.append(
                        Conflict(
                            conflict_type=ConflictType.RESOURCE_COMPETITION,
                            severity=Severity.LOW,
                            goal_ids=(goal_a.id, goal_b.id),
                            description=(
                                f"Goals require shared resources: "
                                f"{', '.join(sorted(overlap))}"
                            ),
                            evidence={
                                "resource_type": "equipment",
                                "shared_resources": sorted(overlap),
                                "resources_a": sorted(equipment_a),
                                "resources_b": sorted(equipment_b),
                            },
                            suggested_resolution=(
                                "Shared resources may need scheduling. "
                                "Coordinate access timing."
                            ),
                        )
                    )
        return conflicts

    def _get_attr(self, goal: Goal, attr: str, default: Any = None) -> Any:
        """Safely get an attribute from a goal, with a default."""
        return getattr(goal, attr, default)
