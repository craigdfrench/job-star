"""Duplicate detection against the goal registry."""
from __future__ import annotations

import re
from typing import Iterable

from .config import TriageConfig
from .models import DuplicateMatch, GoalRef, IntakeRequest


def _tokenize(text: str, stopwords: set[str]) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {t for t in tokens if t not in stopwords and len(t) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _title_similarity(req: IntakeRequest, goal: GoalRef,
                      stopwords: set[str]) -> float:
    """Weighted similarity: 70% title Jaccard, 30% description overlap."""
    req_title_tokens = _tokenize(req.title, stopwords)
    goal_title_tokens = _tokenize(goal.title, stopwords)
    title_sim = _jaccard(req_title_tokens, goal_title_tokens)

    req_desc_tokens = _tokenize(req.description, stopwords)
    goal_desc_tokens = _tokenize(goal.description, stopwords)
    desc_sim = _jaccard(req_desc_tokens, goal_desc_tokens)

    if req_desc_tokens and goal_desc_tokens:
        return 0.7 * title_sim + 0.3 * desc_sim
    return title_sim


def check_duplicates(
    request: IntakeRequest,
    goals: Iterable[GoalRef],
    config: TriageConfig,
) -> DuplicateMatch | None:
    """Find the best duplicate match among existing goals.

    Returns the highest-similarity match above the threshold, or None.
    """
    dup_cfg = config.duplicate
    if len(request.title) < dup_cfg.min_title_length:
        return None

    best_match: DuplicateMatch | None = None
    best_sim = 0.0

    for goal in goals:
        sim = _title_similarity(request, goal, dup_cfg.stopwords)

        # Boost similarity if tags overlap significantly
        req_tags = {t.lower() for t in request.tags}
        goal_tags = {t.lower() for t in goal.tags}
        if req_tags and goal_tags:
            tag_sim = _jaccard(req_tags, goal_tags)
            sim = 0.8 * sim + 0.2 * tag_sim

        if sim > best_sim:
            best_sim = sim
            if sim >= dup_cfg.similarity_threshold:
                reason = _explain_match(sim, request, goal, dup_cfg)
                best_match = DuplicateMatch(
                    goal_id=goal.id,
                    similarity=round(sim, 3),
                    reason=reason,
                )

    return best_match


def _explain_match(
    sim: float,
    request: IntakeRequest,
    goal: GoalRef,
    dup_cfg,
) -> str:
    level = "high" if sim >= dup_cfg.high_confidence_threshold else "possible"
    return (
        f"{level} duplicate (similarity={sim:.2f}) of goal "
        f"'{goal.title}' [{goal.id}]"
    )


// --- DUPLICATE BLOCK ---

"""
Duplicate checker: compares an incoming intake request against the goal registry.

Signals used (highest weight first):
  1. Exact source hash match  -> near-certain duplicate
  2. Title similarity (token-based Jaccard) -> strong signal
  3. Keyword overlap          -> moderate signal
  4. Same domain + shared keywords -> weak corroborating signal

Returns a DuplicateReport with candidates and a recommended action.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

from job_star.triage.goal_registry import GoalRecord, GoalRegistry

DuplicateAction = Literal["create", "merge", "link", "reject"]


@dataclass
class DuplicateCandidate:
    goal: GoalRecord
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class DuplicateReport:
    is_duplicate: bool
    confidence: float
    action: DuplicateAction
    candidates: list[DuplicateCandidate] = field(default_factory=list)
    source_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "is_duplicate": self.is_duplicate,
            "confidence": self.confidence,
            "action": self.action,
            "source_hash": self.source_hash,
            "candidates": [
                {
                    "goal_id": c.goal.goal_id,
                    "title": c.goal.title,
                    "score": c.score,
                    "reasons": c.reasons,
                }
                for c in self.candidates
            ],
        }


# Weights for each signal
W_EXACT_HASH = 1.0
W_TITLE = 0.6
W_KEYWORD = 0.3
W_DOMAIN_CORROBORATION = 0.1

THRESHOLD_MERGE = 0.85
THRESHOLD_LINK = 0.55
THRESHOLD_REJECT = 0.98  # exact hash duplicates


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _normalize(text)))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def compute_source_hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


def extract_keywords(text: str, max_keywords: int = 12) -> list[str]:
    """Very small keyword extractor: tokens longer than 3 chars, minus stopwords."""
    stop = {
        "the", "and", "for", "that", "this", "with", "from", "have", "will",
        "your", "you", "are", "not", "but", "can", "all", "need", "want",
        "into", "our", "their", "they", "them", "then", "than", "also",
        "make", "made", "just", "like", "what", "when", "which", "where",
        "about", "there", "here", "some", "more", "such", "only", "very",
    }
    tokens = re.findall(r"[a-z][a-z0-9]{2,}", _normalize(text))
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t in stop or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_keywords:
            break
    return out


class DuplicateChecker:
    def __init__(self, registry: GoalRegistry) -> None:
        self.registry = registry

    def check(
        self,
        title: str,
        source_text: str,
        domain: str | None = None,
        keywords: list[str] | None = None,
    ) -> DuplicateReport:
        source_hash = compute_source_hash(source_text)
        kw = keywords if keywords is not None else extract_keywords(source_text)
        title_tokens = _tokens(title)

        candidates: list[DuplicateCandidate] = []

        # Signal 1: exact source hash
        for rec in self.registry.find_by_source_hash(source_hash):
            candidates.append(
                DuplicateCandidate(
                    goal=rec,
                    score=W_EXACT_HASH,
                    reasons=["exact source hash match"],
                )
            )

        # Signal 2 + 3 + 4: scan active goals for title/keyword/domain overlap
        # To avoid scanning everything every time, narrow by domain if available,
        # otherwise fall back to keyword overlap scan.
        pool: list[GoalRecord]
        if domain:
            pool = self.registry.find_by_domain(domain)
        else:
            pool = self.registry.find_by_keyword_overlap(kw)

        seen_ids = {c.goal.goal_id for c in candidates}
        for rec in pool:
            if rec.goal_id in seen_ids:
                continue
            score = 0.0
            reasons: list[str] = []

            t_sim = _jaccard(title_tokens, _tokens(rec.title))
            if t_sim > 0.3:
                score += W_TITLE * t_sim
                reasons.append(f"title similarity {t_sim:.2f}")

            rec_kw = {k.lower() for k in rec.keywords}
            kw_set = {k.lower() for k in kw}
            kw_overlap = len(kw_set & rec_kw)
            if kw_overlap > 0:
                kw_score = W_KEYWORD * min(kw_overlap / 5.0, 1.0)
                score += kw_score
                reasons.append(f"keyword overlap {kw_overlap}")

            if domain and rec.domain == domain and kw_overlap > 0:
                score += W_DOMAIN_CORROBORATION
                reasons.append("same domain corroborates")

            if score > 0:
                candidates.append(
                    DuplicateCandidate(goal=rec, score=min(score, 1.0), reasons=reasons)
                )
                seen_ids.add(rec.goal_id)

        candidates.sort(key=lambda c: c.score, reverse=True)
        candidates = candidates[:5]

        if not candidates:
            return DuplicateReport(
                is_duplicate=False,
                confidence=0.0,
                action="create",
                candidates=[],
                source_hash=source_hash,
            )

        best = candidates[0]
        confidence = best.score

        if confidence >= THRESHOLD_REJECT:
            action: DuplicateAction = "reject"
            is_dup = True
        elif confidence >= THRESHOLD_MERGE:
            action = "merge"
            is_dup = True
        elif confidence >= THRESHOLD_LINK:
            action = "link"
            is_dup = True
        else:
            action = "create"
            is_dup = False

        return DuplicateReport(
            is_duplicate=is_dup,
            confidence=confidence,
            action=action,
            candidates=candidates,
            source_hash=source_hash,
        )


// --- DUPLICATE BLOCK ---

"""Duplicate detection against the goal registry."""
from __future__ import annotations

import re
from typing import Iterable

from .config import TriageConfig
from .models import DuplicateMatch, GoalRef, IntakeRequest


def _tokenize(text: str, stopwords: set[str]) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return {t for t in tokens if t not in stopwords and len(t) > 1}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def _title_similarity(req: IntakeRequest, goal: GoalRef,
                      stopwords: set[str]) -> float:
    """Weighted similarity: 70% title Jaccard, 30% description overlap."""
    req_title_tokens = _tokenize(req.title, stopwords)
    goal_title_tokens = _tokenize(goal.title, stopwords)
    title_sim = _jaccard(req_title_tokens, goal_title_tokens)

    req_desc_tokens = _tokenize(req.description, stopwords)
    goal_desc_tokens = _tokenize(goal.description, stopwords)
    desc_sim = _jaccard(req_desc_tokens, goal_desc_tokens)

    if req_desc_tokens and goal_desc_tokens:
        return 0.7 * title_sim + 0.3 * desc_sim
    return title_sim


def check_duplicates(
    request: IntakeRequest,
    goals: Iterable[GoalRef],
    config: TriageConfig,
) -> DuplicateMatch | None:
    """Find the best duplicate match among existing goals.

    Returns the highest-similarity match above the threshold, or None.
    """
    dup_cfg = config.duplicate
    if len(request.title) < dup_cfg.min_title_length:
        return None

    best_match: DuplicateMatch | None = None
    best_sim = 0.0

    for goal in goals:
        sim = _title_similarity(request, goal, dup_cfg.stopwords)

        # Boost similarity if tags overlap significantly
        req_tags = {t.lower() for t in request.tags}
        goal_tags = {t.lower() for t in goal.tags}
        if req_tags and goal_tags:
            tag_sim = _jaccard(req_tags, goal_tags)
            sim = 0.8 * sim + 0.2 * tag_sim

        if sim > best_sim:
            best_sim = sim
            if sim >= dup_cfg.similarity_threshold:
                reason = _explain_match(sim, request, goal, dup_cfg)
                best_match = DuplicateMatch(
                    goal_id=goal.id,
                    similarity=round(sim, 3),
                    reason=reason,
                )

    return best_match


def _explain_match(
    sim: float,
    request: IntakeRequest,
    goal: GoalRef,
    dup_cfg,
) -> str:
    level = "high" if sim >= dup_cfg.high_confidence_threshold else "possible"
    return (
        f"{level} duplicate (similarity={sim:.2f}) of goal "
        f"'{goal.title}' [{goal.id}]"
    )


// --- DUPLICATE BLOCK ---

"""
Duplicate checker: compares an incoming intake request against the goal registry.

Signals used (highest weight first):
  1. Exact source hash match  -> near-certain duplicate
  2. Title similarity (token-based Jaccard) -> strong signal
  3. Keyword overlap          -> moderate signal
  4. Same domain + shared keywords -> weak corroborating signal

Returns a DuplicateReport with candidates and a recommended action.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

from job_star.triage.goal_registry import GoalRecord, GoalRegistry

DuplicateAction = Literal["create", "merge", "link", "reject"]


@dataclass
class DuplicateCandidate:
    goal: GoalRecord
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class DuplicateReport:
    is_duplicate: bool
    confidence: float
    action: DuplicateAction
    candidates: list[DuplicateCandidate] = field(default_factory=list)
    source_hash: str = ""

    def to_dict(self) -> dict:
        return {
            "is_duplicate": self.is_duplicate,
            "confidence": self.confidence,
            "action": self.action,
            "source_hash": self.source_hash,
            "candidates": [
                {
                    "goal_id": c.goal.goal_id,
                    "title": c.goal.title,
                    "score": c.score,
                    "reasons": c.reasons,
                }
                for c in self.candidates
            ],
        }


# Weights for each signal
W_EXACT_HASH = 1.0
W_TITLE = 0.6
W_KEYWORD = 0.3
W_DOMAIN_CORROBORATION = 0.1

THRESHOLD_MERGE = 0.85
THRESHOLD_LINK = 0.55
THRESHOLD_REJECT = 0.98  # exact hash duplicates


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", _normalize(text)))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def compute_source_hash(text: str) -> str:
    return hashlib.sha256(_normalize(text).encode("utf-8")).hexdigest()


def extract_keywords(text: str, max_keywords: int = 12) -> list[str]:
    """Very small keyword extractor: tokens longer than 3 chars, minus stopwords."""
    stop = {
        "the", "and", "for", "that", "this", "with", "from", "have", "will",
        "your", "you", "are", "not", "but", "can", "all", "need", "want",
        "into", "our", "their", "they", "them", "then", "than", "also",
        "make", "made", "just", "like", "what", "when", "which", "where",
        "about", "there", "here", "some", "more", "such", "only", "very",
    }
    tokens = re.findall(r"[a-z][a-z0-9]{2,}", _normalize(text))
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t in stop or t in seen:
            continue
        seen.add(t)
        out.append(t)
        if len(out) >= max_keywords:
            break
    return out


class DuplicateChecker:
    def __init__(self, registry: GoalRegistry) -> None:
        self.registry = registry

    def check(
        self,
        title: str,
        source_text: str,
        domain: str | None = None,
        keywords: list[str] | None = None,
    ) -> DuplicateReport:
        source_hash = compute_source_hash(source_text)
        kw = keywords if keywords is not None else extract_keywords(source_text)
        title_tokens = _tokens(title)

        candidates: list[DuplicateCandidate] = []

        # Signal 1: exact source hash
        for rec in self.registry.find_by_source_hash(source_hash):
            candidates.append(
                DuplicateCandidate(
                    goal=rec,
                    score=W_EXACT_HASH,
                    reasons=["exact source hash match"],
                )
            )

        # Signal 2 + 3 + 4: scan active goals for title/keyword/domain overlap
        # To avoid scanning everything every time, narrow by domain if available,
        # otherwise fall back to keyword overlap scan.
        pool: list[GoalRecord]
        if domain:
            pool = self.registry.find_by_domain(domain)
        else:
            pool = self.registry.find_by_keyword_overlap(kw)

        seen_ids = {c.goal.goal_id for c in candidates}
        for rec in pool:
            if rec.goal_id in seen_ids:
                continue
            score = 0.0
            reasons: list[str] = []

            t_sim = _jaccard(title_tokens, _tokens(rec.title))
            if t_sim > 0.3:
                score += W_TITLE * t_sim
                reasons.append(f"title similarity {t_sim:.2f}")

            rec_kw = {k.lower() for k in rec.keywords}
            kw_set = {k.lower() for k in kw}
            kw_overlap = len(kw_set & rec_kw)
            if kw_overlap > 0:
                kw_score = W_KEYWORD * min(kw_overlap / 5.0, 1.0)
                score += kw_score
                reasons.append(f"keyword overlap {kw_overlap}")

            if domain and rec.domain == domain and kw_overlap > 0:
                score += W_DOMAIN_CORROBORATION
                reasons.append("same domain corroborates")

            if score > 0:
                candidates.append(
                    DuplicateCandidate(goal=rec, score=min(score, 1.0), reasons=reasons)
                )
                seen_ids.add(rec.goal_id)

        candidates.sort(key=lambda c: c.score, reverse=True)
        candidates = candidates[:5]

        if not candidates:
            return DuplicateReport(
                is_duplicate=False,
                confidence=0.0,
                action="create",
                candidates=[],
                source_hash=source_hash,
            )

        best = candidates[0]
        confidence = best.score

        if confidence >= THRESHOLD_REJECT:
            action: DuplicateAction = "reject"
            is_dup = True
        elif confidence >= THRESHOLD_MERGE:
            action = "merge"
            is_dup = True
        elif confidence >= THRESHOLD_LINK:
            action = "link"
            is_dup = True
        else:
            action = "create"
            is_dup = False

        return DuplicateReport(
            is_duplicate=is_dup,
            confidence=confidence,
            action=action,
            candidates=candidates,
            source_hash=source_hash,
        )
