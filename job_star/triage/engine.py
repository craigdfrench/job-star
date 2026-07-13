"""Triage engine: classifies intake requests and checks for duplicates.

Classifies by:
- Domain (coding, personal, infra, meta)
- Urgency (imperative, soon, idle-opportunistic, timed)
- Type (bug, feature, refactor, question, chore, etc.)

Then checks against existing goals for duplicates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..models import Domain, Goal, IntakeRequest, TriageResult, Urgency
from ..db import list_goals


# ============================================================================
# Keyword-based classification rules
# ============================================================================

DOMAIN_KEYWORDS: dict[Domain, list[str]] = {
    Domain.META: [
        "job-star", "jobstar", "bootstrap", "self-host", "orchestrat",
        "triage", "router", "supervisor", "idle loop", "follow-up",
        "conflict detection", "goal registry", "system itself",
    ],
    Domain.CODING: [
        "bug", "fix", "code", "function", "api", "endpoint", "test",
        "refactor", "type error", "crash", "stack trace", "compile",
        "import", "module", "class", "method", "merge", "deploy",
        "feature", "implement", "build", "database", "query",
    ],
    Domain.INFRA: [
        "docker", "kubernetes", "nginx", "caddy", "dns", "ssl",
        "firewall", "network", "server", "port", "systemd", "service",
        "container", "vps", "cloud", "terraform", "ansible",
    ],
    Domain.PERSONAL: [
        "maddy", "family", "photo", "image", "video", "youtube",
        "google takeout", "email", "calendar", "home assistant",
        "budget", "health", "exercise", "reading", "learn",
    ],
}

# Expert detection: keywords that indicate a goal should be owned by a
# specialized expert agent. Maps expert name → detection keywords/paths.
EXPERT_KEYWORDS: dict[str, list[str]] = {
    "job-star": [
        "job star", "job-star", "job_star", "check-in", "checkin",
        "upgrade tool", "blue-green", "schema migration", "worker registry",
        "orphan step", "reap", "triage engine", "follow-up engine",
        "idle loop", "goal registry", "PRExecutor", "UPGRADE.md",
        "schema_migrations", "worker_registry", "check_ins",
        "graceful shutdown", "drain signal", "health endpoint",
    ],
    "gatehouse-ai": [
        "gatehouse", "gatehouse-ai", "cog-proxy", "model_costs",
        "cost_class", "routing_advice", "quota_pool", "windsurf_daily",
        "x_gatehouse", "provider registration", "mpp", "model provider protocol",
        "aperture", "litellm", "bifrost", "rate limit", "cost ledger",
        "admin tokens", "trust tier", "deferred jobs", "prompt packing",
        "/etc/gatehouse", "100.64.158.87:8090", "gatehouse-ai.craigdfrench.com",
    ],
    "research": [
        "tickle file", "research", "monitor", "check-in", "check in",
        "recurring", "monthly check", "track developments", "follow up on",
        "keep an eye on", "watch for", "stay updated", "stay current",
        "new insights", "interesting articles", "tickle",
    ],
}

URGENCY_KEYWORDS: dict[Urgency, list[str]] = {
    Urgency.IMPERATIVE: [
        "urgent", "critical", "blocking", "broken", "down", "crash",
        "production", "security", "vulnerability", "asap", "immediately",
        "broken", "not working", "failing", "error",
    ],
    Urgency.SOON: [
        "soon", "important", "should", "need", "want", "plan",
        "next", "this week", "priority",
    ],
    Urgency.IDLE_OPPORTUNISTIC: [
        "eventually", "nice to have", "backlog", "someday", "when free",
        "idle", "opportunistic", "low priority", "whenever",
    ],
    Urgency.TIMED: [
        "deadline", "by friday", "by monday", "before", "due",
        "schedule", "remind", "tomorrow", "next week",
    ],
}

TYPE_KEYWORDS: dict[str, list[str]] = {
    "bug": ["bug", "fix", "broken", "error", "crash", "fail", "wrong", "incorrect"],
    "feature": ["add", "create", "build", "implement", "new", "support", "enable"],
    "refactor": ["refactor", "clean up", "reorganize", "simplify", "rename", "restructure"],
    "question": ["how", "what", "why", "where", "which", "should", "could", "?"],
    "chore": ["update", "upgrade", "dependency", "cleanup", "maintain", "version"],
    "docs": ["document", "readme", "guide", "wiki", "explain", "describe"],
    "research": ["investigate", "explore", "analyze", "study", "compare", "evaluate"],
}


def _score_text(text: str, keywords: dict[str, list[str]]) -> dict[str, float]:
    """Score text against keyword categories. Returns normalized scores."""
    text_lower = text.lower()
    scores: dict[str, float] = {}
    for category, words in keywords.items():
        score = 0.0
        for kw in words:
            if kw in text_lower:
                score += 1.0
        scores[category] = score
    # Normalize
    total = sum(scores.values()) or 1.0
    return {k: v / total for k, v in scores.items()}


def _classify_domain(request: IntakeRequest) -> tuple[Domain, float]:
    """Classify the domain. Returns (domain, confidence)."""
    if request.domain_override:
        return request.domain_override, 1.0

    scores = _score_text(request.full_text, DOMAIN_KEYWORDS)
    if not scores or max(scores.values()) == 0:
        return Domain.CODING, 0.3  # default

    best = max(scores, key=scores.get)
    confidence = scores[best]
    return Domain(best), confidence


def _classify_urgency(request: IntakeRequest) -> tuple[Urgency, float]:
    """Classify urgency. Returns (urgency, confidence)."""
    if request.urgency_override:
        return request.urgency_override, 1.0

    scores = _score_text(request.full_text, URGENCY_KEYWORDS)
    if not scores or max(scores.values()) == 0:
        return Urgency.SOON, 0.3  # default

    best = max(scores, key=scores.get)
    return Urgency(best), scores[best]


def _classify_type(request: IntakeRequest) -> tuple[str, float]:
    """Classify request type. Returns (type, confidence)."""
    scores = _score_text(request.full_text, TYPE_KEYWORDS)
    if not scores or max(scores.values()) == 0:
        return "feature", 0.3

    best = max(scores, key=scores.get)
    return best, scores[best]


def _extract_keywords(text: str, max_count: int = 10) -> list[str]:
    """Extract significant keywords from text."""
    # Remove common stop words
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
        "have", "has", "had", "do", "does", "did", "will", "would", "could",
        "should", "may", "might", "must", "can", "to", "of", "in", "for",
        "on", "at", "by", "with", "from", "as", "into", "through", "during",
        "and", "or", "but", "if", "then", "else", "when", "where", "why",
        "how", "what", "which", "who", "whom", "this", "that", "these",
        "those", "i", "you", "he", "she", "it", "we", "they", "me", "him",
        "her", "us", "them", "my", "your", "his", "its", "our", "their",
    }
    words = re.findall(r"[a-z]{3,}", text.lower())
    keywords = [w for w in words if w not in stop_words]
    # Deduplicate while preserving order
    seen = set()
    result = []
    for w in keywords:
        if w not in seen:
            seen.add(w)
            result.append(w)
    return result[:max_count]


# ============================================================================
# Duplicate detection
# ============================================================================

def _tokenize(text: str) -> set[str]:
    """Tokenize text for similarity comparison."""
    return set(re.findall(r"[a-z]{2,}", text.lower()))


def _jaccard_similarity(a: set[str], b: set[str]) -> float:
    """Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    intersection = a & b
    union = a | b
    return len(intersection) / len(union) if union else 0.0


def _check_duplicates(
    request: IntakeRequest,
    existing_goals: list[Goal],
    threshold: float = 0.65,
) -> tuple[bool, Optional[str], float]:
    """Check if request duplicates an existing goal.

    Returns (is_duplicate, duplicate_goal_id, confidence).
    """
    request_tokens = _tokenize(request.full_text)

    best_match: Optional[str] = None
    best_score = 0.0

    for goal in existing_goals:
        if goal.status == GoalStatus.COMPLETED or goal.status == GoalStatus.ABANDONED:
            continue
        goal_text = f"{goal.title} {goal.description or ''}"
        goal_tokens = _tokenize(goal_text)
        score = _jaccard_similarity(request_tokens, goal_tokens)

        if score > best_score:
            best_score = score
            best_match = goal.id

    is_dup = best_score >= threshold
    return is_dup, best_match if is_dup else None, best_score


def _detect_expert(request: IntakeRequest) -> Optional[str]:
    """Detect which expert agent should own this request.

    Returns the expert name (e.g. 'gatehouse-ai') or None for the generic pool.
    """
    text = request.full_text
    for expert, keywords in EXPERT_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                return expert
    return None


# Need GoalStatus for duplicate check
from ..models import GoalStatus


# ============================================================================
# Main triage function
# ============================================================================

async def triage(request: IntakeRequest, check_duplicates: bool = True) -> TriageResult:
    """Triage an intake request: classify and check for duplicates.

    Args:
        request: The raw intake request.
        check_duplicates: Whether to check against existing goals.

    Returns:
        TriageResult with classification and duplicate info.
    """
    # Classify
    domain, domain_conf = _classify_domain(request)
    urgency, urgency_conf = _classify_urgency(request)
    req_type, type_conf = _classify_type(request)

    # Extract keywords
    keywords = _extract_keywords(request.full_text)

    # Detect expert
    expert = _detect_expert(request)

    # Check duplicates
    is_dup = False
    dup_of = None
    dup_conf = 0.0
    if check_duplicates:
        existing = await list_goals()
        is_dup, dup_of, dup_conf = _check_duplicates(request, existing)

    # Overall confidence
    confidence = (domain_conf + urgency_conf + type_conf) / 3.0

    # Build rationale
    rationale_parts = [
        f"domain={domain.value}({domain_conf:.1f})",
        f"urgency={urgency.value}({urgency_conf:.1f})",
        f"type={req_type}({type_conf:.1f})",
    ]
    if is_dup:
        rationale_parts.append(f"DUPLICATE of {dup_of}({dup_conf:.2f})")
    if expert:
        rationale_parts.append(f"expert={expert}")

    return TriageResult(
        domain=domain,
        urgency=urgency,
        request_type=req_type,
        confidence=confidence,
        is_duplicate=is_dup,
        duplicate_of=dup_of,
        duplicate_confidence=dup_conf,
        keywords=keywords,
        rationale=" | ".join(rationale_parts),
        expert=expert,
    )