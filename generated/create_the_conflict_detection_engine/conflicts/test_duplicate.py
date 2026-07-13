"""
Tests for duplicate detection engine.
"""

import pytest
from jobstar.conflict.base import Goal
from jobstar.conflict.duplicate import DuplicateDetector


@pytest.fixture
def detector():
    return DuplicateDetector()


@pytest.fixture
def make_goal():
    """Factory for creating test goals."""
    counter = [0]
    def _make(title, description="A test goal", **kwargs):
        counter[0] += 1
        defaults = dict(
            id=f"goal-{counter[0]}",
            title=title,
            description=description,
            domain="general",
            urgency="normal",
            steps=[],
            resources=[],
            expected_outputs=[],
            tags=[],
        )
        defaults.update(kwargs)
        return Goal(**defaults)
    return _make


class TestTextSimilarity:
    def test_identical_text(self):
        from jobstar.conflict.duplicate import _text_similarity
        assert _text_similarity("hello world", "hello world") == 1.0

    def test_completely_different(self):
        from jobstar.conflict.duplicate import _text_similarity
        assert _text_similarity("hello world", "goodbye universe") < 0.4

    def test_case_insensitive(self):
        from jobstar.conflict.duplicate import _text_similarity
        assert _text_similarity("Hello World", "hello world") == 1.0

    def test_empty_strings(self):
        from jobstar.conflict.duplicate import _text_similarity
        assert _text_similarity("", "hello") == 0.0


class TestListOverlap:
    def test_identical_lists(self):
        from jobstar.conflict.duplicate import _list_overlap
        ratio, count = _list_overlap(["a", "b", "c"], ["a", "b", "c"])
        assert ratio == 1.0
        assert count == 3

    def test_no_overlap(self):
        from jobstar.conflict.duplicate import _list_overlap
        ratio, count = _list_overlap(["a", "b"], ["x", "y"])
        assert ratio == 0.0
        assert count == 0

    def test_empty_lists(self):
        from jobstar.conflict.duplicate import _list_overlap
        ratio, count = _list_overlap([], ["a"])
        assert ratio == 0.0

    def test_fuzzy_match(self):
        from jobstar.conflict.duplicate import _list_overlap
        ratio, count = _list_overlap(
            ["Create config file"],
            ["create a config file"]
        )
        assert count == 1


class TestDuplicateDetection:

    def test_identical_goals_detected(self, detector, make_goal):
        """Two goals with identical titles and descriptions should be duplicates."""
        g1 = make_goal("Build authentication system", "Implement OAuth2 login flow")
        g2 = make_goal("Build authentication system", "Implement OAuth2 login flow")

        result = detector.compare(g1, g2)
        assert result.is_duplicate
        assert result.confidence >= 0.75

    def test_near_identical_titles_detected(self, detector, make_goal):
        """Slightly different wording but same intent should be detected."""
        g1 = make_goal("Build authentication system", "Implement OAuth2 login flow")
        g2 = make_goal("Build the authentication system", "Implement OAuth2 login flow")

        result = detector.compare(g1, g2)
        assert result.is_duplicate

    def test_completely_different_goals_not_flagged(self, detector, make_goal):
        """Unrelated goals should not be flagged."""
        g1 = make_goal("Build authentication system", "Implement OAuth2 login flow",
                       domain="security")
        g2 = make_goal("Write marketing copy", "Create landing page text",
                       domain="marketing")

        result = detector.compare(g1, g2)
        assert not result.is_duplicate
        assert not result.is_likely
        assert result.confidence < 0.50

    def test_same_goal_not_compared(self, detector, make_goal):
        """A goal compared with itself should not be flagged as duplicate."""
        g1 = make_goal("Build auth", "Do the thing")
        result = detector.compare(g1, g1)
        assert not result.is_duplicate

    def test_structural_overlap_increases_confidence(self, detector, make_goal):
        """Goals with overlapping steps should score higher than without."""
        g1 = make_goal("Set up CI/CD", "Configure deployment pipeline",
                       steps=["Create GitHub Actions workflow", "Set up staging env"],
                       resources=["GitHub", "AWS"])
        g2 = make_goal("Set up CI/CD", "Configure deployment pipeline",
                       steps=["Create GitHub Actions workflow", "Set up staging env"],
                       resources=["GitHub", "AWS"])

        result = detector.compare(g1, g2)
        assert result.is_duplicate
        assert result.signals["structural"] >= 0.9

    def test_different_domains_reduces_confidence(self, detector, make_goal):
        """Same title but different domains should reduce confidence."""
        g1 = make_goal("Set up monitoring", "Configure system monitoring",
                       domain="devops")
        g2 = make_goal("Set up monitoring", "Configure system monitoring",
                       domain="marketing")  # monitoring marketing campaigns

        result = detector.compare(g1, g2)
        # High semantic + low domain → might still be likely but confidence reduced
        assert result.signals["domain"] < 0.5

    def test_temporal_match_increases_score(self, detector, make_goal):
        """Same urgency increases temporal signal."""
        g1 = make_goal("Deploy hotfix", "Fix production bug", urgency="critical")
        g2 = make_goal("Deploy hotfix", "Fix production bug", urgency="critical")

        result = detector.compare(g1, g2)
        assert result.signals["temporal"] == 1.0

    def test_scan_all_finds_pairs(self, detector, make_goal):
        """scan_all should find duplicate pairs in a collection."""
        goals = [
            make_goal("Build auth", "OAuth2 login", domain="security"),
            make_goal("Build auth", "OAuth2 login", domain="security"),  # dup of 0
            make_goal("Write docs", "API documentation", domain="docs"),
            make_goal("Write API docs", "API documentation", domain="docs"),  # likely dup of 2
            make_goal("Buy groceries", "Weekly shopping", domain="personal"),
        ]

        results = detector.scan_all(goals)
        # Should find at least the first duplicate pair
        assert len(results) >= 1
        # Verify the first pair is goals 0 and 1
        pair_ids = {(r.goal_a_id, r.goal_b_id) for r in results}
        assert ("goal-1", "goal-2") in pair_ids or ("goal-2", "goal-1") in pair_ids

    def test_find_duplicates_of_target(self, detector, make_goal):
        """find_duplicates_of should return matches for a specific goal."""
        target = make_goal("Build auth", "OAuth2 login", domain="security")
        candidates = [
            make_goal("Build auth", "OAuth2 login", domain="security"),  # dup
            make_goal("Write docs", "API docs", domain="docs"),          # not dup
        ]

        results = detector.find_duplicates_of(target, candidates)
        assert len(results) == 1
        assert results[0].is_duplicate

    def test_likely_duplicate_threshold(self, detector, make_goal):
        """Goals above 'likely' but below 'duplicate' threshold get is_likely."""
        g1 = make_goal("Refactor database layer", "Clean up ORM models and queries",
                       domain="backend", steps=["Audit existing queries", "Create new models"])
        g2 = make_goal("Refactor DB layer", "Clean up ORM models and database queries",
                       domain="backend", steps=["Audit existing queries", "Create new models"])

        result = detector.compare(g1, g2)
        # Should be at least likely
        assert result.is_duplicate or result.is_likely

    def test_result_explanation_is_readable(self, detector, make_goal):
        """Explanation should contain human-readable text."""
        g1 = make_goal("Build auth", "OAuth2 login", domain="security")
        g2 = make_goal("Build auth", "OAuth2 login", domain="security")

        result = detector.compare(g1, g2)
        assert "DUPLICATE" in result.explanation or "LIKELY" in result.explanation
        assert "Confidence" in result.explanation
        assert "Semantic" in result.explanation

    def test_custom_config_thresholds(self, make_goal):
        """Custom config should affect detection sensitivity."""
        # Very high threshold — should not flag near-duplicates
        strict_config = {"threshold_duplicate": 0.95, "threshold_likely": 0.90}
        strict_detector = DuplicateDetector(config=strict_config)

        g1 = make_goal("Build auth system", "OAuth2 login flow", domain="security")
        g2 = make_goal("Build auth system", "OAuth2 login flow", domain="security")

        result = strict_detector.compare(g1, g2)
        # With 0.95 threshold, even identical goals might not hit it
        # depending on signal weights. This tests config is respected.
        assert result.confidence < 0.95 or result.is_duplicate

    def test_signals_dict_populated(self, detector, make_goal):
        """Result should include breakdown of all signals."""
        g1 = make_goal("Goal A", "Description A")
        g2 = make_goal("Goal B", "Description B")

        result = detector.compare(g1, g2)
        assert "semantic" in result.signals
        assert "structural" in result.signals
        assert "temporal" in result.signals
        assert "domain" in result.signals
        assert "confidence" in result.signals
