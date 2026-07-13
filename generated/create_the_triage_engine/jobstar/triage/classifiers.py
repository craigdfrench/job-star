"""Keyword-based classifiers for domain, urgency, and request type.

These are intentionally rule-based for the bootstrap phase. They can be
swapped for ML-based classifiers later without changing the interface.
"""

from __future__ import annotations

import re
from typing import Tuple

from .models import Domain, IntakeRequest, RequestType, Urgency

# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------

_DOMAIN_KEYWORDS: dict[Domain, list[str]] = {
    Domain.META: [
        "job-star", "job star", "triage", "bootstrap", "workflow",
        "agent", "supervisor", "self-hosted", "system itself",
    ],
    Domain.BACKEND: [
        "api", "endpoint", "server", "database", "db", "sql", "postgres",
        "redis", "queue", "worker", "service", "auth", "authentication",
        "orm", "migration", "schema",
    ],
    Domain.FRONTEND: [
        "ui", "ux", "css", "react", "vue", "svelte", "component",
        "page", "button", "form", "layout", "frontend", "render",
        "dom", "tailwind",
    ],
    Domain.INFRA: [
        "docker", "kubernetes", "k8s", "deploy", "deployment", "ci",
        "cd", "pipeline", "terraform", "ansible", "nginx", "load balancer",
        "infrastructure", "container", "helm",
    ],
    Domain.DATA: [
        "data", "etl", "pipeline", "analytics", "metrics", "report",
        "dashboard", "warehouse", "ingest", "transform", "dataset",
    ],
    Domain.DOCS: [
        "docs", "documentation", "readme", "wiki", "guide", "tutorial",
        "manual", "help text",
    ],
    Domain.SECURITY: [
        "security", "vulnerability", "cve", "auth", "permission", "rbac",
        "encryption", "token", "secret", "audit", "compliance",
    ],
}

_URGENCY_KEYWORDS: dict[Urgency, list[str]] = {
    Urgency.NOW: [
        "urgent", "critical", "blocker", "blocking", "down", "outage",
        "broken", "cannot", "can't", "immediately", "asap", "production down",
        "sev1", "sev-1", "p0",
    ],
    Urgency.SOON: [
        "soon", "this week", "this sprint", "needed", "should",
        "important", "priority", "p1", "sev2",
    ],
    Urgency.LATER: [
        "later", "next week", "next sprint", "queued", "planned",
        "p2", "when possible",
    ],
    Urgency.EVENTUALLY: [
        "eventually", "backlog", "nice to have", "nice-to-have",
        "someday", "future", "idea", "p3", "p4", "wishlist",
    ],
}

_TYPE_KEYWORDS: dict[RequestType, list[str]] = {
    RequestType.BUG: [
        "bug", "error", "crash", "fail", "failure", "broken", "wrong",
        "incorrect", "exception", "traceback", "issue", "regression",
        "doesn't work", "does not work",
    ],
    RequestType.FEATURE: [
        "feature", "add", "support", "new", "request", "enable",
        "allow", "implement", "build", "create", "want", "need",
        "should be able to",
    ],
    RequestType.REFACTOR: [
        "refactor", "cleanup", "clean up", "restructure", "reorganize",
        "simplify", "technical debt", "tech debt", "improve code",
        "rename", "extract",
    ],
    RequestType.QUESTION: [
        "question", "how do i", "how to", "what is", "why does",
        "is it possible", "can i", "help me understand", "clarify",
        "wondering",
    ],
    RequestType.CHORE: [
        "chore", "update", "upgrade", "dependency", "bump version",
        "maintenance", "housekeeping", "rotate", "renew",
    ],
}

# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, split on whitespace."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _score_text(text: str, keywords: list[str]) -> int:
    """Count keyword occurrences in text (case-insensitive substring match)."""
    lower = text.lower()
    score = 0
    for kw in keywords:
        if kw in lower:
            score += 1
    return score


def _best_match(
    text: str, table: dict
) -> Tuple[object, int, float]:
    """Return (best_key, best_score, confidence) from a keyword table.

    Confidence = best_score / (best_score + second_best_score + epsilon)
    If no match, returns (default, 0, 0.0).
    """
    scores = []
    for key, keywords in table.items():
        s = _score_text(text, keywords)
        scores.append((key, s))
    scores.sort(key=lambda x: x[1], reverse=True)

    if not scores or scores[0][1] == 0:
        return None, 0, 0.0

    best_key, best_score = scores[0]
    second_score = scores[1][1] if len(scores) > 1 else 0
    confidence = best_score / (best_score + second_score + 1e-6)
    return best_key, best_score, round(confidence, 3)


# ---------------------------------------------------------------------------
# Public classifier functions
# ---------------------------------------------------------------------------

def classify_domain(request: IntakeRequest) -> Tuple[Domain, float]:
    text = f"{request.title} {request.description}"
    key, _, conf = _best_match(text, _DOMAIN_KEYWORDS)
    return (key or Domain.UNKNOWN, conf)


def classify_urgency(request: IntakeRequest) -> Tuple[Urgency, float]:
    text = f"{request.title} {request.description}"
    key, _, conf = _best_match(text, _URGENCY_KEYWORDS)
    # Default urgency if no signal
    return (key or Urgency.SOON, conf)


def classify_type(request: IntakeRequest) -> Tuple[RequestType, float]:
    text = f"{request.title} {request.description}"
    key, _, conf = _best_match(text, _TYPE_KEYWORDS)
    return (key or RequestType.UNKNOWN, conf)
