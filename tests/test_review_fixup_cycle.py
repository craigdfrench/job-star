"""Tests for the §4.8 fixup cycle, the deterministic #133 preamble strip, and
the reviewed-head recording in the review executor.

The fixup re-review is the spec'd path from BLOCK back to merge-eligible
(docs/development-workflow-specification.md §4.8): a single adjudicator
receives the original BLOCK output (from the PR thread) + the fixup diff ONLY
and answers CONFIRMED/UNRESOLVED per item. These tests pin the parsing and
comment-building contracts without any network or model calls.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_star.executors.review import (
    ReviewExecutor,
    strip_reasoning_preamble,
)


# --------------------------------------------------------------------------- #
# strip_reasoning_preamble (#133: no thinking narration in PR comments)
# --------------------------------------------------------------------------- #

def test_strip_preamble_camp_heading():
    content = (
        "I'm working through each model's verdict to build a consensus report.\n"
        "The skeptics want more proof.\n\n"
        "## Camp breakdown\n\n| a |\n\nVERDICT: BLOCK (reasons: x)"
    )
    out = strip_reasoning_preamble(content)
    assert out.startswith("## Camp breakdown"), out[:80]
    assert "working through" not in out
    assert "VERDICT: BLOCK" in out  # never touch anything after the section


def test_strip_preamble_numbered_bold_section():
    content = (
        "Reasoning narration first...\n\n"
        "1. **Camp breakdown** - the table\n\nVERDICT: PASS"
    )
    out = strip_reasoning_preamble(content)
    assert out.startswith("1. **Camp breakdown**"), out[:80]
    assert "narration" not in out


def test_strip_preamble_heading_fallback():
    content = "preamble\n\n# Merged review\n\nbody\n\nVERDICT: PASS"
    out = strip_reasoning_preamble(content)
    assert out.startswith("# Merged review")


def test_strip_preamble_no_section_leaves_content():
    content = "no section markers here. VERDICT: PASS"
    assert strip_reasoning_preamble(content) == content
    assert strip_reasoning_preamble("") == ""


# --------------------------------------------------------------------------- #
# _parse_fixup_adjudication (per-item CONFIRMED/UNRESOLVED + FIXUP VERDICT)
# --------------------------------------------------------------------------- #

def test_parse_fixup_all_confirmed():
    text = (
        "1. shallow copy concern - the map must be deep-copied\n"
        "CONFIRMED - item 1: the fixup diff shows the Models slice copied in WithBatchRouting\n"
        "2. e2e reconciliation test missing\n"
        "CONFIRMED - item 2: TestModelsEndpointMatchesExecutorConfig added in the fixup\n"
        "FIXUP VERDICT: CONDITIONAL_PASS"
    )
    items, verdict, unresolved = ReviewExecutor._parse_fixup_adjudication(text)
    assert verdict == "CONDITIONAL_PASS"
    assert unresolved == []
    assert [i["status"] for i in items] == ["CONFIRMED", "CONFIRMED"]
    assert all(i["evidence"] for i in items)


def test_parse_fixup_unresolved():
    text = (
        "CONFIRMED - item 1: applied\n"
        "UNRESOLVED - item 2: no atomic swap shown\n"
        "FIXUP VERDICT: STILL_BLOCKED (unresolved items: 2)"
    )
    items, verdict, unresolved = ReviewExecutor._parse_fixup_adjudication(text)
    assert verdict == "STILL_BLOCKED"
    assert unresolved == [2]
    assert len(items) == 2


def test_parse_fixup_contradiction_trusts_items():
    # A CONDITIONAL_PASS summary line contradicted by an UNRESOLVED per-item
    # line: the per-item lines win (defensive against summary-line optimism).
    text = (
        "UNRESOLVED - item 1: still missing\n"
        "FIXUP VERDICT: CONDITIONAL_PASS"
    )
    items, verdict, unresolved = ReviewExecutor._parse_fixup_adjudication(text)
    assert verdict == "STILL_BLOCKED"
    assert unresolved == [1]


def test_parse_fixup_still_blocked_pulls_numbers_from_tail():
    # Adjudicator said STILL_BLOCKED but forgot per-item UNRESOLVED lines:
    # the item numbers named on the verdict line become the unresolved list.
    text = (
        "CONFIRMED - item 1: applied\n"
        "CONFIRMED - item 2: applied\n"
        "FIXUP VERDICT: STILL_BLOCKED (unresolved items: 2)"
    )
    items, verdict, unresolved = ReviewExecutor._parse_fixup_adjudication(text)
    assert verdict == "STILL_BLOCKED"
    assert 2 in unresolved


def test_parse_fixup_no_verdict_line():
    text = "CONFIRMED - item 1: applied"
    items, verdict, unresolved = ReviewExecutor._parse_fixup_adjudication(text)
    assert verdict is None
    assert items
    assert ReviewExecutor._parse_fixup_adjudication("") == ([], None, [])


# --------------------------------------------------------------------------- #
# _fixup_base_from_comment (Reviewed head recording)
# --------------------------------------------------------------------------- #

def test_fixup_base_from_comment():
    comment = (
        "## Review Gate: VERDICT: BLOCK\n\n"
        "**Blocking concerns:** xyz\n\n---\n\n"
        "_Reviewed head: 2e874755aabbccdd11223344556677889900aabb_\n\n"
        "## Camp breakdown\n"
    )
    assert ReviewExecutor._fixup_base_from_comment(comment) == \
        "2e874755aabbccdd11223344556677889900aabb"


def test_fixup_base_from_comment_legacy_missing():
    comment = "## Review Gate: VERDICT: BLOCK\n\n**Blocking concerns:** xyz"
    assert ReviewExecutor._fixup_base_from_comment(comment) == ""
    assert ReviewExecutor._fixup_base_from_comment("") == ""


# --------------------------------------------------------------------------- #
# _build_pr_comment (fixup headers, reviewed-head line, preamble strip)
# --------------------------------------------------------------------------- #

def test_build_pr_comment_block_records_reviewed_head():
    body = ReviewExecutor._build_pr_comment(
        "BLOCK", "concern", "## Camp breakdown\n\nreport\n\nVERDICT: BLOCK",
        reviewed_sha="abc123def456",
    )
    assert "VERDICT: BLOCK" in body
    assert "_Reviewed head: abc123def456_" in body
    # Deterministic #133: the body starts at the report section, and the
    # reviewed-head note precedes it.
    assert body.index("Reviewed head") < body.index("Camp breakdown")
    assert "**Blocking concerns:** concern" in body


def test_build_pr_comment_strips_narration():
    body = ReviewExecutor._build_pr_comment(
        "BLOCK", "r",
        "I'm organizing the verdicts now.\n\n## Camp breakdown\n\nVERDICT: BLOCK (r)",
    )
    assert "organizing" not in body
    assert "## Camp breakdown" in body


def test_build_pr_comment_fixup_headers():
    body = ReviewExecutor._build_pr_comment(
        "PASS", "conditional", "1. Item enumeration\nCONFIRMED - item 1: x",
        reviewed_sha="fff333", fixup=True,
    )
    assert "Fixup re-review" in body
    assert "CONDITIONAL_PASS" in body
    assert "_Reviewed head: fff333_" in body

    body = ReviewExecutor._build_pr_comment(
        "BLOCK", "item 2 unresolved", "adjudication", fixup=True,
    )
    assert "STILL_BLOCKED" in body
    assert "**Unresolved:** item 2 unresolved" in body


def test_build_pr_comment_empty_aggregate():
    body = ReviewExecutor._build_pr_comment(None, "", "")
    assert "VERDICT: ERROR" in body
    assert "No aggregated report" in body


# --------------------------------------------------------------------------- #
# _preset_aggregator_model (adjudicator model resolution)
# --------------------------------------------------------------------------- #

def test_preset_aggregator_model(monkeypatch, tmp_path):
    import job_star.executors.review as review_mod
    presets = {
        "swe-glm": {"aggregator": {"label": "agg", "model": "model=opus&prov=cog-proxy"}},
    }
    (tmp_path / "intense-public-review").mkdir()
    (tmp_path / "intense-public-review" / "presets.json").write_text(
        json.dumps(presets))
    monkeypatch.setattr(review_mod, "SKILLS_ROOT", str(tmp_path))
    ex = ReviewExecutor()
    assert ex._preset_aggregator_model("public", "swe-glm") == "model=opus&prov=cog-proxy"
    # private sensitivity looks in the private skill dir; missing -> ""
    assert ex._preset_aggregator_model("private", "swe-glm") == ""
    assert ex._preset_aggregator_model("public", "no-such-preset") == ""


# --------------------------------------------------------------------------- #
# FIXUP_REVIEW_PROMPT shape (the adjudicator contract)
# --------------------------------------------------------------------------- #

def test_fixup_review_prompt_contract():
    from job_star.executors.review import FIXUP_REVIEW_PROMPT
    prompt = FIXUP_REVIEW_PROMPT.format(
        block="## Review Gate: VERDICT: BLOCK ...",
        rng="aaa..bbb",
        log="- fixup: x",
        diff="+ diff lines",
    )
    # The contract pieces the parser relies on:
    assert "CONFIRMED - item <n>:" in prompt
    assert "UNRESOLVED - item <n>:" in prompt
    assert "FIXUP VERDICT: CONDITIONAL_PASS" in prompt
    assert "FIXUP VERDICT: STILL_BLOCKED" in prompt
    # The BLOCK output + fixup material are embedded verbatim:
    assert "## Review Gate: VERDICT: BLOCK ..." in prompt
    assert "- fixup: x" in prompt
    assert "+ diff lines" in prompt
    assert "aaa..bbb" in prompt

# --------------------------------------------------------------------------- #
# strip_fixup_narration (round-2: the fixup adjudication must not leak its
# chain-of-thought into the PR thread either)
# --------------------------------------------------------------------------- #

def test_strip_fixup_narration_item_heading():
    from job_star.executors.review import strip_fixup_narration
    content = (
        "I'm going through the original review to pin down the BLOCK items. "
        "Weighing which list to use... reasoning...\n\n"
        "**BLOCK items**\n"
        "1. MatchesModel false-equivalence\n"
        "CONFIRMED - item 1: dispatch code cited\n"
        "FIXUP VERDICT: CONDITIONAL_PASS"
    )
    out = strip_fixup_narration(content)
    # The block anchors at the first numbered item line (the bold heading
    # above it is narration-adjacent and may carry no delimiter).
    assert out.startswith("1. MatchesModel"), out[:80]
    assert "pin down" not in out
    assert "FIXUP VERDICT: CONDITIONAL_PASS" in out


def test_strip_fixup_narration_per_item_first():
    from job_star.executors.review import strip_fixup_narration
    content = (
        "Long narration about deciding which enumeration to anchor on.\n\n"
        "CONFIRMED - item 1: applied in the fixup diff\n"
        "UNRESOLVED - item 2: no estimated flag in output\n"
        "FIXUP VERDICT: STILL_BLOCKED (unresolved items: 2)"
    )
    out = strip_fixup_narration(content)
    assert out.startswith("CONFIRMED - item 1"), out[:80]
    assert "narration" not in out
    assert "UNRESOLVED - item 2" in out


def test_strip_fixup_narration_noop_when_no_block():
    from job_star.executors.review import strip_fixup_narration
    assert strip_fixup_narration("") == ""
    content = "no block markers VERDICT-ish text"
    assert strip_fixup_narration(content) == content


def test_fixup_comment_uses_fixup_strip():
    # A fixup comment must keep only the adjudication block, not the model's
    # decision narration (observed leaking in round 1).
    body = ReviewExecutor._build_pr_comment(
        "BLOCK", "item 2 unresolved",
        "I'm settling on the minimum-to-clear list as the anchor...\n\n"
        "**BLOCK items**\n"
        "CONFIRMED - item 1: ok\n"
        "UNRESOLVED - item 2: missing\n"
        "FIXUP VERDICT: STILL_BLOCKED (unresolved items: 2)",
        fixup=True,
    )
    assert "settling on" not in body
    assert "CONFIRMED - item 1: ok" in body
    assert "STILL_BLOCKED" in body


def test_fixup_diff_budget_covers_large_fixups():
    # Round-1 adjudication could not see the WithBatchRouting deep-copy code
    # because the diff truncated at 30k chars. The budget must accommodate
    # realistic multi-commit fixups.
    from job_star.executors import review as review_mod
    assert review_mod.FIXUP_DIFF_MAX_CHARS >= 60_000


def test_latest_block_comment_matches_fixup_still_blocked(monkeypatch, tmp_path):
    # A fixup cycle iterates: round 2 must adjudicate against the round-1
    # fixup adjudication (STILL_BLOCKED), not the older full-panel BLOCK.
    import subprocess as sp

    def fake_run(cmd, cwd=None, capture_output=False, text=False, timeout=None):
        class R:
            returncode = 0
            stdout = (
                '{"comments": ['
                '{"body": "## Review Gate: VERDICT: BLOCK\\n\\n**Blocking concerns:** old"},'
                '{"body": "Some unrelated comment"},'
                '{"body": "## Review Gate: Fixup re-review (single adjudicator) - '
                'VERDICT: STILL_BLOCKED\\n\\n**Unresolved:** items 2,3,4"}'
                "]}"
            )
            stderr = ""
        return R()

    monkeypatch.setattr(sp, "run", fake_run)
    ex = ReviewExecutor()
    body, err = ex._latest_block_comment("/tmp/wt", "138")
    assert body.startswith("## Review Gate: Fixup re-review"), body[:80]
    assert "STILL_BLOCKED" in body
    assert err == ""


def test_strip_fixup_narration_round2_regression():
    """Round-2 regression (PR #138, 2026-09-12): the adjudicator's narration
    began 'Item 4, the Anthropic completion-window concern, seems resolved...'
    and the first strip version matched it at position 0 (all delimiters in
    the items-heading pattern were optional), leaking the whole
    chain-of-thought into the PR thread. The block must anchor at the
    numbered item list / per-item lines, never at a mid-sentence 'Item N,'
    narration line."""
    from job_star.executors.review import strip_fixup_narration
    content = (
        "Item 4, the Anthropic completion-window concern, seems resolved since "
        "the fixup makes the asymmetry explicit.\n\n"
        "I'm second-guessing whether to treat the narration as authoritative...\n\n"
        "3. Synthetic/default SLA advertised as authoritative; not flagged as "
        "an estimate versus a provider-reported value.\n"
        "4. Write-once batch-routing invariant only documented in prose, not "
        "enforced in code.\n"
        "CONFIRMED - item 3: batch_route.go adds SLAEstimated with json tag "
        "sla_estimated, populated in Route(), default-true in batchroutes.go.\n"
        "CONFIRMED - item 4: server.go WithBatchRouting returns early with a "
        "log when s.batchRoutes != nil, and the test verifies it.\n"
        "FIXUP VERDICT: CONDITIONAL_PASS"
    )
    out = strip_fixup_narration(content)
    assert out.startswith("3. Synthetic/default SLA"), out[:80]
    assert "second-guessing" not in out
    assert "seems resolved" not in out
    assert "CONFIRMED - item 3" in out
    assert "FIXUP VERDICT: CONDITIONAL_PASS" in out
