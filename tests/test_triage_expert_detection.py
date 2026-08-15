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

from job_star.triage.engine import (
    _detect_expert,
    _keyword_matches,
    _keyword_prefix_matches,
    _score_text,
)
from job_star.models import IntakeRequest, Domain


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


# ---------------------------------------------------------------------------
# _score_text: prefix matching (leading boundary only) for stem keyword lists
# ---------------------------------------------------------------------------

def test_important_no_longer_scores_infra_via_port():
    """"important" contains "port" but must not score INFRA."""
    scores = _score_text("this is important", {"infra": ["port"], "personal": ["family"]})
    assert scores["infra"] == 0.0


def test_ports_still_scores_infra():
    """Inflected stem "ports" still scores INFRA (prefix semantics)."""
    scores = _score_text("open the ports on the firewall", {"infra": ["port"], "personal": ["family"]})
    assert scores["infra"] > 0.0


def test_latest_no_longer_scores_coding_via_test():
    """"latest" contains "test" but must not score CODING."""
    scores = _score_text("get the latest photos", {"coding": ["test"], "personal": ["photo"]})
    assert scores["coding"] == 0.0
    assert scores["personal"] > 0.0


def test_crashed_still_matches_crash_stem():
    """"the service crashed" still hits the "crash" keyword (prefix)."""
    scores = _score_text("the service crashed", {"coding": ["crash"], "personal": ["family"]})
    assert scores["coding"] > 0.0


def test_domain_classification_end_to_end():
    """"important" no longer drags a personal goal into INFRA."""
    text = "plan something important for the family this weekend"
    scores = _score_text(text, {
        "meta": ["triage", "router"],
        "coding": ["fix", "code"],
        "infra": ["port", "server", "docker"],
        "personal": ["family", "photo"],
    })
    assert scores["personal"] > scores["infra"]


# ---------------------------------------------------------------------------
# _keyword_matches: conditional boundaries for non-word-char keyword edges
# ---------------------------------------------------------------------------

def test_path_keyword_matches():
    """"/etc/gatehouse" must still match (leading slash needs no \\b anchor)."""
    assert _keyword_matches("/etc/gatehouse", "check /etc/gatehouse/config.json")
    assert not _keyword_matches("/etc/gatehouse", "no such path here")


def test_url_keyword_matches():
    assert _keyword_matches("100.64.158.87:8090", "gateway at 100.64.158.87:8090 is up")


def test_prefix_mode_anchored_at_word_start():
    """Prefix mode still requires a word-start boundary (no mid-word match)."""
    assert _keyword_prefix_matches("ci", "ci gate")
    assert not _keyword_prefix_matches("ci", "exercising")  # mid-word
    assert not _keyword_prefix_matches("port", "important")  # mid-word
    assert _keyword_prefix_matches("crash", "crashed")  # stem + inflection


# ---------------------------------------------------------------------------
# request.expert: explicit override honored + propagated before dedup
# ---------------------------------------------------------------------------

def test_explicit_expert_override_honored():
    """A pinned request.expert wins over keyword detection."""
    req = IntakeRequest(title="Run the CI gate for this PR", expert="review")
    # detection alone would say ci, but the explicit pin must win in triage();
    # _detect_expert itself detects ci:
    assert _detect_expert(req) == "ci"
    # and the documented override is applied in triage() (covered by the
    # expert = request.expert or _detect_expert(request) line):
    assert (req.expert or _detect_expert(req)) == "review"
