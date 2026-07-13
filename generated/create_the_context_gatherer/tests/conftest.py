"""Shared pytest fixtures for Job-Star context gatherer tests."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


@pytest.fixture
def sample_intake_request() -> dict:
    """A canonical intake request fixture representing a bug report."""
    return {
        "id": "JOB-42",
        "title": "Login fails on Safari when password contains '+'",
        "description": (
            "Users on Safari 17 report that login submits but the session "
            "cookie is not set. Reproducible when the password contains a "
            "'+' character. Possibly related to URL encoding in auth.py."
        ),
        "keywords": ["login", "auth", "safari", "cookie", "encoding"],
        "file_hints": ["src/auth.py", "src/session.py"],
        "error_signature": "ValueError: invalid cookie value",
        "created_at": "2025-01-15T10:30:00Z",
    }


@pytest.fixture
def mock_repo(tmp_path: Path) -> Path:
    """Create a small mock git repository with realistic structure."""
    repo = tmp_path / "repo"
    repo.mkdir()

    # Directory structure
    (repo / "src").mkdir()
    (repo / "tests").mkdir()
    (repo / "docs").mkdir()

    # Source files
    (repo / "src" / "auth.py").write_text(
        "def login(user, password):\n"
        "    # TODO: handle special chars in password\n"
        "    token = encode(password)\n"
        "    return set_session(user, token)\n"
    )
    (repo / "src" / "session.py").write_text(
        "def set_session(user, token):\n"
        "    cookie = build_cookie(token)\n"
        "    return cookie\n"
    )
    (repo / "src" / "utils.py").write_text(
        "def encode(value):\n"
        "    return value  # placeholder encoding\n"
    )
    (repo / "src" / "unrelated.py").write_text(
        "def unrelated():\n"
        "    return 42\n"
    )

    # Test file
    (repo / "tests" / "test_auth.py").write_text(
        "def test_login():\n"
        "    assert login('alice', 'secret') is not None\n"
    )

    # Docs
    (repo / "docs" / "auth.md").write_text(
        "# Authentication\n\nHandles login and session cookies.\n"
    )

    # Initialize git
    env = {**os.environ, "GIT_AUTHOR_NAME": "Test", "GIT_AUTHOR_EMAIL": "t@e.st",
           "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "t@e.st"}
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "Initial commit"], cwd=repo, check=True, env=env)

    # Second commit modifying auth.py
    (repo / "src" / "auth.py").write_text(
        "def login(user, password):\n"
        "    token = encode(password)\n"
        "    return set_session(user, token)\n"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "Remove TODO from auth"], cwd=repo, check=True, env=env)

    return repo


@pytest.fixture
def error_log_file(tmp_path: Path) -> Path:
    """Create a sample error log file."""
    log = tmp_path / "errors.log"
    log.write_text(
        "2025-01-15T10:29:00Z ERROR ValueError: invalid cookie value\n"
        "  File \"src/session.py\", line 2, in set_session\n"
        "    cookie = build_cookie(token)\n"
        "2025-01-15T10:28:00Z INFO  Starting server\n"
        "2025-01-15T10:27:00Z ERROR KeyError: 'user_id'\n"
        "  File \"src/auth.py\", line 3, in login\n"
        "    return set_session(user, token)\n"
        "2025-01-15T10:25:00Z WARN  Slow query detected\n"
    )
    return log
