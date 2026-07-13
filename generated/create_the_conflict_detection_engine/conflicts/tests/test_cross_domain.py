"""
Tests for the cross-domain awareness layer.
"""

import pytest
from jobstar.conflict import (
    CrossDomainDetector,
    GoalContext,
    Domain,
    ConflictType,
    Severity,
    DomainRegistry,
    DomainProfile,
)


class TestDomainRegistry:
    def test_default_profiles_loaded(self):
        reg = DomainRegistry()
        assert reg.get(Domain.WORK).display_name == "Work"
        assert reg.get(Domain.HEALTH).display_name == "Health"

    def test_tension_is_bidirectional(self):
        reg = DomainRegistry()
        # WORK lists REST as tension; REST should also show WORK
        assert Domain.REST in reg.get_tensions(Domain.WORK)
        assert Domain.WORK in reg.get_tensions(Domain.REST)

    def test_alignment_is_bidirectional(self):
        reg = DomainRegistry()
        assert Domain.CAREER in reg.get_alignments(Domain.WORK)
        assert Domain.WORK in reg.get_alignments(Domain.CAREER)

    def test_shared_resources(self):
        reg = DomainRegistry()
        shared = reg.get_shared_resources(Domain.WORK, Domain.LEARNING)
        assert "energy_mental" in shared
        assert "attention" in shared

    def test_custom_domain_registration(self):
        reg = DomainRegistry()
        custom = Domain("GARDENING")
        profile = DomainProfile(
            domain=custom,
            display_name="Gardening",
            description="Growing plants and maintaining a garden.",
            resource_consumption={"time_weekly": 0.1, "energy_physical": 0.2},
            aligned_with={Domain.HOME, Domain.HEALTH},
        )
        reg.register(profile)
        assert reg.get(custom).display_name == "Gardening"


class TestCrossDomainDetector:
    def setup_method(self):
        self.detector = CrossDomainDetector()

    def test_resource_competition_detected(self):
        """Work goal + learning goal should flag mental energy competition."""
        work_goal = GoalContext(
            goal_id="g1",
            title="Ship Q4 project",
            domains=[Domain.WORK],
            priority=0.8,
            intensity=0.9,
        )
        learning_goal = GoalContext(
            goal_id="g2",
            title="Master machine learning",
            domains=[Domain.LEARNING],
            priority=0.7,
            intensity=0.8,
        )
        conflicts = self.detector.analyze([work_goal, learning_goal])

        resource_conflicts = [c for c in conflicts if c.conflict_type == ConflictType.RESOURCE_COMPETITION]
        assert len(resource_conflicts) > 0
        assert any(c.resource == "energy_mental" for c in resource_conflicts)

    def test_domain_tension_detected(self):
        """Work goal + rest goal should flag domain tension."""
        work_goal = GoalContext(
            goal_id="g1",
            title="Crunch week",
            domains=[Domain.WORK],
            priority=0.8,
            intensity=0.9,
        )
        rest_goal = GoalContext(
            goal_id="g2",
            title="Sleep 8 hours nightly",
            domains=[Domain.REST],
            priority=0.7,
            intensity=0.7,
        )
        conflicts = self.detector.analyze([work_goal, rest_goal])

        tension_conflicts = [c for c in conflicts if c.conflict_type == ConflictType.DOMAIN_TENSION]
        assert len(tension_conflicts) > 0
        assert Severity.HIGH in [c.severity for c in tension_conflicts]

    def test_no_conflict_between_aligned_domains(self):
        """Health + fitness goals should not produce tension conflicts."""
        health_goal = GoalContext(
            goal_id="g1",
            title="Improve diet",
            domains=[Domain.HEALTH],
            priority=0.7,
            intensity=0.5,
        )
        fitness_goal = GoalContext(
            goal_id="g2",
            title="Run 3x per week",
            domains=[Domain.FITNESS],
            priority=0.6,
            intensity=0.5,
        )
        conflicts = self.detector.analyze([health_goal, fitness_goal])

        tension_conflicts = [c for c in conflicts if c.conflict_type == ConflictType.DOMAIN_TENSION]
        assert len(tension_conflicts) == 0

    def test_schedule_collision_detected(self):
        """Two goals in different domains with same time window."""
        goal_a = GoalContext(
            goal_id="g1",
            title="Morning workout",
            domains=[Domain.FITNESS],
            priority=0.7,
            intensity=0.6,
            time_window="weekday_mornings",
        )
        goal_b = GoalContext(
            goal_id="g2",
            title="Morning deep work",
            domains=[Domain.WORK],
            priority=0.8,
            intensity=0.7,
            time_window="weekday_mornings",
        )
        conflicts = self.detector.analyze([goal_a, goal_b])

        schedule_conflicts = [c for c in conflicts if c.conflict_type == ConflictType.SCHEDULE_COLLISION]
        assert len(schedule_conflicts) == 1
        assert schedule_conflicts[0].severity == Severity.HIGH

    def test_resource_depletion_detected(self):
        """Many high-intensity goals should trigger resource depletion."""
        goals = [
            GoalContext(
                goal_id=f"g{i}",
                title=f"Goal {i}",
                domains=[Domain.WORK],
                priority=0.8,
                intensity=0.9,
            )
            for i in range(8)
        ]
        conflicts = self.detector.analyze(goals)

        depletion_conflicts = [c for c in conflicts if c.conflict_type == ConflictType.RESOURCE_DEPLETION]
        assert len(depletion_conflicts) > 0
        # Should flag at least time and mental energy
        resources_flagged = {c.resource for c in depletion_conflicts}
        assert "time_daily" in resources_flagged or "time_weekly" in resources_flagged
        assert "energy_mental" in resources_flagged

    def test_analyze_single_new_goal(self):
        """analyze_single should check a new goal against existing ones."""
        existing = [
            GoalContext(
                goal_id="g1",
                title="Existing work goal",
                domains=[Domain.WORK],
                priority=0.7,
                intensity=0.8,
            )
        ]
        new_goal = GoalContext(
            goal_id="g2",
            title="New learning goal",
            domains=[Domain.LEARNING],
            priority=0.6,
            intensity=0.7,
        )
        conflicts = self.detector.analyze_single(new_goal, existing)
        assert len(conflicts) > 0

    def test_inactive_goals_ignored(self):
        """Inactive goals should not produce conflicts."""
        goals = [
            GoalContext(
                goal_id="g1",
                title="Active work goal",
                domains=[Domain.WORK],
                priority=0.8,
                intensity=0.9,
                active=True,
            ),
            GoalContext(
                goal_id="g2",
                title="Inactive rest goal",
                domains=[Domain.REST],
                priority=0.7,
                intensity=0.7,
                active=False,
            ),
        ]
        conflicts = self.detector.analyze(goals)
        # No pairwise conflicts since g2 is inactive
        assert all("g2" not in c.goal_ids for c in conflicts)

    def test_multi_domain_goal(self):
        """A goal spanning multiple domains should be checked against all."""
        goal_a = GoalContext(
            goal_id="g1",
            title="Build a health-focused startup",
            domains=[Domain.WORK, Domain.HEALTH],
            priority=0.9,
            intensity=0.8,
        )
        goal_b = GoalContext(
            goal_id="g2",
            title="Family time every evening",
            domains=[Domain.FAMILY],
            priority=0.8,
            intensity=0.7,
        )
        conflicts = self.detector.analyze([goal_a, goal_b])

        # WORK-FAMILY tension should be detected
        tension = [c for c in conflicts if c.conflict_type == ConflictType.DOMAIN_TENSION]
        assert any(Domain.WORK in c.domains and Domain.FAMILY in c.domains for c in tension)

    def test_suggestions_are_generated(self):
        """Every conflict should include at least one suggestion."""
        goals = [
            GoalContext(
                goal_id="g1",
                title="Work crunch",
                domains=[Domain.WORK],
                priority=0.8,
                intensity=0.9,
            ),
            GoalContext(
                goal_id="g2",
                title="Learn Rust",
                domains=[Domain.LEARNING],
                priority=0.7,
                intensity=0.8,
            ),
        ]
        conflicts = self.detector.analyze(goals)
        for c in conflicts:
            assert len(c.suggestions) > 0
