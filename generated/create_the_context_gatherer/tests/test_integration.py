"""
Integration test for the Job-Star context gatherer agent.

End-to-end test: builds a temporary git repo with sample files, commit
history, and error logs, then feeds a realistic intake string into the
ContextGatherer and asserts the returned ContextBundle contains the
expected files, commits, and errors.

Run with:
    pytest tests/test_integration.py -v
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import is_dataclass
from datetime import datetime, timezone
from pathlib import Path

import pytest

from jobstar.context_gatherer import ContextBundle, ContextGatherer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a command in cwd, returning the completed process. Fails loudly."""
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed: {' '.join(cmd)}\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return proc


def git_repo(path: Path) -> None:
    """Initialize a git repo with a deterministic identity."""
    run(["git", "init", "-q", "-b", "main"], path)
    run(["git", "config", "user.email", "jobstar@test.local"], path)
    run(["git", "config", "user.name", "Job Star Test"], path)


def commit_all(path: Path, message: str) -> None:
    run(["git", "add", "-A"], path)
    run(["git", "commit", "-q", "-m", message], path)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_repo(tmp_path: Path) -> Path:
    """
    Build a realistic small project repo:

        repo/
          src/
            auth.py          # mentions login/session (matches intake)
            billing.py       # unrelated
            utils.py
          tests/
            test_auth.py
          logs/
            errors.log       # recent error entries
          README.md

    With a few commits so git history is non-trivial.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    git_repo(repo)

    # --- initial structure ---
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "logs").mkdir()

    (repo / "src" / "auth.py").write_text(
        "def login(user, password):\n"
        "    # handles user login and session creation\n"
        "    session = create_session(user)\n"
        "    return session\n"
    )
    (repo / "src" / "billing.py").write_text(
        "def charge(amount):\n"
        "    # unrelated to login\n"
        "    return amount\n"
    )
    (repo / "src" / "utils.py").write_text(
        "def create_session(user):\n"
        "    return {'user': user, 'token': 'abc'}\n"
    )
    (repo / "tests" / "test_auth.py").write_text(
        "def test_login():\n"
        "    assert login('alice', 'pw') is not None\n"
    )
    (repo / "README.md").write_text("# Sample project for Job-Star tests\n")
    commit_all(repo, "Initial commit: auth, billing, utils")

    # --- second commit: tweak auth ---
    (repo / "src" / "auth.py").write_text(
        "def login(user, password):\n"
        "    # handles user login and session creation\n"
        "    if not user:\n"
        "        raise ValueError('missing user')\n"
        "    session = create_session(user)\n"
        "    return session\n"
    )
    commit_all(repo, "Add validation to login")

    # --- third commit: add error log ---
    (repo / "logs" / "errors.log").write_text(
        "2024-01-15T10:00:00Z ERROR auth login failed for user=alice reason=bad_password\n"
        "2024-01-15T10:05:00Z ERROR auth session expired user=bob\n"
        "2024-01-15T11:00:00Z INFO billing charge amount=42\n"
        "2024-01-16T09:00:00Z ERROR auth login failed for user=carol reason=null_user\n"
    )
    commit_all(repo, "Add error logs")

    return repo


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestContextGathererIntegration:
    """End-to-end integration tests for the context gatherer."""

    def test_context_bundle_is_dataclass(self):
        """Sanity: ContextBundle should be a dataclass we can introspect."""
        assert is_dataclass(ContextBundle)

    def test_gather_returns_context_bundle(self, sample_repo: Path):
        gatherer = ContextGatherer(repo_path=sample_repo)
        intake = "Users are reporting login failures. Investigate the auth login flow."
        bundle = gatherer.gather(intake)

        assert isinstance(bundle, ContextBundle), \
            f"Expected ContextBundle, got {type(bundle)}"
        assert bundle.intake == intake

    def test_bundle_contains_relevant_files(self, sample_repo: Path):
        """
        The intake mentions 'login' and 'auth'. The bundle should surface
        files whose content or path relates to those terms, and should NOT
        be dominated by unrelated files like billing.py.
        """
        gatherer = ContextGatherer(repo_path=sample_repo)
        intake = "Users are reporting login failures. Investigate the auth login flow."
        bundle = gatherer.gather(intake)

        file_paths = {Path(f.path).as_posix() for f in bundle.files}
        assert file_paths, "Expected at least one file in the bundle"

        # auth.py must be surfaced (path + content match)
        assert "src/auth.py" in file_paths, \
            f"Expected src/auth.py in {file_paths}"
        # test_auth.py should be surfaced (path mentions auth)
        assert "tests/test_auth.py" in file_paths, \
            f"Expected tests/test_auth.py in {file_paths}"
        # utils.py should be surfaced (defines create_session referenced by auth)
        assert "src/utils.py" in file_paths, \
            f"Expected src/utils.py in {file_paths}"

    def test_bundle_contains_recent_commits(self, sample_repo: Path):
        """
        The bundle should include recent git history. At minimum it should
        surface commits whose messages or diffs touch the relevant area
        (auth/login), and should include the most recent commit.
        """
        gatherer = ContextGatherer(repo_path=sample_repo)
        intake = "Users are reporting login failures. Investigate the auth login flow."
        bundle = gatherer.gather(intake)

        assert bundle.commits, "Expected at least one commit in the bundle"

        messages = [c.message for c in bundle.commits]
        # The most recent commit (Add error logs) should be present
        assert any("error logs" in m.lower() for m in messages), \
            f"Expected a commit mentioning error logs in {messages}"
        # A commit touching auth should be present
        assert any("login" in m.lower() or "auth" in m.lower() for m in messages), \
            f"Expected a commit touching auth/login in {messages}"

    def test_bundle_contains_recent_errors(self, sample_repo: Path):
        """
        The bundle should surface recent error log entries relevant to the
        intake (auth/login failures), not unrelated INFO/billing lines.
        """
        gatherer = ContextGatherer(repo_path=sample_repo)
        intake = "Users are reporting login failures. Investigate the auth login flow."
        bundle = gatherer.gather(intake)

        assert bundle.errors, "Expected at least one error entry in the bundle"

        error_texts = [e.message for e in bundle.errors]
        # At least one auth/login-related error
        assert any("login" in t.lower() or "auth" in t.lower() for t in error_texts), \
            f"Expected an auth/login error in {error_texts}"
        # Billing INFO line should not appear as an error
        assert not any("billing" in t.lower() and "charge" in t.lower() for t in error_texts), \
            f"Did not expect billing info line among errors: {error_texts}"

    def test_bundle_summary_is_nonempty(self, sample_repo: Path):
        """The bundle should carry a short human-readable summary."""
        gatherer = ContextGatherer(repo_path=sample_repo)
        intake = "Users are reporting login failures. Investigate the auth login flow."
        bundle = gatherer.gather(intake)

        assert bundle.summary, "Expected a non-empty summary"
        assert isinstance(bundle.summary, str)

    def test_irrelevant_intake_yields_smaller_bundle(self, sample_repo: Path):
        """
        An intake about an unrelated area (billing) should still return a
        bundle, but the file set should lean toward billing, not auth.
        This guards against the gatherer just dumping the whole repo.
        """
        gatherer = ContextGatherer(repo_path=sample_repo)
        intake = "Billing charges are being doubled for some customers."
        bundle = gatherer.gather(intake)

        file_paths = {Path(f.path).as_posix() for f in bundle.files}
        assert "src/billing.py" in file_paths, \
            f"Expected src/billing.py for billing intake, got {file_paths}"

    def test_gather_is_idempotent(self, sample_repo: Path):
        """Calling gather twice with the same intake should yield equivalent bundles."""
        gatherer = ContextGatherer(repo_path=sample_repo)
        intake = "Users are reporting login failures. Investigate the auth login flow."

        b1 = gatherer.gather(intake)
        b2 = gatherer.gather(intake)

        assert {Path(f.path).as_posix() for f in b1.files} == \
               {Path(f.path).as_posix() for f in b2.files}
        assert [c.message for c in b1.commits] == [c.message for c in b2.commits]

    def test_handles_empty_repo(self, tmp_path: Path):
        """A freshly initialized repo with no commits should not crash."""
        repo = tmp_path / "empty"
        repo.mkdir()
        git_repo(repo)
        # No commits yet

        gatherer = ContextGatherer(repo_path=repo)
        bundle = gatherer.gather("Anything at all.")

        assert isinstance(bundle, ContextBundle)
        assert bundle.commits == []
        assert bundle.files == []

    def test_handles_missing_logs_dir(self, tmp_path: Path):
        """A repo with no logs directory should still gather files and commits."""
        repo = tmp_path / "nologs"
        repo.mkdir()
        git_repo(repo)
        (repo / "src").mkdir()
        (repo / "src" / "auth.py").write_text("def login(): pass\n")
        commit_all(repo, "add auth")

        gatherer = ContextGatherer(repo_path=repo)
        bundle = gatherer.gather("login is broken")

        assert isinstance(bundle, ContextBundle)
        assert "src/auth.py" in {Path(f.path).as_posix() for f in bundle.files}
        assert bundle.errors == []
