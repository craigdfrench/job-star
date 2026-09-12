"""Review executor: multi-model adversarial review gate. Shells out to skills.

Implements docs/development-workflow-specification.md §4. This executor makes
NO AI call of its own for the panel — it shells out to the existing
``intense-public-review`` / ``intense-private-review`` skills, which submit the
panel + aggregator jobs to gatehouse ``POST /v1/jobs`` and run them to
completion. The executor's job is orchestration + verdict parsing + the
single-job retry path.

Contract (§4):
  1. Reads metadata.ref / metadata.repo / metadata.sensitivity / metadata.preset.
  2. No panel reinvention: shells out to the skill CLI:
       sensitivity=="public"  -> intense-public-review --preset <preset> --findings <file>
       sensitivity=="private" -> intense-private-review --preset <preset> --findings <file>
     where <file> is produced by scripts/pr-review-adapter in the gatehouse-ai
     repo (cloned into a throwaway worktree for the run).
  3. The skill submits the panel jobs via gatehouse POST /v1/jobs; we just call
     the CLI and let it run to completion.
  4. Parse aggregated.md for the verdict line:
       VERDICT: PASS                  -> goal status review_pass
       VERDICT: BLOCK (reasons: ...)  -> goal status review_block
       verdict missing / agg failed   -> goal status review_error (never dropped)
  5. Artifacts: review_result (verdict), review_per_model (per-model JSON),
     review_aggregated (aggregated.md).
  6. Post the adjudicator-screened NOTE section (the aggregator's report — NOT
     raw per-model notes) + verdict to the PR thread via gh pr comment.
  7. No wall-clock kill switch. Poll gatehouse /v1/jobs/{id} to terminal status.
     Retry only on real failure (status failed/error/cancelled, or complete with
     no parseable verdict line): resubmit that ONE job (the aggregator, which is
     the verdict producer) up to review_max_retries. Never retry the whole panel
     pass. Record each retry as an artifact. All retries exhausted -> review_error.
  8. Raise max_tokens for the aggregator (>=10240) and panel (>=8192), read from
     .gatehouse-ci.json in the target repo (review_max_tokens_panel /
     review_max_tokens_aggregator), defaulting to the floors if absent.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from ..models import Artifact, ExecutionResult, Goal, GoalStatus, Step
from . import Executor
# Share the slug->URL normalizer with the CI executor so both gates treat
# metadata.repo identically: it's the "owner/name" slug the deploy gate uses,
# but git ls-remote/clone need a fetchable URL.
from .ci import _to_git_url

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

WORKTREE_ROOT = "/tmp/job-star-review-worktrees"

# The adapter lives in the gatehouse-ai repo (merged PR #80). We clone it into a
# throwaway worktree to obtain scripts/pr-review-adapter for the run.
GATEHOUSE_AI_REPO = os.environ.get(
    "GATEHOUSE_AI_REPO", "https://github.com/craigdfrench/gatehouse-ai"
)
GATEHOUSE_AI_REF = os.environ.get("GATEHOUSE_AI_REF", "main")
ADAPTER_PATH_IN_REPO = "scripts/pr-review-adapter"

# Skill CLIs live under the synced ~/.agents/skills tree (present on every
# worker machine). Override the root via REVIEW_SKILLS_DIR if needed.
SKILLS_ROOT = os.path.expanduser(
    os.environ.get("REVIEW_SKILLS_DIR", "~/.agents/skills")
)
SKILL_BINARIES = {
    "public": "intense-public-review/intense-public-review",
    "private": "intense-private-review/intense-private-review",
}

DEFAULT_BASE = "origin/main"

# §4.7/§4.8 token floors and retry budget. These are FLOORS — values read from
# .gatehouse-ci.json are clamped up to these minimums.
DEFAULT_PANEL_MAX_TOKENS = 8192
DEFAULT_AGGREGATOR_MAX_TOKENS = 10240
DEFAULT_MAX_RETRIES = 5

# Gatehouse job statuses that constitute a real failure warranting a retry.
REAL_FAILURE_STATUSES = {
    "failed", "error", "cancelled", "cancel", "timeout", "submit_failed",
}
# Statuses that mean a job is done (no point polling further).
TERMINAL_STATUSES = {
    "complete", "completed", "done", "succeeded", "failed", "cancelled", "error", "timeout",
}
# Statuses that mean a panelist job actually produced a usable verdict. Used by
# the zero-panelist guard: if NO panelist reached one of these, the run is a
# total panel failure and must NOT fall through to PASS-by-default (the
# perplexity-rot incident was exactly this hollow PASS).
PANEL_SUCCESS_STATUSES = {"complete", "completed", "done", "succeeded"}

# §4.8 fixup re-review budgets: the BLOCK comment + fixup diff must fit one
# adjudicator prompt. Truncation is explicit, never silent.
BLOCK_COMMENT_MAX_CHARS = 16_000
FIXUP_DIFF_MAX_CHARS = 60_000

ADAPTER_TIMEOUT_S = 120
SKILL_SUBPROCESS_TIMEOUT_S = 3600  # the skill polls its own jobs; give it room
GIT_TIMEOUT_S = 60
GATEHOUSE_HTTP_TIMEOUT_S = 30
POLL_INTERVAL_S = 5.0
# When re-confirming terminal status of jobs the skill already polled, don't
# hang — they should already be terminal. Give a short window just in case.
RECONFIRM_POLL_TIMEOUT_S = 30

# Cap artifact payload sizes so we don't blow up the step_result JSON column.
AGGREGATED_ARTIFACT_MAX_BYTES = 16 * 1024
PER_MODEL_CONTENT_EXCERPT = 600
PR_COMMENT_MAX_BYTES = 24 * 1024

_HEXADECIMAL = set("0123456789abcdefABCDEF")


# --------------------------------------------------------------------------- #
# Aggregator prompts
#
# These mirror the AGGREGATOR_SYSTEM_PROMPT in intense-public-review.py and
# intense-private-review.py verbatim. They are duplicated here ONLY so the
# single-job aggregator retry (§4.7) can resubmit the aggregator in isolation
# without re-running the whole panel — this is "resubmit that ONE job", not
# panel reinvention. The VERDICT_SUFFIX is appended on retry to force a
# machine-parseable verdict line (the skill's base prompt asks for prose).
# --------------------------------------------------------------------------- #

AGGREGATOR_PROMPT_PUBLIC = (
    "You are the aggregator for an intense multi-model review. Synthesize the "
    "per-model verdicts into a consensus + dissent report.\n\n"
    "HARD RULE: Output ONLY the report sections in the specified shape below. "
    "No preamble, no thinking narration, no commentary about the task, no "
    "description of what you are about to do. The report must start with the "
    "first section heading directly.\n\n"
    "Structure your output as:\n"
    "1. **Camp breakdown** — which models agreed, which disagreed, which were "
    "partial. Use a table.\n"
    "2. **Key objections** — the strongest objections raised, with which models "
    "raised them.\n"
    "3. **Web verification** — if the panel included a verifier model, what it "
    "confirmed or refuted. If no verifier was available, write "
    "\"(none available - ground truth from model knowledge only)\" and skip "
    "the section. Do not discuss the absence.\n"
    "4. **Resolved vs. standing** — which objections were resolved by evidence "
    "or verification, which still stand.\n"
    "5. **Overall verdict** — is the hypothesis well-supported enough to act on, "
    "or does it need more proof? Be honest about residual uncertainty.\n\n"
    "The very last line of your output must be exactly:\n"
    "VERDICT: PASS\n"
    "or\n"
    "VERDICT: BLOCK (reasons: <one sentence summary of the blocking finding>)\n"
    "No other text after the VERDICT line.\n\n"
    "Findings under review:\n{findings}\n\n"
    "Per-model verdicts:\n{verdicts}"
)

AGGREGATOR_PROMPT_PRIVATE = (
    "You are the aggregator for an intense multi-model review. Synthesize the "
    "per-model verdicts into a consensus + dissent report.\n\n"
    "HARD RULE: Output ONLY the report sections in the specified shape below. "
    "No preamble, no thinking narration, no commentary about the task, no "
    "description of what you are about to do. The report must start with the "
    "first section heading directly.\n\n"
    "Structure your output as:\n"
    "1. **Camp breakdown** — which models agreed, which disagreed, which were "
    "partial. Use a table.\n"
    "2. **Key objections** — the strongest objections raised, with which models "
    "raised them.\n"
    "3. **Resolved vs. standing** — which objections were resolved by evidence, "
    "which still stand.\n"
    "4. **Overall verdict** — is the hypothesis well-supported enough to act on, "
    "or does it need more proof? Be honest about residual uncertainty.\n\n"
    "The very last line of your output must be exactly:\n"
    "VERDICT: PASS\n"
    "or\n"
    "VERDICT: BLOCK (reasons: <one sentence summary of the blocking finding>)\n"
    "No other text after the VERDICT line.\n\n"
    "Findings under review:\n{findings}\n\n"
    "Per-model verdicts:\n{verdicts}"
)

# §4.8 fixup re-review prompt: a single adjudicator (the same model class the
# original panel trusted for the final verdict) receives the BLOCK items from
# the original review (verbatim, from the PR thread) plus the fixup diff ONLY
# (not the full PR diff), and answers CONFIRMED/UNRESOLVED per item. Full
# panel re-run is explicitly wrong for fixups - the panel already adjudicated
# the findings; re-deriving them gains nothing (workflow spec §4.8).
FIXUP_REVIEW_PROMPT = (
    "You are the single adjudicator for a review-gate fixup re-review "
    "(development-workflow-specification.md 4.8). An earlier review panel "
    "returned VERDICT: BLOCK on this PR. The author has pushed fixup commits "
    "addressing the BLOCK items. Your job is to confirm, per item, that the "
    "specified remediation was applied - nothing more. Do NOT re-review the "
    "whole PR. Do NOT re-derive the original findings.\n\n"
    "HARD RULE: Output ONLY the adjudication described below - the numbered "
    "item list, one line per item, and the FIXUP VERDICT line. No preamble, "
    "no thinking narration, no commentary about the task, no description of "
    "how you are approaching the decision. Do NOT show your reasoning "
    "process at all.\n\n"
    "Step 1: Enumerate the BLOCK items you can identify in the original "
    "review output below, numbering them 1..N. Quote each item's core "
    "concern in at most 20 words. If the original output lists its blocking "
    "items explicitly (e.g. a numbered \"Blocking concerns\" list), use those "
    "verbatim as the item list.\n\n"
    "Step 2: For EACH item, output EXACTLY one line of one of these forms:\n"
    "CONFIRMED - item <n>: <one sentence citing the evidence from the fixup "
    "diff that shows the remediation applied>\n"
    "UNRESOLVED - item <n>: <one sentence stating what remains missing>\n\n"
    "Step 3: Your very last line must be EXACTLY one of:\n"
    "FIXUP VERDICT: CONDITIONAL_PASS\n"
    "or\n"
    "FIXUP VERDICT: STILL_BLOCKED (unresolved items: <comma-separated item "
    "numbers>)\n\n"
    "Judging contract: an item is CONFIRMED only when the fixup diff below "
    "shows the specified remediation (or the item was factually incorrect and "
    "the fixup diff disproves it - then say so in your evidence sentence). "
    "An item you cannot verify from the material below is UNRESOLVED. Do not "
    "guess.\n\n"
    "Original review's BLOCK output (verbatim, from the PR thread):\n{block}\n\n"
    "Fixup commits ({rng}):\n{log}\n\n"
    "Fixup diff (verbatim):\n{diff}"
)

# Deterministic #133 companion (gatehouse-ai): the aggregator is prompted with
# a hard no-preamble rule, but models sometimes emit thinking narration anyway
# (observed on claude-opus-4-8-max across PR #138 review rounds 1-3). The
# skill strips at write time; the executor strips again before posting so the
# PR thread never shows pre-report narration regardless of model compliance.
_CAMP_SECTION = re.compile(
    r"(?im)^\s*(?:#{1,3}\s*)?(?:\*\*)?(?:\d+[.)]\s*)?(?:\*\*)?\s*camp\s+breakdown"
)
_MD_HEADING = re.compile(r"(?m)^#{1,3}\s+\S")


def strip_reasoning_preamble(content: str) -> str:
    """Drop reasoning narration before the report's first required section.

    The report starts at its 'Camp breakdown' section (heading, numbered, or
    bold variants). Everything before it is narration. Never touch anything
    after. No section start found -> return unchanged (the VERDICT parser
    scans the whole text, so a mangled report is still parseable)."""
    if not content:
        return content
    m = _CAMP_SECTION.search(content)
    start = m.start() if m else None
    if start is None:
        m2 = _MD_HEADING.search(content)
        start = m2.start() if m2 else None
    if start:
        return content[start:].lstrip()
    return content


# The fixup adjudication's adjudication block: the numbered item list, the
# per-item CONFIRMED/UNRESOLVED lines, and the FIXUP VERDICT line. Posted
# PR comments keep only this block - the model's decision narration is
# dropped deterministically (observed: the round-1 fixup adjudication leaked
# its full chain-of-thought into the PR thread).
_FIXUP_ITEMS_HDR = re.compile(
    r"(?im)^\s*(?:#{0,3}\s*)?(?:\*\*)?(?:BLOCK\s+)?[Ii]tems?\s*[:\-]?\s*[:)]?"
)
_FIXUP_PER_ITEM = re.compile(r"(?im)^\s*\*{0,2}(CONFIRMED|UNRESOLVED)\b")
_FIXUP_NUMBERED = re.compile(r"(?im)^\s*\*{0,2}1[.:)]\s")


def strip_fixup_narration(content: str) -> str:
    """Keep only the adjudication block of a fixup re-review output.

    The block starts at the item enumeration (a 'Items:' heading, the first
    per-item CONFIRMED/UNRESOLVED line, or the first numbered item). If no
    block start is found, return the content unchanged (the per-item parser
    scans the whole text, so an unstripped adjudication still parses)."""
    if not content:
        return content
    best = None
    for pat in (_FIXUP_PER_ITEM, _FIXUP_ITEMS_HDR, _FIXUP_NUMBERED):
        m = pat.search(content)
        if m:
            best = m.start() if best is None else min(best, m.start())
    if best is not None:
        return content[best:].lstrip()
    return content


# Forces a machine-parseable verdict line on retry. §4.5 contract.
VERDICT_SUFFIX = (
    "\n\n--- MANDATORY OUTPUT REQUIREMENT ---\n"
    "You MUST end your report with a single line, on its own, of EXACTLY one of "
    "these two forms:\n"
    "  VERDICT: PASS\n"
    "  VERDICT: BLOCK (reason: <one concise sentence>)\n"
    "Use PASS as the default verdict. Use BLOCK only when there is a *critical, "
    "concrete, and unresolved* problem: a correctness bug, a security flaw, a "
    "data race, or a test-coverage gap that would allow a real defect to ship.\n"
    "Style preferences, speculative hypotheticals, documentation nitpicks, or "
    "requests for additional tests that do not block correctness are NOT "
    "sufficient for BLOCK. If the strongest standing objection is not a "
    "critical issue, verdict MUST be PASS. Do not omit this line."
)


class ReviewExecutor(Executor):
    """Multi-model review gate executor. Shells out to the review skills."""

    name = "review"
    description = (
        "Multi-model adversarial review gate (§4): shells out to "
        "intense-public/private-review, parses the VERDICT line, posts screened "
        "notes to the PR, retries only the aggregator job on real failure"
    )

    # ====================================================================== #
    # Entry point
    # ====================================================================== #

    async def execute(
        self,
        goal: Goal,
        step: Step,
        context: dict | None = None,
        model_override: str | None = None,
    ) -> ExecutionResult:
        meta = goal.metadata or {}

        # --- §4.1: read metadata -------------------------------------------
        ref: Optional[str] = meta.get("ref")
        repo: Optional[str] = meta.get("repo")
        # metadata.repo is the owner/name slug the deploy gate identifies a
        # repo by, but git ls-remote/clone need a fetchable URL. Normalize
        # once up front and reuse it for every git op below; keep the raw
        # slug for state/audit (so reported artifacts look the same as the
        # CI gate's).
        repo_url: Optional[str] = _to_git_url(repo) if repo else None
        sensitivity: str = (meta.get("sensitivity") or "").strip().lower()
        preset: str = (meta.get("preset") or "default").strip() or "default"
        base: str = (meta.get("base") or DEFAULT_BASE).strip() or DEFAULT_BASE
        pr_number = meta.get("pr") or meta.get("pr_number")
        if not pr_number and ref:
            m = re.search(r"pull/(\d+)", ref)
            if m:
                pr_number = m.group(1)

        # State accumulated across the run; consumed by _finish().
        state: dict[str, Any] = {
            "ref": ref or "",
            "repo": repo or "",
            "pr": str(pr_number) if pr_number else "",
            "preset": preset,
            "sensitivity": sensitivity,
            "base": base,
            "aggregated": "",
            "per_model": [],
            "panel_job_ids": [],
            "aggregator_job_id": "",
            "aggregator_status": "",
            "retries": [],
            "verdict": None,
            "verdict_reason": "",
            "target_worktree": "",
        }

        target_worktree = ""
        adapter_worktree = ""
        out_dir = ""
        try:
            # --- §4.8: fixup re-review branch ---------------------------------
            # A re-review goal carries metadata.re_review_of = <original
            # review goal id> whose verdict was BLOCK. It runs a single
            # adjudicator over the BLOCK list + the fixup diff ONLY - never a
            # full panel re-run (workflow spec §4.8).
            re_review_of = (meta.get("re_review_of") or "").strip()
            if re_review_of:
                return await self._execute_fixup_review(
                    goal, state, meta, repo_url, ref, str(pr_number),
                )

            # --- validate ----------------------------------------------------
            if not ref or not repo:
                return await self._finish(
                    goal, state, GoalStatus.REVIEW_ERROR,
                    error=f"review: missing metadata.{', '.join(k for k in ('ref','repo') if not meta.get(k))}",
                )
            if not repo_url:
                return await self._finish(
                    goal, state, GoalStatus.REVIEW_ERROR,
                    error=f"review: metadata.repo '{repo}' is neither an owner/name slug nor a git URL",
                )
            if sensitivity not in ("public", "private"):
                return await self._finish(
                    goal, state, GoalStatus.REVIEW_ERROR,
                    error=f"review: invalid sensitivity '{sensitivity}' (expected 'public' or 'private')",
                )
            if not pr_number:
                return await self._finish(
                    goal, state, GoalStatus.REVIEW_ERROR,
                    error="review: no PR number (metadata.pr/pr_number missing and ref is not a pull ref)",
                )

            # --- §4.2/§4.3: prepare throwaway worktrees ----------------------
            target_worktree = self._make_workdir(goal, "target")
            state["target_worktree"] = target_worktree
            ok, err = self._prepare_target_worktree(repo_url, ref, target_worktree)
            if not ok:
                return await self._finish(
                    goal, state, GoalStatus.REVIEW_ERROR, error=err,
                )
            # The sha this review adjudicated. Recorded so a later §4.8 fixup
            # re-review can compute its fixup diff as reviewed_sha..head
            # without guessing which commits the panel saw.
            state["reviewed_sha"] = self._resolve_ref(repo_url, ref) or ""

            adapter_worktree = self._make_workdir(goal, "adapter")
            ok, err = self._prepare_adapter_worktree(adapter_worktree)
            if not ok:
                return await self._finish(
                    goal, state, GoalStatus.REVIEW_ERROR, error=err,
                )

            # --- §4.8: read token/retry config from the target repo ----------
            cfg = self._read_gatehouse_ci_config(target_worktree)

            # --- §4.4: produce the findings doc via pr-review-adapter --------
            out_dir = self._make_workdir(goal, "out")
            findings_path = os.path.join(out_dir, "findings.md")
            findings, ferr = self._run_adapter(
                adapter_worktree, target_worktree, pr_number, ref, base,
                findings_path,
            )
            if not findings:
                return await self._finish(
                    goal, state, GoalStatus.REVIEW_ERROR,
                    error=f"review: pr-review-adapter produced no findings: {ferr}",
                )
            state["findings_chars"] = len(findings)

            # --- §4.2/§4.3: shell out to the review skill --------------------
            skill_path = self._skill_path(sensitivity)
            if not skill_path or not os.path.exists(skill_path):
                return await self._finish(
                    goal, state, GoalStatus.REVIEW_ERROR,
                    error=f"review: skill binary not found for sensitivity '{sensitivity}'"
                          f" (looked for {skill_path})",
                )

            skill_out = os.path.join(out_dir, "skill-out")
            os.makedirs(skill_out, exist_ok=True)
            ok, serr = self._run_skill(
                skill_path, preset, findings_path, skill_out,
            )
            # The skill writes its outputs even when some jobs fail, so we
            # proceed to parse regardless of exit code. A non-zero exit usually
            # means an unknown preset, which surfaces as no outputs below.

            # --- §4.5: read + parse the skill's outputs ----------------------
            self._load_skill_outputs(skill_out, state)
            if not state["aggregated"]:
                # No aggregated.md at all — treat as aggregator failure below.
                state["aggregator_status"] = state.get("aggregator_status") or "missing"

            # --- §4.7: confirm terminal status of every job ------------------
            self._confirm_terminal_statuses(state)

            # --- zero-panelist guard -----------------------------------------
            # If no panelist reached a success status, the aggregator's verdict
            # (often a hollow PASS-by-default from VERDICT_SUFFIX) is meaningless.
            # Refuse to PASS; treat as review_error so a human re-runs the gate.
            panel_ok = [
                r for r in state.get("per_model", [])
                if (r.get("status") or "").lower() in PANEL_SUCCESS_STATUSES
            ]
            if not panel_ok:
                state["verdict"] = None
                state["verdict_reason"] = "zero panelists completed successfully"
                return await self._finish(
                    goal, state, GoalStatus.REVIEW_ERROR,
                    error="review: zero panelists completed successfully "
                          "(total panel failure) — refusing PASS-by-default",
                )

            verdict, reason = self._parse_verdict(state["aggregated"])
            state["verdict"] = verdict
            state["verdict_reason"] = reason or ""

            # --- §4.7: single-job retry on real failure ----------------------
            agg_failed = (
                state.get("aggregator_status", "") in REAL_FAILURE_STATUSES
                or not state.get("aggregator")
            )
            if verdict is None or agg_failed:
                verdict, reason = await self._retry_aggregator(
                    goal, state, findings, sensitivity, cfg,
                )
                state["verdict"] = verdict
                state["verdict_reason"] = reason or ""

            # --- §4.4/§4.5: map verdict -> goal status -----------------------
            if verdict == "PASS":
                goal_status = GoalStatus.REVIEW_PASS
            elif verdict == "BLOCK":
                goal_status = GoalStatus.REVIEW_BLOCK
            else:
                goal_status = GoalStatus.REVIEW_ERROR

            return await self._finish(goal, state, goal_status)

        except Exception as exc:  # never let the gate crash the worker
            return await self._finish(
                goal, state, GoalStatus.REVIEW_ERROR,
                error=f"review: executor error: {exc}",
            )
        finally:
            for d in (target_worktree, adapter_worktree, out_dir):
                if d and os.path.exists(d):
                    shutil.rmtree(d, ignore_errors=True)

    # ====================================================================== #
    # Worktree preparation
    # ====================================================================== #

    def _git(self, args: list[str], cwd: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
        )

    def _make_workdir(self, goal: Goal, label: str) -> str:
        os.makedirs(WORKTREE_ROOT, exist_ok=True)
        slug = re.sub(r"[^a-z0-9-]", "-", (goal.title or "").lower())[:24].strip("-")
        ts = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(
            WORKTREE_ROOT, f"{goal.id[:8]}-{label}-{slug}-{ts}-{os.getpid()}",
        )
        # Create the directory so callers can write into it (the out_dir for
        # findings.md / skill outputs is created here; the target/adapter dirs
        # are subsequently created by git clone).
        os.makedirs(path, exist_ok=True)
        return path

    def _prepare_target_worktree(
        self, repo: str, ref: str, work_dir: str,
    ) -> tuple[bool, str]:
        """Clone the target repo and check out the ref (detached)."""
        # repo may arrive as the owner/name slug the deploy gate uses; git needs
        # a URL. Keep this self-normalizing so the method is safe regardless of
        # whether the caller already normalized (execute() also normalizes).
        repo_url = _to_git_url(repo) or repo
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
        clone = self._git(["clone", "--no-checkout", repo_url, work_dir], os.getcwd())
        if clone.returncode != 0:
            return False, f"review: clone failed: {clone.stderr.strip() or clone.stdout.strip()}"
        # Fetch the specific ref so the SHA is present even if the default
        # branch differed. A fetch failure is non-fatal (adapter has its own
        # fetch + gh fallback).
        self._git(["fetch", "origin", ref], work_dir)
        sha = self._resolve_ref(repo_url, ref)
        target = sha or "FETCH_HEAD"
        checkout = self._git(["checkout", "--detach", target], work_dir)
        if checkout.returncode != 0:
            return False, f"review: checkout {target} failed: {checkout.stderr.strip() or checkout.stdout.strip()}"
        return True, ""

    def _prepare_adapter_worktree(self, work_dir: str) -> tuple[bool, str]:
        """Shallow-clone gatehouse-ai at main to obtain scripts/pr-review-adapter."""
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
        clone = self._git(
            ["clone", "--depth", "1", "-b", GATEHOUSE_AI_REF, GATEHOUSE_AI_REPO, work_dir],
            os.getcwd(),
        )
        if clone.returncode != 0:
            return False, f"review: gatehouse-ai clone failed: {clone.stderr.strip() or clone.stdout.strip()}"
        if not os.path.exists(os.path.join(work_dir, ADAPTER_PATH_IN_REPO)):
            return False, f"review: {ADAPTER_PATH_IN_REPO} not found in gatehouse-ai clone"
        return True, ""

    # ====================================================================== #
    # §4.8 — fixup re-review (single adjudicator, BLOCK list + fixup diff)
    # ====================================================================== #

    async def _execute_fixup_review(
        self,
        goal: Goal,
        state: dict[str, Any],
        meta: dict[str, Any],
        repo_url: str,
        ref: str,
        pr_number: str,
    ) -> ExecutionResult:
        """Single-adjudicator re-review after a BLOCK (workflow spec §4.8).

        Inputs: the ORIGINAL review's BLOCK output (verbatim, latest
        '## Review Gate: VERDICT: BLOCK' PR comment) + the fixup diff
        (fixup_base..current head). Output: CONFIRMED/UNRESOLVED per BLOCK
        item; all CONFIRMED -> REVIEW_PASS (conditional pass per §4.8 step 4),
        any UNRESOLVED -> REVIEW_BLOCK. Never a full panel re-run.
        """
        state["fixup"] = True
        state["re_review_of"] = (meta.get("re_review_of") or "").strip()
        if not ref or not repo_url or not pr_number:
            return await self._finish(
                goal, state, GoalStatus.REVIEW_ERROR,
                error="fixup re-review: requires metadata.ref, metadata.repo and a PR number",
            )

        target_worktree = self._make_workdir(goal, "target")
        state["target_worktree"] = target_worktree
        ok, err = self._prepare_target_worktree(repo_url, ref, target_worktree)
        if not ok:
            return await self._finish(goal, state, GoalStatus.REVIEW_ERROR, error=err)
        head_sha = self._resolve_ref(repo_url, ref) or ""
        state["reviewed_sha"] = head_sha

        # --- BLOCK output: the latest BLOCK comment on the PR thread --------
        block_comment, block_err = self._latest_block_comment(
            target_worktree, pr_number,
        )
        if not block_comment:
            return await self._finish(
                goal, state, GoalStatus.REVIEW_ERROR,
                error=f"fixup re-review: no BLOCK comment found to adjudicate against: {block_err}",
            )
        state["block_comment_chars"] = len(block_comment)

        # --- fixup base: explicit metadata > 'Reviewed head:' in the comment -
        fixup_base = (meta.get("fixup_base") or "").strip()
        if not fixup_base:
            fixup_base = self._fixup_base_from_comment(block_comment)
        if not fixup_base:
            return await self._finish(
                goal, state, GoalStatus.REVIEW_ERROR,
                error="fixup re-review: cannot resolve the original review's "
                      "head sha (no metadata.fixup_base and the BLOCK comment "
                      "predates 'Reviewed head:' recording). Re-submit with "
                      "metadata.fixup_base = <sha the BLOCK review ran on>.",
            )
        base_sha = fixup_base if set(fixup_base) <= _HEXADECIMAL else (
            self._resolve_ref(repo_url, fixup_base) or ""
        )
        if not base_sha:
            return await self._finish(
                goal, state, GoalStatus.REVIEW_ERROR,
                error=f"fixup re-review: could not resolve fixup base {fixup_base!r}",
            )
        self._git(["fetch", "origin", base_sha], target_worktree)
        state["fixup_range"] = f"{base_sha[:12]}..{head_sha[:12] if head_sha else 'HEAD'}"

        log_res = self._git(["log", "--oneline", f"{base_sha}..HEAD"], target_worktree)
        fixup_log = (log_res.stdout or "").strip()[:3000]
        diff_res = self._git(["diff", f"{base_sha}..HEAD"], target_worktree)
        fixup_diff = (diff_res.stdout or "").strip()
        if not fixup_diff:
            return await self._finish(
                goal, state, GoalStatus.REVIEW_ERROR,
                error=f"fixup re-review: empty fixup diff for {state['fixup_range']} "
                      "- push the fixup commit(s) before re-submitting",
            )
        if len(fixup_diff) > FIXUP_DIFF_MAX_CHARS:
            fixup_diff = fixup_diff[:FIXUP_DIFF_MAX_CHARS] + "\n...[fixup diff truncated]"

        # --- adjudicator model: the preset's aggregator ----------------------
        sensitivity = (meta.get("sensitivity") or "public").strip().lower()
        preset_name = (meta.get("preset") or "").strip()
        agg_model = (meta.get("agg_model") or "").strip()
        if not agg_model and preset_name:
            agg_model = self._preset_aggregator_model(sensitivity, preset_name) or ""
        if not agg_model:
            return await self._finish(
                goal, state, GoalStatus.REVIEW_ERROR,
                error="fixup re-review: no adjudicator model (set metadata.preset "
                      "or metadata.agg_model)",
            )

        cfg = self._read_gatehouse_ci_config(target_worktree)
        prompt = FIXUP_REVIEW_PROMPT.format(
            block=block_comment[:BLOCK_COMMENT_MAX_CHARS], rng=state["fixup_range"],
            log=fixup_log or "(none)", diff=fixup_diff,
        )

        # --- submit the single adjudicator job, retry on real failure --------
        for attempt in range(1, cfg["max_retries"] + 1):
            messages = [
                {"role": "system", "content": prompt},
                {"role": "user", "content":
                    "Adjudicate the BLOCK items against the fixup diff now, "
                    "ending with the FIXUP VERDICT line."},
            ]
            try:
                resp = self._submit_job(agg_model, messages, cfg["aggregator_max_tokens"])
                job_id = resp.get("job_id") or resp.get("id")
            except Exception as e:
                state["retries"].append({
                    "attempt": attempt, "status": "submit_failed",
                    "error": f"{type(e).__name__}: {e}",
                })
                continue
            if not job_id:
                state["retries"].append({
                    "attempt": attempt, "status": "submit_failed",
                    "error": f"no job_id in response: {str(resp)[:200]}",
                })
                continue
            job = self._poll_job(job_id, self._retry_poll_timeout())
            content = self._extract_content(job or {}) if job else ""
            rec = {
                "attempt": attempt, "job_id": job_id,
                "status": (job or {}).get("status", "unknown"), "model": agg_model,
            }
            state["retries"].append(rec)
            if not content or content.startswith("[failed"):
                rec["error"] = content[:200] if content else "empty response"
                continue
            state["aggregated"] = content
            state["aggregator_job_id"] = job_id
            state["aggregator_status"] = (job or {}).get("status", "")
            items, fixup_verdict, unresolved = self._parse_fixup_adjudication(content)
            state["fixup_items"] = items
            state["fixup_unresolved"] = unresolved
            if fixup_verdict is None:
                rec["error"] = "no parseable FIXUP VERDICT line"
                continue
            if fixup_verdict == "CONDITIONAL_PASS" and items and not unresolved:
                state["verdict"] = "PASS"
                state["verdict_reason"] = (
                    "conditional pass (§4.8): all BLOCK items CONFIRMED by the "
                    "single adjudicator against the fixup diff "
                    f"({state['fixup_range']})"
                )
                return await self._finish(goal, state, GoalStatus.REVIEW_PASS)
            state["verdict"] = "BLOCK"
            state["verdict_reason"] = (
                f"fixup re-review: {len(unresolved)} BLOCK item(s) UNRESOLVED "
                + (f"({', '.join(str(i) for i in unresolved)})" if unresolved else "(adjudicator said STILL_BLOCKED)")
            )
            return await self._finish(goal, state, GoalStatus.REVIEW_BLOCK)

        state["verdict"] = None
        state["verdict_reason"] = "fixup adjudicator never produced a parseable per-item verdict"
        return await self._finish(goal, state, GoalStatus.REVIEW_ERROR)

    def _latest_block_comment(
        self, target_worktree: str, pr_number: str,
    ) -> tuple[str, str]:
        """The latest '## Review Gate: VERDICT: BLOCK' comment body on the PR.

        The BLOCK items the fixup cycle adjudicates against are the ones the
        original panel posted to the PR thread (workflow spec §4.8 step 1).
        Outbound-only gh call; run inside the target worktree so gh resolves
        the repo from the checkout's remote.
        """
        cmd = ["gh", "pr", "view", str(pr_number), "--json", "comments"]
        try:
            proc = subprocess.run(
                cmd, cwd=target_worktree, capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return "", "gh pr view timed out"
        if proc.returncode != 0:
            return "", f"gh pr view exit {proc.returncode}: {(proc.stderr or '').strip()[:200]}"
        try:
            comments = json.loads(proc.stdout or "{}").get("comments", [])
        except json.JSONDecodeError:
            return "", "could not parse gh pr view comments JSON"
        for c in reversed(comments):
            body = c.get("body") or ""
            if body.lstrip().startswith("## Review Gate: VERDICT: BLOCK"):
                return body, ""
        return "", "no BLOCK comment on the PR thread"

    @staticmethod
    def _fixup_base_from_comment(block_comment: str) -> str:
        """Parse 'Reviewed head: <sha>' from a BLOCK comment (the sha the
        original review adjudicated). Empty when the comment predates the
        recording (legacy reviews need metadata.fixup_base)."""
        # The comment writes the sha as markdown italic: _Reviewed head: <sha>_
        # so tolerate the wrapping underscores on both sides.
        m = re.search(r"(?im)^_?\s*Reviewed head:\s*([0-9a-f]{7,40})_?\s*$", block_comment or "")
        return m.group(1) if m else ""

    def _preset_aggregator_model(self, sensitivity: str, preset_name: str) -> str:
        """The aggregator model for a preset (the single adjudicator is the
        same model class the original panel trusted for its final verdict)."""
        skill_dir = (
            "intense-private-review" if sensitivity == "private"
            else "intense-public-review"
        )
        presets_path = os.path.join(SKILLS_ROOT, skill_dir, "presets.json")
        try:
            with open(presets_path) as f:
                presets = json.load(f)
            agg = (presets.get(preset_name) or {}).get("aggregator") or {}
            return agg.get("model") or ""
        except (OSError, json.JSONDecodeError):
            return ""

    @staticmethod
    def _parse_fixup_adjudication(text: str):
        """Parse the per-item CONFIRMED/UNRESOLVED lines + the final
        FIXUP VERDICT line.

        Returns (items, fixup_verdict, unresolved):
          items         - list of {n, status, evidence} in adjudication order
          fixup_verdict - 'CONDITIONAL_PASS' | 'STILL_BLOCKED' | None
          unresolved    - list of UNRESOLVED item numbers
        """
        if not text:
            return [], None, []
        items: list[dict[str, Any]] = []
        unresolved: list[int] = []
        line_re = re.compile(
            r"(?im)^\s*\*{0,2}(CONFIRMED|UNRESOLVED)\*{0,2}\s*[-:]\s*"
            r"(?:item\s*)?(\d+)\s*[:.]?\s*(.*)$"
        )
        for m in line_re.finditer(text):
            status, n, evidence = m.group(1).upper(), int(m.group(2)), m.group(3).strip()
            items.append({"n": n, "status": status, "evidence": evidence[:300]})
            if status == "UNRESOLVED":
                unresolved.append(n)
        vm = re.search(r"(?im)^\s*FIXUP\s+VERDICT\s*:\s*"
                       r"(CONDITIONAL_PASS|STILL_BLOCKED)", text)
        fixup_verdict = vm.group(1).upper() if vm else None
        # Cross-check: a CONDITIONAL_PASS line with UNRESOLVED items is a
        # contradiction; trust the per-item lines over the summary line.
        if fixup_verdict == "CONDITIONAL_PASS" and unresolved:
            fixup_verdict = "STILL_BLOCKED"
        if fixup_verdict == "STILL_BLOCKED" and items and not unresolved:
            # Pull unresolved item numbers the adjudicator named on the verdict
            # line when it forgot the per-item UNRESOLVED lines.
            tail = text[vm.end():]
            unresolved = [int(x) for x in re.findall(r"\b(\d+)\b", tail[:200])]
        return items, fixup_verdict, unresolved

    def _resolve_ref(self, repo: str, ref: str) -> Optional[str]:
        """Resolve a ref to a SHA via `git ls-remote` (no clone needed)."""
        repo_url = _to_git_url(repo) or repo
        result = self._git(["ls-remote", repo_url, ref], os.getcwd())
        if result.returncode != 0:
            return None
        lines = result.stdout.strip().splitlines()
        if not lines:
            return None
        sha = lines[0].split("\t", 1)[0].strip()
        if not sha or not set(sha) <= _HEXADECIMAL:
            return None
        return sha

    # ====================================================================== #
    # Config (§4.8)
    # ====================================================================== #

    def _read_gatehouse_ci_config(self, work_dir: str) -> dict[str, int]:
        """Read review token/retry config from .gatehouse-ci.json in the repo.

        Values are clamped UP to the §4.8 floors (panel >= 8192, aggregator
        >= 10240). review_max_retries defaults to 3.
        """
        cfg_path = os.path.join(work_dir, ".gatehouse-ci.json")
        raw: dict[str, Any] = {}
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path) as f:
                    raw = json.load(f)
            except (json.JSONDecodeError, OSError):
                raw = {}

        def _int(key: str, default: int) -> int:
            try:
                return int(raw.get(key, default))
            except (TypeError, ValueError):
                return default

        panel = max(_int("review_max_tokens_panel", DEFAULT_PANEL_MAX_TOKENS), DEFAULT_PANEL_MAX_TOKENS)
        aggregator = max(
            _int("review_max_tokens_aggregator", DEFAULT_AGGREGATOR_MAX_TOKENS),
            DEFAULT_AGGREGATOR_MAX_TOKENS,
        )
        retries = max(0, _int("review_max_retries", DEFAULT_MAX_RETRIES))
        return {
            "panel_max_tokens": panel,
            "aggregator_max_tokens": aggregator,
            "max_retries": retries,
        }

    # ====================================================================== #
    # §4.4 — findings via pr-review-adapter
    # ====================================================================== #

    def _run_adapter(
        self,
        adapter_worktree: str,
        target_worktree: str,
        pr_number: str,
        ref: str,
        base: str,
        findings_path: str,
    ) -> tuple[str, str]:
        """Run scripts/pr-review-adapter (cwd=target repo) -> findings file.

        The adapter runs `gh pr view` and `git fetch/diff` against the target
        repo's origin, so it MUST execute inside the target repo checkout.
        Returns (findings_text, error). findings_text is '' on failure.
        """
        adapter = os.path.join(adapter_worktree, ADAPTER_PATH_IN_REPO)
        cmd = [
            sys.executable, adapter,
            "--pr", str(pr_number),
            "--ref", ref,
            "--base", base,
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=target_worktree, capture_output=True, text=True,
                timeout=ADAPTER_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return "", f"pr-review-adapter timed out after {ADAPTER_TIMEOUT_S}s"
        if proc.returncode != 0:
            return "", f"pr-review-adapter exit {proc.returncode}: {(proc.stderr or '').strip()[:300]}"
        findings = proc.stdout or ""
        try:
            with open(findings_path, "w") as f:
                f.write(findings)
        except OSError as e:
            return "", f"could not write findings file: {e}"
        return findings, ""

    # ====================================================================== #
    # §4.2/§4.3 — shell out to the review skill
    # ====================================================================== #

    def _skill_path(self, sensitivity: str) -> Optional[str]:
        rel = SKILL_BINARIES.get(sensitivity)
        if not rel:
            return None
        return os.path.join(SKILLS_ROOT, rel)

    def _run_skill(
        self, skill_path: str, preset: str, findings_path: str, out_dir: str,
    ) -> tuple[bool, str]:
        """Run the skill CLI to completion. Returns (ok, stderr_tail)."""
        cmd = [
            skill_path,
            "--preset", preset,
            "--findings", findings_path,
            "--output", out_dir,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=SKILL_SUBPROCESS_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return False, f"skill timed out after {SKILL_SUBPROCESS_TIMEOUT_S}s"
        return proc.returncode == 0, (proc.stderr or "").strip()[-400:]

    def _load_skill_outputs(self, skill_out: str, state: dict[str, Any]) -> None:
        """Read aggregated.md / summary.json / per-model JSON into state."""
        agg_path = os.path.join(skill_out, "aggregated.md")
        if os.path.exists(agg_path):
            try:
                with open(agg_path) as f:
                    state["aggregated"] = f.read()
            except OSError:
                state["aggregated"] = ""

        per_model: list[dict[str, Any]] = []
        pm_dir = os.path.join(skill_out, "per-model")
        if os.path.isdir(pm_dir):
            for jf in sorted(Path(pm_dir).glob("*.json")):
                try:
                    with open(jf) as f:
                        per_model.append(json.load(f))
                except (json.JSONDecodeError, OSError):
                    continue
        state["per_model"] = per_model
        state["panel_job_ids"] = [
            r.get("job_id") for r in per_model if r.get("job_id")
        ]

        summary_path = os.path.join(skill_out, "summary.json")
        if os.path.exists(summary_path):
            try:
                with open(summary_path) as f:
                    summary = json.load(f)
            except (json.JSONDecodeError, OSError):
                summary = {}
            agg = summary.get("aggregator") or {}
            state["aggregator"] = agg
            state["aggregator_job_id"] = agg.get("job_id") or ""
            state["aggregator_status"] = agg.get("status") or ""
            # summary also carries panel_models with job_ids/statuses; merge any
            # job_ids we didn't pick up from per-model files.
            for m in summary.get("panel_models", []) or []:
                jid = m.get("job_id")
                if jid and jid not in state["panel_job_ids"]:
                    state["panel_job_ids"].append(jid)

    # ====================================================================== #
    # §4.7 — poll / retry
    # ====================================================================== #

    def _confirm_terminal_statuses(self, state: dict[str, Any]) -> None:
        """Re-fetch each job's status to confirm it's terminal (defense in depth).

        The skill already polled these to completion; this catches drift and
        gives us authoritative final statuses for the retry decision.
        """
        agg_id = state.get("aggregator_job_id") or ""
        if agg_id:
            job = self._poll_job(agg_id, RECONFIRM_POLL_TIMEOUT_S)
            if job:
                state["aggregator_status"] = job.get("status") or state.get("aggregator_status", "")
        # Refresh per-model statuses from the API where we have job_ids.
        for r in state.get("per_model", []):
            jid = r.get("job_id")
            if not jid:
                continue
            job = self._poll_job(jid, RECONFIRM_POLL_TIMEOUT_S)
            if job:
                r["status"] = job.get("status") or r.get("status")

    async def _retry_aggregator(
        self,
        goal: Goal,
        state: dict[str, Any],
        findings: str,
        sensitivity: str,
        cfg: dict[str, int],
    ) -> tuple[Optional[str], str]:
        """Resubmit ONLY the aggregator job until a verdict appears or retries
        are exhausted. Records each attempt in state['retries'].

        Returns (verdict, reason). verdict is None if never obtained.
        """
        agg = state.get("aggregator") or {}
        agg_model = agg.get("model")
        if not agg_model:
            # Without the aggregator model we can't resubmit the one job.
            state["retries"].append({
                "attempt": 0, "status": "no_aggregator_model",
                "error": "skill summary.json had no aggregator.model",
            })
            return None, ""

        prompt_tpl = (
            AGGREGATOR_PROMPT_PUBLIC if sensitivity == "public"
            else AGGREGATOR_PROMPT_PRIVATE
        )
        max_tokens = cfg["aggregator_max_tokens"]
        max_retries = cfg["max_retries"]

        for attempt in range(1, max_retries + 1):
            verdicts_text = self._build_verdicts_text(state.get("per_model", []))
            system = prompt_tpl.format(findings=findings, verdicts=verdicts_text) + VERDICT_SUFFIX
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": "Produce the consensus + dissent report now, ending with the VERDICT line."},
            ]
            try:
                resp = self._submit_job(agg_model, messages, max_tokens)
                job_id = resp.get("job_id") or resp.get("id")
            except Exception as e:
                state["retries"].append({
                    "attempt": attempt, "status": "submit_failed",
                    "error": f"{type(e).__name__}: {e}",
                    "max_tokens": max_tokens,
                })
                continue
            if not job_id:
                state["retries"].append({
                    "attempt": attempt, "status": "submit_failed",
                    "error": f"no job_id in response: {str(resp)[:200]}",
                    "max_tokens": max_tokens,
                })
                continue

            job = self._poll_job(job_id, self._retry_poll_timeout())
            status = (job or {}).get("status", "unknown")
            content = self._extract_content(job or {}) if job else ""
            retry_rec = {
                "attempt": attempt,
                "job_id": job_id,
                "status": status,
                "model": agg_model,
                "max_tokens": max_tokens,
            }

            if content and not content.startswith("[failed"):
                # Rewrite aggregated.md with the retry's report and re-parse.
                # Strip reasoning narration (#133): models sometimes open
                # with thinking despite the hard rule in the prompt.
                state["aggregated"] = strip_reasoning_preamble(content)
                verdict, reason = self._parse_verdict(content)
                retry_rec["verdict"] = verdict
                retry_rec["reason"] = reason or ""
                state["retries"].append(retry_rec)
                state["aggregator_job_id"] = job_id
                state["aggregator_status"] = status
                if verdict is not None:
                    return verdict, reason or ""
            else:
                retry_rec["error"] = content[:200] if content else "empty response"
                state["retries"].append(retry_rec)

        return None, ""

    def _retry_poll_timeout(self) -> float:
        """Per-job poll timeout for the aggregator retry (env-tunable)."""
        return float(os.environ.get("REVIEW_RETRY_JOB_TIMEOUT", "600"))

    @staticmethod
    def _build_verdicts_text(per_model: list[dict[str, Any]]) -> str:
        return "\n\n".join(
            f"### {r.get('label', '?')} ({r.get('role', '?')}) — status: {r.get('status', '?')}\n\n{r.get('content', '')}"
            for r in per_model
        )

    # ====================================================================== #
    # Verdict parsing (§4.5)
    # ====================================================================== #

    @staticmethod
    def _parse_verdict(text: str) -> tuple[Optional[str], str]:
        """Find a 'VERDICT: PASS' / 'VERDICT: BLOCK (reason...)' line.

        Lenient about markdown emphasis, leading headings/bullets, and case.
        Returns (verdict|None, reason). reason is the trailing text for BLOCK.
        """
        if not text:
            return None, ""
        for raw in text.splitlines():
            clean = re.sub(r"[*_`#>]", "", raw).strip().lstrip("-").strip()
            m = re.match(r"VERDICT\s*:\s*(PASS|BLOCK)\b", clean, re.IGNORECASE)
            if not m:
                continue
            verdict = m.group(1).upper()
            tail = clean[m.end():].strip()
            # Normalise the common wrappers so the stored reason is clean prose:
            #   BLOCK (reasons: ...) / BLOCK (reason: ...) / BLOCK - reason / BLOCK: reason
            tail = re.sub(r"^\(?\s*reasons?\s*:\s*", "", tail, flags=re.IGNORECASE)
            tail = re.sub(r"^[\s\-:;]+", "", tail).strip().rstrip(").;").strip()
            return verdict, tail
        return None, ""

    # ====================================================================== #
    # Gatehouse jobs API (mirror the skill's proven helpers)
    # ====================================================================== #

    @staticmethod
    def _gatehouse_config() -> tuple[str, str]:
        url = os.environ.get(
            "GATEHOUSE_URL", "http://gatehouse-ai.craigdfrench.com"
        ).rstrip("/")
        token = os.environ.get("GATEHOUSE_TOKEN", "gatehouse")
        return url, token

    def _submit_job(
        self, model: str, messages: list[dict], max_tokens: int,
    ) -> dict:
        url, token = self._gatehouse_config()
        body = {
            "model": model,
            "priority": "normal",
            "request": {"messages": messages, "max_tokens": max_tokens, "stream": False},
        }
        req = urllib.request.Request(
            f"{url}/v1/jobs",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=GATEHOUSE_HTTP_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _poll_job(self, job_id: str, timeout: float) -> Optional[dict]:
        """Poll GET /v1/jobs/{id} until terminal or timeout."""
        if not job_id:
            return None
        url, token = self._gatehouse_config()
        deadline = time.time() + timeout
        while time.time() < deadline:
            req = urllib.request.Request(
                f"{url}/v1/jobs/{job_id}",
                headers={"Authorization": f"Bearer {token}"},
                method="GET",
            )
            try:
                with urllib.request.urlopen(req, timeout=GATEHOUSE_HTTP_TIMEOUT_S) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                return {"id": job_id, "status": "error", "error": f"HTTP {e.code}"}
            except (urllib.error.URLError, json.JSONDecodeError):
                time.sleep(POLL_INTERVAL_S)
                continue
            status = data.get("status", "unknown")
            if status in TERMINAL_STATUSES:
                return data
            time.sleep(POLL_INTERVAL_S)
        return {"id": job_id, "status": "timeout", "error": f"timed out after {timeout}s"}

    @staticmethod
    def _extract_content(job: dict) -> str:
        """Extract assistant text from a completed job (mirrors the skill).

        Any non-complete status (including a missing/None status) is treated as
        a failure so the retry path can resubmit."""
        if job.get("status") not in ("complete", "completed", "done", "succeeded"):
            return f"[failed: {job.get('status', 'unknown')} — {job.get('error', '')}]"
        response = job.get("response") or job.get("result") or job.get("output")
        if response is None:
            return "[failed: no response field in completed job]"
        if isinstance(response, dict):
            choices = response.get("choices", [])
            if choices:
                msg = choices[0].get("message", {})
                content = msg.get("content", "")
            else:
                content = response.get("content", "")
        elif isinstance(response, str):
            content = response
        else:
            content = str(response)
        if isinstance(content, str):
            text = content.strip()
        elif isinstance(content, list):
            parts = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text" and part.get("text"):
                    parts.append(part["text"])
                elif part.get("type") == "thinking":
                    for t in part.get("thinking", []) or []:
                        if isinstance(t, dict) and t.get("text"):
                            parts.append(t["text"])
            text = "\n".join(parts).strip()
        else:
            text = str(content).strip()
        if not text:
            return "[failed: empty response — model returned no text content]"
        return text

    # ====================================================================== #
    # §4.6 — post screened notes to the PR
    # ====================================================================== #

    def _post_pr_comment(
        self, target_worktree: Optional[str], repo: str, pr_number: str,
        verdict: Optional[str], reason: str, aggregated: str,
        reviewed_sha: str = "", fixup: bool = False,
    ) -> tuple[bool, str]:
        """Post the adjudicator-screened report + verdict to the PR thread.

        Outbound only — gh carries its own auth. Best-effort: a comment failure
        is recorded but does not change the gate outcome.
        """
        if not pr_number:
            return False, "no PR number"
        body = self._build_pr_comment(verdict, reason, aggregated,
                                      reviewed_sha=reviewed_sha, fixup=fixup)
        repo_arg = self._owner_repo_for_gh(repo)
        cmd = ["gh", "pr", "comment", str(pr_number), "--body", body]
        if repo_arg:
            cmd += ["-R", repo_arg]
        try:
            proc = subprocess.run(
                cmd, cwd=target_worktree or None, capture_output=True,
                text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            return False, "gh pr comment timed out"
        if proc.returncode != 0:
            return False, f"gh pr comment exit {proc.returncode}: {(proc.stderr or '').strip()[:200]}"
        return True, (proc.stdout or "").strip()

    @staticmethod
    def _owner_repo_for_gh(repo: str) -> Optional[str]:
        """Best-effort owner/repo extraction for `gh -R`. Returns None if we
        can't parse it (gh will then resolve from the cwd's remote)."""
        m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?$", repo or "")
        return m.group(1) if m else None

    @staticmethod
    def _build_pr_comment(
        verdict: Optional[str], reason: str, aggregated: str,
        reviewed_sha: str = "", fixup: bool = False,
    ) -> str:
        if fixup:
            label = "Fixup re-review (single adjudicator, \u00a74.8)"
            if verdict == "PASS":
                head = f"## Review Gate: {label} - VERDICT: CONDITIONAL_PASS"
            elif verdict == "BLOCK":
                head = f"## Review Gate: {label} - VERDICT: STILL_BLOCKED\n\n**Unresolved:** {reason or 'see adjudication below'}"
            else:
                head = f"## Review Gate: {label} - VERDICT: ERROR (no parseable adjudication)"
        elif verdict == "PASS":
            head = "## Review Gate: VERDICT: PASS"
        elif verdict == "BLOCK":
            head = f"## Review Gate: VERDICT: BLOCK\n\n**Blocking concerns:** {reason or 'see report below'}"
        else:
            head = "## Review Gate: VERDICT: ERROR (no parseable verdict)"

        # Deterministic #133 fix: whatever the model emitted, the posted body
        # starts at the report's first section - never at thinking narration.
        # The fixup shape has its own strip (its block is the item list, not
        # 'Camp breakdown').
        if fixup:
            body = strip_fixup_narration(aggregated) or "_No adjudication was produced._"
        else:
            body = strip_reasoning_preamble(aggregated) or "_No aggregated report was produced._"
        if reviewed_sha:
            body = f"_Reviewed head: {reviewed_sha}_\n\n{body}"
        # Keep the comment within gh/GitHub's comfortable body size.
        if len(body) > PR_COMMENT_MAX_BYTES:
            body = body[:PR_COMMENT_MAX_BYTES] + "\n\n…_(aggregated report truncated)_"
        return f"{head}\n\n---\n\n{body}\n\n---\n_Posted by the job-star review gate (multi-model adjudicated review). The notes above are the aggregator's screened report, not raw per-model output._"

    # ====================================================================== #
    # Finalize — status, artifacts, result
    # ====================================================================== #

    async def _finish(
        self,
        goal: Goal,
        state: dict[str, Any],
        goal_status: GoalStatus,
        error: Optional[str] = None,
    ) -> ExecutionResult:
        verdict = state.get("verdict")
        repo = state.get("repo", "")

        # §4.5/§4.6: post the screened notes + verdict to the PR (best-effort).
        # The target worktree still exists at this point (cleanup runs in the
        # caller's finally), so run gh there so it can resolve the repo from the
        # checkout's remote even when -R can't be parsed from the repo string.
        pr_ok, pr_msg = (False, "skipped (no pr)")
        if state.get("pr"):
            try:
                pr_ok, pr_msg = self._post_pr_comment(
                    state.get("target_worktree"), repo, state["pr"], verdict,
                    state.get("verdict_reason", ""), state.get("aggregated", ""),
                    reviewed_sha=state.get("reviewed_sha", ""),
                    fixup=state.get("fixup", False),
                )
            except Exception as e:
                pr_ok, pr_msg = False, f"{type(e).__name__}: {e}"

        # Reflect outcome on the in-memory goal + persist (best-effort, like ci).
        goal.status = goal_status
        try:
            from ..db import update_goal_status
            await update_goal_status(goal.id, goal_status)
        except Exception:
            pass

        artifacts = self._build_artifacts(state, goal_status, pr_ok, pr_msg)

        # success mirrors ci: a gate PASS is a step success; BLOCK/ERROR are not.
        # (BLOCK/ERROR persist because the orchestrator only clobbers goal
        # status on the success->all-steps-done path — known ci interaction.)
        success = goal_status == GoalStatus.REVIEW_PASS

        content = self._summary_content(goal_status, state, pr_ok, pr_msg)
        if not success and error is None:
            error = content
        return ExecutionResult(
            content=content,
            model="review",  # sentinel — no fallback retry by the orchestrator
            success=success,
            error=None if success else error,
            artifacts=artifacts,
        )

    def _build_artifacts(
        self, state: dict[str, Any], goal_status: GoalStatus,
        pr_ok: bool, pr_msg: str,
    ) -> list[Artifact]:
        repo = state.get("repo", "")
        artifacts: list[Artifact] = []

        # §4.5 review_result: the verdict + outcome metadata.
        result_payload = {
            "verdict": state.get("verdict"),
            "reason": state.get("verdict_reason", ""),
            "goal_status": goal_status.value,
            "ref": state.get("ref", ""),
            "pr": state.get("pr", ""),
            "preset": state.get("preset", ""),
            "sensitivity": state.get("sensitivity", ""),
            "panel_job_ids": state.get("panel_job_ids", []),
            "aggregator_job_id": state.get("aggregator_job_id", ""),
            "aggregator_status": state.get("aggregator_status", ""),
            "reviewed_sha": state.get("reviewed_sha", ""),
            "retry_count": len(state.get("retries", [])),
            "pr_comment_posted": pr_ok,
            "pr_comment_note": pr_msg[:200] if not pr_ok else "",
        }
        if state.get("fixup"):
            result_payload["re_review_of"] = state.get("re_review_of", "")
            result_payload["fixup_range"] = state.get("fixup_range", "")
        artifacts.append(Artifact(
            kind="review_result", value=json.dumps(result_payload, ensure_ascii=False),
            repo=repo,
        ))

        # §4.5 review_per_model: the per-model verdicts (compact).
        per_model_compact = [
            {
                "label": r.get("label"),
                "model": r.get("model"),
                "role": r.get("role"),
                "job_id": r.get("job_id"),
                "status": r.get("status"),
                "content_excerpt": (r.get("content") or "")[:PER_MODEL_CONTENT_EXCERPT],
            }
            for r in state.get("per_model", [])
        ]
        artifacts.append(Artifact(
            kind="review_per_model",
            value=json.dumps(per_model_compact, ensure_ascii=False),
            repo=repo,
        ))

        # §4.5 review_aggregated: the adjudicator's screened report.
        aggregated = state.get("aggregated", "") or ""
        if len(aggregated) > AGGREGATED_ARTIFACT_MAX_BYTES:
            aggregated = aggregated[:AGGREGATED_ARTIFACT_MAX_BYTES] + "\n\n…_(truncated)_"
        artifacts.append(Artifact(
            kind="review_aggregated", value=aggregated, repo=repo,
        ))

        # §4.8 fixup_review: the single adjudicator's per-item adjudication.
        if state.get("fixup"):
            artifacts.append(Artifact(
                kind="fixup_review",
                value=json.dumps({
                    "re_review_of": state.get("re_review_of", ""),
                    "fixup_range": state.get("fixup_range", ""),
                    "items": state.get("fixup_items", []),
                    "unresolved": state.get("fixup_unresolved", []),
                }, ensure_ascii=False),
                repo=repo,
            ))

        # §4.7: one artifact per aggregator retry attempt.
        for rec in state.get("retries", []):
            artifacts.append(Artifact(
                kind="review_retry",
                value=json.dumps(rec, ensure_ascii=False),
                repo=repo,
            ))

        return artifacts

    @staticmethod
    def _summary_content(
        goal_status: GoalStatus, state: dict[str, Any],
        pr_ok: bool, pr_msg: str,
    ) -> str:
        verdict = state.get("verdict") or "NONE"
        parts = [
            f"{goal_status.value}: verdict={verdict}",
            f"preset={state.get('preset','')} sensitivity={state.get('sensitivity','')}",
        ]
        if state.get("fixup"):
            parts.append(f"fixup re-review of goal {state.get('re_review_of','')}")
            parts.append(f"fixup_range={state.get('fixup_range','')} "
                         f"items={len(state.get('fixup_items', []))} "
                         f"unresolved={state.get('fixup_unresolved', [])}")
        else:
            parts.append(f"panel_jobs={len(state.get('panel_job_ids', []))} "
                         f"aggregator={state.get('aggregator_job_id','') or '-'}")
        parts.append(f"retries={len(state.get('retries', []))}")
        if state.get("verdict_reason"):
            parts.append(f"reason: {state['verdict_reason']}")
        parts.append(f"pr_comment: {'posted' if pr_ok else 'failed: ' + pr_msg[:120]}")
        return "\n".join(parts)
