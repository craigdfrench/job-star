"""Smoke tests for duplicate detection."""
from triage.models import GoalRegistryEntry, IntakeRequest, Domain, Urgency
from triage.duplicate import check_duplicates


def _entry(gid, title, desc=""):
    return GoalRegistryEntry(
        goal_id=gid, title=title, description=desc,
        domain=Domain.META, urgency=Urgency.SOON,
    )


def test_exact_match_is_duplicate():
    reg = [_entry("g1", "Build the triage engine", "Classify intake requests")]
    req = IntakeRequest(id="r1", raw_text="Build the triage engine to classify intake requests")
    res = check_duplicates(req, reg)
    assert res.is_duplicate is True
    assert res.matched_goal_id == "g1"
    assert res.similarity_score >= 0.65


def test_unrelated_is_not_duplicate():
    reg = [_entry("g1", "Refactor the scheduler module", "Clean up timing logic")]
    req = IntakeRequest(id="r2", raw_text="Write a blog post about weekend hiking trips")
    res = check_duplicates(req, reg)
    assert res.is_duplicate is False
    assert res.matched_goal_id is None


def test_empty_registry():
    req = IntakeRequest(id="r3", raw_text="anything")
    res = check_duplicates(req, [])
    assert res.is_duplicate is False
    assert res.method == "none"


def test_candidates_returned():
    reg = [
        _entry("g1", "Build the triage engine"),
        _entry("g2", "Build a classifier for intake"),
        _entry("g3", "Cook dinner"),
    ]
    req = IntakeRequest(id="r4", raw_text="Build an intake classifier engine")
    res = check_duplicates(req, reg)
    assert len(res.candidates) >= 2
    # top candidate should be one of the build/classifier ones
    assert res.candidates[0][0] in ("g1", "g2")


if __name__ == "__main__":
    for fn in [
        test_exact_match_is_duplicate,
        test_unrelated_is_not_duplicate,
        test_empty_registry,
        test_candidates_returned,
    ]:
        fn()
        print(f"PASS: {fn.__name__}")
