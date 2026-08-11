"""CI executor: a pure build/test gate. No AI.

Reads ``metadata.ref``, ``metadata.repo`` and ``metadata.command`` from the
goal, clones the repo at the resolved ref into a throwaway worktree under
``/tmp/job-star-ci-worktrees/<sha>``, runs the command exactly once
(``subprocess.run`` timeout 300s), records a ``test_result`` artifact, and sets
the goal status to ``ci_pass`` (exit 0) or ``ci_fail`` (otherwise).

This executor makes no AI call. It is pure subprocess + DB. It performs no file
writes, no branch creation, and no push — it is a read-only gate over an
existing ref.

Contract (docs/development-workflow-specification.md §3):
  1. metadata.ref / metadata.repo / metadata.command. No AI.
  2. git fetch origin <ref> into a throwaway clone at the resolved SHA;
     work dir /tmp/job-star-ci-worktrees/<sha>; remove on exit.
  3. subprocess.run(timeout=300). Single run. No retry, no writes, no branch,
     no push.
  4. artifact kind="test_result" carrying {ref, command, exit_code, duration_s,
     output_tail (last 4 KB of stdout+stderr)}.
  5. goal status ci_pass if exit 0, else ci_fail.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any, Optional

from ..models import Artifact, ExecutionResult, Goal, GoalStatus, Step
from . import Executor

WORKTREE_ROOT = "/tmp/job-star-ci-worktrees"
COMMAND_TIMEOUT_S = 300
OUTPUT_TAIL_BYTES = 4096
# Exit code used when the command timed out (the conventional Unix timeout
# exit, and a clear non-zero for the ci_fail path).
TIMEOUT_EXIT_CODE = 124
# Exit code used when the gate could not run at all (bad metadata, ref not
# resolvable, clone/checkout failure, unexpected exception). Still ci_fail,
# but distinguished from a real command exit code.
SETUP_FAILURE_EXIT_CODE = -1

_HEXADECIMAL = set("0123456789abcdefABCDEF")


def _to_git_url(repo: str) -> Optional[str]:
    """Normalize a metadata.repo value into a fetchable git URL.

    The deploy gate identifies a repo by its "owner/name" slug (shortest form),
    but git ls-remote/clone require a URL. Accept either form and return a URL:
      - "owner/name"        -> "https://github.com/owner/name"
      - "https://github.com/owner/name" or ".git"-suffixed variant -> unchanged
    Returns None when the value is neither (not usable by git).
    """
    if not repo:
        return None
    r = repo.strip()
    if not r:
        return None
    # Already a URL (https, ssh, git).
    if "://" in r or r.startswith("git@"):
        return r
    # owner/name slug. Reject a bare name (no slash) so a typo doesn't silently
    # become https://github.com/singleword.
    if "/" in r and not r.startswith("/") and not r.endswith("/"):
        return f"https://github.com/{r}"
    return None


class CIExecutor(Executor):
    """Build/test gate executor. No AI; pure subprocess + DB."""

    name = "ci"
    description = "CI build/test gate (no AI): clone ref, run command, record result"

    async def execute(
        self,
        goal: Goal,
        step: Step,
        context: dict | None = None,
        model_override: str | None = None,
    ) -> ExecutionResult:
        meta = goal.metadata or {}
        ref: Optional[str] = meta.get("ref")
        repo: Optional[str] = meta.get("repo")
        command: Optional[str] = meta.get("command")
        # The deploy gate identifies a repo by its "owner/name" slug (metadata.repo),
        # but git ls-remote/clone need a fetchable URL. Normalize a slug to a GitHub
        # URL for git ops; raise a clear error when the value is neither a slug nor
        # a URL we can derive one from.
        repo_url = _to_git_url(repo)

        if not ref or not repo or not command:
            missing = ", ".join(
                k for k in ("ref", "repo", "command") if not meta.get(k)
            )
            return await self._finish(
                goal,
                success=False,
                error=f"ci: missing metadata.{missing}",
                ref=ref or "",
                command=command or "",
                exit_code=SETUP_FAILURE_EXIT_CODE,
                duration_s=0.0,
                output_tail=f"metadata missing: {missing}",
                repo=repo or "",
            )
        if not repo_url:
            return await self._finish(
                goal,
                success=False,
                error=f"ci: metadata.repo '{repo}' is neither an owner/name slug nor a git URL",
                ref=ref,
                command=command,
                exit_code=SETUP_FAILURE_EXIT_CODE,
                duration_s=0.0,
                output_tail=f"unusable repo: {repo}",
                repo=repo,
            )

        work_dir = ""
        try:
            sha = self._resolve_ref(repo_url, ref)
            if not sha:
                msg = f"ci: could not resolve ref '{ref}' in {repo}"
                return await self._finish(
                    goal, success=False, error=msg,
                    ref=ref, command=command,
                    exit_code=SETUP_FAILURE_EXIT_CODE, duration_s=0.0,
                    output_tail=msg, repo=repo,
                )

            work_dir = os.path.join(WORKTREE_ROOT, sha)
            ok, err = self._prepare_worktree(repo_url, ref, sha, work_dir)
            if not ok:
                return await self._finish(
                    goal, success=False, error=err,
                    ref=ref, command=command,
                    exit_code=SETUP_FAILURE_EXIT_CODE, duration_s=0.0,
                    output_tail=err, repo=repo,
                )

            exit_code, duration_s, output_tail = self._run_command(work_dir, command)
            return await self._finish(
                goal,
                success=(exit_code == 0),
                ref=ref, command=command, exit_code=exit_code,
                duration_s=duration_s, output_tail=output_tail, repo=repo,
            )
        except Exception as exc:  # never let the gate crash the worker
            msg = f"ci: executor error: {exc}"
            return await self._finish(
                goal, success=False, error=msg,
                ref=ref or "", command=command or "",
                exit_code=SETUP_FAILURE_EXIT_CODE, duration_s=0.0,
                output_tail=msg, repo=repo or "",
            )
        finally:
            if work_dir and os.path.exists(work_dir):
                shutil.rmtree(work_dir, ignore_errors=True)

    # ------------------------------------------------------------------ git

    def _git(self, args: list[str], cwd: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def _resolve_ref(self, repo: str, ref: str) -> Optional[str]:
        """Resolve a ref to a SHA via `git ls-remote` (no clone needed)."""
        result = self._git(["ls-remote", repo, ref], cwd=os.getcwd())
        if result.returncode != 0:
            return None
        lines = result.stdout.strip().splitlines()
        if not lines:
            return None
        sha = lines[0].split("\t", 1)[0].strip()
        if not sha or not set(sha) <= _HEXADECIMAL:
            return None
        return sha

    def _prepare_worktree(
        self, repo: str, ref: str, sha: str, work_dir: str,
    ) -> tuple[bool, str]:
        """Clone the repo into <work_dir> and check out the resolved SHA.

        A bare clone is then fetched and the SHA checked out (detached HEAD).
        No branch is created. Returns (ok, error).
        """
        os.makedirs(WORKTREE_ROOT, exist_ok=True)
        if os.path.exists(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)

        clone = self._git(["clone", "--no-checkout", repo, work_dir], cwd=os.getcwd())
        if clone.returncode != 0:
            return False, f"ci: clone failed: {clone.stderr.strip() or clone.stdout.strip()}"

        # Fetch the specific ref so we have the exact objects for the SHA even
        # if the default clone branch was different. A fetch failure is not
        # fatal — the ref may already be present from the clone.
        fetch = self._git(["fetch", "origin", ref], work_dir)
        if fetch.returncode != 0:
            # Best-effort: continue to checkout; it will fail below if the SHA
            # is genuinely absent.
            pass

        checkout = self._git(["checkout", "--detach", sha], work_dir)
        if checkout.returncode != 0:
            return False, f"ci: checkout {sha} failed: {checkout.stderr.strip() or checkout.stdout.strip()}"

        return True, ""

    # -------------------------------------------------------------- command

    def _run_command(
        self, work_dir: str, command: str,
    ) -> tuple[int, float, str]:
        """Run the command once. Returns (exit_code, duration_s, output_tail).

        output_tail is the last 4 KB of stdout+stderr. On timeout the
        conventional timeout exit code (124) is returned with whatever output
        was captured before the kill.
        """
        start = time.time()
        exit_code: int
        output: str
        try:
            proc = subprocess.run(
                command,
                cwd=work_dir,
                shell=True,
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT_S,
            )
            exit_code = proc.returncode
            output = (proc.stdout or "") + (proc.stderr or "")
        except subprocess.TimeoutExpired as exc:
            exit_code = TIMEOUT_EXIT_CODE
            out = exc.stdout or ""
            err = exc.stderr or ""
            if isinstance(out, bytes):
                out = out.decode("utf-8", "replace")
            if isinstance(err, bytes):
                err = err.decode("utf-8", "replace")
            output = (out + err) + f"\n[ci: timed out after {COMMAND_TIMEOUT_S}s]"
        duration_s = time.time() - start
        output_tail = self._tail(output, OUTPUT_TAIL_BYTES)
        return exit_code, duration_s, output_tail

    @staticmethod
    def _tail(text: str, n_bytes: int) -> str:
        """Return the last n_bytes of text, decoded safely from UTF-8."""
        if not text:
            return ""
        tail = text.encode("utf-8", "replace")[-n_bytes:]
        return tail.decode("utf-8", "replace")

    # -------------------------------------------------------------- finalize

    async def _finish(
        self,
        goal: Goal,
        success: bool,
        error: Optional[str] = None,
        ref: str = "",
        command: str = "",
        exit_code: int = SETUP_FAILURE_EXIT_CODE,
        duration_s: float = 0.0,
        output_tail: str = "",
        repo: str = "",
    ) -> ExecutionResult:
        """Record the test_result artifact, set goal status, return result."""
        payload: dict[str, Any] = {
            "ref": ref,
            "command": command,
            "exit_code": exit_code,
            "duration_s": round(duration_s, 6),
            "output_tail": output_tail,
        }
        artifact = Artifact(
            kind="test_result",
            value=json.dumps(payload, ensure_ascii=False),
            repo=repo,
        )

        new_status = GoalStatus.CI_PASS if success else GoalStatus.CI_FAIL
        # Reflect the outcome on the in-memory goal the orchestrator holds...
        goal.status = new_status
        # ...and persist it. Point 1 sanctions DB access; this is the only
        # write the executor performs. Persisting is best-effort: a DB error
        # must not mask the gate result.
        try:
            from ..db import update_goal_status
            await update_goal_status(goal.id, new_status)
        except Exception:
            # The gate result (artifact + returned ExecutionResult) is the
            # source of truth; status persistence is secondary.
            pass

        content = (
            f"ci_pass: exit {exit_code} in {duration_s:.2f}s"
            if success
            else f"ci_fail: exit {exit_code} in {duration_s:.2f}s"
        )
        # The orchestrator logs `result.error[:80]` on its failure path, so a
        # ci_fail must carry a non-None error string.
        if not success and error is None:
            error = content
        return ExecutionResult(
            content=content,
            model="ci",  # not a real model — keeps the orchestrator's
                         # fallback picker from retrying (no fallback for "ci")
            success=success,
            error=None if success else error,
            artifacts=[artifact],
        )