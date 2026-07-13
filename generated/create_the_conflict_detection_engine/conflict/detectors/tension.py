"""
Tension detector.

Detects softer conflicts — goals that don't directly contradict but
pull in different directions, creating friction. This includes:

- Strategic direction mismatches (e.g., "reduce costs" vs "premium quality")
- Priority conflicts (two high-priority goals in different domains
  that compete for attention)
- Values tensions (speed vs. thoroughness, automation vs. headcount)
"""

from typing import Any

from job_star.conflict.models import Conflict
from job_star.conflict.types import ConflictType, Severity
from job_star.goal.models import Goal

# Tension axis pairs — goals on opposite ends of these axes create tension
TENSION_AXES: list[dict[str, Any]] = [
    {
        "name": "cost_vs_quality",
        "side_a": ["cheap", "low cost", "budget", "frugal", "reduce cost", "save money", "cut spending"],
        "side_b": ["premium", "high quality", "best", "luxury", "top-tier", "excellence"],
        "description": "Cost reduction vs. quality maximization",
    },
    {
        "name": "speed_vs_thoroughness",
        "side_a": ["fast", "quick", "rapid", "agile", "speed", "ship quickly", "minimum viable"],
        "side_b": ["thorough", "comprehensive", "complete", "rigorous", "detailed", "careful"],
        "description": "Speed vs. thoroughness",
    },
    {
        "name": "automation_vs_headcount",
        "side_a": ["automate", "ai", "machine learning", "self-service", "no-code", "script"],
        "side_b": ["hire", "expand team", "headcount", "staff up", "recruit", "human"],
        "description": "Automation vs. increasing headcount",
    },
    {
        "name": "centralize_vs_decentralize",
        "side_a": ["centralize", "consolidate", "unify", "standardize", "single source"],
        "side_b": ["decentralize", "distribute", "autonomous", "independent", "local"],
        "description": "Centralization vs. decentralization",
    },
    {
        "name": "build_vs_buy",
        "side_a": ["build in-house", "develop internally", "custom build", "from scratch"],
        "side_b": ["buy", "purchase", "off-the-shelf", "vendor", "third-party", "saas"],
        "description": "Build vs. buy",
    },
]


def _text_contains_any(text: str, phrases: list[str]) -> list[str]:
    """Return which phrases appear in the text."""
    text_lower = text.lower()
    return [p for p in phrases if p in text_lower]


class TensionDetector:
    """Detects tension conflicts between goals on opposing strategic axes."""

    name: str = "tension_detector"

    def detect(self, goals: list[Goal]) -> list[Conflict]:
        conflicts: list[Conflict] = []

        # For each axis, find goals on side A and side B
        for axis in TENSION_AXES:
            side_a_goals: list[tuple[Goal, list[str]]] = []
            side_b_goals: list[tuple[Goal, list[str]]] = []

            for goal in goals:
                text = f"{goal.title} {getattr(goal, 'description', '') or ''}"
                matches_a = _text_contains_any(text, axis["side_a"])
                matches_b = _text_contains_any(text, axis["side_b"])
                if matches_a and not matches_b:
                    side_a_goals.append((goal, matches_a))
                elif matches_b and not matches_a:
                    side_b_goals.append((goal, matches_b))

            # Flag tensions between any goal on side A and any on side B
            for goal_a, matches_a in side_a_goals:
                for goal_b, matches_b in side_b_goals:
                    if goal_a.id == goal_b.id:
                        continue
                    conflicts.append(
                        Conflict(
                            conflict_type=ConflictType.TENSION,
                            severity=Severity.LOW,
                            goal_ids=(goal_a.id, goal_b.id),
                            description=(
                                f"Tension detected: {axis['description']} "
                                f"(axis: {axis['name']})"
                            ),
                            evidence={
                                "tension_axis": axis["name"],
                                "axis_description": axis["description"],
                                "goal_a_signals": matches_a,
                                "goal_b_signals": matches_b,
                                "goal_a_title": goal_a.title,
                                "goal_b_title": goal_b.title,
                            },
                            suggested_resolution=(
                                f"These goals create tension on the "
                                f"'{axis['name']}' axis. Consider whether "
                                f"both can be pursued simultaneously or if "
                                f"a trade-off decision is needed."
                            ),
                        )
                    )

        # Also detect priority tension: multiple high-priority goals
        # across different domains
        high_priority = [
            g for g in goals
            if self._get_attr(g, "priority", "").lower() in ("high", "critical", "urgent")
        ]
        if len(high_priority) > 2:
            domains = set()
            for g in high_priority:
                d = self._get_attr(g, "domain")
                if d:
                    domains.add(d)
            if len(domains) > 1:
                conflicts.append(
                    Conflict(
                        conflict_type=ConflictType.TENSION,
                        severity=Severity.MEDIUM,
                        goal_ids=tuple(g.id for g in high_priority[:5]),
                        description=(
                            f"{len(high_priority)} high-priority goals across "
                            f"{len(domains)} domains may compete for attention"
                        ),
                        evidence={
                            "tension_type": "priority_overload",
                            "high_priority_count": len(high_priority),
                            "domains": sorted(domains),
                            "goal_titles": [g.title for g in high_priority[:5]],
                        },
                        suggested_resolution=(
                            "Too many high-priority goals across domains. "
                            "Consider prioritizing or staggering."
                        ),
                    )
                )

        return conflicts

    def _get_attr(self, goal: Goal, attr: str, default: Any = None) -> Any:
        return getattr(goal, attr, default)
