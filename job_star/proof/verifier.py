"""Independent artifact verifier.

The verifier re-checks each Artifact claim against ground truth. It never
trusts the implementor's word — it calls gh, git, re-runs tests, or queries
the witness to confirm the claim is real.

Verification methods by kind:
  pr         — gh pr view <n> --json state,mergedAt (is it real? merged?)
  commit     — git cat-file -e <sha> in the repo (does the commit exist?)
  file       — os.path.exists in the repo worktree (does the file exist?)
  test_pass  — re-run the test command, check exit 0
  witnessed  — query the witness service for the evidence GUID
  command    — re-run the command, check exit 0

The verifier is deliberately conservative: if it can't check something (e.g.
gh isn't installed, the repo doesn't exist), it marks the artifact as
unverified with a note explaining why, rather than trusting the claim.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from ..models import Artifact


@dataclass
class VerificationResult:
    """Result of verifying a list of artifacts."""
    artifacts: list[Artifact] = field(default_factory=list)
    verified_count: int = 0
    failed_count: int = 0
    unverified_count: int = 0  # couldn't check (not same as failed)

    @property
    def total(self) -> int:
        return len(self.artifacts)

    @property
    def has_verified(self) -> bool:
        """True if at least one artifact was independently verified."""
        return self.verified_count > 0

    @property
    def has_failures(self) -> bool:
        """True if at least one claim was checked and found false."""
        return self.failed_count > 0

    def summary(self) -> str:
        """Human-readable summary for inclusion in check-ins."""
        lines = []
        for a in self.artifacts:
            icon = "✅" if a.verified else ("❌" if "false" in a.verification_note.lower() or "not found" in a.verification_note.lower() or "failed" in a.verification_note.lower() else "⚠️")
            note = a.verification_note or "not checked"
            lines.append(f"  {icon} {a.kind}: {a.value[:80]} — {note}")
        header = f"{self.verified_count} verified, {self.failed_count} failed, {self.unverified_count} unverified"
        return header + "\n" + "\n".join(lines)


def _run_sync(args: list[str], cwd: str | None = None, timeout: int = 30) -> tuple[int, str, str]:
    """Run a command synchronously, return (exit_code, stdout, stderr)."""
    try:
        result = subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except FileNotFoundError:
        return -1, "", f"command not found: {args[0]}"
    except subprocess.TimeoutExpired:
        return -2, "", f"timeout after {timeout}s"


def _run_shell(command: str, cwd: str | None = None, timeout: int = 120) -> tuple[int, str, str]:
    """Run a shell command string, return (exit_code, stdout, stderr)."""
    try:
        result = subprocess.run(
            command, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout,
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -2, "", f"timeout after {timeout}s"
    except Exception as e:
        return -1, "", str(e)


def _verify_pr(artifact: Artifact) -> Artifact:
    """Verify a PR claim via gh CLI."""
    url = artifact.value
    # Extract PR number and repo from URL: github.com/owner/repo/pull/123
    import re
    m = re.match(r'https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)', url)
    if not m:
        artifact.verification_note = f"unverifiable: not a GitHub PR URL: {url}"
        return artifact
    owner, repo, pr_num = m.group(1), m.group(2), m.group(3)
    code, out, err = _run_sync(
        ["gh", "pr", "view", pr_num, "--repo", f"{owner}/{repo}",
         "--json", "state,mergedAt,url"],
        timeout=15,
    )
    if code == -1:
        artifact.verification_note = "unverified: gh CLI not available"
        return artifact
    if code != 0:
        artifact.verification_note = f"false: PR #{pr_num} not found or gh error: {err.strip()[:100]}"
        return artifact
    try:
        data = json.loads(out)
        state = data.get("state", "unknown")
        merged = bool(data.get("mergedAt"))
        if merged:
            artifact.verified = True
            artifact.verification_note = f"PR #{pr_num} merged ({state})"
        elif state == "OPEN":
            artifact.verified = True
            artifact.verification_note = f"PR #{pr_num} open (not merged)"
        elif state == "CLOSED":
            artifact.verification_note = f"false: PR #{pr_num} closed (not merged)"
        else:
            artifact.verification_note = f"PR #{pr_num} state={state}"
    except json.JSONDecodeError:
        artifact.verification_note = "unverified: gh returned non-JSON"
    return artifact


def _verify_commit(artifact: Artifact) -> Artifact:
    """Verify a commit exists in the repo."""
    sha = artifact.value
    repo = artifact.repo
    if not repo or not os.path.isdir(repo):
        artifact.verification_note = f"unverified: repo not accessible: {repo}"
        return artifact
    if not os.path.isdir(os.path.join(repo, ".git")):
        artifact.verification_note = f"unverified: not a git repo: {repo}"
        return artifact
    code, out, err = _run_sync(["git", "cat-file", "-e", sha], cwd=repo, timeout=10)
    if code == 0:
        artifact.verified = True
        artifact.verification_note = f"commit {sha[:8]} exists in repo"
    else:
        artifact.verification_note = f"false: commit {sha[:8]} not found in repo"
    return artifact


def _verify_file(artifact: Artifact) -> Artifact:
    """Verify a file exists in the repo worktree."""
    path = artifact.value
    repo = artifact.repo
    if not repo:
        # If no repo, check if it's an absolute path that exists
        if os.path.isabs(path) and os.path.exists(path):
            artifact.verified = True
            artifact.verification_note = f"file exists: {path}"
        else:
            artifact.verification_note = f"unverified: no repo and path not absolute: {path}"
        return artifact
    full = os.path.join(repo, path)
    if os.path.exists(full):
        artifact.verified = True
        artifact.verification_note = f"file exists: {path}"
    else:
        artifact.verification_note = f"false: file not found: {path}"
    return artifact


def _verify_test_pass(artifact: Artifact) -> Artifact:
    """Re-run a test command and check it passes."""
    command = artifact.value
    repo = artifact.repo
    if not repo or not os.path.isdir(repo):
        artifact.verification_note = f"unverified: repo not accessible: {repo}"
        return artifact
    code, out, err = _run_shell(command, cwd=repo, timeout=300)
    if code == 0:
        artifact.verified = True
        artifact.verification_note = f"tests pass: {command}"
    elif code == -2:
        artifact.verification_note = f"unverified: test re-run timed out: {command}"
    else:
        artifact.verification_note = f"false: tests fail (exit {code}): {command}"
    return artifact


def _verify_command(artifact: Artifact) -> Artifact:
    """Re-run a command and check it exits 0."""
    command = artifact.value
    repo = artifact.repo or None
    cwd = repo if (repo and os.path.isdir(repo)) else None
    code, out, err = _run_shell(command, cwd=cwd, timeout=120)
    if code == 0:
        artifact.verified = True
        artifact.verification_note = f"command succeeded: {command}"
    elif code == -2:
        artifact.verification_note = f"unverified: command timed out: {command}"
    else:
        artifact.verification_note = f"false: command failed (exit {code}): {command}"
    return artifact


async def _verify_witnessed(artifact: Artifact, witness_client=None) -> Artifact:
    """Verify a witnessed artifact by looking up the evidence GUID."""
    guid = artifact.value
    if witness_client is None:
        artifact.verification_note = "unverified: no witness client configured"
        return artifact
    try:
        evidence = await witness_client.lookup(guid)
        if evidence is None:
            artifact.verification_note = f"false: evidence {guid} not found in witness store"
            return artifact
        exit_code = evidence.get("exit_code")
        if exit_code == 0:
            artifact.verified = True
            cmd = evidence.get("command", "")
            if isinstance(cmd, list):
                cmd = " ".join(cmd)
            artifact.verification_note = f"witnessed (exit 0): {cmd[:60]}"
        else:
            cmd = evidence.get("command", "")
            if isinstance(cmd, list):
                cmd = " ".join(cmd)
            artifact.verification_note = f"false: witnessed command failed (exit {exit_code}): {cmd[:60]}"
    except Exception as e:
        artifact.verification_note = f"unverified: witness lookup error: {e}"
    return artifact


async def verify_artifact(artifact: Artifact, witness_client=None) -> Artifact:
    """Verify a single artifact claim. Returns a new Artifact with verified set.

    Async because witnessed artifacts require an async witness HTTP lookup.
    Other kinds run synchronously within this coroutine.
    """
    if artifact.kind == "witnessed":
        return await _verify_witnessed(artifact, witness_client)

    sync_verifiers = {
        "pr": _verify_pr,
        "commit": _verify_commit,
        "file": _verify_file,
        "test_pass": _verify_test_pass,
        "command": _verify_command,
    }
    verifier = sync_verifiers.get(artifact.kind)
    if verifier is None:
        artifact.verification_note = f"unverified: unknown artifact kind: {artifact.kind}"
        return artifact
    return verifier(artifact)


async def verify_artifacts(artifacts: list[Artifact], witness_client=None) -> VerificationResult:
    """Verify a list of artifact claims independently.

    Returns a VerificationResult with each artifact's verified flag set and
    counts of verified / failed / unverified.
    """
    verified_list = []
    v_count = f_count = u_count = 0
    for a in artifacts:
        result = await verify_artifact(a, witness_client=witness_client)
        verified_list.append(result)
        if result.verified:
            v_count += 1
        elif result.verification_note.startswith("false"):
            f_count += 1
        else:
            u_count += 1
    return VerificationResult(
        artifacts=verified_list,
        verified_count=v_count,
        failed_count=f_count,
        unverified_count=u_count,
    )
