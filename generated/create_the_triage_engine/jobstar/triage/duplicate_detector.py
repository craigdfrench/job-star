"""
Duplicate Detector — checks incoming intake requests against the goal registry.

Uses a layered approach:
    1. Exact title match (normalized)
    2. Keyword overlap (Jaccard on keyword sets)
    3. TF-IDF cosine similarity on title + description

Returns a DuplicateReport with the best candidate (if any) and a confidence score.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from job_star.triage.goal_registry import Goal, GoalRegistry


# ---- text utilities ----

_STOPWORDS: Set[str] = {
    "the", "a", "an", "and", "or", "but", "for", "to", "of", "in", "on",
    "is", "are", "be", "with", "by", "this", "that", "it", "as", "at",
    "from", "into", "build", "create", "make", "set", "up", "do", "get",
    "need", "want", "should", "will", "can", "we", "i", "you", "our",
}

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    """Lowercase, collapse whitespace, strip punctuation edges."""
    return " ".join(text.lower().split()).strip()


def tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokens, stopwords removed."""
    return [
        t for t in _TOKEN_RE.findall(text.lower())
        if t not in _STOPWORDS and len(t) > 1
    ]


def keyword_set(text: str) -> Set[str]:
    return set(tokenize(text))


# ---- TF-IDF ----

class TfidfIndex:
    """
    Minimal in-memory TF-IDF index over a corpus of documents.
    Recomputed on demand — fine for bootstrap-scale registries.
    """

    def __init__(self) -> None:
        self._docs: List[List[str]] = []
        self._doc_ids: List[str] = []
        self._idf: Dict[str, float] = {}
        self._doc_vectors: List[Dict[str, float]] = []

    def build(self, docs: List[Tuple[str, str]]) -> None:
        """docs: list of (id, text)."""
        self._doc_ids = [d[0] for d in docs]
        self._docs = [tokenize(d[1]) for d in docs]
        n = len(self._docs)

        # document frequency
        df: Dict[str, int] = {}
        for tokens in self._docs:
            for term in set(tokens):
                df[term] = df.get(term, 0) + 1

        # idf with smoothing
        self._idf = {
            term: math.log((1 + n) / (1 + count)) + 1.0
            for term, count in df.items()
        }

        # tf-idf vectors
        self._doc_vectors = []
        for tokens in self._docs:
            vec: Dict[str, float] = {}
            length = len(tokens) or 1
            for term in tokens:
                tf = tokens.count(term) / length
                vec[term] = tf * self._idf.get(term, 0.0)
            self._doc_vectors.append(vec)

    def query(self, text: str) -> List[Tuple[str, float]]:
        """Return list of (doc_id, cosine_similarity) sorted desc."""
        q_tokens = tokenize(text)
        if not q_tokens or not self._doc_vectors:
            return []

        q_vec: Dict[str, float] = {}
        length = len(q_tokens)
        for term in q_tokens:
            tf = q_tokens.count(term) / length
            q_vec[term] = tf * self._idf.get(term, 0.0)

        q_norm = math.sqrt(sum(v * v for v in q_vec.values())) or 1.0
        results: List[Tuple[str, float]] = []
        for doc_id, d_vec in zip(self._doc_ids, self._doc_vectors):
            d_norm = math.sqrt(sum(v * v for v in d_vec.values())) or 1.0
            dot = sum(q_vec.get(t, 0.0) * v for t, v in d_vec.items())
            sim = dot / (q_norm * d_norm)
            results.append((doc_id, sim))
        results.sort(key=lambda x: x[1], reverse=True)
        return results


# ---- duplicate report ----

@dataclass
class DuplicateCandidate:
    goal_id: str
    title: str
    score: float
    reasons: List[str] = field(default_factory=list)


@dataclass
class DuplicateReport:
    is_duplicate: bool
    confidence: float
    best_candidate: Optional[DuplicateCandidate] = None
    all_candidates: List[DuplicateCandidate] = field(default_factory=list)
    method: str = "none"


# ---- detector ----

class DuplicateDetector:
    """
    Detects duplicate intake requests against the goal registry.

    Thresholds are configurable; defaults are conservative to avoid
    false merges during bootstrap.
    """

    def __init__(
        self,
        registry: GoalRegistry,
        exact_threshold: float = 1.0,
        keyword_threshold: float = 0.6,
        tfidf_threshold: float = 0.75,
        combined_threshold: float = 0.7,
    ) -> None:
        self.registry = registry
        self.exact_threshold = exact_threshold
        self.keyword_threshold = keyword_threshold
        self.tfidf_threshold = tfidf_threshold
        self.combined_threshold = combined_threshold
        self._index = TfidfIndex()
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        goals = self.registry.active_goals()
        docs = [(g.id, f"{g.title} {g.description}") for g in goals]
        self._index.build(docs)

    # ---- public API ----

    def check(
        self,
        title: str,
        description: str = "",
        domain: Optional[str] = None,
    ) -> DuplicateReport:
        """
        Check an intake request for duplicates.

        Args:
            title: Request title.
            description: Request description (optional).
            domain: If provided, only compare against goals in same domain.

        Returns:
            DuplicateReport with decision and best candidate.
        """
        goals = self.registry.active_goals()
        if domain:
            goals = [g for g in goals if g.domain == domain]
        if not goals:
            return DuplicateReport(is_duplicate=False, confidence=0.0)

        query_text = f"{title} {description}".strip()
        norm_query = normalize(title)

        candidates: List[DuplicateCandidate] = []

        # Layer 1: exact title match
        for g in goals:
            if normalize(g.title) == norm_query:
                candidates.append(DuplicateCandidate(
                    goal_id=g.id,
                    title=g.title,
                    score=1.0,
                    reasons=["exact_title_match"],
                ))

        if candidates:
            best = max(candidates, key=lambda c: c.score)
            return DuplicateReport(
                is_duplicate=True,
                confidence=1.0,
                best_candidate=best,
                all_candidates=candidates,
                method="exact",
            )

        # Layer 2: keyword overlap (Jaccard)
        query_kw = keyword_set(query_text)
        for g in goals:
            g_kw = set(g.keywords) or keyword_set(f"{g.title} {g.description}")
            if not g_kw or not query_kw:
                continue
            jaccard = len(query_kw & g_kw) / len(query_kw | g_kw)
            if jaccard >= self.keyword_threshold:
                candidates.append(DuplicateCandidate(
                    goal_id=g.id,
                    title=g.title,
                    score=jaccard,
                    reasons=[f"keyword_overlap={jaccard:.2f}"],
                ))

        # Layer 3: TF-IDF cosine similarity
        tfidf_results = self._index.query(query_text)
        id_to_goal = {g.id: g for g in goals}
        for goal_id, sim in tfidf_results:
            if goal_id not in id_to_goal:
                continue
            if sim >= self.tfidf_threshold:
                g = id_to_goal[goal_id]
                # avoid double-adding if already a keyword candidate
                existing = next((c for c in candidates if c.goal_id == goal_id), None)
                if existing:
                    existing.reasons.append(f"tfidf_sim={sim:.2f}")
                    existing.score = max(existing.score, sim)
                else:
                    candidates.append(DuplicateCandidate(
                        goal_id=goal_id,
                        title=g.title,
                        score=sim,
                        reasons=[f"tfidf_sim={sim:.2f}"],
                    ))

        if not candidates:
            return DuplicateReport(is_duplicate=False, confidence=0.0)

        # Combine: if a candidate appears via multiple signals, boost score
        best = max(candidates, key=lambda c: c.score)
        # multi-signal boost
        signal_count = len(best.reasons)
        boosted = min(1.0, best.score + 0.1 * (signal_count - 1))

        is_dup = boosted >= self.combined_threshold
        return DuplicateReport(
            is_duplicate=is_dup,
            confidence=round(boosted, 3),
            best_candidate=best,
            all_candidates=sorted(candidates, key=lambda c: c.score, reverse=True),
            method="combined",
        )

    def refresh(self) -> None:
        """Rebuild the TF-IDF index after registry changes."""
        self._rebuild_index()
