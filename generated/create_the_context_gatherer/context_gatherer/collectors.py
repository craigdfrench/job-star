"""Individual context collectors.

Each collector is a small, focused function that gathers one slice of context.
The orchestrator (agent.py) calls them and assembles results.
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

from .bundle import FileHit, GitCommit, LogError


# ---------------------------------------------------------------------------
# Keyword extraction
# ---------------------------------------------------------------------------

_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "at", "by", "for", "with", "about", "against",
    "and", "or", "but", "not", "no", "yes", "this", "that", "these", "those",
    "it", "its", "i", "we", "you", "they", "he", "she", "them",
    "do", "does", "did", "done", "have", "has", "had",
    "will", "would", "could", "should", "shall", "may", "might", "must",
    "can", "need", "please", "fix", "issue", "problem", "bug", "error",
    "when", "where", "why", "how", "what", "which", "who",
})

_TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]*")


def extract_keywords(raw_intake: str, max_keywords: int = 20) -> list[str]:
    """Extract meaningful keywords from a raw intake string.

    Strategy:
    - CamelCase / snake_case identifiers are kept whole (likely code refs).
    - Common English stopwords are dropped.
    - Duplicates removed; ranked by frequency then length.
    """
    tokens = _TOKEN_RE.findall(raw_intake)
    seen: dict[str, int] = {}
    for tok in tokens:
        low = tok.lower()
        if low in _STOPWORDS or len(low) < 3:
            continue
        seen[low] = seen.get(low, 0) + 1

    # Sort by frequency desc, then length desc, then alphabetical
    ranked = sorted(seen.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    return [kw for kw, _ in ranked[:max_keywords]]


# ---------------------------------------------------------------------------
# File collector
# ---------------------------------------------------------------------------

_IGNORED_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".eggs", ".tox", ".idea", ".vscode",
}

_CODE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".go", ".rs", ".java",
    ".rb", ".php", ".c", ".h", ".cpp", ".hpp", ".cs", ".swift",
    ".kt", ".scala", ".sh", ".bash", ".yml", ".yaml", ".toml",
    ".cfg", ".ini", ".json", ".sql", ".html", ".css", ".scss",
    ".md", ".txt", ".env",
}


def collect_files(
    repo_root: Path,
    keywords: list[str],
    max_files: int = 30,
) -> list[FileHit]:
    """Scan the repo for files relevant to the given keywords.

    Scoring:
    - Filename contains a keyword: +0.5
    - File path contains a keyword: +0.3
    - File content contains a keyword (first 4 KB): +0.2 per hit (capped)
    """
    hits: list[FileHit] = []
    kw_lower = [k.lower() for k in keywords]

    for path in repo_root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _IGNORED_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in _CODE_EXTENSIONS:
            continue

        rel = path.relative_to(repo_root)
        rel_str = str(rel).lower()
        name_lower = path.name.lower()

        score = 0.0
        reasons: list[str] = []

        for kw in kw_lower:
            if kw in name_lower:
                score += 0.5
                reasons.append(f"filename:{kw}")
            elif kw in rel_str:
                score += 0.3
                reasons.append(f"path:{kw}")

        # Lightweight content scan (first 4 KB)
        if score > 0 or len(kw_lower) <= 8:
            try:
                raw = path.read_bytes()[:4096]
                text = raw.decode("utf-8", errors="ignore").lower()
                content_hits = sum(1 for kw in kw_lower if kw in text)
                if content_hits:
                    score += min(content_hits * 0.2, 1.0)
                    reasons.append(f"content:{content_hits}")
            except (OSError, PermissionError):
                pass

        if score > 0:
            stat = path.stat()
            hits.append(FileHit(
                path=rel,
                score=min(score, 1.0),
                reason=", ".join(reasons[:4]),
                last_modified=datetime.fromtimestamp(stat.st_mtime),
                size_bytes=stat.st_size,
            ))

    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:max_files]


# ---------------------------------------------------------------------------
# Git history collector
# ---------------------------------------------------------------------------

def collect_git_history(
    repo_root: Path,
    keywords: list[str],
    max_commits: int = 20,
    days_back: int = 30,
) -> list[GitCommit]:
    """Collect recent git commits whose message or changed files match keywords."""
    if not (repo_root / ".git").exists() and not _is_git_repo(repo_root):
        return []

    since = f"--since={days_back} days ago"
    # Format: <sha>\x1f<author>\x1f<iso-date>\x1f<message>\x1f<file1>\x1f<file2>...
    fmt = "%H\x1f%an\x1f%aI\x1f%s\x1f"
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "log", since, f"--max-count={max_commits * 3}",
             f"--pretty=format:{fmt}", "--name-only"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []

    if result.returncode != 0:
        return []

    commits: list[GitCommit] = []
    kw_lower = [k.lower() for k in keywords]

    for raw_entry in result.stdout.strip().split("\n\n"):
        if not raw_entry.strip():
            continue
        parts = raw_entry.strip().split("\x1f")
        if len(parts) < 4:
            continue
        sha, author, date_str, message = parts[0], parts[1], parts[2], parts[3]
        files_changed = [p.strip() for p in parts[4:] if p.strip()]

        # Score: keyword in message or in changed file paths
        msg_lower = message.lower()
        files_lower = " ".join(files_changed).lower()
        matched = any(kw in msg_lower or kw in files_lower for kw in kw_lower)

        if matched or not kw_lower:
            try:
                dt = datetime.fromisoformat(date_str)
            except ValueError:
                continue
            commits.append(GitCommit(
                sha=sha,
                author=author,
                date=dt,
                message=message,
                files_changed=files_changed,
            ))

    commits.sort(key=lambda c: c.date, reverse=True)
    return commits[:max_commits]


def _is_git_repo(repo_root: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == "true"
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


# ---------------------------------------------------------------------------
# Log / error collector
# ---------------------------------------------------------------------------

_ERROR_PATTERNS = [
    re.compile(r"\b(ERROR|CRITICAL|FATAL|PANIC|Traceback)\b", re.IGNORECASE),
    re.compile(r"\bWARNING\b", re.IGNORECASE),
]

_TS_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)"
)


def collect_log_errors(
    log_paths: list[Path],
    keywords: list[str],
    max_errors: int = 50,
    tail_lines: int = 2000,
) -> list[LogError]:
    """Scan log files for recent errors/warnings, optionally filtered by keywords."""
    errors: list[LogError] = []
    kw_lower = [k.lower() for k in keywords]

    for log_path in log_paths:
        if not log_path.exists() or not log_path.is_file():
            continue

        try:
            # Read tail of file to limit memory
            with log_path.open("rb") as f:
                try:
                    f.seek(0, 2)
                    size = f.tell()
                    read_size = min(size, tail_lines * 200)  # rough byte budget
                    f.seek(max(0, size - read_size))
                    lines = f.read().decode("utf-8", errors="ignore").splitlines()
                except OSError:
                    lines = []
        except (OSError, PermissionError):
            continue

        # Keep only last tail_lines
        lines = lines[-tail_lines:]

        for i, line in enumerate(lines):
            level = None
            for pat in _ERROR_PATTERNS:
                m = pat.search(line)
                if m:
                    level = m.group(1).upper()
                    break
            if not level:
                continue

            # If we have keywords, require at least one to appear in the line
            # (or surrounding context) — unless no keywords, then keep all errors.
            if kw_lower:
                window = " ".join(lines[max(0, i - 2):i + 3]).lower()
                if not any(kw in window for kw in kw_lower):
                    continue

            ts_match = _TS_RE.search(line)
            timestamp = None
            if ts_match:
                try:
                    timestamp = datetime.fromisoformat(
                        ts_match.group(1).replace("Z", "+00:00")
                    )
                except ValueError:
                    timestamp = None

            context = lines[max(0, i - 2):i + 3]

            errors.append(LogError(
                source=log_path,
                timestamp=timestamp,
                level=level,
                message=line.strip()[:500],
                context_lines=[c.strip()[:300] for c in context],
            ))

    # Sort by timestamp desc if available, otherwise keep file order
    errors.sort(
        key=lambda e: e.timestamp or datetime.min,
        reverse=True,
    )
    return errors[:max_errors]
