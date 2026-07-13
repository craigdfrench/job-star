"""Tests for tension detection engine."""

import sys
from pathlib import Path

# Ensure we can import from job_star
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from job_star.conflict import (
    TensionDetector,
    GoalProxy,
    TensionCategory,
    TensionSeverity,
)


def make_goal(
    id: str,
    title: str = "",
    description: str = "",
    domain: str = "",
    tags=None,
    values=None,
    energy_mode: str = "",
    context: str = "",
    metadata=None,
    timeline_end=None,
) -> GoalProxy:
    return GoalProxy(
        id=id,
        title=title,
        description=description,
        domain=domain,
        tags=tags or [],
        values=values or [],
        energy_mode=energy_mode,
        context=context,
        metadata=metadata or {},
        timeline_end=timeline_end,
    )


class TestAttentionTension:
    def test_deep_focus_vs_reactive(self):
        a = make_goal("g1", "Write research paper", energy_mode="deep-focus")
        b = make_goal("g2", "Manage customer support inbox", energy_mode="reactive")
        
        detector = TensionDetector()
        result = detector.detect_pair(a, b)
        
        attention_signals = [s for s in result.signals if s.category == TensionCategory.ATTENTION]
        assert len(attention_signals) == 1
        assert attention_signals[0].severity == TensionSeverity.HIGH

    def test_creative_vs_analytical(self):
        a = make_goal("g1", "Brainstorm new product ideas", energy_mode="creative")
        b = make_goal("g2", "Audit financial records", energy_mode="analytical")
        
        detector = TensionDetector()
        result = detector.detect_pair(a, b)
        
        attention_signals = [s for s in result.signals if s.category == TensionCategory.ATTENTION]
        assert len(attention_signals) == 1
        assert attention_signals[0].severity == TensionSeverity.MODERATE

    def test_compatible_modes_no_tension(self):
        a = make_goal("g1", "Write blog post", energy_mode="deep-focus")
        b = make_goal("g2", "Edit book chapter", energy_mode="deep-focus")
        
        detector = TensionDetector()
        result = detector.detect_pair(a, b)
        
        attention_signals = [s for s in result.signals if s.category == TensionCategory.ATTENTION]
        assert len(attention_signals) == 0


class TestValueTension:
    def test_security_vs_freedom(self):
        a = make_goal("g1", "Get tenured position", values=["security", "stability"])
        b = make_goal("g2", "Start nomadic consulting business", values=["freedom", "autonomy"])
        
        detector = TensionDetector()
        result = detector.detect_pair(a, b)
        
        value_signals = [s for s in result.signals if s.category == TensionCategory.VALUE]
        assert len(value_signals) == 1
        assert "Security vs. Freedom" in value_signals[0].description

    def test_growth_vs_contentment(self):
        a = make_goal("g1", "Climb to VP level", values=["growth", "ambition"])
        b = make_goal("g2", "Simplify life and enjoy present", values=["contentment", "peace"])
        
        detector = TensionDetector()
        result = detector.detect_pair(a, b)
        
        value_signals = [s for s in result.signals if s.category == TensionCategory.VALUE]
        assert len(value_signals) == 1

    def test_aligned_values_no_tension(self):
        a = make_goal("g1", "Build startup", values=["growth", "freedom"])
        b = make_goal("g2", "Ship MVP", values=["growth", "achievement"])
        
        detector = TensionDetector()
        result = detector.detect_pair(a, b)
        
        value_signals = [s for s in result.signals if s.category == TensionCategory.VALUE]
        assert len(value_signals) == 0


class TestTemporalTension:
    def test_deadline_clustering(self):
        a = make_goal("g1", "Submit grant proposal", timeline_end="2024-03-15")
        b = make_goal("g2", "Finish quarterly report", timeline_end="2024-03-17")
        
        detector = TensionDetector()
        result = detector.detect_pair(a, b)
        
        temporal_signals = [s for s in result.signals if s.category == TensionCategory.TEMPORAL]
        assert len(temporal_signals) == 1
        assert temporal_signals[0].severity == TensionSeverity.HIGH

    def test_distant_deadlines_no_tension(self):
        a = make_goal("g1", "Submit grant proposal", timeline_end="2024-03-15")
        b = make_goal("g2", "Finish book draft", timeline_end="2024-09-15")
        
        detector = TensionDetector()
        result = detector.detect_pair(a, b)
        
        temporal_signals = [s for s in result.signals if s.category == TensionCategory.TEMPORAL]
        assert len(temporal_signals) == 0


class TestContextTension:
    def test_solo_vs_social(self):
        a = make_goal("g1", "Solo writing retreat", context="solo")
        b = make_goal("g2", "Build team culture", context="social")
        
        detector = TensionDetector()
        result = detector.detect_pair(a, b)
        
        context_signals = [s for s in result.signals if s.category == TensionCategory.CONTEXT]
        assert len(context_signals) == 1


class TestIdentityTension:
    def test_leader_vs_ic(self):
        a = make_goal("g1", "Become engineering manager", description="lead the team")
        b = make_goal("g2", "Master individual craft of programming", description="individual contributor")
        
        detector = TensionDetector()
        result = detector.detect_pair(a, b)
        
        identity_signals = [s for s in result.signals if s.category == TensionCategory.IDENTITY]
        assert len(identity_signals) == 1


class TestProgressTension:
    def test_explicit_drag(self):
        a = make_goal("g1", "Build side project", metadata={"slows": ["g2"]})
        b = make_goal("g2", "Get promotion at work")
        
        detector = TensionDetector()
        result = detector.detect_pair(a, b)
        
        progress_signals = [s for s in result.signals if s.category == TensionCategory.PROGRESS]
        assert len(progress_signals) == 1
        assert progress_signals[0].severity == TensionSeverity.HIGH
        assert progress_signals[0].confidence == 0.9

    def test_high_effort_same_domain(self):
        a = make_goal("g1", "Ship feature A", domain="engineering", metadata={"effort_hours": 25})
        b = make_goal("g2", "Ship feature B", domain="engineering", metadata={"effort_hours": 30})
        
        detector = TensionDetector()
        result = detector.detect_pair(a, b)
        
        progress_signals = [s for s in result.signals if s.category == TensionCategory.PROGRESS]
        assert len(progress_signals) == 1
        assert progress_signals[0].severity == TensionSeverity.MODERATE


class TestDetectorIntegration:
    def test_detect_all_finds_multiple_tensions(self):
        goals = [
            make_goal("g1", "Write thesis", energy_mode="deep-focus", 
                      values=["growth"], timeline_end="2024-03-15"),
            make_goal("g2", "Manage support tickets", energy_mode="reactive",
                      values=["contentment"], timeline_end="2024-03-16"),
            make_goal("g3", "Read fiction for pleasure", energy_mode="deep-focus",
                      values=["contentment"]),
        ]
        
        detector = TensionDetector()
        results = detector.detect_all(goals)
        
        # g1-g2 should have attention + value + temporal tensions
        g1_g2 = [r for r in results if r.goal_a_id == "g1" and r.goal_b_id == "g2"]
        assert len(g1_g2) == 1
        categories = g1_g2[0].categories
        assert TensionCategory.ATTENTION in categories
        assert TensionCategory.VALUE in categories
        assert TensionCategory.TEMPORAL in categories
        
        # Results sorted by severity
        assert results[0].max_severity.value >= results[-1].max_severity.value

    def test_detect_for_new_goal(self):
        existing = [
            make_goal("g1", "Deep research work", energy_mode="deep-focus"),
            make_goal("g2", "Write novel", energy_mode="deep-focus"),
        ]
        new_goal = make_goal("g3", "Be on-call", energy_mode="reactive")
        
        detector = TensionDetector()
        results = detector.detect_for_goal(new_goal, existing)
        
        assert len(results) == 2
        # Both should have attention tension
        for r in results:
            assert any(s.category == TensionCategory.ATTENTION for s in r.signals)

    def test_min_severity_filter(self):
        a = make_goal("g1", "Solo retreat", context="solo")
        b = make_goal("g2", "Team building", context="social")
        
        # With HIGH min severity, MODERATE signals filtered out
        detector = TensionDetector(min_severity=TensionSeverity.HIGH)
        result = detector.detect_pair(a, b)
        
        # Context tension is MODERATE, should be filtered
        assert len(result.signals) == 0

    def test_no_tension_returns_empty_signals(self):
        a = make_goal("g1", "Learn Spanish", domain="languages")
        b = make_goal("g2", "Learn guitar", domain="music")
        
        detector = TensionDetector()
        result = detector.detect_pair(a, b)
        
        assert len(result.signals) == 0
        assert result.max_severity == TensionSeverity.NEGLIGIBLE
        assert not result.is_actionable

    def test_combined_confidence_increases_with_multiple_signals(self):
        a = make_goal("g1", "Write thesis in solitude", 
                      energy_mode="deep-focus", context="solo",
                      values=["growth"], timeline_end="2024-03-15")
        b = make_goal("g2", "Lead team through crisis",
                      energy_mode="reactive", context="social",
                      values=["contentment"], timeline_end="2024-03-16")
        
        detector = TensionDetector()
        result = detector.detect_pair(a, b)
        
        # Multiple signals should boost confidence above any single signal's confidence
        assert len(result.signals) >= 3
        max_single_confidence = max(s.confidence for s in result.signals)
        assert result.combined_confidence > max_single_confidence


if __name__ == "__main__":
    # Run tests
    import pytest
    pytest.main([__file__, "-v"])
