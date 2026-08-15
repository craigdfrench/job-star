"""Tests for expert detection in the triage engine (word-boundary matching).

_detect_expert previously used plain substring matching (`kw in text`). The
bare "ci" keyword matched any text containing a word with "ci" inside it
("exercising", "decide", "special", "practice"), hijacking personal/feature
goals into the CI expert worker -- which stamps template steps instead of
AI-planning them. This is a root cause of "non-expert planning isn't robust".

Found 2026-08-15 validating the planning chain: a validation goal whose
description contained "exercising" was claimed by the ci expert within 11
seconds and stamped "Run ci gate" (no AI planning).
"""

from job_star.triage.engine import _detect_expert
from job_star.models import IntakeRequest


def _req(text: str) -> IntakeRequest:
    return IntakeRequest(title=text)


def test_plain_ci_keyword_no_longer_hijacks_substrings():
    """Words merely containing 'ci' must NOT route to the ci expert."""
    for text in [
        "Validation test exercising the planning chain end to end",
        "Decide what to do about the garage shelving",
        "Practice the guitar more often",
        "Special dinner for Tara this weekend",
    ]:
        assert _detect_expert(_req(text)) is None, f"{text!r} should stay unowned"


def test_standalone_ci_word_still_routes():
    """The word 'ci' on its own still routes to the ci expert."""
    assert _detect_expert(_req("Run the CI gate for this PR")) == "ci"
    assert _detect_expert(_req("fix the ci pipeline")) == "ci"


def test_ci_phrase_keywords_route():
    """Multi-word ci keywords route on whole-phrase boundaries."""
    assert _detect_expert(_req("The build gate is red on main")) == "ci"
    assert _detect_expert(_req("continuous integration is flaky")) == "ci"


def test_gatehouse_keywords_route():
    assert _detect_expert(_req("Check the gatehouse config for the provider")) == "gatehouse-ai"
    # substring inside another word must not route: "gatehousex" is not "gatehouse"
    assert _detect_expert(_req("agatehousey nonsense")) is None


def test_research_monitor_keyword_routes():
    assert _detect_expert(_req("keep an eye on the packages")) == "research"


def test_generic_goal_stays_unowned():
    """A personal goal with no expert keywords stays in the generic pool."""
    text = ("Add Tara iPhone notifications to Frigate person-detection "
            "automations so she gets pinged when someone is at the door")
    assert _detect_expert(_req(text)) is None
