"""
Competing resource detection for Job-Star conflict engine.

Detects when goals draw from the same finite resource beyond available capacity.
Cross-domain aware: a work goal and a personal goal can compete for the same
time budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Iterable, Optional

from jobstar.conflict.base import (
    Conflict,
    ConflictKind,
    ConflictSeverity,
    Detector,
    GoalRef,
)
from jobstar.model.resource import Resource, ResourceDemand, ResourceKind


class CompetitionMode(Enum):
    """How a goal consumes a resource."""
    EXCLUSIVE = "exclusive"      # fully occupies resource while active
    SHARED = "shared"            # can overlap with other shared consumers
    DEPLETING = "depleting"      # consumes a stock that must be replenished


@dataclass(frozen=True)
class ResourceCompetition:
    """A single goal's draw on a resource within a window."""
    goal: GoalRef
    resource: Resource
    demand: ResourceDemand
    mode: CompetitionMode
    window_start: datetime
    window_end: datetime

    @property
    def overlaps_exclusively(self) -> bool:
        return self.mode == CompetitionMode.EXCLUSIVE


@dataclass
class ResourcePool:
    """
    A finite pool of a resource available over a time window.
    capacity is the total available units in [window_start, window_end].
    """
    resource: Resource
    capacity: float
    window_start: datetime
    window_end: datetime
    replenishable: bool = True
    replenish_rate: float = 0.0  # units per day, if replenishable

    def available_in(self, start: datetime, end: datetime) -> float:
        """Capacity available within a sub-window, accounting for replenishment."""
        if start < self.window_start:
            start = self.window_start
        if end > self.window_end:
            end = self.window_end
        if end <= start:
            return 0.0
        if self.replenishable and self.replenish_rate > 0:
            days = (end - start).total_seconds() / 86400.0
            # capacity is a rate-limited budget, not a fixed stock
            return min(self.capacity, self.replenish_rate * days)
        # fixed stock: prorate by fraction of window
        total_days = max(
            (self.window_end - self.window_start).total_seconds() / 86400.0, 1e-9
        )
        frac = (end - start).total_seconds() / 86400.0 / total_days
        return self.capacity * frac


@dataclass
class CompetitionCluster:
    """A set of goals competing for one resource pool."""
    pool: ResourcePool
    competitions: list[ResourceCompetition] = field(default_factory=list)

    def total_demand(self) -> float:
        return sum(c.demand.amount for c in self.competitions)

    def exclusive_demand_in(self, start: datetime, end: datetime) -> float:
        """Sum of exclusive demands whose windows intersect [start, end]."""
        total = 0.0
        for c in self.competitions:
            if c.mode != CompetitionMode.EXCLUSIVE:
                continue
            # overlap of [c.window_start, c.window_end] with [start, end]
            ov_start = max(c.window_start, start)
            ov_end = min(c.window_end, end)
            if ov_end <= ov_start:
                continue
            full_days = max(
                (c.window_end - c.window_start).total_seconds() / 86400.0, 1e-9
            )
            ov_days = (ov_end - ov_start).total_seconds() / 86400.0
            total += c.demand.amount * (ov_days / full_days)
        return total

    def utilization(self) -> float:
        avail = self.pool.available_in(self.pool.window_start, self.pool.window_end)
        if avail <= 0:
            return float("inf") if self.total_demand() > 0 else 0.0
        return self.total_demand() / avail


def _severity_from_utilization(u: float) -> ConflictSeverity:
    if u >= 1.5:
        return ConflictSeverity.CRITICAL
    if u >= 1.0:
        return ConflictSeverity.HIGH
    if u >= 0.8:
        return ConflictSeverity.MEDIUM
    if u >= 0.6:
        return ConflictSeverity.LOW
    return ConflictSeverity.NEGLIGIBLE


@dataclass
class CompetingResourceDetector(Detector):
    """
    Detects goals competing for the same resource pool beyond capacity.

    Inputs: goals with declared resource demands, and known resource pools.
    Output: Conflict objects of kind COMPETING_RESOURCE.
    """

    pools: list[ResourcePool] = field(default_factory=list)
    # threshold below which we don't emit a conflict
    report_threshold: ConflictSeverity = ConflictSeverity.LOW

    def detect(self, goals: Iterable[GoalRef]) -> list[Conflict]:
        competitions_by_pool: dict[int, CompetitionCluster] = {}

        for goal in goals:
            for demand in goal.resource_demands():
                pool = self._find_pool(demand.resource)
                if pool is None:
                    # No declared pool: infer a default from resource kind
                    pool = self._infer_default_pool(demand.resource, goal)
                    if pool is None:
                        continue
                comp = ResourceCompetition(
                    goal=goal,
                    resource=demand.resource,
                    demand=demand,
                    mode=self._mode_for(demand, goal),
                    window_start=goal.effective_start(),
                    window_end=goal.effective_deadline(),
                )
                cluster = competitions_by_pool.setdefault(
                    id(pool), CompetitionCluster(pool=pool)
                )
                cluster.competitions.append(comp)

        conflicts: list[Conflict] = []
        for cluster in competitions_by_pool.values():
            if len(cluster.competitions) < 2:
                continue
            util = cluster.utilization()
            severity = _severity_from_utilization(util)
            if severity.value < self.report_threshold.value:
                continue
            conflicts.append(self._build_conflict(cluster, util, severity))
        return conflicts

    def _find_pool(self, resource: Resource) -> Optional[ResourcePool]:
        for p in self.pools:
            if p.resource == resource:
                return p
        return None

    def _infer_default_pool(
        self, resource: Resource, goal: GoalRef
    ) -> Optional[ResourcePool]:
        """Fallback pools for common resources when none declared."""
        if resource.kind == ResourceKind.TIME:
            # 16 waking hours/day over the goal's active window
            start = goal.effective_start()
            end = goal.effective_deadline()
            days = max((end - start).total_seconds() / 86400.0, 1.0)
            return ResourcePool(
                resource=resource,
                capacity=16.0 * days,
                window_start=start,
                window_end=end,
                replenishable=True,
                replenish_rate=16.0,
            )
        if resource.kind == ResourceKind.ATTENTION:
            return ResourcePool(
                resource=resource,
                capacity=4.0,  # deep-focus hours/day
                window_start=goal.effective_start(),
                window_end=goal.effective_deadline(),
                replenishable=True,
                replenish_rate=4.0,
            )
        return None

    @staticmethod
    def _mode_for(demand: ResourceDemand, goal: GoalRef) -> CompetitionMode:
        if demand.resource.kind in (ResourceKind.TIME, ResourceKind.ATTENTION):
            return CompetitionMode.EXCLUSIVE
        if demand.resource.kind in (ResourceKind.MONEY, ResourceKind.ENERGY):
            return CompetitionMode.DEPLETING
        return CompetitionMode.SHARED

    def _build_conflict(
        self, cluster: CompetitionCluster, util: float, severity: ConflictSeverity
    ) -> Conflict:
        goal_ids = sorted({c.goal.id for c in cluster.competitions})
        demand_total = cluster.total_demand()
        avail = cluster.pool.available_in(
            cluster.pool.window_start, cluster.pool.window_end
        )
        return Conflict(
            kind=ConflictKind.COMPETING_RESOURCE,
            severity=severity,
            goal_ids=goal_ids,
            title=f"Resource competition: {cluster.resource.name}",
            description=(
                f"{len(goal_ids)} goals demand {demand_total:.1f} "
                f"{cluster.resource.unit} of '{cluster.resource.name}' "
                f"against available {avail:.1f} "
                f"(utilization {util:.0%}). "
                f"Domains: {', '.join(sorted({c.goal.domain for c in cluster.competitions}))}."
            ),
            evidence=[
                {
                    "resource": cluster.resource.name,
                    "resource_kind": cluster.resource.kind.value,
                    "capacity": cluster.pool.capacity,
                    "replenishable": cluster.pool.replenishable,
                    "replenish_rate": cluster.pool.replenish_rate,
                    "total_demand": demand_total,
                    "available": avail,
                    "utilization": util,
                    "goals": [
                        {
                            "id": c.goal.id,
                            "domain": c.goal.domain,
                            "demand": c.demand.amount,
                            "mode": c.mode.value,
                            "window": [
                                c.window_start.isoformat(),
                                c.window_end.isoformat(),
                            ],
                        }
                        for c in sorted(
                            cluster.competitions,
                            key=lambda x: (x.window_start, x.goal.id),
                        )
                    ],
                }
            ],
            suggested_resolutions=self._suggest(cluster, util),
        )

    @staticmethod
    def _suggest(cluster: CompetitionCluster, util: float) -> list[str]:
        suggestions: list[str] = []
        if util >= 1.0:
            suggestions.append(
                "Reduce scope or defer one or more goals to lower total demand "
                "below available capacity."
            )
        # identify the heaviest consumer
        heaviest = max(cluster.competitions, key=lambda c: c.demand.amount)
        suggestions.append(
            f"Consider reducing demand from goal '{heaviest.goal.id}' "
            f"({heaviest.demand.amount:.1f} {cluster.resource.unit}), "
            f"the largest consumer."
        )
        if cluster.pool.replenishable and cluster.pool.replenish_rate > 0:
            suggestions.append(
                f"Resource is replenishable at "
                f"{cluster.pool.replenish_rate:.1f} {cluster.resource.unit}/day; "
                f"stagger goals across time to stay within rate."
            )
        cross_domain = len({c.goal.domain for c in cluster.competitions}) > 1
        if cross_domain:
            suggestions.append(
                "Competition spans multiple domains; negotiate a domain-priority "
                "order so lower-priority domain goals yield first."
            )
        return suggestions
