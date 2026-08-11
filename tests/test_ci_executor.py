"""Unit tests for the CI gate executor's repo normalization."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from job_star.executors.ci import _to_git_url


def test_slug_becomes_github_url():
    assert _to_git_url("craigdfrench/gatehouse-ai") == \
        "https://github.com/craigdfrench/gatehouse-ai"


def test_url_passthrough():
    assert _to_git_url("https://github.com/craigdfrench/gatehouse-ai") == \
        "https://github.com/craigdfrench/gatehouse-ai"


def test_ssh_url_passthrough():
    assert _to_git_url("git@github.com:craigdfrench/gatehouse-ai.git") == \
        "git@github.com:craigdfrench/gatehouse-ai"


def test_bare_name_rejected():
    # A single word is not a valid owner/name slug and must not silently become
    # a URL pointing at a bogus repo.
    assert _to_git_url("rogue") is None
    assert _to_git_url("gatehouse-ai") is None


def test_empty_and_none_rejected():
    assert _to_git_url("") is None
    assert _to_git_url(None) is None
    assert _to_git_url("   ") is None