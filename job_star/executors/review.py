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
    "per-model verdicts and web verification into a consensus + dissent report.\n\n"
    "Structure your output as:\n"
    "1. **Camp breakdown** — which models agreed, which disagreed, which were "
    "partial. Use a table.\n"
    "2. **Key objections** — the strongest objections raised, with which models "
    "raised them.\n"
    "3. **Web verification** — what the verifier confirmed or refuted, with "
    "citations. Which claims are now factually settled vs. still open.\n"
    "4. **Resolved vs. standing** — which objections were resolved by evidence or "
    "verification, which still stand.\n"
    "5. **Overall verdict** — is the hypothesis well-supported enough to act on, "
    "or does it need more proof? Be honest about residual uncertainty.\n\n"
    "Do NOT show your reasoning process. Output only the final report.\n\n"
    "Findings under review:\n{findings}\n\n"
    "Per-model verdicts:\n{verdicts}"
)

AGGREGATOR_PROMPT_PRIVATE = (
    "You are the aggregator for an intense multi-model review. Synthesize the "
    "per-model verdicts into a consensus + dissent report.\n\n"
    "Structure your output as:\n"
    "1. **Camp breakdown** — which models agreed, which disagreed, which were "
    "partial. Use a table.\n"
    "2. **Key objections** — the strongest objections raised, with which models "
    "raised them.\n"
    "3. **Resolved vs. standing** — which objections were resolved by evidence, "
    "which still stand.\n"
    "4. **Overall verdict** — is the hypothesis well-supported enough to act on, "
    "or does it need more proof? Be honest about residual uncertainty.\n\n"
    "Do NOT show your reasoning process. Output only the final report.\n\n"
    "Findings under review:\n{findings}\n\n"
    "Per-model verdicts:\n{verdicts}"
)

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
                state["aggregated"] = content
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
    ) -> tuple[bool, str]:
        """Post the adjudicator-screened report + verdict to the PR thread.

        Outbound only — gh carries its own auth. Best-effort: a comment failure
        is recorded but does not change the gate outcome.
        """
        if not pr_number:
            return False, "no PR number"
        body = self._build_pr_comment(verdict, reason, aggregated)
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
    ) -> str:
        if verdict == "PASS":
            head = "## Review Gate: VERDICT: PASS"
        elif verdict == "BLOCK":
            head = f"## Review Gate: VERDICT: BLOCK\n\n**Blocking concerns:** {reason or 'see report below'}"
        else:
            head = "## Review Gate: VERDICT: ERROR (no parseable verdict)"

        body = aggregated or "_No aggregated report was produced._"
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
            "retry_count": len(state.get("retries", [])),
            "pr_comment_posted": pr_ok,
            "pr_comment_note": pr_msg[:200] if not pr_ok else "",
        }
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
            f"panel_jobs={len(state.get('panel_job_ids', []))} "
            f"aggregator={state.get('aggregator_job_id','') or '-'}",
            f"retries={len(state.get('retries', []))}",
        ]
        if state.get("verdict_reason"):
            parts.append(f"reason: {state['verdict_reason']}")
        parts.append(f"pr_comment: {'posted' if pr_ok else 'failed: ' + pr_msg[:120]}")
        return "\n".join(parts)
