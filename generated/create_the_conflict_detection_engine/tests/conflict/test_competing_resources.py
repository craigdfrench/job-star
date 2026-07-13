"""Tests for competing resource detection."""

from datetime import datetime, timedelta

from jobstar.conflict import (
    CompetingResourceDetector,
    ConflictKind,
    ConflictSeverity,
    ResourcePool,
)
from jobstar.model.resource import Resource, ResourceDemand, ResourceKind


class FakeGoal:
    def __init__(
        self,
        gid: str,
        domain: str,
        start: datetime,
        deadline: datetime,
        demands: list[ResourceDemand],
    ):
        self.id = gid
        self.domain = domain
        self._start = start
        self._deadline = deadline
        self._demands = demands

    def effective_start(self) -> datetime:
        return self._start

    def effective_deadline(self) -> datetime:
        return self._deadline

    def resource_demands(self) -> list[ResourceDemand]:
        return self._demands


TIME = Resource("focus-hours", ResourceKind.TIME, "hours")
MONEY = Resource("budget", ResourceKind.MONEY, "USD")


def test_no_conflict_when_capacity_sufficient():
    now = datetime(2025, 1, 1)
    week = now + timedelta(days=7)
    goals = [
        FakeGoal("g1", "work", now, week, [ResourceDemand(TIME, 10.0)]),
        FakeGoal("g2", "personal", now, week, [ResourceDemand(TIME, 10.0)]),
    ]
    pool = ResourcePool(TIME, capacity=100.0, window_start=now, window_end=week)
    detector = CompetingResourceDetector(pools=[pool])
    conflicts = detector.detect(goals)
    # 20 hours demanded vs 100 available -> negligible
    assert all(c.kind != ConflictKind.COMPETING_RESOURCE for c in conflicts)


def test_conflict_when_demand_exceeds_capacity():
    now = datetime(2025, 1, 1)
    week = now + timedelta(days=7)
    goals = [
        FakeGoal("g1", "work", now, week, [ResourceDemand(TIME, 60.0)]),
        FakeGoal("g2", "personal", now, week, [ResourceDemand(TIME, 60.0)]),
    ]
    pool = ResourcePool(TIME, capacity=50.0, window_start=now, window_end=week)
    detector = CompetingResourceDetector(pools=[pool])
    conflicts = detector.detect(goals)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c.kind == ConflictKind.COMPETING_RESOURCE
    assert c.severity == ConflictSeverity.CRITICAL
    assert set(c.goal_ids) == {"g1", "g2"}
    assert "focus-hours" in c.title


def test_cross_domain_awareness_noted_in_suggestions():
    now = datetime(2025, 1, 1)
    week = now + timedelta(days=7)
    goals = [
        FakeGoal("g1", "work", now, week, [ResourceDemand(MONEY, 4000.0)]),
        FakeGoal("g2", "health", now, week, [ResourceDemand(MONEY, 4000.0)]),
    ]
    pool = ResourcePool(
        MONEY, capacity=5000.0, window_start=now, window_end=week,
        replenishable=False,
    )
    detector = CompetingResourceDetector(pools=[pool])
    conflicts = detector.detect(goals)
    assert len(conflicts) == 1
    joined = " ".join(conflicts[0].suggested_resolutions)
    assert "multiple domains" in joined.lower() or "domain" in joined.lower()


def test_single_goal_no_conflict():
    now = datetime(2025, 1, 1)
    week = now + timedelta(days=7)
    goals = [FakeGoal("solo", "work", now, week, [ResourceDemand(TIME, 999.0)])]
    pool = ResourcePool(TIME, capacity=10.0, window_start=now, window_end=week)
    detector = CompetingResourceDetector(pools=[pool])
    assert detector.detect(goals) == []


def test_default_pool_inferred_for_time():
    now = datetime(2025, 1, 1)
    day = now + timedelta(days=1)
    # 30 hours of time demand in a 1-day window vs 16 waking hours
    goals = [
        FakeGoal("a", "work", now, day, [ResourceDemand(TIME, 16.0)]),
        FakeGoal("b", "personal", now, day, [ResourceDemand(TIME, 16.0)]),
    ]
    detector = CompetingResourceDetector(pools=[])  # no explicit pools
    conflicts = detector.detect(goals)
    assert len(conflicts) == 1
    assert conflicts[0].severity in (
        ConflictSeverity.HIGH, ConflictSeverity.CRITICAL
    )
