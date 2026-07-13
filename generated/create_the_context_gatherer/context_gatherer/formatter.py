"""Serialize a ContextBundle into concise structured text for the triage agent.

The triage agent consumes the output of ``format_for_triage`` as part of its
prompt context. The format is intentionally plain-text and sectioned so it is
easy for an LLM to parse and so ordering is deterministic across runs.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Sequence

try:  # Type hints only; runtime uses duck typing for robustness.
    from .models import ContextBundle, FileContext, GitEvent, ErrorEntry, IntakeRequest
except Exception:  # pragma: no cover - models may not yet exist in early bootstrap
    ContextBundle = None  # type: ignore
    FileContext = None  # type: ignore
    GitEvent = None  # type: ignore
    ErrorEntry = None  # type: ignore
    IntakeRequest = None  # type: ignore


# --- Tunable limits (keep prompt size bounded) -----------------------------

MAX_SNIPPET_LINES = 40
MAX_SNIPPET_CHARS = 2000
MAX_DIFF_LINES = 60
MAX_DIFF_CHARS = 3000
MAX_ERROR_CHARS = 1500
MAX_ERRORS = 5
MAX_GIT_EVENTS = 8
MAX_FILES = 12
MAX_TAG_LEN = 200


# --- Helpers ---------------------------------------------------------------

def _truncate(text: str, max_lines: int, max_chars: int) -> str:
    """Truncate ``text`` to at most ``max_lines`` lines and ``max_chars`` chars."""
    if not text:
        return ""
    lines = text.splitlines()
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines.append(f"... [truncated {len(text.splitlines()) - max_lines} lines]")
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[:max_chars] + f"\n... [truncated at {max_chars} chars]"
    return out


def _short(s: object, limit: int = MAX_TAG_LEN) -> str:
    s = "" if s is None else str(s)
    return s if len(s) <= limit else s[: limit - 3] + "..."


def _get(obj: object, attr: str, default: object = "") -> object:
    """Defensive attribute access for duck-typed bundle objects."""
    return getattr(obj, attr, default)


# --- Section formatters ----------------------------------------------------

def _format_request(req: object) -> str:
    lines = ["## Intake Request"]
    lines.append(f"- summary: {_short(_get(req, 'summary', _get(req, 'title', '')))}")
    desc = str(_get(req, "description", _get(req, "body", "")) or "")
    if desc:
        lines.append("- description:")
        lines.append(_truncate(desc.strip(), 30, 1200))
    requester = _get(req, "requester", _get(req, "author", ""))
    if requester:
        lines.append(f"- requester: {_short(requester)}")
    urgency = _get(req, "urgency", "")
    if urgency:
        lines.append(f"- urgency: {_short(urgency)}")
    tags = _get(req, "tags", [])
    if tags:
        tag_list = ", ".join(_short(t) for t in tags)
        lines.append(f"- tags: {tag_list}")
    created = _get(req, "created_at", _get(req, "timestamp", ""))
    if created:
        lines.append(f"- created_at: {_short(created)}")
    return "\n".join(lines)


def _format_files(files: Sequence[object]) -> str:
    if not files:
        return "## Related Files\n(none found)"
    lines = ["## Related Files"]
    for i, f in enumerate(files[:MAX_FILES], start=1):
        path = _short(_get(f, "path", _get(f, "name", "?")))
        reason = _short(_get(f, "reason", _get(f, "why", "")))
        score = _get(f, "score", _get(f, "relevance", ""))
        header = f"{i}. {path}"
        if score not in ("", None):
            header += f"  [score={_short(score)}]"
        lines.append(header)
        if reason:
            lines.append(f"   reason: {reason}")
        snippet = str(_get(f, "snippet", _get(f, "content", "")) or "")
        if snippet:
            lines.append("   snippet:")
            for sl in _truncate(snippet, MAX_SNIPPET_LINES, MAX_SNIPPET_CHARS).splitlines():
                lines.append(f"     {sl}")
    if len(files) > MAX_FILES:
        lines.append(f"... [{len(files) - MAX_FILES} additional files omitted]")
    return "\n".join(lines)


def _format_git(events: Sequence[object]) -> str:
    if not events:
        return "## Recent Git History\n(none found)"
    lines = ["## Recent Git History"]
    for i, e in enumerate(events[:MAX_GIT_EVENTS], start=1):
        sha = _short(_get(e, "sha", _get(e, "hash", "?")), 12)
        msg = _short(_get(e, "message", _get(e, "subject", "")), 160)
        author = _short(_get(e, "author", ""))
        date = _short(_get(e, "date", _get(e, "timestamp", "")))
        lines.append(f"{i}. {sha} {msg}")
        meta_bits = []
        if author:
            meta_bits.append(f"by {author}")
        if date:
            meta_bits.append(date)
        changed = _get(e, "files_changed", _get(e, "files", []))
        if changed:
            meta_bits.append(f"{len(list(changed)) if not isinstance(changed, (str, bytes)) else changed} files")
        if meta_bits:
            lines.append(f"   ({', '.join(meta_bits)})")
        diff = str(_get(e, "diff", _get(e, "patch", "")) or "")
        if diff:
            lines.append("   diff:")
            for dl in _truncate(diff, MAX_DIFF_LINES, MAX_DIFF_CHARS).splitlines():
                lines.append(f"     {dl}")
    if len(events) > MAX_GIT_EVENTS:
        lines.append(f"... [{len(events) - MAX_GIT_EVENTS} additional commits omitted]")
    return "\n".join(lines)


def _format_errors(errors: Sequence[object]) -> str:
    if not errors:
        return "## Recent Errors\n(none found)"
    lines = ["## Recent Errors"]
    for i, e in enumerate(errors[:MAX_ERRORS], start=1):
        ts = _short(_get(e, "timestamp", _get(e, "time", "")))
        source = _short(_get(e, "source", _get(e, "service", "")))
        msg = _short(_get(e, "message", _get(e, "error", "")), 300)
        lines.append(f"{i}. {msg}")
        meta_bits = []
        if ts:
            meta_bits.append(ts)
        if source:
            meta_bits.append(source)
        if meta_bits:
            lines.append(f"   ({', '.join(meta_bits)})")
        trace = str(_get(e, "stack_trace", _get(e, "traceback", "")) or "")
        if trace:
            lines.append("   trace:")
            for tl in _truncate(trace, 30, MAX_ERROR_CHARS).splitlines():
                lines.append(f"     {tl}")
    if len(errors) > MAX_ERRORS:
        lines.append(f"... [{len(errors) - MAX_ERRORS} additional errors omitted]")
    return "\n".join(lines)


def _format_metadata(bundle: object) -> str:
    lines = ["## Gatherer Metadata"]
    gathered_at = _get(bundle, "gathered_at", None)
    if not gathered_at:
        gathered_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    lines.append(f"- gathered_at: {gathered_at}")
    version = _get(bundle, "gatherer_version", "unknown")
    lines.append(f"- gatherer_version: {version}")
    counts = []
    files = _get(bundle, "files", []) or []
    git = _get(bundle, "git_history", _get(bundle, "git_events", [])) or []
    errors = _get(bundle, "errors", []) or []
    try:
        counts.append(f"files={len(files)}")
        counts.append(f"git_events={len(git)}")
        counts.append(f"errors={len(errors)}")
    except Exception:
        pass
    if counts:
        lines.append(f"- collected: {', '.join(counts)}")
    notes = _get(bundle, "notes", "")
    if notes:
        lines.append(f"- notes: {_short(notes, 400)}")
    return "\n".join(lines)


# --- Public API ------------------------------------------------------------

def format_for_triage(bundle: "ContextBundle") -> str:
    """Serialize a :class:`ContextBundle` into concise structured text.

    The output is a deterministic, sectioned plain-text document suitable for
    inclusion in a triage-agent prompt. Sections, in order:

      1. Intake Request
      2. Related Files
      3. Recent Git History
      4. Recent Errors
      5. Gatherer Metadata

    Long snippets, diffs, and traces are truncated to keep the total size
    bounded for downstream LLM consumption.
    """
    if bundle is None:
        return "## Intake Request\n(empty bundle)"

    sections: list[str] = []

    req = _get(bundle, "request", _get(bundle, "intake_request", None))
    if req is not None:
        sections.append(_format_request(req))

    files = _get(bundle, "files", []) or []
    sections.append(_format_files(list(files)))

    git = _get(bundle, "git_history", _get(bundle, "git_events", [])) or []
    sections.append(_format_git(list(git)))

    errors = _get(bundle, "errors", []) or []
    sections.append(_format_errors(list(errors)))

    sections.append(_format_metadata(bundle))

    header = "# Context Bundle for Triage"
    return "\n\n".join([header, *sections]) + "\n"


__all__ = ["format_for_triage"]
