"""Tests for the cross-domain conflict detection engine."""

import pytest
from datetime import datetime, timedelta
from uuid import uuid4

from job_star.conflict.cross_domain import (
    CrossDomainDetector,
    Domain,
    Goal,
    ResourceDemand,
    get_domain_relationship,
)


def make_goal(
    title: str,
    domains: list[Domain],
    priority: int = 3,
    resource_demands: list[ResourceDemand] = None,
    start_date: datetime = None,
    target_date: datetime = None,
    metadata: dict = None,
) -> Goal:
    return Goal(
        id=uuid4(),
        title=title,
        domains=domains,
        priority=priority,
        resource_demands=resource_demands or [],
        start_date=start_date,
        target_date=target_date,
        metadata=metadata or {},
    )


class TestDomainRelationships:
    def test_competing_domains(self):
        assert get_domain_relationship(Domain.WORK, Domain.HEALTH) == "compete"
        assert get_domain_relationship(Domain.HEALTH, Domain.WORK) == "compete"

    def test_reinforcing_domains(self):
        assert get_domain_relationship(Domain.HEALTH, Domain.MENTAL) == "reinforce"
        assert get_domain_relationship(Domain.WORK, Domain.LEARNING) == "reinforce"

    def test_same_domain_is_neutral(self):
        assert get_domain_relationship(Domain.WORK, Domain.WORK) == "neutral"

    def test_unknown_pair_is_neutral(self):
        assert get_domain_relationship(Domain.SPIRITUAL, Domain.FINANCIAL) == "neutral"


class TestResourceCompetition:
    def test_over_allocation_across_domains(self):
        """Two goals in different domains over-allocate time."""
        detector = CrossDomainDetector()
        goals = [
            make_goal(
                "Ship product launch",
                [Domain.WORK],
                priority=1,
                resource_demands=[
                    ResourceDemand("time_hours_week", 60, "hours", 0.8)
                ],
            ),
            make_goal(
                "Train for marathon",
                [Domain.HEALTH],
                priority=2,
                resource_demands=[
                    ResourceDemand("time_hours_week", 30, "hours", 0.7)
                ],
            ),
        ]

        conflicts = detector.detect(goals)
        resource_conflicts = [
            c for c in conflicts if c.conflict_type == "resource_competition"
        ]
        assert len(resource_conflicts) >= 1
        assert resource_conflicts[0].severity > 0
        assert Domain.WORK in resource_conflicts[0].domains
        assert Domain.HEALTH in resource_conflicts[0].domains

    def test_no_conflict_when_within_budget(self):
        """Goals within resource capacity don't trigger conflicts."""
        detector = CrossDomainDetector()
        goals = [
            make_goal(
                "Read a book",
                [Domain.LEARNING],
                resource_demands=[
                    ResourceDemand("time_hours_week", 5, "hours", 0.9)
                ],
            ),
            make_goal(
                "Weekly grocery run",
                [Domain.PERSONAL],
                resource_demands=[
                    ResourceDemand("time_hours_week", 2, "hours", 0.9)
                ],
            ),
        ]

        conflicts = detector.detect(goals)
        resource_conflicts = [
            c for c in conflicts if c.conflict_type == "resource_competition"
        ]
        assert len(resource_conflicts) == 0


class TestTemporalOverlap:
    def test_overlapping_competing_domains(self):
        """Goals in competing domains with overlapping time windows."""
        detector = CrossDomainDetector()
        now = datetime.now()
        goals = [
            make_goal(
                "Major work project",
                [Domain.WORK],
                priority=1,
                start_date=now - timedelta(days=10),
                target_date=now + timedelta(days=60),
            ),
            make_goal(
                "Family vacation planning",
                [Domain.RELATIONSHIPS],
                priority=2,
                start_date=now - timedelta(days=5),
                target_date=now + timedelta(days=45),
            ),
        ]

        conflicts = detector.detect(goals)
        temporal = [c for c in conflicts if c.conflict_type == "temporal_overlap"]
        assert len(temporal) >= 1
        assert temporal[0].severity > 0

    def test_no_temporal_conflict_for_non_overlapping(self):
        detector = CrossDomainDetector()
        now = datetime.now()
        goals = [
            make_goal(
                "Q1 project",
                [Domain.WORK],
                start_date=now,
                target_date=now + timedelta(days=30),
            ),
            make_goal(
                "Q2 project",
                [Domain.WORK],
                start_date=now + timedelta(days=60),
                target_date=now + timedelta(days=90),
            ),
        ]

        conflicts = detector.detect(goals)
        temporal = [c for c in conflicts if c.conflict_type == "temporal_overlap"]
        assert len(temporal) == 0


class TestPriorityTension:
    def test_two_p1_goals_in_competing_domains(self):
        detector = CrossDomainDetector()
        goals = [
            make_goal("Critical work deadline", [Domain.WORK], priority=1),
            make_goal("Health crisis management", [Domain.HEALTH], priority=1),
        ]

        conflicts = detector.detect(goals)
        tension = [c for c in conflicts if c.conflict_type == "priority_tension"]
        assert len(tension) >= 1
        assert tension[0].severity > 0.5

    def test_low_priority_no_tension(self):
        detector = CrossDomainDetector()
        goals = [
            make_goal("Minor work task", [Domain.WORK], priority=4),
            make_goal("Minor health task", [Domain.HEALTH], priority=4),
        ]

        conflicts = detector.detect(goals)
        tension = [c for c in conflicts if c.conflict_type == "priority_tension"]
        assert len(tension) == 0


class TestValueFriction:
    def test_opposing_value_tags(self):
        detector = CrossDomainDetector()
        goals = [
            make_goal(
                "Reduce working hours",
                [Domain.META],
                metadata={
                    "value_friction_tags": ["reduce_work_hours"],
                    "friction_pairs": [["reduce_work_hours", "increase_work_hours"]],
                },
            ),
            make_goal(
                "Get promoted to senior",
                [Domain.WORK],
                metadata={
                    "value_friction_tags": ["increase_work_hours"],
                    "friction_pairs": [["reduce_work_hours", "increase_work_hours"]],
                },
            ),
        ]

        conflicts = detector.detect(goals)
        friction = [c for c in conflicts if c.conflict_type == "value_friction"]
        assert len(friction) >= 1
        assert friction[0].severity > 0.7


class TestSpilloverRisk:
    def test_work_stress_spilling_to_health(self):
        detector = CrossDomainDetector()
        goals = [
            make_goal(
                "Crunch period: ship v2",
                [Domain.WORK],
                metadata={
                    "spillover_risk": {
                        "type": "stress",
                        "level": 0.8,
                        "target_domains": ["health", "relationships"],
                    }
                },
            ),
            make_goal(
                "Maintain daily exercise routine",
                [Domain.HEALTH],
            ),
        ]

        conflicts = detector.detect(goals)
        spillover = [c for c in conflicts if c.conflict_type == "spillover_risk"]
        assert len(spillover) >= 1
        assert Domain.HEALTH in spillover[0].domains


class TestDomainImbalance:
    def test_extreme_work_dominance(self):
        detector = CrossDomainDetector()
        goals = [
            make_goal(
                "Work goal 1",
                [Domain.WORK],
                resource_demands=[
                    ResourceDemand("time_hours_week", 50, "hours", 0.9)
                ],
            ),
            make_goal(
                "Work goal 2",
                [Domain.WORK],
                resource_demands=[
                    ResourceDemand("time_hours_week", 30, "hours", 0.9)
                ],
            ),
            make_goal(
                "Meditate daily",
                [Domain.MENTAL],
                resource_demands=[
                    ResourceDemand("time_hours_week", 1, "hours", 0.9)
                ],
            ),
        ]

        conflicts = detector.detect(goals)
        imbalance = [c for c in conflicts if c.conflict_type == "domain_imbalance"]
        assert len(imbalance) >= 1
        assert Domain.WORK in imbalance[0].domains


class TestEmptyAndEdgeCases:
    def test_no_goals(self):
        detector = CrossDomainDetector()
        assert detector.detect([]) == []

    def test_single_goal(self):
        detector = CrossDomainDetector()
        goals = [make_goal("Solo goal", [Domain.WORK])]
        assert detector.detect(goals) == []

    def test_same_domain_goals_no_cross_domain(self):
        """Two goals in the same single domain shouldn't produce cross-domain conflicts."""
        detector = CrossDomainDetector()
        goals = [
            make_goal("Work task A", [Domain.WORK], priority=1),
            make_goal("Work task B", [Domain.WORK], priority=1),
        ]
        conflicts = detector.detect(goals)
        # Priority tension requires cross-domain, so should be empty
        tension = [c for c in conflicts if c.conflict_type == "priority_tension"]
        assert len(tension) == 0
