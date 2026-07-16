"""Tests for the proof-of-work verifier.

The verifier independently re-checks artifact claims against ground truth.
These tests mock the subprocess calls (gh, git, test commands) so they don't
depend on real repos or PRs.
"""

from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from job_star.models import Artifact
from job_star.proof.verifier import (
    verify_artifact,
    verify_artifacts,
    VerificationResult,
)


class TestVerifyPR:
    """Tests for PR artifact verification via gh CLI."""

    async def test_verified_merged_pr(self):
        """A merged PR should verify as True."""
        artifact = Artifact(kind="pr", value="https://github.com/owner/repo/pull/123")
        gh_output = json.dumps({"state": "MERGED", "mergedAt": "2026-07-15T20:00:00Z", "url": "..."})
        with patch("job_star.proof.verifier._run_sync", return_value=(0, gh_output, "")):
            result = await verify_artifact(artifact)
        assert result.verified is True
        assert "merged" in result.verification_note.lower()

    async def test_open_pr_verifies_but_not_merged(self):
        """An open PR verifies as True (it exists) but note says not merged."""
        artifact = Artifact(kind="pr", value="https://github.com/owner/repo/pull/456")
        gh_output = json.dumps({"state": "OPEN", "mergedAt": None, "url": "..."})
        with patch("job_star.proof.verifier._run_sync", return_value=(0, gh_output, "")):
            result = await verify_artifact(artifact)
        assert result.verified is True
        assert "open" in result.verification_note.lower()

    async def test_closed_pr_fails_verification(self):
        """A closed (not merged) PR should fail verification."""
        artifact = Artifact(kind="pr", value="https://github.com/owner/repo/pull/789")
        gh_output = json.dumps({"state": "CLOSED", "mergedAt": None, "url": "..."})
        with patch("job_star.proof.verifier._run_sync", return_value=(0, gh_output, "")):
            result = await verify_artifact(artifact)
        assert result.verified is False
        assert result.verification_note.startswith("false")

    async def test_pr_not_found(self):
        """A non-existent PR should fail verification."""
        artifact = Artifact(kind="pr", value="https://github.com/owner/repo/pull/999")
        with patch("job_star.proof.verifier._run_sync", return_value=(1, "", "not found")):
            result = await verify_artifact(artifact)
        assert result.verified is False
        assert result.verification_note.startswith("false")

    async def test_gh_not_installed(self):
        """If gh CLI is not available, the artifact is unverified (not failed)."""
        artifact = Artifact(kind="pr", value="https://github.com/owner/repo/pull/123")
        with patch("job_star.proof.verifier._run_sync", return_value=(-1, "", "command not found: gh")):
            result = await verify_artifact(artifact)
        assert result.verified is False
        assert result.verification_note.startswith("unverified")

    async def test_invalid_pr_url(self):
        """A non-GitHub-PR URL should be unverifiable."""
        artifact = Artifact(kind="pr", value="https://example.com/something")
        result = await verify_artifact(artifact)
        assert result.verified is False
        assert "unverifiable" in result.verification_note


class TestVerifyCommit:
    """Tests for commit artifact verification via git."""

    async def test_commit_exists(self, tmp_path):
        """A commit that exists in the repo should verify."""
        repo = str(tmp_path)
        (tmp_path / ".git").mkdir()
        artifact = Artifact(kind="commit", value="abc123def456", repo=repo)
        with patch("job_star.proof.verifier._run_sync", return_value=(0, "", "")):
            result = await verify_artifact(artifact)
        assert result.verified is True
        assert "exists" in result.verification_note

    async def test_commit_not_found(self, tmp_path):
        """A commit that doesn't exist should fail."""
        repo = str(tmp_path)
        (tmp_path / ".git").mkdir()
        artifact = Artifact(kind="commit", value="nonexistent", repo=repo)
        with patch("job_star.proof.verifier._run_sync", return_value=(1, "", "error")):
            result = await verify_artifact(artifact)
        assert result.verified is False
        assert result.verification_note.startswith("false")

    async def test_commit_no_repo(self):
        """A commit with no repo should be unverified."""
        artifact = Artifact(kind="commit", value="abc123", repo="")
        result = await verify_artifact(artifact)
        assert result.verified is False
        assert "unverified" in result.verification_note


class TestVerifyFile:
    """Tests for file artifact verification."""

    async def test_file_exists_in_repo(self, tmp_path):
        """A file that exists in the repo should verify."""
        repo = str(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print('hello')")
        artifact = Artifact(kind="file", value="src/main.py", repo=repo)
        result = await verify_artifact(artifact)
        assert result.verified is True
        assert "exists" in result.verification_note

    async def test_file_not_found_in_repo(self, tmp_path):
        """A file that doesn't exist should fail."""
        repo = str(tmp_path)
        artifact = Artifact(kind="file", value="missing.py", repo=repo)
        result = await verify_artifact(artifact)
        assert result.verified is False
        assert result.verification_note.startswith("false")

    async def test_absolute_path_exists(self, tmp_path):
        """An absolute path that exists (no repo) should verify."""
        filepath = str(tmp_path / "config.json")
        with open(filepath, "w") as f:
            f.write("{}")
        artifact = Artifact(kind="file", value=filepath, repo="")
        result = await verify_artifact(artifact)
        assert result.verified is True


class TestVerifyTestPass:
    """Tests for test_pass artifact verification (re-run tests)."""

    async def test_tests_pass(self, tmp_path):
        """A test command that exits 0 should verify."""
        repo = str(tmp_path)
        artifact = Artifact(kind="test_pass", value="pytest tests/", repo=repo)
        with patch("job_star.proof.verifier._run_shell", return_value=(0, "all passed", "")):
            result = await verify_artifact(artifact)
        assert result.verified is True
        assert "tests pass" in result.verification_note

    async def test_tests_fail(self, tmp_path):
        """A test command that exits non-zero should fail verification."""
        repo = str(tmp_path)
        artifact = Artifact(kind="test_pass", value="pytest tests/", repo=repo)
        with patch("job_star.proof.verifier._run_shell", return_value=(1, "FAILED", "error")):
            result = await verify_artifact(artifact)
        assert result.verified is False
        assert result.verification_note.startswith("false")

    async def test_tests_timeout(self, tmp_path):
        """A timed-out test re-run should be unverified."""
        repo = str(tmp_path)
        artifact = Artifact(kind="test_pass", value="pytest tests/", repo=repo)
        with patch("job_star.proof.verifier._run_shell", return_value=(-2, "", "timeout")):
            result = await verify_artifact(artifact)
        assert result.verified is False
        assert "unverified" in result.verification_note


class TestVerifyCommand:
    """Tests for command artifact verification."""

    async def test_command_succeeds(self):
        """A command that exits 0 should verify."""
        artifact = Artifact(kind="command", value="echo hello")
        with patch("job_star.proof.verifier._run_shell", return_value=(0, "hello", "")):
            result = await verify_artifact(artifact)
        assert result.verified is True

    async def test_command_fails(self):
        """A command that exits non-zero should fail."""
        artifact = Artifact(kind="command", value="false")
        with patch("job_star.proof.verifier._run_shell", return_value=(1, "", "")):
            result = await verify_artifact(artifact)
        assert result.verified is False
        assert result.verification_note.startswith("false")


class TestVerifyWitnessed:
    """Tests for witnessed artifact verification via witness client."""

    async def test_witnessed_exit_zero(self):
        """A witnessed command with exit 0 should verify."""
        artifact = Artifact(kind="witnessed", value="ev_abc123")
        class FakeWitness:
            async def lookup(self, guid):
                return {"guid": "ev_abc123", "exit_code": 0, "command": ["node", "enhance.mjs"]}
        result = await verify_artifact(artifact, witness_client=FakeWitness())
        assert result.verified is True
        assert "witnessed" in result.verification_note

    async def test_witnessed_exit_nonzero(self):
        """A witnessed command with non-zero exit should fail."""
        artifact = Artifact(kind="witnessed", value="ev_abc123")
        class FakeWitness:
            async def lookup(self, guid):
                return {"guid": "ev_abc123", "exit_code": 1, "command": "bad-cmd"}
        result = await verify_artifact(artifact, witness_client=FakeWitness())
        assert result.verified is False
        assert result.verification_note.startswith("false")

    async def test_witnessed_not_found(self):
        """A witnessed GUID not in the store should fail."""
        artifact = Artifact(kind="witnessed", value="ev_nonexistent")
        class FakeWitness:
            async def lookup(self, guid):
                return None
        result = await verify_artifact(artifact, witness_client=FakeWitness())
        assert result.verified is False
        assert result.verification_note.startswith("false")

    async def test_witnessed_no_client(self):
        """A witnessed artifact with no witness client should be unverified."""
        artifact = Artifact(kind="witnessed", value="ev_abc123")
        result = await verify_artifact(artifact, witness_client=None)
        assert result.verified is False
        assert "unverified" in result.verification_note


class TestVerifyArtifacts:
    """Tests for the batch verify_artifacts function."""

    async def test_mixed_artifacts(self):
        """A mix of verified, failed, and unverified artifacts."""
        artifacts = [
            Artifact(kind="command", value="true"),
            Artifact(kind="command", value="false"),
            Artifact(kind="witnessed", value="ev_123"),
        ]
        def shell_mock(cmd, cwd=None, timeout=120):
            if cmd == "true":
                return (0, "", "")
            return (1, "", "")
        with patch("job_star.proof.verifier._run_shell", side_effect=shell_mock):
            result = await verify_artifacts(artifacts, witness_client=None)
        assert result.verified_count == 1
        assert result.failed_count == 1  # "false" command
        assert result.unverified_count == 1  # witnessed with no client
        assert result.total == 3
        assert result.has_verified is True
        assert result.has_failures is True

    async def test_empty_list(self):
        """An empty artifact list should produce an empty result."""
        result = await verify_artifacts([])
        assert result.total == 0
        assert result.has_verified is False

    async def test_unknown_kind(self):
        """An unknown artifact kind should be unverified."""
        artifact = Artifact(kind="unknown_kind", value="something")
        result = await verify_artifacts([artifact])
        assert result.unverified_count == 1
        assert result.verified_count == 0

    async def test_summary_string(self):
        """The summary should be human-readable."""
        artifacts = [Artifact(kind="command", value="true")]
        with patch("job_star.proof.verifier._run_shell", return_value=(0, "", "")):
            result = await verify_artifacts(artifacts)
        summary = result.summary()
        assert "1 verified" in summary
        assert "command" in summary


class TestParseWitnessBlocks:
    """Tests for the witness directive parser in pr_executor."""

    def test_parses_witness_directives(self):
        """## Witness: directives should be parsed into command strings."""
        from job_star.executors.pr_executor import parse_witness_blocks
        content = """## File: src/main.py
```python
print('hello')
```

## Witness: python migrate.py --upgrade
## Witness: node enhance.mjs --embeddings --all
"""
        cmds = parse_witness_blocks(content)
        assert len(cmds) == 2
        assert "python migrate.py --upgrade" in cmds[0]
        assert "node enhance.mjs" in cmds[1]

    def test_no_witness_directives(self):
        """Content without witness directives should return empty list."""
        from job_star.executors.pr_executor import parse_witness_blocks
        content = "Just regular text output with no directives."
        cmds = parse_witness_blocks(content)
        assert len(cmds) == 0

    def test_strips_backticks(self):
        """Witness commands wrapped in backticks should be stripped."""
        from job_star.executors.pr_executor import parse_witness_blocks
        content = "## Witness: `echo hello`"
        cmds = parse_witness_blocks(content)
        assert len(cmds) == 1
        assert cmds[0] == "echo hello"
