"""Unit tests for the file scanner collector."""
from __future__ import annotations

from pathlib import Path

import pytest

from job_star.context_gatherer.file_scanner import FileScanResult, FileScanner


class TestFileScanner:
    """Tests for the file scanner that finds related files in a repo."""

    def test_scan_finds_files_by_keyword(self, mock_repo: Path):
        """Scanner should find files whose content matches keywords."""
        scanner = FileScanner(repo_path=mock_repo)
        result = scanner.scan(keywords=["login", "auth", "cookie"])

        assert isinstance(result, FileScanResult)
        paths = [r.relative_path for r in result.matches]
        assert "src/auth.py" in paths
        assert "src/session.py" in paths

    def test_scan_respects_file_hints(self, mock_repo: Path):
        """Explicitly hinted files should always be included."""
        scanner = FileScanner(repo_path=mock_repo)
        result = scanner.scan(
            keywords=[],
            file_hints=["src/auth.py", "src/session.py"],
        )

        paths = [r.relative_path for r in result.matches]
        assert "src/auth.py" in paths
        assert "src/session.py" in paths

    def test_scan_nonexistent_hint_skipped(self, mock_repo: Path):
        """Non-existent file hints should be skipped without error."""
        scanner = FileScanner(repo_path=mock_repo)
        result = scanner.scan(file_hints=["src/missing.py"])

        assert result.matches == []

    def test_scan_unrelated_files_excluded(self, mock_repo: Path):
        """Files without keyword matches should not appear."""
        scanner = FileScanner(repo_path=mock_repo)
        result = scanner.scan(keywords=["login", "auth"])

        paths = [r.relative_path for r in result.matches]
        assert "src/unrelated.py" not in paths

    def test_scan_ranks_by_relevance(self, mock_repo: Path):
        """Files matching more keywords should rank higher."""
        scanner = FileScanner(repo_path=mock_repo)
        result = scanner.scan(keywords=["login", "auth", "session", "cookie"])

        assert len(result.matches) >= 2
        # auth.py matches 'login' and 'auth' — should be top or near top
        top_match = result.matches[0]
        assert top_match.relative_path == "src/auth.py"
        assert top_match.score > 0

    def test_scan_includes_test_files(self, mock_repo: Path):
        """Test files related to matched source should be included."""
        scanner = FileScanner(repo_path=mock_repo)
        result = scanner.scan(keywords=["login", "auth"], include_tests=True)

        paths = [r.relative_path for r in result.matches]
        assert "tests/test_auth.py" in paths

    def test_scan_excludes_tests_when_disabled(self, mock_repo: Path):
        """When include_tests=False, test files should be excluded."""
        scanner = FileScanner(repo_path=mock_repo)
        result = scanner.scan(keywords=["login"], include_tests=False)

        paths = [r.relative_path for r in result.matches]
        assert not any("test_" in p for p in paths)

    def test_scan_respects_max_results(self, mock_repo: Path):
        """Scanner should limit results to max_results."""
        scanner = FileScanner(repo_path=mock_repo)
        result = scanner.scan(keywords=["def"], max_results=2)

        assert len(result.matches) <= 2

    def test_scan_empty_repo(self, tmp_path: Path):
        """Scanning an empty repo should return no matches."""
        (tmp_path / ".git").mkdir()
        scanner = FileScanner(repo_path=tmp_path)
        result = scanner.scan(keywords=["anything"])

        assert result.matches == []

    def test_scan_result_has_metadata(self, mock_repo: Path):
        """Each match should carry metadata: path, score, matched_keywords."""
        scanner = FileScanner(repo_path=mock_repo)
        result = scanner.scan(keywords=["login"])

        match = result.matches[0]
        assert hasattr(match, "relative_path")
        assert hasattr(match, "absolute_path")
        assert hasattr(match, "score")
        assert hasattr(match, "matched_keywords")
        assert "login" in match.matched_keywords

    def test_scan_ignores_binary_files(self, mock_repo: Path):
        """Binary files should be skipped during content scan."""
        # Create a fake binary file
        (mock_repo / "src" / "data.bin").write_bytes(b"\x00\x01\x02login\x00")

        scanner = FileScanner(repo_path=mock_repo)
        result = scanner.scan(keywords=["login"])

        paths = [r.relative_path for r in result.matches]
        assert "src/data.bin" not in paths
