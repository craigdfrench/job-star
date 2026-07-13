"""
Competing resource detection strategy.

Identifies goals that need the same limited resource — time, money, attention,
energy, etc. — where the combined demand exceeds available supply.
"""

from __future__ import annotations

from typing import Optional

from ..types import ConflictEvidence, ConflictReport, ConflictSeverity, ConflictType, GoalSnapshot


# Default resource budgets — what a typical person has available.
# These are configurable and should be overridden with actual user data.
DEFAULT_RESOURCE_BUDGETS = {
    "time_hours_week": 80,  # waking hours available for goal-directed activity
    "money_monthly": 5000,  # discretionary budget
    "focus_score": 100,  # abstract attention/focus capacity (0-100 scale)
    "energy_score": 100,  # physical/mental energy capacity (0-100 scale)
    "social_capital": 100,  # relationship/favor budget (abstract)
}


class ResourceConflictDetector:
    """Detects goals competing for the same limited resources."""

    def __init__(self, resource_budgets: Optional[dict[str, float]] = None):
        """
        Args:
            resource_budgets: Override default resource budgets with user-specific values.
        """
        self.budgets = {**DEFAULT_RESOURCE_BUDGETS, **(resource_budgets or {})}

    def detect(
        self,
        a: GoalSnapshot,
        b: GoalSnapshot,
        all_goals: Optional[list[GoalSnapshot]] = None,
    ) -> Optional[ConflictReport]:
        """
        Check if goals a and b compete for the same limited resources.

        Args:
            a, b: Goal snapshots to compare.
            all_goals: All active goals, to check total resource demand
                (not just the pair). If provided, we check if a+b push
                total demand over budget.

        Returns:
            ConflictReport if resource competition detected, None otherwise.
        """
        shared_resources = set(a.resources.keys()) & set(b.resources.keys())
        if not shared_resources:
            return None

        evidence: list[ConflictEvidence] = []
        worst_severity = ConflictSeverity.LOW
        worst_overcommit_ratio = 0.0

        for resource in shared_resources:
            val_a = a.resources[resource]
            val_b = b.resources[resource]
            combined = val_a + val_b
            budget = self.budgets.get(resource, None)

            if budget is None:
                # Unknown budget — still flag shared usage but lower confidence
                evidence.append(
                    ConflictEvidence(
                        source="resource",
                        description=(
                            f"Both goals consume '{resource}': "
                            f"'{a.title}' needs {val_a}, '{b.title}' needs {val_b}"
                        ),
                        confidence=0.50,
                        metadata={
                            "resource": resource,
                            "value_a": val_a,
                            "value_b": val_b,
                            "budget": None,
                        },
                    )
                )
                continue

            # Check pair-level demand
            pair_ratio = combined / budget if budget > 0 else float("inf")

            # Check total demand across all goals if available
            total_demand = combined
            if all_goals:
                for g in all_goals:
                    if g.id in (a.id, b.id):
                        continue
                    total_demand += g.resources.get(resource, 0)

            total_ratio = total_demand / budget if budget > 0 else float("inf")

            if total_ratio > 1.0:
                # Over budget — critical
                confidence = min(0.95, 0.60 + total_ratio * 0.15)
                severity = ConflictSeverity.CRITICAL if total_ratio > 1.5 else ConflictSeverity.HIGH
                evidence.append(
                    ConflictEvidence(
                        source="resource",
                        description=(
                            f"Resource '{resource}' over-committed: "
                            f"total demand {total_demand:.1f} exceeds budget {budget:.1f} "
                            f"({total_ratio:.0%}). This pair contributes {combined:.1f}."
                        ),
                        confidence=confidence,
                        metadata={
                            "resource": resource,
                            "value_a": val_a,
                            "value_b": val_b,
                            "combined": combined,
                            "total_demand": total_demand,
                            "budget": budget,
                            "overcommit_ratio": total_ratio,
                        },
                    )
                )
                worst_severity = max(worst_severity, severity, key=lambda s: list(ConflictSeverity).index(s))
                worst_overcommit_ratio = max(worst_overcommit_ratio, total_ratio)

            elif pair_ratio > 0.5:
                # Pair alone uses >50% of budget — notable even if not over
                confidence = min(0.80, 0.40 + pair_ratio * 0.30)
                severity = ConflictSeverity.MEDIUM if pair_ratio > 0.75 else ConflictSeverity.LOW
                evidence.append(
                    ConflictEvidence(
                        source="resource",
                        description=(
                            f"Resource '{resource}' heavily consumed by this pair: "
                            f"{combined:.1f} of {budget:.1f} budget ({pair_ratio:.0%})"
                        ),
                        confidence=confidence,
                        metadata={
                            "resource": resource,
                            "value_a": val_a,
                            "value_b": val_b,
                            "combined": combined,
                            "budget": budget,
                            "pair_ratio": pair_ratio,
                        },
                    )
                )
                worst_severity = max(worst_severity, severity, key=lambda s: list(ConflictSeverity).index(s))

        if not evidence:
            return None

        shared_list = ", ".join(shared_resources)
        return ConflictReport(
            conflict_type=ConflictType.COMPETING_RESOURCE,
            severity=worst_severity,
            goal_ids=[a.id, b.id],
            title=f"Resource competition: '{a.title}' and '{b.title}' share {shared_list}",
            description=(
                f"Goals '{a.title}' and '{b.title}' both require: {shared_list}. "
                f"Their combined demand may exceed available capacity. "
                f"Consider prioritizing, sequencing, or reducing scope."
            ),
            evidence=evidence,
            recommendation="resource-allocate",
        )
