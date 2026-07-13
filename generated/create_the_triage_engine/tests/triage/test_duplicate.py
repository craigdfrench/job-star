"""Tests for duplicate detection."""

import uuid
from datetime import datetime, timezone

from job_star.triage.duplicate import check_duplicate, _jaccard, _tokenize
from job_star.triage.models import GoalRegistryEntry, IntakeRequest


def _make_entry(title: str, description: str = "") -> GoalRegistryEntry:
    return GoalRegistryEntry(
        id=uuid.uuid4(),
        title=title,
        description=description,
        created_at=datetime.now(timezone.utc),
    )


class TestJaccard:
    def test_identical(self):
        assert _jaccard({"a", "b", "c"}, {"a", "b", "c"}) == 1.0

    def test_disjoint(self):
        assert _jaccard({"a", "b"}, {"c", "d"}) == 0.0

    def test_partial(self):
        # intersection {a, b} = 2, union {a,b,c,d} = 4 => 0.5
        assert _jaccard({"a", "b", "c"}, {"a", "b", "d"}) == 0.5

    def test_both_empty(self):
        assert _jaccard(set(), set()) == 0.0


class TestTokenize:
    def test_basic(self):
        assert _tokenize("Fix the login bug") == {"fix", "the", "login", "bug"}

    def test_punctuation(self):
        assert _tokenize("API, endpoint! (v2)") == {"api", "endpoint", "v2"}

    def test_case_insensitive(self):
        assert _tokenize("FixLogin BUG") == {"fixlogin", "bug"}


class TestCheckDuplicate:
    def test_exact_duplicate(self):
        registry = [_make_entry("Fix login bug", "Login page crashes")]
        req = IntakeRequest(title="Fix login bug", description="Login page crashes")
        result = check_duplicate(req, registry)
        assert result.is_duplicate is True
        assert result.matched_goal_id == registry[0].id
        assert result.similarity_score == 1.0

    def test_near_duplicate(self):
        registry = [_make_entry("Fix login bug on the page")]
        req = IntakeRequest(title="Fix the login bug")
        result = check_duplicate(req, registry, threshold=0.4)
        assert result.is_duplicate is True
        assert result.similarity_score > 0.4

    def test_not_duplicate(self):
        registry = [_make_entry("Add dark mode feature")]
        req = IntakeRequest(title="Fix database migration error")
        result = check_duplicate(req, registry)
        assert result.is_duplicate is False
        assert result.matched_goal_id is None

    def test_empty_registry(self):
        req = IntakeRequest(title="Fix login bug")
        result = check_duplicate(req, [])
        assert result.is_duplicate is False
        assert result.similarity_score == 0.0

    def test_best_match_selected(self):
        entry1 = _make_entry("Fix login bug crash")
        entry2 = _make_entry("Fix login bug on page")
        registry = [entry1, entry2]
        req = IntakeRequest(title="Fix login bug")
        result = check_duplicate(req, registry, threshold=0.0)
        # Should match the more similar one
        assert result.matched_goal_id is not None
        assert result.similarity_score > 0

    def test_custom_threshold(self):
        registry = [_make_entry("Fix login bug crash error")]
        req = IntakeRequest(title="Fix login bug")
        # With high threshold, should not be dup
        result_high = check_duplicate(req, registry, threshold=0.99)
        assert result_high.is_duplicate is False
        # With low threshold, should be dup
        result_low = check_duplicate(req, registry, threshold=0.01)
        assert result_low.is_duplicate is True

    def test_reason_message(self):
        registry = [_make_entry("Fix login bug")]
        req = IntakeRequest(title="Fix login bug")
        result = check_duplicate(req, registry)
        assert "threshold" in result.reason.lower()
