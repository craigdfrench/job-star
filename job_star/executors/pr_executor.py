"""PR-based executor: writes code to a git branch, runs tests, creates a PR.

This is the real execution layer for code-generation goals. Instead of storing
AI-generated code in Postgres, it:
  1. Parses AI output for file blocks
  2. Writes files to the repo working tree (supervisor checks paths)
  3. Runs the test command (e.g. 'go test ./...')
  4. If tests fail, feeds the failure back to the AI and retries
  5. When tests pass (or budget exhausted), commits, pushes, creates a PR
  6. Stores {pr_url, branch, commit_sha, files, test_output} in the step result

The test suite is the ground truth. The PR is the artifact. The DB tracks linkage.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..models import ExecutionResult, Goal, Step, Artifact
from ..router import route
from ..gatehouse import execute as execute_ai
from ..gatehouse import GatewayMonitor
from .default import DefaultExecutor


@dataclass
class FileChange:
    """A file change parsed from AI output."""
    path: str
    content: str
    action: str = "create"  # create, modify, delete


@dataclass
class TestResult:
    """Result of running the test command."""
    passed: bool
    output: str
    exit_code: int
    duration_s: float = 0.0


def parse_file_blocks(content: str) -> list[FileChange]:
    """Parse AI output for file blocks.

    Recognizes:
      ## File: path/to/file.go
      ```language
      content
      ```

    Robust to:
      - Explanatory text before the first file block
      - 2 or 3 backtick code fences
      - Missing language tag
      - File: `path` or ## File: path
    """
    changes: list[FileChange] = []

    # Strip any thinking/explanation before the first file block
    if "## File:" in content:
        first = content.find("## File:")
        content = content[first:]

    # Match header then a fenced code block (2 or 3 backticks, optional language)
    pattern = r'(?:##\s*)?File:\s*`?([^\n`]+?)`?\s*\n+(``|```)\w*\n(.*?)\n\2'
    for match in re.finditer(pattern, content, re.DOTALL):
        path = match.group(1).strip()
        file_content = match.group(3)
        # Strip trailing newline
        if file_content.endswith('\n'):
            file_content = file_content[:-1]
        changes.append(FileChange(path=path, content=file_content))

    return changes


def parse_delete_blocks(content: str) -> list[FileChange]:
    """Parse AI output for file deletion directives."""
    deletions: list[FileChange] = []
    pattern = r'(?:##\s*)?Delete:\s*`?([^\n`]+)`?'
    for match in re.finditer(pattern, content):
        deletions.append(FileChange(path=match.group(1).strip(), content="", action="delete"))
    return deletions


class PRExecutor(DefaultExecutor):
    """PR-based executor with test-run-iterate loop.

    Writes AI-generated code to a git branch, runs tests, feeds failures back,
    and creates a PR when tests pass (or budget exhausted).
    """

    name = "default"  # overridden by subclass or instance
    description = "PR-based executor with test feedback loop"

    def __init__(
        self,
        gateway_monitor: GatewayMonitor | None = None,
        repo_path: str | None = None,
        test_command: str | None = None,
        base_branch: str = "main",
        max_test_retries: int = 3,
        worktree_dir: str | None = None,
    ):
        super().__init__(gateway_monitor)
        self.repo_path = repo_path
        self.test_command = test_command
        self.base_branch = base_branch
        self.max_test_retries = max_test_retries
        # Directory for isolated git worktrees (default: /tmp/job-star-worktrees)
        self.worktree_dir = worktree_dir or "/tmp/job-star-worktrees"
        # Active worktree path for the current execution (set by _ensure_branch)
        self._active_worktree: str | None = None

    def _branch_name(self, goal: Goal) -> str:
        """Generate a branch name for a goal."""
        slug = re.sub(r'[^a-z0-9-]', '-', goal.title.lower())[:40].strip('-')
        return f"job-star/{goal.id[:8]}-{slug}"

    def _git(self, args: list[str], cwd: str) -> tuple[int, str, str]:
        """Run a git command in the repo."""
        result = subprocess.run(
            ["git"] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode, result.stdout, result.stderr

    def _ensure_branch(self, repo_path: str, branch: str) -> tuple[bool, str]:
        """Create an isolated checkout for the branch.

        Uses a `git clone` of the repo path into a temp directory, then checks
        out the requested branch from the origin. This is more robust than
        `git worktree add` because the resulting directory is a normal git repo
        with its own .git directory, and it doesn't leave stale worktree
        metadata behind.

        Returns (ok, message). On success, sets self._active_worktree to the
        checkout path.
        """
        import shutil

        os.makedirs(self.worktree_dir, exist_ok=True)
        worktree_path = os.path.join(self.worktree_dir, branch.replace("/", "_"))

        # Always use a fresh clone. Cloning is fast over the local filesystem and
        # avoids the fragility of git worktree metadata (which can lose the .git
        # pointer if the worktree directory is removed outside git).
        if os.path.exists(worktree_path):
            shutil.rmtree(worktree_path)

        # Clone the repo into the worktree path
        code, _, err = self._git(
            ["clone", "-b", self.base_branch, "--single-branch", repo_path, worktree_path],
            repo_path,
        )
        if code != 0:
            return False, f"failed to clone repo: {err}"

        # Ensure it's a valid git repo
        git_dir = os.path.join(worktree_path, ".git")
        if not os.path.isdir(git_dir):
            return False, f"clone created at {worktree_path} but .git directory is missing"

        # Create or switch the target branch
        # Ensure the remote points to the actual GitHub URL so gh pr create works.
        # The local clone inherited origin = repo_path, which is not a GitHub host.
        code, remote_url, _ = self._git(["remote", "get-url", "origin"], repo_path)
        if code == 0 and remote_url.strip():
            self._git(["remote", "set-url", "origin", remote_url.strip()], worktree_path)

        # Create or switch the target branch
        code, out, err = self._git(["checkout", "-B", branch, f"origin/{self.base_branch}"], worktree_path)
        if code != 0:
            return False, f"failed to create branch in checkout: {err}"

        self._active_worktree = worktree_path
        return True, f"created checkout {worktree_path}"

    def _cleanup_worktree(self, repo_path: str) -> None:
        """Remove the active worktree after execution."""
        if not self._active_worktree:
            return
        wt = self._active_worktree
        self._active_worktree = None
        # Remove the worktree
        self._git(["worktree", "remove", "--force", wt], repo_path)
        # Prune worktree metadata
        self._git(["worktree", "prune"], repo_path)

    def _write_files(self, repo_path: str, changes: list[FileChange]) -> list[str]:
        """Write file changes to the working tree. Returns list of written paths."""
        written: list[str] = []
        for change in changes:
            full_path = os.path.join(repo_path, change.path)
            # Security: ensure path is within repo
            real = os.path.realpath(full_path)
            repo_real = os.path.realpath(repo_path)
            if not real.startswith(repo_real):
                continue  # supervisor would flag this

            if change.action == "delete":
                if os.path.exists(full_path):
                    os.remove(full_path)
                written.append(change.path)
            else:
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w') as f:
                    f.write(change.content)
                written.append(change.path)
        return written

    def _run_tests(self, repo_path: str, test_command: str) -> TestResult:
        """Run the test command and capture output."""
        import time
        start = time.time()
        # Parse the command (support shell operators)
        result = subprocess.run(
            test_command,
            cwd=repo_path,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,  # 5 min max for tests
        )
        duration = time.time() - start
        output = result.stdout + result.stderr
        passed = result.returncode == 0
        return TestResult(
            passed=passed,
            output=output[-4000:] if len(output) > 4000 else output,  # truncate
            exit_code=result.returncode,
            duration_s=duration,
        )

    def _commit_and_push(self, repo_path: str, branch: str, message: str, files: list[str]) -> tuple[bool, str]:
        """Stage, commit, and push files. Returns (ok, message)."""
        # Stage specific files
        if files:
            self._git(["add"] + files, repo_path)
        else:
            self._git(["add", "-A"], repo_path)

        # Check if there's anything to commit
        code, out, _ = self._git(["diff", "--cached", "--quiet"], repo_path)
        if code == 0:
            return True, "no changes to commit"

        code, out, err = self._git(["commit", "-m", message], repo_path)
        if code != 0:
            return False, f"commit failed: {err}"

        code, out, err = self._git(["push", "-u", "origin", branch], repo_path)
        if code != 0:
            # Remote branch may be behind/diverged because the worktree is a
            # fresh clone and the previous push was from an earlier state. Fetch
            # and then force push the feature branch to overwrite the remote.
            self._git(["fetch", "origin", branch], repo_path)
            code, out, err = self._git(["push", "--force", "-u", "origin", branch], repo_path)
            if code != 0:
                return False, f"push failed: {err}"

        return True, "committed and pushed"

    def _create_pr(self, repo_path: str, goal: Goal, branch: str, test_result: TestResult) -> tuple[bool, str]:
        """Create a PR via gh CLI. Returns (ok, pr_url)."""
        title = f"[job-star] {goal.title}"
        body = f"""## Goal
{goal.title}

{goal.description or ''}

**Goal ID:** {goal.id}
**Expert:** {goal.expert or 'default'}
**Branch:** `{branch}`

## Test Results
{'✅ Tests passing' if test_result.passed else '❌ Tests failing (exit code ' + str(test_result.exit_code) + ')'}

```
{test_result.output[:2000]}
```

---
_Generated by job-star. Review the approach before merging._
"""
        result = subprocess.run(
            ["gh", "pr", "create",
             "--title", title,
             "--body", body,
             "--base", self.base_branch,
             "--head", branch],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            # PR might already exist — try to get its URL
            r2 = subprocess.run(
                ["gh", "pr", "view", branch, "--json", "url"],
                cwd=repo_path, capture_output=True, text=True, timeout=15,
            )
            if r2.returncode == 0:
                try:
                    return True, json.loads(r2.stdout)["url"]
                except (json.JSONDecodeError, KeyError):
                    pass
            return False, f"gh pr create failed: {result.stderr}"
        return True, result.stdout.strip()

    async def execute(
        self,
        goal: Goal,
        step: Step,
        context: dict | None = None,
        model_override: str | None = None,
    ) -> ExecutionResult:
        """Execute a step with the test-run-iterate loop."""
        if not self.repo_path or not self.test_command:
            # No repo configured — fall back to text-only execution
            return await super().execute(goal, step, context, model_override)

        context = context or {}
        prev_context = context.get("prev_context", "")
        repo_path = self.repo_path
        branch = self._branch_name(goal)

        # Ensure we're on the right branch (in an isolated worktree)
        ok, msg = self._ensure_branch(repo_path, branch)
        if not ok:
            return ExecutionResult(success=False, error=msg, model="none")

        # Work in the isolated worktree, not the user's primary checkout
        work_dir = self._active_worktree or repo_path

        try:
            return await self._execute_in_worktree(
                goal, step, work_dir, branch, prev_context, model_override,
            )
        finally:
            # Always clean up the worktree
            self._cleanup_worktree(repo_path)

    async def _execute_in_worktree(
        self,
        goal: Goal,
        step: Step,
        work_dir: str,
        branch: str,
        prev_context: str,
        model_override: str | None,
    ) -> ExecutionResult:
        """Run the test-iterate loop in the isolated worktree."""
        allow_expensive = bool(model_override)
        # Code-editing tasks need a model that can follow the file-block format.
        # Use request_type="code" so the router picks a capable cheap/quota-free model.
        routing = await route(
            urgency=goal.urgency,
            request_type="code",
            description=step.description or step.title,
            model_override=model_override,
            allow_expensive=allow_expensive,
            gateway_monitor=self.gateway_monitor,
        )
        if not routing.model:
            return ExecutionResult(success=False, error=f"No model: {routing.reason}", model="none")

        # Detect review/analysis steps (no file changes expected)
        review_keywords = ("review", "inspect", "scan", "verify", "check ", "audit", "analyze")
        is_review_step = any(kw in step.title.lower() for kw in review_keywords)
        if is_review_step:
            return await self._execute_review_step(
                goal, step, work_dir, branch, routing, model_override, prev_context
            )

        # Test-run-iterate loop
        test_feedback = ""
        all_written_files: list[str] = []
        last_test_result: TestResult | None = None
        result = None
        push_error: str | None = None

        for attempt in range(self.max_test_retries):
            # Build the prompt with test feedback from previous attempt
            feedback_block = ""
            if test_feedback:
                feedback_block = f"""

## Previous attempt failed tests

Your last change produced these test failures:

```
{test_feedback}
```

Fix the code so the tests pass. Generate the corrected file(s)."""

            system = self._system_prompt()
            user = f"""You are editing files in a real git repository. Generate the code changes for this step. Do not explain. Do not use <read_file> tags or tool-call XML. Only output file blocks in the exact format shown below.

Goal: {goal.title}
{goal.description or ''}
{prev_context}

Current Step: {step.title}
{step.description or ''}

Repo: {self.repo_path}
Base branch: {self.base_branch}
Test command: {self.test_command}

Use this format for each file:

## File: path/to/file.go
```language
<full file content>
```

To delete a file: ## Delete: path/to/file{feedback_block}

Output ONLY file blocks."""

            # Use a generous max_tokens for code generation: the default 4096
            # truncates multi-file Go output mid-file, so the closing code fence
            # never appears and parse_file_blocks finds no complete block (the
            # "No file changes were generated" failure). 16384 fits large files.
            result = await execute_ai(user, model=routing.model, system_prompt=system, max_tokens=16384)
            if not result.success:
                self.gateway_monitor.record_failure(routing.model, result.error or "error")
                return result
            self.gateway_monitor.record_success(
                routing.model, result.input_tokens + result.output_tokens,
                x_gatehouse=result.x_gatehouse,
            )

            # Parse file changes from AI output
            changes = parse_file_blocks(result.content) + parse_delete_blocks(result.content)
            if not changes:
                return ExecutionResult(
                    content=result.content,
                    model=result.model,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    success=False,
                    error="No file changes were generated. The model output did not contain any '## File:' blocks. Output sample:\n" + result.content[:500],
                    x_gatehouse=result.x_gatehouse,
                )

            # Write files to working tree (the worktree, not the user's checkout)
            written = self._write_files(work_dir, changes)
            all_written_files.extend(written)

            # Run tests in the worktree
            last_test_result = self._run_tests(work_dir, self.test_command)

            if last_test_result.passed:
                # Tests pass — commit. Push and PR are best-effort; if the
                # remote doesn't exist or the token is missing, the step is
                # still successful because the code is committed locally.
                ok, commit_msg = self._commit_and_push(
                    work_dir, branch,
                    f"job-star: {step.title} (step {step.order_index})",
                    written,
                )
                if not ok:
                    push_error = commit_msg
                    pr_ok = False
                    pr_url = ""
                else:
                    pr_ok, pr_url = self._create_pr(work_dir, goal, branch, last_test_result)
                    if not pr_ok:
                        push_error = pr_url

                success_msg = "Tests passed. Files committed."
                if pr_ok:
                    success_msg += f"\nPR: {pr_url}"
                elif push_error:
                    success_msg += f"\nPush/PR warning: {push_error}"
                success_msg += f"\nFiles: {', '.join(written)}\n\n{result.content[:800]}"

                # Declare proof-of-work artifacts for the verifier to re-check.
                # The implementor only declares what it actually did — the
                # verifier independently confirms each claim.
                artifacts: list[Artifact] = []
                if pr_ok and pr_url:
                    artifacts.append(Artifact(kind="pr", value=pr_url, repo=self.repo_path))
                for fpath in written:
                    artifacts.append(Artifact(kind="file", value=fpath, repo=self.repo_path))
                if last_test_result and last_test_result.passed:
                    artifacts.append(Artifact(
                        kind="test_pass", value=self.test_command, repo=self.repo_path,
                    ))
                # Capture the commit SHA for commit-kind verification
                code, sha_out, _ = self._git(["rev-parse", "HEAD"], work_dir)
                if code == 0 and sha_out.strip():
                    artifacts.append(Artifact(
                        kind="commit", value=sha_out.strip(), repo=self.repo_path,
                    ))

                return ExecutionResult(
                    content=success_msg,
                    model=result.model,
                    input_tokens=result.input_tokens,
                    output_tokens=result.output_tokens,
                    success=True,
                    x_gatehouse=result.x_gatehouse,
                    artifacts=artifacts,
                )
            else:
                # Tests failed — feed back for next iteration
                test_feedback = last_test_result.output
                continue

        # Budget exhausted — create PR with failing tests for human review, but
        # mark the step as failed so the orchestrator does not treat it as done.
        if last_test_result and all_written_files:
            ok, commit_msg = self._commit_and_push(
                work_dir, branch,
                f"job-star: {step.title} (tests failing, needs review)",
                all_written_files,
            )
            pr_url = ""
            if ok:
                ok, pr_url = self._create_pr(work_dir, goal, branch, last_test_result)
            return ExecutionResult(
                success=False,
                error=f"Tests still failing after {self.max_test_retries} attempts. PR created with failing tests: {pr_url}\n\nLast test output:\n{last_test_result.output[:1000]}",
                model=routing.model,
                x_gatehouse=result.x_gatehouse if result else {},
            )

        return ExecutionResult(
            success=False,
            error=f"Tests failed after {self.max_test_retries} attempts:\n{last_test_result.output[:500] if last_test_result else 'no output'}",
            model=routing.model,
        )


    async def _execute_review_step(
        self,
        goal: Goal,
        step: Step,
        work_dir: str,
        branch: str,
        routing: RoutingDecision,
        model_override: str | None,
        prev_context: str,
    ) -> ExecutionResult:
        """Execute a review/analysis step. Produces a text review, no file changes."""
        system = (
            "You are Job-Star, reviewing code in a git repository. "
            "Analyze the codebase and produce a clear, structured review. "
            "Do NOT edit files. Output your findings as plain text. "
            "Cover: what the code does, any issues, risks, and recommendations."
        )
        user = f"""Goal: {goal.title}
{goal.description or ''}
{prev_context}

Current Step: {step.title}
{step.description or ''}

Repo: {self.repo_path}
Base branch: {self.base_branch}

Review the relevant code for this step. Report your findings as structured text.
Do not output file blocks. Do not edit files. Just analyze and report."""

        result = await execute_ai(user, model=routing.model, system_prompt=system)
        if not result.success:
            self.gateway_monitor.record_failure(routing.model, result.error or "error")
            return result
        self.gateway_monitor.record_success(
            routing.model, result.input_tokens + result.output_tokens,
            x_gatehouse=result.x_gatehouse,
        )
        # Review steps succeed with a text response — no file changes needed.
        return ExecutionResult(
            content=f"Review complete.\n\n{result.content}",
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            success=True,
            x_gatehouse=result.x_gatehouse,
        )

    def _system_prompt(self) -> str:
        """System prompt for the PR executor."""
        return f"""You are Job-Star, working on a code change in a git repository.

You will generate code changes, and they will be written to the repo and tested.
If tests fail, you'll see the failure output and must fix it.

Rules:
- Output file changes in this exact format:

## File: path/to/file.ext
```language
<full file content>
```

- Include the FULL file content, not just the diff
- Use real paths relative to the repo root: {self.repo_path}
- To delete a file: ## Delete: path/to/file
- Be consistent with the existing code style and structure
- Make sure your changes will pass the test command: {self.test_command}

This is a supervised system. Your changes will be tested before creating a PR."""
