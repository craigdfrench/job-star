"""Classification logic: domain, urgency, type, and tag suggestion."""
from __future__ import annotations

import re
from typing import Iterable

from .config import TriageConfig
from .models import Classification, IntakeRequest


def _tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokenization."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _score_rules(
    text: str,
    keywords: dict[str, float],
    regexes: list[tuple[re.Pattern, float]],
    rule_weight: float,
) -> float:
    """Score a text against keyword + regex rules.

    Returns a raw score. Normalization to confidence happens in the caller.
    """
    lower = text.lower()
    tokens = set(_tokenize(lower))
    score = 0.0
    for kw, w in keywords.items():
        # Match multi-word keywords as substrings, single words as tokens
        if " " in kw:
            if kw in lower:
                score += w * rule_weight
        else:
            if kw in tokens:
                score += w * rule_weight
    for pattern, w in regexes:
        if pattern.search(text):
            score += w * rule_weight
    return score


def _normalize_confidence(score: float, all_scores: Iterable[float]) -> float:
    """Convert a raw score into a 0..1 confidence relative to the max."""
    max_score = max(all_scores)
    if max_score <= 0:
        return 0.0
    return round(score / max_score, 3)


def classify_domain(
    request: IntakeRequest, config: TriageConfig
) -> Classification:
    text = request.full_text
    scores: dict[str, float] = {}
    for name, rule in config.domains.items():
        scores[name] = _score_rules(
            text, rule.keywords, [], rule.weight
        )
    if not any(scores.values()):
        return Classification(label="unknown", confidence=0.0)
    best = max(scores, key=scores.get)
    conf = _normalize_confidence(scores[best], scores.values())
    return Classification(label=best, confidence=conf)


def classify_urgency(
    request: IntakeRequest, config: TriageConfig
) -> Classification:
    text = request.full_text
    scores: dict[str, float] = {}
    for name, rule in config.urgency.items():
        scores[name] = _score_rules(
            text, rule.keywords, rule.regex, rule.weight
        )

    # Default to "later" if nothing matched — a reasonable backlog default.
    if not any(scores.values()):
        return Classification(label="later", confidence=0.3)

    best = max(scores, key=scores.get)
    conf = _normalize_confidence(scores[best], scores.values())
    # Floor confidence a bit so we don't claim certainty from a single keyword
    conf = max(conf, 0.5) if scores[best] > 0 else conf
    return Classification(label=best, confidence=conf)


def classify_type(
    request: IntakeRequest, config: TriageConfig
) -> Classification:
    text = request.full_text
    scores: dict[str, float] = {}
    for name, rule in config.types.items():
        scores[name] = _score_rules(
            text, rule.keywords, rule.regex, rule.weight
        )
    if not any(scores.values()):
        return Classification(label="unknown", confidence=0.0)
    best = max(scores, key=scores.get)
    conf = _normalize_confidence(scores[best], scores.values())
    return Classification(label=best, confidence=conf)


def suggest_tags(
    request: IntakeRequest, config: TriageConfig
) -> list[str]:
    """Suggest tags based on the tag_map and token frequency."""
    tokens = _tokenize(request.full_text)
    tag_map = config.tagging.tag_map
    min_len = config.tagging.min_keyword_length
    max_tags = config.tagging.max_tags

    seen: set[str] = set()
    candidates: list[tuple[str, int]] = []

    # First pass: explicit tag_map matches
    for tok in tokens:
        if len(tok) < min_len:
            continue
        canonical = tag_map.get(tok)
        if canonical and canonical not in seen:
            seen.add(canonical)
            candidates.append((canonical, 1))

    # Second pass: frequent significant tokens not yet tagged
    freq: dict[str, int] = {}
    for tok in tokens:
        if len(tok) < min_len:
            continue
        freq[tok] = freq.get(tok, 0) + 1
    for tok, count in sorted(freq.items(), key=lambda x: -x[1]):
        if len(candidates) >= max_tags:
            break
        if tok in seen or tok in tag_map:
            continue
        seen.add(tok)
        candidates.append((tok, count))

    return [tag for tag, _ in candidates[:max_tags]]


// --- DUPLICATE BLOCK ---

"""Core classification engine.

Scores signals from rules.py against incoming request text and
produces a classification with confidence scores.
"""

from __future__ import annotations

from collections import defaultdict

from .models import (
    ClassificationResult,
    Domain,
    DuplicateStatus,
    IntakeRequest,
    RequestType,
    Urgency,
)
from .registry import DuplicateDetector
from .rules import ALL_SIGNALS, Signal


class Classifier:
    """Classifies intake requests by domain, urgency, and type."""

    def __init__(self, duplicate_detector: DuplicateDetector) -> None:
        self._dup_detector = duplicate_detector

    def classify(self, request: IntakeRequest) -> ClassificationResult:
        """Classify a single intake request."""
        text = request.full_text

        # Score all signals
        domain_scores: dict[Domain, float] = defaultdict(float)
        urgency_scores: dict[Urgency, float] = defaultdict(float)
        type_scores: dict[RequestType, float] = defaultdict(float)
        matched_signals: list[str] = []

        for signal in ALL_SIGNALS:
            if signal.matcher.matches(text):
                matched_signals.append(signal.name)
                if signal.domain:
                    domain_scores[signal.domain] += signal.weight
                if signal.urgency:
                    urgency_scores[signal.urgency] += signal.weight
                if signal.request_type:
                    type_scores[signal.request_type] += signal.weight

        # Pick best in each category
        domain = self._pick_best(domain_scores, Domain.UNKNOWN)
        urgency = self._pick_best(urgency_scores, Urgency.SOON)
        request_type = self._pick_best(type_scores, RequestType.UNKNOWN)

        # Calculate confidence
        confidence = self._calculate_confidence(
            domain_scores, urgency_scores, type_scores,
            domain, urgency, request_type,
        )

        # Check for duplicates
        dup_result = self._dup_detector.check(
            request, domain=domain if domain != Domain.UNKNOWN else None
        )

        return ClassificationResult(
            request_id=request.id,
            domain=domain,
            urgency=urgency,
            request_type=request_type,
            confidence=confidence,
            duplicate_status=dup_result.status,
            duplicate_of=dup_result.duplicate_of,
            related_goals=dup_result.related_goals,
            matched_signals=matched_signals,
        )

    def _pick_best(self, scores: dict, default) -> any:
        """Pick the highest-scoring category, or default if none matched."""
        if not scores:
            return default
        return max(scores.items(), key=lambda x: x[1])[0]

    def _calculate_confidence(
        self,
        domain_scores: dict[Domain, float],
        urgency_scores: dict[Urgency, float],
        type_scores: dict[RequestType, float],
        domain: Domain,
        urgency: Urgency,
        request_type: RequestType,
    ) -> float:
        """Calculate overall confidence in the classification.

        Confidence is based on:
        - Whether each category had any matches
        - The margin between top score and second-best
        - Normalized to 0.0-1.0
        """
        confidences: list[float] = []

        for scores, value, default in [
            (domain_scores, domain, Domain.UNKNOWN),
            (urgency_scores, urgency, Urgency.SOON),
            (type_scores, request_type, RequestType.UNKNOWN),
        ]:
            if not scores or value == default:
                confidences.append(0.3)  # Low confidence for defaults
                continue

            sorted_scores = sorted(scores.values(), reverse=True)
            top = sorted_scores[0]
            second = sorted_scores[1] if len(sorted_scores) > 1 else 0.0

            # Confidence from signal strength and margin
            strength = min(top / 3.0, 1.0)  # Normalize: 3+ weight = full
            margin = (top - second) / top if top > 0 else 0.0
            cat_conf = (strength * 0.6) + (margin * 0.4)
            confidences.append(cat_conf)

        return round(sum(confidences) / len(confidences), 3)


// --- DUPLICATE BLOCK ---

"""Rule-based triage classifier.

Takes an IntakeRequest and produces a ClassificationResult by scoring
keyword matches across domain, urgency, and type axes.

Designed to be swappable: the public `classify()` function is the
stable interface. The internal scoring can be replaced with an
LLM-based implementation without changing callers.
"""

from __future__ import annotations

import re
from typing import Optional

from triage.models import IntakeRequest, ClassificationResult
from triage.keywords import (
    DOMAIN_KEYWORDS,
    URGENCY_KEYWORDS,
    TYPE_KEYWORDS,
    DEFAULT_DOMAIN,
    DEFAULT_URGENCY,
    DEFAULT_TYPE,
    MIN_SCORE_THRESHOLD,
)

__all__ = ["classify", "check_duplicate"]


# ─── Scoring ──────────────────────────────────────────────────────────────

def _score_category(text: str, keywords: dict[str, dict[str, float]]) -> dict[str, float]:
    """Score every category in a keyword group against the text.

    Returns a dict of {category_name: total_score}.
    Uses word-boundary matching for single words and substring
    matching for multi-word phrases to reduce false positives.
    """
    scores: dict[str, float] = {}

    for category, terms in keywords.items():
        total = 0.0
        for term, weight in terms.items():
            if " " in term or "'" in term:
                # Multi-word or phrase — substring match (already lowercased text)
                if term in text:
                    total += weight
            else:
                # Single word — use word boundary to avoid partial matches
                # e.g. "test" shouldn't match "latest"
                pattern = r"\b" + re.escape(term) + r"\b"
                if re.search(pattern, text):
                    total += weight
        scores[category] = total

    return scores


def _pick_best(
    scores: dict[str, float],
    default: str,
    threshold: float = MIN_SCORE_THRESHOLD,
) -> tuple[str, float, float]:
    """Pick the best-scoring category.

    Returns (winner, confidence, top_score).
    Falls back to default if top score is below threshold.
    Confidence is a rough heuristic: top_score / (top_score + second_score),
    or 1.0 if there's only one contender, or 0.0 if falling back to default.
    """
    # Filter to nonzero scores, sorted descending
    ranked = sorted(
        ((cat, score) for cat, score in scores.items() if score > 0),
        key=lambda x: x[1],
        reverse=True,
    )

    if not ranked or ranked[0][1] < threshold:
        return default, 0.0, 0.0

    top_cat, top_score = ranked[0]

    if len(ranked) == 1:
        confidence = min(1.0, top_score / 3.0)  # normalize: 3.0 weight = full confidence
    else:
        second_score = ranked[1][1]
        confidence = top_score / (top_score + second_score) if (top_score + second_score) > 0 else 1.0

    return top_cat, confidence, top_score


# ─── Duplicate Detection ──────────────────────────────────────────────────

def check_duplicate(
    request: IntakeRequest,
    goal_registry: list[dict],
    similarity_threshold: float = 0.6,
) -> tuple[bool, Optional[str]]:
    """Check if a request is a duplicate of an existing goal.

    Uses a simple Jaccard similarity on token sets of the title.
    This is a placeholder heuristic — can be upgraded to embeddings later.

    Args:
        request: The incoming intake request.
        goal_registry: List of existing goals, each with at least 'id' and 'title'.
        similarity_threshold: Jaccard similarity above which we flag as duplicate.

    Returns:
        (is_duplicate, duplicate_of_goal_id)
    """
    def tokenize(s: str) -> set[str]:
        # Simple non-alphanumeric tokenization, lowercase
        return set(re.findall(r"\w+", s.lower()))

    request_tokens = tokenize(request.title)

    if not request_tokens:
        return False, None

    best_sim = 0.0
    best_goal_id: Optional[str] = None

    for goal in goal_registry:
        goal_title = goal.get("title", "")
        goal_tokens = tokenize(goal_title)
        if not goal_tokens:
            continue

        # Jaccard similarity
        intersection = request_tokens & goal_tokens
        union = request_tokens | goal_tokens
        sim = len(intersection) / len(union) if union else 0.0

        if sim > best_sim:
            best_sim = sim
            best_goal_id = goal.get("id")

    if best_sim >= similarity_threshold and best_goal_id:
        return True, best_goal_id

    return False, None


# ─── Public API ───────────────────────────────────────────────────────────

def classify(
    request: IntakeRequest,
    goal_registry: Optional[list[dict]] = None,
) -> ClassificationResult:
    """Classify an IntakeRequest into domain, urgency, and type.

    Args:
        request: The incoming intake request to classify.
        goal_registry: Optional list of existing goals for duplicate checking.
                       Each goal should be a dict with 'id' and 'title' keys.

    Returns:
        ClassificationResult with scores, confidence values, and duplicate info.
    """
    text = request.text

    # Score all three axes
    domain_scores = _score_category(text, DOMAIN_KEYWORDS)
    urgency_scores = _score_category(text, URGENCY_KEYWORDS)
    type_scores = _score_category(text, TYPE_KEYWORDS)

    # Pick winners
    domain, domain_conf, _ = _pick_best(domain_scores, DEFAULT_DOMAIN)
    urgency, urgency_conf, _ = _pick_best(urgency_scores, DEFAULT_URGENCY)
    req_type, type_conf, _ = _pick_best(type_scores, DEFAULT_TYPE)

    # Duplicate check
    is_dup = False
    dup_of: Optional[str] = None
    if goal_registry is not None:
        is_dup, dup_of = check_duplicate(request, goal_registry)

    return ClassificationResult(
        domain=domain,
        urgency=urgency,
        type=req_type,
        domain_scores=domain_scores,
        urgency_scores=urgency_scores,
        type_scores=type_scores,
        domain_confidence=round(domain_conf, 3),
        urgency_confidence=round(urgency_conf, 3),
        type_confidence=round(type_conf, 3),
        is_duplicate=is_dup,
        duplicate_of=dup_of,
    )


// --- DUPLICATE BLOCK ---

"""Classification logic: domain, urgency, type, and tag suggestion."""
from __future__ import annotations

import re
from typing import Iterable

from .config import TriageConfig
from .models import Classification, IntakeRequest


def _tokenize(text: str) -> list[str]:
    """Lowercase alphanumeric tokenization."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _score_rules(
    text: str,
    keywords: dict[str, float],
    regexes: list[tuple[re.Pattern, float]],
    rule_weight: float,
) -> float:
    """Score a text against keyword + regex rules.

    Returns a raw score. Normalization to confidence happens in the caller.
    """
    lower = text.lower()
    tokens = set(_tokenize(lower))
    score = 0.0
    for kw, w in keywords.items():
        # Match multi-word keywords as substrings, single words as tokens
        if " " in kw:
            if kw in lower:
                score += w * rule_weight
        else:
            if kw in tokens:
                score += w * rule_weight
    for pattern, w in regexes:
        if pattern.search(text):
            score += w * rule_weight
    return score


def _normalize_confidence(score: float, all_scores: Iterable[float]) -> float:
    """Convert a raw score into a 0..1 confidence relative to the max."""
    max_score = max(all_scores)
    if max_score <= 0:
        return 0.0
    return round(score / max_score, 3)


def classify_domain(
    request: IntakeRequest, config: TriageConfig
) -> Classification:
    text = request.full_text
    scores: dict[str, float] = {}
    for name, rule in config.domains.items():
        scores[name] = _score_rules(
            text, rule.keywords, [], rule.weight
        )
    if not any(scores.values()):
        return Classification(label="unknown", confidence=0.0)
    best = max(scores, key=scores.get)
    conf = _normalize_confidence(scores[best], scores.values())
    return Classification(label=best, confidence=conf)


def classify_urgency(
    request: IntakeRequest, config: TriageConfig
) -> Classification:
    text = request.full_text
    scores: dict[str, float] = {}
    for name, rule in config.urgency.items():
        scores[name] = _score_rules(
            text, rule.keywords, rule.regex, rule.weight
        )

    # Default to "later" if nothing matched — a reasonable backlog default.
    if not any(scores.values()):
        return Classification(label="later", confidence=0.3)

    best = max(scores, key=scores.get)
    conf = _normalize_confidence(scores[best], scores.values())
    # Floor confidence a bit so we don't claim certainty from a single keyword
    conf = max(conf, 0.5) if scores[best] > 0 else conf
    return Classification(label=best, confidence=conf)


def classify_type(
    request: IntakeRequest, config: TriageConfig
) -> Classification:
    text = request.full_text
    scores: dict[str, float] = {}
    for name, rule in config.types.items():
        scores[name] = _score_rules(
            text, rule.keywords, rule.regex, rule.weight
        )
    if not any(scores.values()):
        return Classification(label="unknown", confidence=0.0)
    best = max(scores, key=scores.get)
    conf = _normalize_confidence(scores[best], scores.values())
    return Classification(label=best, confidence=conf)


def suggest_tags(
    request: IntakeRequest, config: TriageConfig
) -> list[str]:
    """Suggest tags based on the tag_map and token frequency."""
    tokens = _tokenize(request.full_text)
    tag_map = config.tagging.tag_map
    min_len = config.tagging.min_keyword_length
    max_tags = config.tagging.max_tags

    seen: set[str] = set()
    candidates: list[tuple[str, int]] = []

    # First pass: explicit tag_map matches
    for tok in tokens:
        if len(tok) < min_len:
            continue
        canonical = tag_map.get(tok)
        if canonical and canonical not in seen:
            seen.add(canonical)
            candidates.append((canonical, 1))

    # Second pass: frequent significant tokens not yet tagged
    freq: dict[str, int] = {}
    for tok in tokens:
        if len(tok) < min_len:
            continue
        freq[tok] = freq.get(tok, 0) + 1
    for tok, count in sorted(freq.items(), key=lambda x: -x[1]):
        if len(candidates) >= max_tags:
            break
        if tok in seen or tok in tag_map:
            continue
        seen.add(tok)
        candidates.append((tok, count))

    return [tag for tag, _ in candidates[:max_tags]]


// --- DUPLICATE BLOCK ---

"""Core classification engine.

Scores signals from rules.py against incoming request text and
produces a classification with confidence scores.
"""

from __future__ import annotations

from collections import defaultdict

from .models import (
    ClassificationResult,
    Domain,
    DuplicateStatus,
    IntakeRequest,
    RequestType,
    Urgency,
)
from .registry import DuplicateDetector
from .rules import ALL_SIGNALS, Signal


class Classifier:
    """Classifies intake requests by domain, urgency, and type."""

    def __init__(self, duplicate_detector: DuplicateDetector) -> None:
        self._dup_detector = duplicate_detector

    def classify(self, request: IntakeRequest) -> ClassificationResult:
        """Classify a single intake request."""
        text = request.full_text

        # Score all signals
        domain_scores: dict[Domain, float] = defaultdict(float)
        urgency_scores: dict[Urgency, float] = defaultdict(float)
        type_scores: dict[RequestType, float] = defaultdict(float)
        matched_signals: list[str] = []

        for signal in ALL_SIGNALS:
            if signal.matcher.matches(text):
                matched_signals.append(signal.name)
                if signal.domain:
                    domain_scores[signal.domain] += signal.weight
                if signal.urgency:
                    urgency_scores[signal.urgency] += signal.weight
                if signal.request_type:
                    type_scores[signal.request_type] += signal.weight

        # Pick best in each category
        domain = self._pick_best(domain_scores, Domain.UNKNOWN)
        urgency = self._pick_best(urgency_scores, Urgency.SOON)
        request_type = self._pick_best(type_scores, RequestType.UNKNOWN)

        # Calculate confidence
        confidence = self._calculate_confidence(
            domain_scores, urgency_scores, type_scores,
            domain, urgency, request_type,
        )

        # Check for duplicates
        dup_result = self._dup_detector.check(
            request, domain=domain if domain != Domain.UNKNOWN else None
        )

        return ClassificationResult(
            request_id=request.id,
            domain=domain,
            urgency=urgency,
            request_type=request_type,
            confidence=confidence,
            duplicate_status=dup_result.status,
            duplicate_of=dup_result.duplicate_of,
            related_goals=dup_result.related_goals,
            matched_signals=matched_signals,
        )

    def _pick_best(self, scores: dict, default) -> any:
        """Pick the highest-scoring category, or default if none matched."""
        if not scores:
            return default
        return max(scores.items(), key=lambda x: x[1])[0]

    def _calculate_confidence(
        self,
        domain_scores: dict[Domain, float],
        urgency_scores: dict[Urgency, float],
        type_scores: dict[RequestType, float],
        domain: Domain,
        urgency: Urgency,
        request_type: RequestType,
    ) -> float:
        """Calculate overall confidence in the classification.

        Confidence is based on:
        - Whether each category had any matches
        - The margin between top score and second-best
        - Normalized to 0.0-1.0
        """
        confidences: list[float] = []

        for scores, value, default in [
            (domain_scores, domain, Domain.UNKNOWN),
            (urgency_scores, urgency, Urgency.SOON),
            (type_scores, request_type, RequestType.UNKNOWN),
        ]:
            if not scores or value == default:
                confidences.append(0.3)  # Low confidence for defaults
                continue

            sorted_scores = sorted(scores.values(), reverse=True)
            top = sorted_scores[0]
            second = sorted_scores[1] if len(sorted_scores) > 1 else 0.0

            # Confidence from signal strength and margin
            strength = min(top / 3.0, 1.0)  # Normalize: 3+ weight = full
            margin = (top - second) / top if top > 0 else 0.0
            cat_conf = (strength * 0.6) + (margin * 0.4)
            confidences.append(cat_conf)

        return round(sum(confidences) / len(confidences), 3)


// --- DUPLICATE BLOCK ---

"""Rule-based triage classifier.

Takes an IntakeRequest and produces a ClassificationResult by scoring
keyword matches across domain, urgency, and type axes.

Designed to be swappable: the public `classify()` function is the
stable interface. The internal scoring can be replaced with an
LLM-based implementation without changing callers.
"""

from __future__ import annotations

import re
from typing import Optional

from triage.models import IntakeRequest, ClassificationResult
from triage.keywords import (
    DOMAIN_KEYWORDS,
    URGENCY_KEYWORDS,
    TYPE_KEYWORDS,
    DEFAULT_DOMAIN,
    DEFAULT_URGENCY,
    DEFAULT_TYPE,
    MIN_SCORE_THRESHOLD,
)

__all__ = ["classify", "check_duplicate"]


# ─── Scoring ──────────────────────────────────────────────────────────────

def _score_category(text: str, keywords: dict[str, dict[str, float]]) -> dict[str, float]:
    """Score every category in a keyword group against the text.

    Returns a dict of {category_name: total_score}.
    Uses word-boundary matching for single words and substring
    matching for multi-word phrases to reduce false positives.
    """
    scores: dict[str, float] = {}

    for category, terms in keywords.items():
        total = 0.0
        for term, weight in terms.items():
            if " " in term or "'" in term:
                # Multi-word or phrase — substring match (already lowercased text)
                if term in text:
                    total += weight
            else:
                # Single word — use word boundary to avoid partial matches
                # e.g. "test" shouldn't match "latest"
                pattern = r"\b" + re.escape(term) + r"\b"
                if re.search(pattern, text):
                    total += weight
        scores[category] = total

    return scores


def _pick_best(
    scores: dict[str, float],
    default: str,
    threshold: float = MIN_SCORE_THRESHOLD,
) -> tuple[str, float, float]:
    """Pick the best-scoring category.

    Returns (winner, confidence, top_score).
    Falls back to default if top score is below threshold.
    Confidence is a rough heuristic: top_score / (top_score + second_score),
    or 1.0 if there's only one contender, or 0.0 if falling back to default.
    """
    # Filter to nonzero scores, sorted descending
    ranked = sorted(
        ((cat, score) for cat, score in scores.items() if score > 0),
        key=lambda x: x[1],
        reverse=True,
    )

    if not ranked or ranked[0][1] < threshold:
        return default, 0.0, 0.0

    top_cat, top_score = ranked[0]

    if len(ranked) == 1:
        confidence = min(1.0, top_score / 3.0)  # normalize: 3.0 weight = full confidence
    else:
        second_score = ranked[1][1]
        confidence = top_score / (top_score + second_score) if (top_score + second_score) > 0 else 1.0

    return top_cat, confidence, top_score


# ─── Duplicate Detection ──────────────────────────────────────────────────

def check_duplicate(
    request: IntakeRequest,
    goal_registry: list[dict],
    similarity_threshold: float = 0.6,
) -> tuple[bool, Optional[str]]:
    """Check if a request is a duplicate of an existing goal.

    Uses a simple Jaccard similarity on token sets of the title.
    This is a placeholder heuristic — can be upgraded to embeddings later.

    Args:
        request: The incoming intake request.
        goal_registry: List of existing goals, each with at least 'id' and 'title'.
        similarity_threshold: Jaccard similarity above which we flag as duplicate.

    Returns:
        (is_duplicate, duplicate_of_goal_id)
    """
    def tokenize(s: str) -> set[str]:
        # Simple non-alphanumeric tokenization, lowercase
        return set(re.findall(r"\w+", s.lower()))

    request_tokens = tokenize(request.title)

    if not request_tokens:
        return False, None

    best_sim = 0.0
    best_goal_id: Optional[str] = None

    for goal in goal_registry:
        goal_title = goal.get("title", "")
        goal_tokens = tokenize(goal_title)
        if not goal_tokens:
            continue

        # Jaccard similarity
        intersection = request_tokens & goal_tokens
        union = request_tokens | goal_tokens
        sim = len(intersection) / len(union) if union else 0.0

        if sim > best_sim:
            best_sim = sim
            best_goal_id = goal.get("id")

    if best_sim >= similarity_threshold and best_goal_id:
        return True, best_goal_id

    return False, None


# ─── Public API ───────────────────────────────────────────────────────────

def classify(
    request: IntakeRequest,
    goal_registry: Optional[list[dict]] = None,
) -> ClassificationResult:
    """Classify an IntakeRequest into domain, urgency, and type.

    Args:
        request: The incoming intake request to classify.
        goal_registry: Optional list of existing goals for duplicate checking.
                       Each goal should be a dict with 'id' and 'title' keys.

    Returns:
        ClassificationResult with scores, confidence values, and duplicate info.
    """
    text = request.text

    # Score all three axes
    domain_scores = _score_category(text, DOMAIN_KEYWORDS)
    urgency_scores = _score_category(text, URGENCY_KEYWORDS)
    type_scores = _score_category(text, TYPE_KEYWORDS)

    # Pick winners
    domain, domain_conf, _ = _pick_best(domain_scores, DEFAULT_DOMAIN)
    urgency, urgency_conf, _ = _pick_best(urgency_scores, DEFAULT_URGENCY)
    req_type, type_conf, _ = _pick_best(type_scores, DEFAULT_TYPE)

    # Duplicate check
    is_dup = False
    dup_of: Optional[str] = None
    if goal_registry is not None:
        is_dup, dup_of = check_duplicate(request, goal_registry)

    return ClassificationResult(
        domain=domain,
        urgency=urgency,
        type=req_type,
        domain_scores=domain_scores,
        urgency_scores=urgency_scores,
        type_scores=type_scores,
        domain_confidence=round(domain_conf, 3),
        urgency_confidence=round(urgency_conf, 3),
        type_confidence=round(type_conf, 3),
        is_duplicate=is_dup,
        duplicate_of=dup_of,
    )
