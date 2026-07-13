"""
Cross-domain awareness for the conflict detection engine.

Detects conflicts between goals in different domains (meta, work, personal,
health, relationships, learning, creative, financial, etc.). Cross-domain
conflicts are typically resource-based, temporal, or tension-based rather
than direct logical contradictions.

This module is part of Job-Star's conflict detection engine and integrates
with the broader conflict detection system.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


class Domain(str, Enum):
    """Canonical domains recognized by Job-Star.

    Domains represent broad life areas. Goals are tagged with one or more
    domains. The cross-domain engine uses domain relationships to detect
    conflicts that wouldn't be visible within a single domain.
    """

    META = "meta"              # Goals about the system itself, self-improvement, process
    WORK = "work"              # Career, job, professional projects
    PERSONAL = "personal"      # General personal life, errands, life admin
    HEALTH = "health"          # Physical health, fitness, medical
    MENTAL = "mental"          # Mental health, therapy, mindfulness
    RELATIONSHIPS = "relationships"  # Family, friends, romantic, social
    LEARNING = "learning"      # Education, skills, courses, reading
    CREATIVE = "creative"      # Art, hobbies, side projects, expression
    FINANCIAL = "financial"    # Money, savings, investments, debt
    COMMUNITY = "community"    # Volunteering, civic, social impact
    SPIRITUAL = "spiritual"    # Meaning, practice, faith, philosophy


# Domain relationship matrix: which domains tend to compete or reinforce.
# Values: "compete", "reinforce", "neutral", "tension"
# This is a heuristic baseline; the AI layer can override or refine.
_DOMAIN_RELATIONSHIPS: dict[tuple[Domain, Domain], str] = {
    (Domain.WORK, Domain.PERSONAL): "compete",
    (Domain.WORK, Domain.HEALTH): "compete",
    (Domain.WORK, Domain.RELATIONSHIPS): "compete",
    (Domain.WORK, Domain.LEARNING): "reinforce",
    (Domain.WORK, Domain.FINANCIAL): "reinforce",
    (Domain.WORK, Domain.CREATIVE): "tension",
    (Domain.WORK, Domain.META): "reinforce",
    (Domain.HEALTH, Domain.MENTAL): "reinforce",
    (Domain.HEALTH, Domain.PERSONAL): "reinforce",
    (Domain.HEALTH, Domain.RELATIONSHIPS): "reinforce",
    (Domain.HEALTH, Domain.WORK): "compete",
    (Domain.MENTAL, Domain.WORK): "compete",
    (Domain.MENTAL, Domain.PERSONAL): "reinforce",
    (Domain.RELATIONSHIPS, Domain.PERSONAL): "reinforce",
    (Domain.RELATIONSHIPS, Domain.WORK): "compete",
    (Domain.RELATIONSHIPS, Domain.CREATIVE): "reinforce",
    (Domain.RELATIONSHIPS, Domain.COMMUNITY): "reinforce",
    (Domain.LEARNING, Domain.CREATIVE): "reinforce",
    (Domain.LEARNING, Domain.WORK): "reinforce",
    (Domain.LEARNING, Domain.FINANCIAL): "reinforce",
    (Domain.LEARNING, Domain.PERSONAL): "tension",
    (Domain.CREATIVE, Domain.FINANCIAL): "tension",
    (Domain.CREATIVE, Domain.WORK): "tension",
    (Domain.FINANCIAL, Domain.PERSONAL): "tension",
    (Domain.FINANCIAL, Domain.HEALTH): "tension",
    (Domain.COMMUNITY, Domain.WORK): "compete",
    (Domain.COMMUNITY, Domain.PERSONAL): "tension",
    (Domain.SPIRITUAL, Domain.WORK): "tension",
    (Domain.SPIRITUAL, Domain.HEALTH): "reinforce",
    (Domain.SPIRITUAL, Domain.MENTAL): "reinforce",
    (Domain.META, Domain.WORK): "reinforce",
    (Domain.META, Domain.PERSONAL): "reinforce",
    (Domain.META, Domain.HEALTH): "reinforce",
}


def get_domain_relationship(d1: Domain, d2: Domain) -> str:
    """Get the baseline relationship between two domains.

    Returns one of: 'compete', 'reinforce', 'tension', 'neutral'.
    Symmetric — (A, B) and (B, A) return the same value.
    """
    if d1 == d2:
        return "neutral"
    key = (d1, d2)
    if key in _DOMAIN_RELATIONSHIPS:
        return _DOMAIN_RELATIONSHIPS[key]
    reverse = (d2, d1)
    if reverse in _DOMAIN_RELATIONSHIPS:
        return _DOMAIN_RELATIONSHIPS[reverse]
    return "neutral"


# Resource budgets per domain — heuristic weekly allocations that a "balanced"
# life might target. Used to detect over-allocation across domains.
# Values are fractions of total weekly capacity (should sum to ~1.0).
_DEFAULT_DOMAIN_BUDGETS: dict[Domain, float] = {
    Domain.WORK: 0.35,
    Domain.HEALTH: 0.10,
    Domain.MENTAL: 0.05,
    Domain.RELATIONSHIPS: 0.15,
    Domain.PERSONAL: 0.10,
    Domain.LEARNING: 0.08,
    Domain.CREATIVE: 0.05,
    Domain.FINANCIAL: 0.02,
    Domain.COMMUNITY: 0.03,
    Domain.SPIRITUAL: 0.02,
    Domain.META: 0.05,
}


@dataclass
class ResourceDemand:
    """A goal's demand on a particular resource.

    Attributes:
        resource: The resource being consumed (time_hours_week, energy,
                  attention, money_month, etc.)
        amount: The estimated amount demanded.
        unit: Unit of measurement.
        confidence: 0.0–1.0 confidence in the estimate.
        notes: Optional context.
    """

    resource: str
    amount: float
    unit: str
    confidence: float = 0.5
    notes: str = ""


@dataclass
class Goal:
    """Minimal goal representation for cross-domain analysis.

    In the full system, this maps to the Goal entity. This is a lightweight
    view containing only the fields needed for cross-domain conflict detection.
    """

    id: UUID
    title: str
    domains: list[Domain]
    priority: int  # 1 (highest) to 5 (lowest)
    active: bool = True
    start_date: Optional[datetime] = None
    target_date: Optional[datetime] = None
    resource_demands: list[ResourceDemand] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_active_now(self) -> bool:
        """Whether the goal is in its active execution window right now."""
        now = datetime.now()
        if self.start_date and now < self.start_date:
            return False
        if self.target_date and now > self.target_date + timedelta(days=7):
            return False
        return self.active


@dataclass
class CrossDomainConflict:
    """A detected conflict between goals in different domains.

    Attributes:
        id: Unique conflict identifier.
        goal_ids: The goals involved (typically 2, but can be more for
                  resource-pool conflicts).
        conflict_type: One of 'resource_competition', 'temporal_overlap',
                       'priority_tension', 'value_friction', 'spillover_risk',
                       'domain_imbalance'.
        domains: The domains involved.
        severity: 0.0–1.0, how serious the conflict is.
        description: Human-readable explanation.
        evidence: Structured evidence supporting the detection.
        suggested_resolution: Optional suggestion for resolving the conflict.
        detected_at: When the conflict was detected.
        confidence: 0.0–1.0 confidence in the detection.
    """

    id: UUID = field(default_factory=uuid4)
    goal_ids: list[UUID] = field(default_factory=list)
    conflict_type: str = ""
    domains: list[Domain] = field(default_factory=list)
    severity: float = 0.0
    description: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    suggested_resolution: str = ""
    detected_at: datetime = field(default_factory=datetime.now)
    confidence: float = 0.5


class CrossDomainDetector:
    """Detects conflicts between goals in different domains.

    This is the cross-domain awareness layer of the conflict detection engine.
    It runs several detection strategies and aggregates results:

    1. Resource competition — goals across domains competing for the same
       finite resource (time, energy, money).
    2. Temporal overlap — goals in competing domains active in the same window.
    3. Priority tension — multiple high-priority goals across competing domains.
    4. Value friction — goals whose outcomes are philosophically opposed
       across domains (detected via metadata tags or AI analysis).
    5. Spillover risk — goals in one domain likely to negatively impact
       another domain (e.g., high-stress work goal degrading health goals).
    6. Domain imbalance — total resource allocation heavily skewed toward
       one domain at the expense of others.
    """

    def __init__(
        self,
        domain_budgets: Optional[dict[Domain, float]] = None,
        resource_capacity: Optional[dict[str, float]] = None,
    ):
        """Initialize the detector.

        Args:
            domain_budgets: Override default domain resource budgets.
            resource_capacity: Total available capacity per resource.
                               Defaults to reasonable weekly capacities.
        """
        self.domain_budgets = domain_budgets or _DEFAULT_DOMAIN_BUDGETS.copy()
        self.resource_capacity = resource_capacity or {
            "time_hours_week": 112.0,  # 16 waking hours * 7 days
            "energy": 100.0,           # Arbitrary unit, 100 = full capacity
            "attention": 100.0,        # Arbitrary unit
            "money_month": 0.0,        # User-specific, 0 = unknown
        }

    def detect(self, goals: list[Goal]) -> list[CrossDomainConflict]:
        """Run all cross-domain detection strategies on the given goals.

        Args:
            goals: All goals to analyze (across all domains).

        Returns:
            List of detected cross-domain conflicts, sorted by severity.
        """
        active_goals = [g for g in goals if g.is_active_now]
        if len(active_goals) < 2:
            return []

        conflicts: list[CrossDomainConflict] = []

        conflicts.extend(self._detect_resource_competition(active_goals))
        conflicts.extend(self._detect_temporal_overlap(active_goals))
        conflicts.extend(self._detect_priority_tension(active_goals))
        conflicts.extend(self._detect_value_friction(active_goals))
        conflicts.extend(self._detect_spillover_risk(active_goals))
        conflicts.extend(self._detect_domain_imbalance(active_goals))

        # Deduplicate: same pair of goals + same conflict_type = merge
        conflicts = self._deduplicate(conflicts)

        # Sort by severity descending
        conflicts.sort(key=lambda c: c.severity, reverse=True)

        logger.info(
            "Cross-domain detection complete: %d conflicts found from %d active goals",
            len(conflicts),
            len(active_goals),
        )
        return conflicts

    def _detect_resource_competition(
        self, goals: list[Goal]
    ) -> list[CrossDomainConflict]:
        """Detect goals across different domains competing for the same resource.

        For each resource, sum demands across goals. If total exceeds capacity,
        flag the contributing goals (especially those in different domains).
        Also flag pairwise competition when two goals in competing domains
        both demand significant amounts of the same resource.
        """
        conflicts: list[CrossDomainConflict] = []

        # Group demands by resource
        resource_to_goals: dict[str, list[tuple[Goal, ResourceDemand]]] = {}
        for goal in goals:
            for demand in goal.resource_demands:
                resource_to_goals.setdefault(demand.resource, []).append(
                    (goal, demand)
                )

        for resource, goal_demands in resource_to_goals.items():
            if resource not in self.resource_capacity:
                # Unknown resource capacity — skip pool analysis but still
                # do pairwise analysis if it's a known-type resource
                continue

            capacity = self.resource_capacity[resource]
            total_demand = sum(d.amount for _, d in goal_demands)

            if total_demand <= capacity * 0.9:
                continue  # Within budget, no pool-level conflict

            # Over-allocated resource. Identify cross-domain contributors.
            domain_contributions: dict[Domain, float] = {}
            for goal, demand in goal_demands:
                for domain in goal.domains:
                    domain_contributions[domain] = (
                        domain_contributions.get(domain, 0.0) + demand.amount
                    )

            involved_domains = list(domain_contributions.keys())
            if len(involved_domains) < 2:
                continue  # Single-domain over-allocation, not cross-domain

            overage = total_demand - capacity
            severity = min(1.0, overage / capacity)

            conflicts.append(
                CrossDomainConflict(
                    goal_ids=[g.id for g, _ in goal_demands],
                    conflict_type="resource_competition",
                    domains=involved_domains,
                    severity=severity,
                    description=(
                        f"Resource '{resource}' is over-allocated: "
                        f"{total_demand:.1f} {goal_demands[0][1].unit} demanded "
                        f"vs {capacity:.1f} available. "
                        f"Contributing domains: "
                        f"{', '.join(d.value for d in involved_domains)}."
                    ),
                    evidence={
                        "resource": resource,
                        "total_demand": total_demand,
                        "capacity": capacity,
                        "overage": overage,
                        "domain_contributions": {
                            d.value: v for d, v in domain_contributions.items()
                        },
                        "goal_demands": [
                            {
                                "goal_id": str(g.id),
                                "goal_title": g.title,
                                "amount": d.amount,
                                "confidence": d.confidence,
                            }
                            for g, d in goal_demands
                        ],
                    },
                    suggested_resolution=self._suggest_resource_resolution(
                        resource, goal_demands, capacity
                    ),
                    confidence=0.7,
                )
            )

        # Pairwise: goals in competing domains with overlapping resource demands
        for resource, goal_demands in resource_to_goals.items():
            for i, (g1, d1) in enumerate(goal_demands):
                for g2, d2 in goal_demands[i + 1 :]:
                    if g1.id == g2.id:
                        continue
                    # Check if they share any competing domains
                    competing = False
                    for dom1 in g1.domains:
                        for dom2 in g2.domains:
                            if dom1 != dom2 and get_domain_relationship(
                                dom1, dom2
                            ) == "compete":
                                competing = True
                                break
                        if competing:
                            break
                    if not competing:
                        continue

                    # Both demand meaningful amounts of the same resource
                    combined = d1.amount + d2.amount
                    if resource in self.resource_capacity:
                        ratio = combined / self.resource_capacity[resource]
                    else:
                        ratio = combined / (d1.amount + d2.amount + 1)

                    if ratio < 0.15:
                        continue  # Too small to matter

                    severity = min(0.8, ratio * 0.5)
                    all_domains = list(set(g1.domains + g2.domains))

                    conflicts.append(
                        CrossDomainConflict(
                            goal_ids=[g1.id, g2.id],
                            conflict_type="resource_competition",
                            domains=all_domains,
                            severity=severity,
                            description=(
                                f"'{g1.title}' ({', '.join(d.value for d in g1.domains)}) "
                                f"and '{g2.title}' ({', '.join(d.value for d in g2.domains)}) "
                                f"compete for {resource}: "
                                f"{d1.amount:.1f} + {d2.amount:.1f} "
                                f"{d1.unit}."
                            ),
                            evidence={
                                "resource": resource,
                                "goal_1_demand": d1.amount,
                                "goal_2_demand": d2.amount,
                                "combined_ratio": ratio,
                                "domain_relationship": "compete",
                            },
                            suggested_resolution=(
                                f"Consider staggering these goals temporally, "
                                f"or reducing the {resource} demand of one. "
                                f"The {', '.join(d.value for d in all_domains)} "
                                f"domains are in inherent tension for this resource."
                            ),
                            confidence=0.6,
                        )
                    )

        return conflicts

    def _detect_temporal_overlap(
        self, goals: list[Goal]
    ) -> list[CrossDomainConflict]:
        """Detect goals in competing domains with overlapping active windows.

        Two goals in competing domains (e.g., WORK and HEALTH) that are both
        active in the same time window create tension even if their resource
        demands aren't explicitly tracked.
        """
        conflicts: list[CrossDomainConflict] = []

        for i, g1 in enumerate(goals):
            for g2 in goals[i + 1 :]:
                # Must be in different domains
                shared = set(g1.domains) & set(g2.domains)
                if shared and len(g1.domains) == 1 and len(g2.domains) == 1:
                    continue  # Same single domain, not cross-domain

                # Check domain relationship
                competing_domains = []
                for d1 in g1.domains:
                    for d2 in g2.domains:
                        rel = get_domain_relationship(d1, d2)
                        if rel in ("compete", "tension"):
                            competing_domains.append((d1, d2, rel))

                if not competing_domains:
                    continue

                # Check temporal overlap
                overlap = self._temporal_overlap(g1, g2)
                if overlap is None or overlap <= timedelta(days=0):
                    continue

                overlap_days = overlap.days
                if overlap_days < 7:
                    continue  # Less than a week overlap — minor

                # Severity scales with overlap duration and domain tension
                worst_rel = max(
                    competing_domains,
                    key=lambda x: 1 if x[2] == "compete" else 0.5,
                )
                base_severity = 0.6 if worst_rel[2] == "compete" else 0.3
                duration_factor = min(1.0, overlap_days / 90.0)
                severity = base_severity * (0.5 + 0.5 * duration_factor)

                all_domains = list(set(g1.domains + g2.domains))

                conflicts.append(
                    CrossDomainConflict(
                        goal_ids=[g1.id, g2.id],
                        conflict_type="temporal_overlap",
                        domains=all_domains,
                        severity=severity,
                        description=(
                            f"'{g1.title}' and '{g2.title}' are both active "
                            f"for {overlap_days} overlapping days. "
                            f"Their domains ({worst_rel[0].value}/{worst_rel[1].value}) "
                            f"are in {worst_rel[2]}."
                        ),
                        evidence={
                            "overlap_days": overlap_days,
                            "domain_relationship": worst_rel[2],
                            "goal_1_window": {
                                "start": g1.start_date.isoformat() if g1.start_date else None,
                                "target": g1.target_date.isoformat() if g1.target_date else None,
                            },
                            "goal_2_window": {
                                "start": g2.start_date.isoformat() if g2.start_date else None,
                                "target": g2.target_date.isoformat() if g2.target_date else None,
                            },
                        },
                        suggested_resolution=(
                            f"Consider adjusting the timeline of one goal to "
                            f"reduce the {overlap_days}-day overlap, or explicitly "
                            f"prioritize one over the other during this period."
                        ),
                        confidence=0.65,
                    )
                )

        return conflicts

    def _detect_priority_tension(
        self, goals: list[Goal]
    ) -> list[CrossDomainConflict]:
        """Detect multiple high-priority goals across competing domains.

        When two priority-1 goals exist in competing domains, the user faces
        an implicit decision point every day. This creates chronic tension.
        """
        conflicts: list[CrossDomainConflict] = []

        high_priority = [g for g in goals if g.priority <= 2]
        if len(high_priority) < 2:
            return conflicts

        for i, g1 in enumerate(high_priority):
            for g2 in high_priority[i + 1 :]:
                competing = False
                tension_domains = []
                for d1 in g1.domains:
                    for d2 in g2.domains:
                        rel = get_domain_relationship(d1, d2)
                        if rel == "compete":
                            competing = True
                            tension_domains.append((d1, d2))
                        elif rel == "tension":
                            tension_domains.append((d1, d2))

                if not tension_domains:
                    continue

                # Higher priority = higher severity
                priority_sum = g1.priority + g2.priority
                severity = max(0.0, 0.9 - (priority_sum - 2) * 0.15)
                if not competing:
                    severity *= 0.6  # Tension is less severe than compete

                all_domains = list(set(g1.domains + g2.domains))

                conflicts.append(
                    CrossDomainConflict(
                        goal_ids=[g1.id, g2.id],
                        conflict_type="priority_tension",
                        domains=all_domains,
                        severity=severity,
                        description=(
                            f"Two high-priority goals in tension: "
                            f"'{g1.title}' (P{g1.priority}, "
                            f"{', '.join(d.value for d in g1.domains)}) vs "
                            f"'{g2.title}' (P{g2.priority}, "
                            f"{', '.join(d.value for d in g2.domains)}). "
                            f"Both demand primary attention."
                        ),
                        evidence={
                            "goal_1_priority": g1.priority,
                            "goal_2_priority": g2.priority,
                            "tension_domains": [
                                [a.value, b.value] for a, b in tension_domains
                            ],
                            "is_competing": competing,
                        },
                        suggested_resolution=(
                            "Explicitly rank these two goals against each other. "
                            "Consider making one P3 temporarily, or defining "
                            "which takes precedence when they conflict in daily "
                            "planning."
                        ),
                        confidence=0.7,
                    )
                )

        return conflicts

    def _detect_value_friction(
        self, goals: list[Goal]
    ) -> list[CrossDomainConflict]:
        """Detect goals whose stated outcomes are philosophically opposed.

        This uses metadata tags like 'value_friction_tags' that the AI layer
        can populate. Example: a META goal tagged 'reduce_work_hours' vs a
        WORK goal tagged 'increase_work_hours'.

        Without AI-populated tags, this is a no-op. The AI layer is expected
        to enrich goals with friction tags before conflict detection runs.
        """
        conflicts: list[CrossDomainConflict] = []

        for i, g1 in enumerate(goals):
            tags1 = g1.metadata.get("value_friction_tags", [])
            if not tags1:
                continue
            for g2 in goals[i + 1 :]:
                # Must be cross-domain
                if set(g1.domains) == set(g2.domains) and len(g1.domains) == 1:
                    continue

                tags2 = g2.metadata.get("value_friction_tags", [])
                if not tags2:
                    continue

                # Check for opposing tags
                friction_pairs = g1.metadata.get("friction_pairs", [])
                for tag1 in tags1:
                    for tag2 in tags2:
                        if [tag1, tag2] in friction_pairs or [
                            tag2,
                            tag1,
                        ] in friction_pairs:
                            all_domains = list(set(g1.domains + g2.domains))
                            conflicts.append(
                                CrossDomainConflict(
                                    goal_ids=[g1.id, g2.id],
                                    conflict_type="value_friction",
                                    domains=all_domains,
                                    severity=0.8,
                                    description=(
                                        f"Value friction: '{g1.title}' promotes "
                                        f"'{tag1}' while '{g2.title}' promotes "
                                        f"'{tag2}'. These are opposing directions."
                                    ),
                                    evidence={
                                        "tag_1": tag1,
                                        "tag_2": tag2,
                                        "goal_1_domains": [
                                            d.value for d in g1.domains
                                        ],
                                        "goal_2_domains": [
                                            d.value for d in g2.domains
                                        ],
                                    },
                                    suggested_resolution=(
                                        "These goals pull in opposite directions. "
                                        "Decide which value takes precedence, or "
                                        "redefine one goal to align with the other."
                                    ),
                                    confidence=0.85,
                                )
                            )

        return conflicts

    def _detect_spillover_risk(
        self, goals: list[Goal]
    ) -> list[CrossDomainConflict]:
        """Detect goals likely to spill over and harm goals in other domains.

        Example: A high-intensity work goal (tagged 'high_stress') is likely
        to degrade health and relationship goals. This uses metadata tags
        like 'spillover_risk' and 'spillover_targets'.
        """
        conflicts: list[CrossDomainConflict] = []

        for source in goals:
            spillover = source.metadata.get("spillover_risk", {})
            if not spillover:
                continue

            risk_type = spillover.get("type", "")
            risk_level = spillover.get("level", 0.5)
            target_domains = spillover.get("target_domains", [])

            if not target_domains:
                continue

            # Find goals in the target domains
            for target in goals:
                if target.id == source.id:
                    continue

                target_in_risk = any(
                    Domain(td) in target.domains for td in target_domains
                )
                if not target_in_risk:
                    continue

                # Don't flag if same domain
                if set(source.domains) & set(target.domains):
                    continue

                severity = risk_level * 0.7  # Spillover is inherently uncertain
                all_domains = list(set(source.domains + target.domains))

                conflicts.append(
                    CrossDomainConflict(
                        goal_ids=[source.id, target.id],
                        conflict_type="spillover_risk",
                        domains=all_domains,
                        severity=severity,
                        description=(
                            f"'{source.title}' has {risk_type} spillover risk "
                            f"(level {risk_level:.1f}) that may undermine "
                            f"'{target.title}' in the "
                            f"{', '.join(target_domains)} domain(s)."
                        ),
                        evidence={
                            "source_goal": source.title,
                            "target_goal": target.title,
                            "risk_type": risk_type,
                            "risk_level": risk_level,
                            "target_domains": target_domains,
                        },
                        suggested_resolution=(
                            f"Add mitigation to '{source.title}' to reduce "
                            f"{risk_type} spillover, or add resilience measures "
                            f"to '{target.title}' to absorb the impact."
                        ),
                        confidence=0.55,
                    )
                )

        return conflicts

    def _detect_domain_imbalance(
        self, goals: list[Goal]
    ) -> list[CrossDomainConflict]:
        """Detect when resource allocation is heavily skewed across domains.

        If 80% of time/energy goes to WORK while HEALTH gets 0%, that's a
        cross-domain imbalance even without a specific pairwise conflict.
        """
        conflicts: list[CrossDomainConflict] = []

        # Aggregate resource demands by domain
        domain_resource_totals: dict[Domain, dict[str, float]] = {}
        for goal in goals:
            for demand in goal.resource_demands:
                for domain in goal.domains:
                    domain_resource_totals.setdefault(domain, {})
                    domain_resource_totals[domain][demand.resource] = (
                        domain_resource_totals[domain].get(demand.resource, 0.0)
                        + demand.amount
                    )

        # Check imbalance for time and energy (the most universal resources)
        for resource in ["time_hours_week", "energy"]:
            if resource not in self.resource_capacity:
                continue

            capacity = self.resource_capacity[resource]
            total_allocated = sum(
                totals.get(resource, 0)
                for totals in domain_resource_totals.values()
            )
            if total_allocated <= 0:
                continue

            domain_shares = {
                d: totals.get(resource, 0) / total_allocated
                for d, totals in domain_resource_totals.items()
            }

            # Compare against budget
            imbalanced_domains = []
            for domain, share in domain_shares.items():
                budget = self.domain_budgets.get(domain, 0.05)
                if share > budget * 2.0 and share > 0.2:
                    imbalanced_domains.append((domain, share, budget))

            if not imbalanced_domains:
                continue

            # Find under-allocated domains
            under_allocated = []
            for domain in self.domain_budgets:
                share = domain_shares.get(domain, 0.0)
                budget = self.domain_budgets[domain]
                if share < budget * 0.3 and budget > 0.03:
                    under_allocated.append((domain, share, budget))

            if not under_allocated:
                continue

            severity = min(
                0.7,
                max(s / b for _, s, b in imbalanced_domains) - 1.0,
            )

            all_domains = [d for d, _, _ in imbalanced_domains] + [
                d for d, _, _ in under_allocated
            ]

            conflicts.append(
                CrossDomainConflict(
                    goal_ids=[
                        g.id
                        for g in goals
                        if any(d in g.domains for d, _, _ in imbalanced_domains)
                    ],
                    conflict_type="domain_imbalance",
                    domains=list(set(all_domains)),
                    severity=severity,
                    description=(
                        f"Resource '{resource}' is imbalanced across domains. "
                        f"Over-allocated: {', '.join(f'{d.value} ({s:.0%})' for d, s, _ in imbalanced_domains)}. "
                        f"Under-allocated: {', '.join(f'{d.value} ({s:.0%})' for d, s, _ in under_allocated)}."
                    ),
                    evidence={
                        "resource": resource,
                        "total_allocated": total_allocated,
                        "domain_shares": {
                            d.value: s for d, s in domain_shares.items()
                        },
                        "budget_shares": {
                            d.value: b for d, b in self.domain_budgets.items()
                        },
                        "imbalanced": [
                            {"domain": d.value, "share": s, "budget": b}
                            for d, s, b in imbalanced_domains
                        ],
                        "under_allocated": [
                            {"domain": d.value, "share": s, "budget": b}
                            for d, s, b in under_allocated
                        ],
                    },
                    suggested_resolution=(
                        f"Rebalance {resource} allocation. Consider reducing "
                        f"commitments in over-allocated domains and ensuring "
                        f"under-allocated domains receive minimum budget."
                    ),
                    confidence=0.6,
                )
            )

        return conflicts

    def _temporal_overlap(
        self, g1: Goal, g2: Goal
    ) -> Optional[timedelta]:
        """Calculate the temporal overlap between two goals' active windows.

        Returns the overlap duration, or None if either goal lacks date info.
        """
        if not g1.start_date or not g1.target_date:
            return None
        if not g2.start_date or not g2.target_date:
            return None

        start = max(g1.start_date, g2.start_date)
        end = min(g1.target_date, g2.target_date)

        if end > start:
            return end - start
        return timedelta(0)

    def _suggest_resource_resolution(
        self,
        resource: str,
        goal_demands: list[tuple[Goal, ResourceDemand]],
        capacity: float,
    ) -> str:
        """Generate a suggested resolution for resource over-allocation."""
        # Sort by demand descending
        sorted_demands = sorted(goal_demands, key=lambda x: x[1].amount, reverse=True)
        top_goal = sorted_demands[0][0]

        return (
            f"Reduce {resource} demand by {sum(d.amount for _, d in goal_demands) - capacity:.1f} "
            f"units. Consider: (1) reducing scope of '{top_goal.title}' which has "
            f"the highest demand, (2) pausing lower-priority goals, "
            f"(3) staggering goals temporally so not all are active simultaneously."
        )

    def _deduplicate(
        self, conflicts: list[CrossDomainConflict]
    ) -> list[CrossDomainConflict]:
        """Merge duplicate conflicts (same goals + same type)."""
        seen: dict[tuple[str, frozenset], CrossDomainConflict] = {}

        for conflict in conflicts:
            key = (
                conflict.conflict_type,
                frozenset(conflict.goal_ids),
            )
            if key in seen:
                existing = seen[key]
                # Keep the higher-severity one, merge evidence
                if conflict.severity > existing.severity:
                    merged_evidence = {**existing.evidence, **conflict.evidence}
                    conflict.evidence = merged_evidence
                    seen[key] = conflict
                else:
                    existing.evidence.update(conflict.evidence)
            else:
                seen[key] = conflict

        return list(seen.values())
