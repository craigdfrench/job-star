"""Unit tests for the git history collector."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from job_star.context_gatherer.git_collector import (
    GitCollector,
    GitHistory,
    CommitInfo,
)


class TestGitCollector:
    """Tests for collecting git history related to an intake request."""

    def test_collect_returns_history(self, mock_repo: Path):
        """Collecting should return a GitHistory object."""
        collector = GitCollector(repo_path=mock_repo)
        history = collector.collect(file_hints=["src/auth.py"])

        assert isinstance(history, GitHistory)
        assert len(history.commits) >= 1

    def test_collect_commit_has_required_fields(self, mock_repo: Path):
        """Each commit should have hash, message, author, date, files."""
        collector = GitCollector(repo_path=mock_repo)
        history = collector.collect(file_hints=["src/auth.py"])

        commit = history.commits[0]
        assert isinstance(commit, CommitInfo)
        assert commit.hash
        assert len(commit.hash) >= 7
        assert commit.message
        assert commit.author
        assert commit.date
        assert isinstance(commit.files, list)

    def test_collect_filters_by_file_hints(self, mock_repo: Path):
        """Only commits touching hinted files should be returned."""
        collector = GitCollector(repo_path=mock_repo)
        history = collector.collect(file_hints=["src/auth.py"])

        for commit in history.commits:
            assert any("auth.py" in f for f in commit.files)

    def test_collect_no_file_hints_returns_recent(self, mock_repo: Path):
        """Without file hints, should return recent commits."""
        collector = GitCollector(repo_path=mock_repo)
        history = collector.collect(file_hints=[], max_commits=5)

        assert len(history.commits) >= 1
        assert len(history.commits) <= 5

    def test_collect_respects_max_commits(self, mock_repo: Path):
        """Should limit number of commits returned."""
        collector = GitCollector(repo_path=mock_repo)
        history = collector.collect(file_hints=[], max_commits=1)

        assert len(history.commits) == 1

    def test_collect_orders_newest_first(self, mock_repo: Path):
        """Commits should be ordered newest first."""
        collector = GitCollector(repo_path=mock_repo)
        history = collector.collect(file_hints=[], max_commits=10)

        dates = [c.date for c in history.commits]
        assert dates == sorted(dates, reverse=True)

    def test_collect_by_keyword_in_message(self, mock_repo: Path):
        """Should find commits whose message contains keywords."""
        collector = GitCollector(repo_path=mock_repo)
        history = collector.collect(keywords=["auth"])

        messages = [c.message.lower() for c in history.commits]
        assert any("auth" in m for m in messages)

    def test_collect_non_git_repo(self, tmp_path: Path):
        """Collecting from a non-git directory should raise or return empty."""
        collector = GitCollector(repo_path=tmp_path)
        history = collector.collect(file_hints=[])

        # Should gracefully return empty history, not crash
        assert history.commits == []

    def test_collect_includes_diff_summary(self, mock_repo: Path):
        """Commits should include a brief diff summary (additions/deletions)."""
        collector = GitCollector(repo_path=mock_repo)
        history = collector.collect(file_hints=["src/auth.py"])

        commit = history.commits[0]
        assert hasattr(commit, "additions")
        assert hasattr(commit, "deletions")
        assert isinstance(commit.additions, int)
        assert isinstance(commit.deletions, int)

    def test_collect_multiple_file_hints(self, mock_repo: Path):
        """Multiple file hints should union commits from all hinted files."""
        collector = GitCollector(repo_path=mock_repo)
        history = collector.collect(file_hints=["src/auth.py", "src/session.py"])

        all_files = set()
        for commit in history.commits:
            all_files.update(commit.files)

        assert any("auth.py" in f for f in all_files)
        assert any("session.py" in f for f in all_files)

    def test_git_history_to_dict(self, mock_repo: Path):
        """GitHistory should serialize to a dict for the agent."""
        collector = GitCollector(repo_path=mock_repo)
        history = collector.collect(file_hints=["src/auth.py"])
        d = history.to_dict()

        assert "commits" in d
        assert isinstance(d["commits"], list)
        assert len(d["commits"]) == len(history.commits)
