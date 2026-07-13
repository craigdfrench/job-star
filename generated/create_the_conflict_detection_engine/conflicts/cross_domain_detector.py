"""
Cross-domain conflict detector.

Analyzes goals across different life domains to detect:
1. Resource competition — goals in different domains competing for the same finite resource
2. Domain tension — goals in domains that have known structural tension
3. Alignment opportunities — goals in aligned domains that could support each other
4. Resource depletion — total resource demand across all domains exceeding capacity
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple
from enum import Enum

from .domains import (
    Domain,
    DomainProfile,
    DomainRegistry,
    SHARED_RESOURCES,
)


class ConflictType(str, Enum):
    RESOURCE_COMPETITION = "resource_competition"
    DOMAIN_TENSION = "domain_tension"
    RESOURCE_DEPLETION = "resource_depletion"
    SCHEDULE_COLLISION = "schedule_collision"


class Severity(str, Enum):
    INFO = "info"          # alignment or minor overlap
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class GoalContext:
    """
    Lightweight representation of a goal for cross-domain analysis.
    The full goal object lives elsewhere; this is the projection needed here.
    """
    goal_id: str
    title: str
    domains: List[Domain]                          # can span multiple domains
    priority: float = 0.5                          # 0.0-1.0, how important to the user
    intensity: float = 0.5                         # 0.0-1.0, how demanding this goal is
    active: bool = True
    estimated_hours_per_week: Optional[float] = None
    estimated_cost: Optional[float] = None         # monetary cost if known
    time_window: Optional[str] = None              # e.g., "weekday_mornings", "weekends", "evenings"
    metadata: Dict = field(default_factory=dict)


@dataclass
class CrossDomainConflict:
    """A detected conflict or tension between goals across domains."""
    conflict_type: ConflictType
    severity: Severity
    goal_ids: List[str]                            # goals involved
    domains: List[Domain]                          # domains involved
    resource: Optional[str]                        # which shared resource (if applicable)
    description: str
    detail: str
    suggestions: List[str] = field(default_factory=list)
    confidence: float = 1.0                        # 0.0-1.0


class CrossDomainDetector:
    """
    Detects conflicts between goals that span different life domains.
    """

    # Resource capacity thresholds (fraction of total capacity, 0.0-1.0)
    # When aggregate demand across all goals exceeds these, we flag depletion.
    RESOURCE_CAPACITY = {
        "time_daily": 1.0,         # 24 hours normalized
        "time_weekly": 1.0,        # 168 hours normalized
        "energy_physical": 1.0,
        "energy_mental": 1.0,
        "energy_emotional": 1.0,
        "money": None,             # no universal cap — handled differently
        "attention": 1.0,
        "willpower": 1.0,
        "social_capital": 1.0,
        "space": 1.0,
    }

    # Thresholds for flagging resource competition between two goals
    RESOURCE_COMPETITION_THRESHOLD = 0.15   # combined demand fraction above this = flag

    def __init__(self, registry: Optional[DomainRegistry] = None):
        self.registry = registry or DomainRegistry()

    # --- Public API ---

    def analyze(self, goals: List[GoalContext]) -> List[CrossDomainConflict]:
        """
        Run full cross-domain analysis on a set of goals.
        Returns all detected conflicts and notable alignments.
        """
        conflicts: List[CrossDomainConflict] = []

        active_goals = [g for g in goals if g.active]
        if len(active_goals) < 1:
            return conflicts

        # Pairwise analysis
        for i, goal_a in enumerate(active_goals):
            for goal_b in active_goals[i + 1:]:
                conflicts.extend(self._check_pair(goal_a, goal_b))

        # Aggregate resource depletion
        conflicts.extend(self._check_resource_depletion(active_goals))

        return conflicts

    def analyze_single(self, goal: GoalContext, existing_goals: List[GoalContext]) -> List[CrossDomainConflict]:
        """
        Analyze a single new/updated goal against existing goals.
        Useful for real-time feedback when a user creates or edits a goal.
        """
        conflicts: List[CrossDomainConflict] = []
        active_existing = [g for g in existing_goals if g.active and g.goal_id != goal.goal_id]

        for existing in active_existing:
            conflicts.extend(self._check_pair(goal, existing))

        # Check depletion including the new goal
        all_active = active_existing + [goal] if goal.active else active_existing
        conflicts.extend(self._check_resource_depletion(all_active))

        return conflicts

    # --- Internal: Pairwise checks ---

    def _check_pair(self, goal_a: GoalContext, goal_b: GoalContext) -> List[CrossDomainConflict]:
        results: List[CrossDomainConflict] = []

        # Get all domain pairs (goals can span multiple domains)
        domain_pairs = self._get_domain_pairs(goal_a.domains, goal_b.domains)

        for domain_a, domain_b in domain_pairs:
            if domain_a == domain_b:
                continue  # same-domain conflicts handled by other detectors

            # Check resource competition
            resource_conflict = self._check_resource_competition(goal_a, goal_b, domain_a, domain_b)
            if resource_conflict:
                results.append(resource_conflict)

            # Check domain tension
            tension_conflict = self._check_domain_tension(goal_a, goal_b, domain_a, domain_b)
            if tension_conflict:
                results.append(tension_conflict)

        # Check schedule collision (time_window overlap) regardless of domain
        schedule_conflict = self._check_schedule_collision(goal_a, goal_b)
        if schedule_conflict:
            results.append(schedule_conflict)

        return results

    def _get_domain_pairs(self, domains_a: List[Domain], domains_b: List[Domain]) -> List[Tuple[Domain, Domain]]:
        """Generate all domain pairs between two goals."""
        pairs = []
        for a in domains_a:
            for b in domains_b:
                pairs.append((a, b))
        return pairs

    def _check_resource_competition(
        self, goal_a: GoalContext, goal_b: GoalContext,
        domain_a: Domain, domain_b: Domain
    ) -> Optional[CrossDomainConflict]:
        """
        Detect when two goals in different domains compete for the same shared resource.
        """
        shared = self.registry.get_shared_resources(domain_a, domain_b)
        if not shared:
            return None

        profile_a = self.registry.get(domain_a)
        profile_b = self.registry.get(domain_b)

        # Calculate combined demand for each shared resource
        competing_resources = []
        max_combined = 0.0
        worst_resource = None

        for resource in shared:
            demand_a = profile_a.resource_consumption.get(resource, 0) * goal_a.intensity
            demand_b = profile_b.resource_consumption.get(resource, 0) * goal_b.intensity
            combined = demand_a + demand_b

            if combined > self.RESOURCE_COMPETITION_THRESHOLD:
                competing_resources.append((resource, demand_a, demand_b, combined))
                if combined > max_combined:
                    max_combined = combined
                    worst_resource = resource

        if not competing_resources:
            return None

        # Determine severity
        severity = self._severity_from_demand(max_combined)

        # Priority amplification: if both goals are high priority, conflict is worse
        priority_product = goal_a.priority * goal_b.priority
        if priority_product > 0.5 and severity == Severity.LOW:
            severity = Severity.MEDIUM
        elif priority_product > 0.7 and severity in (Severity.LOW, Severity.MEDIUM):
            severity = Severity.HIGH

        resource_list = ", ".join(r[0] for r in competing_resources)
        suggestions = self._resource_suggestions(worst_resource, domain_a, domain_b)

        return CrossDomainConflict(
            conflict_type=ConflictType.RESOURCE_COMPETITION,
            severity=severity,
            goal_ids=[goal_a.goal_id, goal_b.goal_id],
            domains=[domain_a, domain_b],
            resource=worst_resource,
            description=f"'{goal_a.title}' ({domain_a.value}) and '{goal_b.title}' ({domain_b.value}) compete for {resource_list}",
            detail=(
                f"Combined demand on {worst_resource}: {max_combined:.0%} of capacity. "
                f"'{goal_a.title}' requires ~{competing_resources[0][1]:.0%}, "
                f"'{goal_b.title}' requires ~{competing_resources[0][2]:.0%}. "
                f"Both goals are in domains that draw from the same finite pool."
            ),
            suggestions=suggestions,
            confidence=0.7,
        )

    def _check_domain_tension(
        self, goal_a: GoalContext, goal_b: GoalContext,
        domain_a: Domain, domain_b: Domain
    ) -> Optional[CrossDomainConflict]:
        """
        Detect when goals are in domains with known structural tension,
        even if specific resource overlap isn't extreme.
        """
        tensions_a = self.registry.get_tensions(domain_a)
        if domain_b not in tensions_a:
            return None

        # Tension exists. Severity depends on intensity of both goals.
        combined_intensity = (goal_a.intensity + goal_b.intensity) / 2

        if combined_intensity > 0.7:
            severity = Severity.HIGH
        elif combined_intensity > 0.5:
            severity = Severity.MEDIUM
        else:
            severity = Severity.LOW

        # Check if there's alignment that could mitigate
        alignments_a = self.registry.get_alignments(domain_a)
        has_mitigating_alignment = domain_b in alignments_a  # unlikely if tension exists, but check

        profile_a = self.registry.get(domain_a)
        profile_b = self.registry.get(domain_b)

        suggestions = [
            f"Consider sequencing: pursue {domain_a.value} goals and {domain_b.value} goals in different time blocks.",
            "Identify which goal has higher current priority and temporarily reduce intensity on the other.",
            "Look for creative integration — can aspects of both goals be combined?",
        ]

        if profile_a.time_bound and profile_b.time_bound:
            suggestions.append("Both domains are time-bound. Explicit scheduling boundaries are essential.")

        return CrossDomainConflict(
            conflict_type=ConflictType.DOMAIN_TENSION,
            severity=severity,
            goal_ids=[goal_a.goal_id, goal_b.goal_id],
            domains=[domain_a, domain_b],
            resource=None,
            description=f"Structural tension between {domain_a.value} and {domain_b.value} domains",
            detail=(
                f"'{goal_a.title}' ({domain_a.value}) and '{{goal_b.title}}' ({domain_b.value}) "
                f"exist in domains with known tension. {profile_a.display_name} and "
                f"{profile_b.display_name} often pull in opposite directions. "
                f"Combined intensity: {combined_intensity:.0%}."
            ).replace("'{goal_b.title}'", f"'{goal_b.title}'"),  # fix f-string nesting
            suggestions=suggestions,
            confidence=0.6,
        )

    def _check_schedule_collision(self, goal_a: GoalContext, goal_b: GoalContext) -> Optional[CrossDomainConflict]:
        """
        Detect when two goals have overlapping time windows.
        This is domain-agnostic but most relevant for cross-domain goals
        (same-domain scheduling is usually handled by project planning).
        """
        if not goal_a.time_window or not goal_b.time_window:
            return None
        if goal_a.time_window != goal_b.time_window:
            return None

        # Same time window — check if domains differ
        if set(goal_a.domains) & set(goal_b.domains):
            return None  # same domain, let other detectors handle

        # Cross-domain schedule collision
        severity = Severity.HIGH if (goal_a.priority > 0.6 and goal_b.priority > 0.6) else Severity.MEDIUM

        return CrossDomainConflict(
            conflict_type=ConflictType.SCHEDULE_COLLISION,
            severity=severity,
            goal_ids=[goal_a.goal_id, goal_b.goal_id],
            domains=list(set(goal_a.domains + goal_b.domains)),
            resource="time_daily",
            description=f"Schedule collision: both goals require {goal_a.time_window}",
            detail=(
                f"'{goal_a.title}' and '{goal_b.title}' both need the same time window "
                f"({goal_a.time_window}). They are in different domains, making "
                f"it hard to combine them into a single activity."
            ),
            suggestions=[
                "Move one goal to a different time window.",
                "Alternate days/weeks for each goal.",
                "Reduce the scope of one goal to fit a smaller time slot.",
            ],
            confidence=0.85,
        )

    # --- Internal: Aggregate checks ---

    def _check_resource_depletion(self, goals: List[GoalContext]) -> List[CrossDomainConflict]:
        """
        Check if total resource demand across ALL goals exceeds capacity.
        This catches systemic overload that pairwise checks might miss.
        """
        conflicts: List[CrossDomainConflict] = []

        # Aggregate demand per resource
        total_demand: Dict[str, float] = {r: 0.0 for r in SHARED_RESOURCES}

        for goal in goals:
            for domain in goal.domains:
                profile = self.registry.get(domain)
                for resource, base_demand in profile.resource_consumption.items():
                    if resource in total_demand:
                        # Negative consumption = production (e.g., finance produces money)
                        total_demand[resource] += base_demand * goal.intensity

        # Check each resource against capacity
        for resource, demand in total_demand.items():
            capacity = self.RESOURCE_CAPACITY.get(resource)
            if capacity is None:
                continue  # skip resources without universal caps (e.g., money)

            # Net demand after production
            net_demand = max(demand, 0)  # don't count net-positive (production > consumption)

            if net_demand > capacity:
                overload = net_demand - capacity
                severity = self._severity_from_overload(overload, capacity)

                # Find which goals contribute most to this resource
                contributors = []
                for goal in goals:
                    goal_demand = 0
                    for domain in goal.domains:
                        profile = self.registry.get(domain)
                        goal_demand += profile.resource_consumption.get(resource, 0) * goal.intensity
                    if goal_demand > 0:
                        contributors.append((goal, goal_demand))

                contributors.sort(key=lambda x: x[1], reverse=True)
                top_contributors = contributors[:3]

                detail_goals = ", ".join(f"'{g.title}' ({d:.0%})" for g, d in top_contributors)

                conflicts.append(CrossDomainConflict(
                    conflict_type=ConflictType.RESOURCE_DEPLETION,
                    severity=severity,
                    goal_ids=[g.goal_id for g, _ in top_contributors],
                    domains=list(set(d for g in goals for d in g.domains)),
                    resource=resource,
                    description=f"Resource depletion: {resource} demand ({net_demand:.0%}) exceeds capacity ({capacity:.0%})",
                    detail=(
                        f"Total demand on {resource} across all active goals is {net_demand:.0%}, "
                        f"exceeding sustainable capacity by {overload:.0%}. "
                        f"Top contributors: {detail_goals}. "
                        f"This is a systemic issue — no single pair of goals is the problem, "
                        f"but the aggregate load is unsustainable."
                    ),
                    suggestions=self._depletion_suggestions(resource, top_contributors),
                    confidence=0.8,
                ))

        return conflicts

    # --- Helpers ---

    def _severity_from_demand(self, combined_demand: float) -> Severity:
        if combined_demand > 0.8:
            return Severity.CRITICAL
        elif combined_demand > 0.6:
            return Severity.HIGH
        elif combined_demand > 0.4:
            return Severity.MEDIUM
        elif combined_demand > 0.2:
            return Severity.LOW
        else:
            return Severity.INFO

    def _severity_from_overload(self, overload: float, capacity: float) -> Severity:
        ratio = overload / capacity
        if ratio > 0.3:
            return Severity.CRITICAL
        elif ratio > 0.15:
            return Severity.HIGH
        elif ratio > 0.05:
            return Severity.MEDIUM
        else:
            return Severity.LOW

    def _resource_suggestions(self, resource: str, domain_a: Domain, domain_b: Domain) -> List[str]:
        suggestions = []

        if resource in ("time_daily", "time_weekly"):
            suggestions.append("Time-box each goal into non-overlapping slots.")
            suggestions.append("Consider which goal is time-sensitive right now and defer the other.")
        elif resource in ("energy_mental", "attention"):
            suggestions.append("Schedule cognitively demanding tasks at different times of day.")
            suggestions.append("Use the less demanding goal as a warm-up or cooldown for the other.")
        elif resource in ("energy_physical",):
            suggestions.append("Ensure adequate rest between physically demanding activities.")
            suggestions.append("Check if one goal can be made less physically intensive.")
        elif resource == "energy_emotional":
            suggestions.append("Build in emotional recovery time between these goals.")
            suggestions.append("Consider whether one goal can be approached more mechanically to reduce emotional load.")
        elif resource == "willpower":
            suggestions.append("Reduce decision fatigue by automating one of these goals (habits, systems).")
            suggestions.append("Stack these goals at different times when willpower reserves differ.")
        elif resource == "money":
            suggestions.append("Review the combined budget impact of both goals.")
            suggestions.append("Phase the goals: pursue one now, save for the other later.")
        elif resource == "social_capital":
            suggestions.append("Avoid asking the same people for help with both goals simultaneously.")
            suggestions.append("Diversify the social network each goal draws on.")

        suggestions.append(f"Explore whether {domain_a.value} and {domain_b.value} goals can be integrated or sequenced.")
        return suggestions

    def _depletion_suggestions(self, resource: str, contributors: List[Tuple[GoalContext, float]]) -> List[str]:
        suggestions = [
            f"Reduce intensity or scope of the highest-consuming goals.",
            "Consider pausing or deprioritizing one goal temporarily.",
            "Look for goals that produce this resource and strengthen them.",
        ]

        if resource in ("time_daily", "time_weekly"):
            suggestions.append("Audit time spending — are there lower-priority activities that can be cut?")
            suggestions.append("Consider delegation, automation, or elimination of non-goal tasks.")
        elif resource in ("energy_mental", "energy_emotional", "willpower"):
            suggestions.append("Prioritize sleep and rest — these resources replenish with recovery.")
            suggestions.append("Batch similar tasks to reduce switching cost.")

        if contributors:
            top_goal = contributors[0][0]
            suggestions.append(f"'{top_goal.title}' is the largest consumer — evaluate if its intensity can be reduced.")

        return suggestions
