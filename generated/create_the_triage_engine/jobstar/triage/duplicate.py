"""Duplicate detection against the goal registry.

Uses Jaccard similarity over tokenized text. This is intentionally
simple for the bootstrap phase — can be upgraded to embedding-based
similarity later.
"""

from __future__ import annotations

import re
from typing import Sequence

from .models import DuplicateMatch, GoalRegistryEntry, IntakeRequest

DEFAULT_THRESHOLD = 0.6


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def check_duplicate(
    request: IntakeRequest,
    registry: Sequence[GoalRegistryEntry],
    threshold: float = DEFAULT_THRESHOLD,
) -> DuplicateMatch:
    """Check if *request* is a duplicate of any entry in *registry*.

    Returns a DuplicateMatch. If a match exceeds *threshold*, it's
    considered a duplicate. The best-scoring match is returned.
    """
    req_tokens = _tokenize(f"{request.title} {request.description}")

    best_id = None
    best_score = 0.0

    for entry in registry:
        entry_tokens = _tokenize(f"{entry.title} {entry.description}")
        score = _jaccard(req_tokens, entry_tokens)
        if score > best_score:
            best_score = score
            best_id = entry.id

    is_dup = best_score >= threshold

    if is_dup:
        reason = f"Jaccard similarity {best_score:.2f} >= threshold {threshold:.2f}"
    elif best_score > 0:
        reason = f"Best similarity {best_score:.2f} below threshold {threshold:.2f}"
    else:
        reason = "No similar goals found in registry"

    return DuplicateMatch(
        is_duplicate=is_dup,
        matched_goal_id=best_id if is_dup else None,
        similarity_score=round(best_score, 4),
        reason=reason,
    )


// --- DUPLICATE BLOCK ---

"""
Duplicate detection against the goal registry.

Compares an incoming IntakeRequest against existing GoalRegistryEntry objects
using text similarity. Prefers TF-IDF cosine similarity (via sklearn) when
available, and falls back to a dependency-free token-overlap Jaccard score.

Usage:
    from triage.models import IntakeRequest, GoalRegistryEntry, DuplicateCheck
    from triage.duplicate import check_duplicates

    result: DuplicateCheck = check_duplicates(request, registry)
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import List, Optional

from triage.models import DuplicateCheck, GoalRegistryEntry, IntakeRequest

# --- Configuration ---------------------------------------------------------

DEFAULT_THRESHOLD = 0.65
"""Minimum similarity score to flag a request as a duplicate."""

CANDIDATE_LIMIT = 5
"""Max number of candidate matches to return in DuplicateCheck.candidates."""

# --- Text utilities --------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _normalize(text: str) -> str:
    """Lowercase and collapse to alphanumeric tokens."""
    return text.lower().strip()


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(_normalize(text))


def _entry_text(entry: GoalRegistryEntry) -> str:
    """Combine title + description for comparison."""
    parts = [entry.title, entry.description]
    return " ".join(p for p in parts if p)


# --- Similarity: token overlap (Jaccard) -----------------------------------

def _jaccard_similarity(a: List[str], b: List[str]) -> float:
    """Jaccard similarity over token sets."""
    set_a = set(a)
    set_b = set(b)
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _weighted_token_overlap(a: List[str], b: List[str]) -> float:
    """Token overlap weighted by term frequency (cosine of count vectors).

    Dependency-free cosine similarity. More forgiving than pure Jaccard
    when one text is much longer than the other.
    """
    if not a or not b:
        return 0.0
    ca = Counter(a)
    cb = Counter(b)
    # Dot product over shared terms
    shared = set(ca) & set(cb)
    if not shared:
        return 0.0
    dot = sum(ca[t] * cb[t] for t in shared)
    norm_a = math.sqrt(sum(v * v for v in ca.values()))
    norm_b = math.sqrt(sum(v * v for v in cb.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# --- Similarity: TF-IDF cosine (optional sklearn) --------------------------

def _tfidf_similarity(
    request_text: str, entry_texts: List[str]
) -> Optional[List[float]]:
    """Return cosine similarities via sklearn TF-IDF, or None if unavailable."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        return None

    if not entry_texts:
        return []

    corpus = [request_text] + entry_texts
    # sublinear_tf + min_df=1 keeps small registries working
    vectorizer = TfidfVectorizer(
        lowercase=True,
        token_pattern=r"[a-z0-9]+",
        ngram_range=(1, 2),
        sublinear_tf=True,
        min_df=1,
    )
    try:
        matrix = vectorizer.fit_transform(corpus)
    except ValueError:
        # Empty vocabulary
        return None

    # Row 0 is the request; rows 1..n are entries
    sims = cosine_similarity(matrix[0:1], matrix[1:]).flatten().tolist()
    return sims


# --- Public API ------------------------------------------------------------

def check_duplicates(
    request: IntakeRequest,
    registry: List[GoalRegistryEntry],
    threshold: float = DEFAULT_THRESHOLD,
    prefer_tfidf: bool = True,
) -> DuplicateCheck:
    """Check an incoming request against the goal registry for duplicates.

    Args:
        request: The incoming IntakeRequest.
        registry: List of existing GoalRegistryEntry objects.
        threshold: Similarity score at/above which we flag a duplicate.
        prefer_tfidf: Try sklearn TF-IDF first; fall back to token overlap.

    Returns:
        DuplicateCheck with is_duplicate flag, matched goal id, score,
        and a short list of candidate matches for review.
    """
    if not registry:
        return DuplicateCheck(
            is_duplicate=False,
            matched_goal_id=None,
            similarity_score=0.0,
            candidates=[],
            method="none",
        )

    request_text = request.raw_text
    entry_texts = [_entry_text(e) for e in registry]

    sims: Optional[List[float]] = None
    method = "token_overlap"

    if prefer_tfidf:
        sims = _tfidf_similarity(request_text, entry_texts)
        if sims is not None:
            method = "tfidf"

    if sims is None:
        req_tokens = _tokenize(request_text)
        sims = [
            _weighted_token_overlap(req_tokens, _tokenize(et))
            for et in entry_texts
        ]

    # Rank candidates
    ranked = sorted(
        zip(registry, sims), key=lambda pair: pair[1], reverse=True
    )

    candidates = [
        (entry.goal_id, round(float(score), 4))
        for entry, score in ranked[:CANDIDATE_LIMIT]
        if score > 0.0
    ]

    best_entry, best_score = ranked[0]
    best_score = float(best_score)

    is_dup = best_score >= threshold

    return DuplicateCheck(
        is_duplicate=is_dup,
        matched_goal_id=best_entry.goal_id if is_dup else None,
        similarity_score=round(best_score, 4),
        candidates=candidates,
        method=method,
    )


// --- DUPLICATE BLOCK ---

"""Duplicate detection against the goal registry.

Uses Jaccard similarity over tokenized text. This is intentionally
simple for the bootstrap phase — can be upgraded to embedding-based
similarity later.
"""

from __future__ import annotations

import re
from typing import Sequence

from .models import DuplicateMatch, GoalRegistryEntry, IntakeRequest

DEFAULT_THRESHOLD = 0.6


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def check_duplicate(
    request: IntakeRequest,
    registry: Sequence[GoalRegistryEntry],
    threshold: float = DEFAULT_THRESHOLD,
) -> DuplicateMatch:
    """Check if *request* is a duplicate of any entry in *registry*.

    Returns a DuplicateMatch. If a match exceeds *threshold*, it's
    considered a duplicate. The best-scoring match is returned.
    """
    req_tokens = _tokenize(f"{request.title} {request.description}")

    best_id = None
    best_score = 0.0

    for entry in registry:
        entry_tokens = _tokenize(f"{entry.title} {entry.description}")
        score = _jaccard(req_tokens, entry_tokens)
        if score > best_score:
            best_score = score
            best_id = entry.id

    is_dup = best_score >= threshold

    if is_dup:
        reason = f"Jaccard similarity {best_score:.2f} >= threshold {threshold:.2f}"
    elif best_score > 0:
        reason = f"Best similarity {best_score:.2f} below threshold {threshold:.2f}"
    else:
        reason = "No similar goals found in registry"

    return DuplicateMatch(
        is_duplicate=is_dup,
        matched_goal_id=best_id if is_dup else None,
        similarity_score=round(best_score, 4),
        reason=reason,
    )


// --- DUPLICATE BLOCK ---

"""
Duplicate detection against the goal registry.

Compares an incoming IntakeRequest against existing GoalRegistryEntry objects
using text similarity. Prefers TF-IDF cosine similarity (via sklearn) when
available, and falls back to a dependency-free token-overlap Jaccard score.

Usage:
    from triage.models import IntakeRequest, GoalRegistryEntry, DuplicateCheck
    from triage.duplicate import check_duplicates

    result: DuplicateCheck = check_duplicates(request, registry)
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import List, Optional

from triage.models import DuplicateCheck, GoalRegistryEntry, IntakeRequest

# --- Configuration ---------------------------------------------------------

DEFAULT_THRESHOLD = 0.65
"""Minimum similarity score to flag a request as a duplicate."""

CANDIDATE_LIMIT = 5
"""Max number of candidate matches to return in DuplicateCheck.candidates."""

# --- Text utilities --------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _normalize(text: str) -> str:
    """Lowercase and collapse to alphanumeric tokens."""
    return text.lower().strip()


def _tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(_normalize(text))


def _entry_text(entry: GoalRegistryEntry) -> str:
    """Combine title + description for comparison."""
    parts = [entry.title, entry.description]
    return " ".join(p for p in parts if p)


# --- Similarity: token overlap (Jaccard) -----------------------------------

def _jaccard_similarity(a: List[str], b: List[str]) -> float:
    """Jaccard similarity over token sets."""
    set_a = set(a)
    set_b = set(b)
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _weighted_token_overlap(a: List[str], b: List[str]) -> float:
    """Token overlap weighted by term frequency (cosine of count vectors).

    Dependency-free cosine similarity. More forgiving than pure Jaccard
    when one text is much longer than the other.
    """
    if not a or not b:
        return 0.0
    ca = Counter(a)
    cb = Counter(b)
    # Dot product over shared terms
    shared = set(ca) & set(cb)
    if not shared:
        return 0.0
    dot = sum(ca[t] * cb[t] for t in shared)
    norm_a = math.sqrt(sum(v * v for v in ca.values()))
    norm_b = math.sqrt(sum(v * v for v in cb.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# --- Similarity: TF-IDF cosine (optional sklearn) --------------------------

def _tfidf_similarity(
    request_text: str, entry_texts: List[str]
) -> Optional[List[float]]:
    """Return cosine similarities via sklearn TF-IDF, or None if unavailable."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        return None

    if not entry_texts:
        return []

    corpus = [request_text] + entry_texts
    # sublinear_tf + min_df=1 keeps small registries working
    vectorizer = TfidfVectorizer(
        lowercase=True,
        token_pattern=r"[a-z0-9]+",
        ngram_range=(1, 2),
        sublinear_tf=True,
        min_df=1,
    )
    try:
        matrix = vectorizer.fit_transform(corpus)
    except ValueError:
        # Empty vocabulary
        return None

    # Row 0 is the request; rows 1..n are entries
    sims = cosine_similarity(matrix[0:1], matrix[1:]).flatten().tolist()
    return sims


# --- Public API ------------------------------------------------------------

def check_duplicates(
    request: IntakeRequest,
    registry: List[GoalRegistryEntry],
    threshold: float = DEFAULT_THRESHOLD,
    prefer_tfidf: bool = True,
) -> DuplicateCheck:
    """Check an incoming request against the goal registry for duplicates.

    Args:
        request: The incoming IntakeRequest.
        registry: List of existing GoalRegistryEntry objects.
        threshold: Similarity score at/above which we flag a duplicate.
        prefer_tfidf: Try sklearn TF-IDF first; fall back to token overlap.

    Returns:
        DuplicateCheck with is_duplicate flag, matched goal id, score,
        and a short list of candidate matches for review.
    """
    if not registry:
        return DuplicateCheck(
            is_duplicate=False,
            matched_goal_id=None,
            similarity_score=0.0,
            candidates=[],
            method="none",
        )

    request_text = request.raw_text
    entry_texts = [_entry_text(e) for e in registry]

    sims: Optional[List[float]] = None
    method = "token_overlap"

    if prefer_tfidf:
        sims = _tfidf_similarity(request_text, entry_texts)
        if sims is not None:
            method = "tfidf"

    if sims is None:
        req_tokens = _tokenize(request_text)
        sims = [
            _weighted_token_overlap(req_tokens, _tokenize(et))
            for et in entry_texts
        ]

    # Rank candidates
    ranked = sorted(
        zip(registry, sims), key=lambda pair: pair[1], reverse=True
    )

    candidates = [
        (entry.goal_id, round(float(score), 4))
        for entry, score in ranked[:CANDIDATE_LIMIT]
        if score > 0.0
    ]

    best_entry, best_score = ranked[0]
    best_score = float(best_score)

    is_dup = best_score >= threshold

    return DuplicateCheck(
        is_duplicate=is_dup,
        matched_goal_id=best_entry.goal_id if is_dup else None,
        similarity_score=round(best_score, 4),
        candidates=candidates,
        method=method,
    )
