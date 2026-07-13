"""
Git history collector for the Job-Star context gatherer.

Examines a repository and gathers recent commits that either:
  - touch the files identified by the file matcher, or
  - have commit messages matching intake keywords.

Results are limited to the last N commits and the last N days.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .file_collector import FileMatch  # re-export for convenience


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CommitStat:
    """Per-file change stats for a single commit."""

    path: str
    added: int
    removed: int


@dataclass(frozen=True)
class CommitInfo:
    """A single git commit with lightweight metadata."""

    sha: str
    author_name: str
    author_email: str
    date: datetime  # author date, UTC
    subject: str
    body: str
    stats: tuple[CommitStat, ...] = ()

    @property
    def is_merge(self) -> bool:
        return self.subject.startswith("Merge")


@dataclass(frozen=True)
class GitHistory:
    """Aggregated git history gathered for an intake request."""

    file_commits: tuple[CommitInfo, ...]
    """Commits that directly touched matched files."""

    keyword_commits: tuple[CommitInfo, ...]
    """Commits whose messages matched intake keywords (excluding file_commits)."""

    repo_root: Path
    truncated: bool = False
    """True if we hit a limit and may have missed relevant commits."""

    @property
    def all_commits(self) -> tuple[CommitInfo, ...]:
        """File-touching commits first, then keyword-only commits."""
        return self.file_commits + self.keyword_commits

    @property
    def unique_shas(self) -> tuple[str, ...]:
        seen: list[str] = []
        for c in self.all_commits:
            if c.sha not in seen:
                seen.append(c.sha)
        return tuple(seen)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GitCollectorConfig:
    max_commits: int = 25
    """Hard cap on total commits returned across both passes."""

    max_days: int = 30
    """Only consider commits within this many days of now."""

    file_pass_limit: int = 15
    """Max commits to fetch when querying by file path."""

    keyword_pass_limit: int = 20
    """Max commits to fetch when querying by keyword."""

    include_stats: bool = True
    """Whether to parse --numstat for per-file line counts."""

    @property
    def since_arg(self) -> str:
        return f"--since={self.max_days}.days"


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


class GitCollector:
    """Runs `git log` against a repository to gather relevant history."""

    def __init__(self, config: GitCollectorConfig | None = None) -> None:
        self.config = config or GitCollectorConfig()

    # -- public API --------------------------------------------------------

    def collect_git_history(
        self,
        file_matches: list[FileMatch],
        repo_root: Path,
        keywords: Iterable[str] | None = None,
    ) -> GitHistory:
        """Gather recent git history relevant to the intake request.

        Args:
            file_matches: Files identified as relevant by the file collector.
            repo_root: Absolute path to the git repository root.
            keywords: Optional intake keywords to match against commit messages.

        Returns:
            A GitHistory containing file-touching and keyword-matching commits.
        """
        repo_root = Path(repo_root).resolve()
        keywords = [k for k in (keywords or []) if k and k.strip()]

        file_commits = self._collect_by_files(file_matches, repo_root)
        keyword_commits = self._collect_by_keywords(keywords, repo_root)

        # Deduplicate keyword commits that already appear in file_commits.
        file_shas = {c.sha for c in file_commits}
        keyword_commits = tuple(
            c for c in keyword_commits if c.sha not in file_shas
        )

        # Enforce the overall max_commits cap.
        truncated = False
        if len(file_commits) + len(keyword_commits) > self.config.max_commits:
            truncated = True
            remaining = max(0, self.config.max_commits - len(file_commits))
            file_commits = file_commits[: self.config.file_pass_limit]
            keyword_commits = keyword_commits[:remaining]

        return GitHistory(
            file_commits=file_commits,
            keyword_commits=keyword_commits,
            repo_root=repo_root,
            truncated=truncated,
        )

    # -- internal helpers --------------------------------------------------

    def _collect_by_files(
        self, file_matches: list[FileMatch], repo_root: Path
    ) -> tuple[CommitInfo, ...]:
        if not file_matches:
            return ()

        # Use repo-relative paths for git log -- <path>.
        rel_paths: list[str] = []
        for fm in file_matches:
            try:
                rel = Path(fm.path).resolve().relative_to(repo_root)
                rel_paths.append(rel.as_posix())
            except ValueError:
                # File outside repo root — skip it.
                continue

        if not rel_paths:
            return ()

        args = [
            "git", "-C", str(repo_root), "log",
            self.config.since_arg,
            f"-n{self.config.file_pass_limit}",
            "--format=%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f%b%x1e",
            "--name-status" if not self.config.include_stats else "--numstat",
            "--",
            *rel_paths,
        ]
        return self._parse_log(args, repo_root)

    def _collect_by_keywords(
        self, keywords: list[str], repo_root: Path
    ) -> tuple[CommitInfo, ...]:
        if not keywords:
            return ()

        # Build a single --grep alternation: (kw1|kw2|...)
        # Escape regex metacharacters in each keyword.
        escaped = [re.escape(k) for k in keywords]
        pattern = "(" + "|".join(escaped) + ")"

        args = [
            "git", "-C", str(repo_root), "log",
            self.config.since_arg,
            f"-n{self.config.keyword_pass_limit}",
            f"--grep={pattern}",
            "-E",  # extended regex
            "-i",  # case-insensitive
            "--format=%H%x1f%an%x1f%ae%x1f%aI%x1f%s%x1f%b%x1e",
            "--numstat" if self.config.include_stats else "--name-status",
        ]
        return self._parse_log(args, repo_root)

    def _parse_log(
        self, args: list[str], repo_root: Path
    ) -> tuple[CommitInfo, ...]:
        result = subprocess.run(
            args,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            # Not a git repo, or git not available — return empty.
            return ()

        return tuple(_parse_log_output(result.stdout, with_stats=self.config.include_stats))


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

# Field separator inside a commit record: \x1f between fields, \x1e between commits.
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"


def _parse_log_output(output: str, *, with_stats: bool) -> list[CommitInfo]:
    """Parse `git log` output produced with our custom --format and --numstat."""
    commits: list[CommitInfo] = []
    # Split on record separator; trailing empty chunk is expected.
    for raw_record in output.split(_RECORD_SEP):
        raw_record = raw_record.strip("\n")
        if not raw_record:
            continue
        # First line(s) are the formatted fields; remaining lines are numstat.
        lines = raw_record.split("\n")
        header = lines[0]
        fields = header.split(_FIELD_SEP)
        if len(fields) < 6:
            continue
        sha, author_name, author_email, date_iso, subject, body = fields[:6]

        stats: list[CommitStat] = []
        if with_stats:
            for line in lines[1:]:
                line = line.strip()
                if not line:
                    continue
                parts = line.split("\t")
                if len(parts) == 3:
                    added_s, removed_s, path = parts
                    added = int(added_s) if added_s != "-" else 0
                    removed = int(removed_s) if removed_s != "-" else 0
                    stats.append(CommitStat(path=path, added=added, removed=removed))

        try:
            date = _parse_iso(date_iso)
        except ValueError:
            continue

        commits.append(
            CommitInfo(
                sha=sha,
                author_name=author_name,
                author_email=author_email,
                date=date,
                subject=subject,
                body=body,
                stats=tuple(stats),
            )
        )
    return commits


def _parse_iso(iso: str) -> datetime:
    """Parse an ISO-8601 timestamp from `git log --format=%aI`."""
    # %aI gives strict ISO 8601, e.g. 2024-05-01T12:34:56+00:00
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Convenience module-level function
# ---------------------------------------------------------------------------


def collect_git_history(
    file_matches: list[FileMatch],
    repo_root: Path,
    keywords: Iterable[str] | None = None,
    config: GitCollectorConfig | None = None,
) -> GitHistory:
    """Functional entry point matching the step spec signature, extended with keywords."""
    collector = GitCollector(config=config)
    return collector.collect_git_history(file_matches, repo_root, keywords=keywords)
