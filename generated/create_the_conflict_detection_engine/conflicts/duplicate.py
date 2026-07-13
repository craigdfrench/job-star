"""
Duplicate detection engine for Job-Star.

Detects when two goals are duplicates using multi-signal scoring:
1. Semantic similarity (text comparison of title + description)
2. Structural similarity (overlapping steps, resources, outputs)
3. Temporal overlap (same urgency/timeframe)
4. Domain match

Each signal produces a 0.0–1.0 score. A weighted combination
produces the final duplicate confidence. If confidence exceeds
the configured threshold, the pair is flagged as a duplicate.
"""

from dataclasses import dataclass, field
from typing import Optional
from difflib import SequenceMatcher
from collections import Counter

from jobstar.conflict.base import (
    ConflictType,
    ConflictSeverity,
    ConflictResult,
    Goal,
)


# ──────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────

DEFAULT_DUPLICATE_CONFIG = {
    # Signal weights (must sum to ~1.0)
    "weight_semantic": 0.45,
    "weight_structural": 0.30,
    "weight_temporal": 0.10,
    "weight_domain": 0.15,

    # Thresholds
    "threshold_duplicate": 0.75,       # confidence >= this → DUPLICATE
    "threshold_likely": 0.60,          # confidence >= this → LIKELY_DUPLICATE (lower severity)
    "threshold_semantic_strong": 0.85, # semantic alone above this → strong signal

    # Structural sub-weights
    "structural_weight_steps": 0.40,
    "structural_weight_resources": 0.35,
    "structural_weight_outputs": 0.25,

    # Minimum overlap counts to consider structural match meaningful
    "min_steps_for_overlap": 1,
    "min_resources_for_overlap": 1,
}


# ──────────────────────────────────────────────
# Result type
# ──────────────────────────────────────────────

@dataclass
class DuplicateResult(ConflictResult):
    """Result specifically from duplicate detection."""
    conflict_type: ConflictType = ConflictType.DUPLICATE
    is_duplicate: bool = False
    is_likely: bool = False


# ──────────────────────────────────────────────
# Similarity helpers
# ──────────────────────────────────────────────

def _text_similarity(a: str, b: str) -> float:
    """Rapid text similarity using SequenceMatcher.

    This is a lightweight fallback. In production, this should be
    replaced with embedding-based similarity (e.g., sentence-transformers).
    The interface remains the same.
    """
    if not a or not b:
        return 0.0
    # Normalize: lowercase, strip, collapse whitespace
    a_norm = " ".join(a.lower().split())
    b_norm = " ".join(b.lower().split())
    return SequenceMatcher(None, a_norm, b_norm).ratio()


def _list_overlap(a: list[str], b: list[str]) -> tuple[float, int]:
    """Compute overlap ratio between two string lists.

    Returns (overlap_ratio, overlap_count).
    Uses fuzzy matching for each pair to handle minor wording differences.
    """
    if not a or not b:
        return (0.0, 0)

    matches = 0
    for item_a in a:
        for item_b in b:
            sim = _text_similarity(item_a, item_b)
            if sim >= 0.70:  # per-item match threshold
                matches += 1
                break  # count each item_a at most once

    # Overlap ratio: matches relative to the smaller list
    min_len = min(len(a), len(b))
    return (matches / min_len if min_len > 0 else 0.0, matches)


def _jaccard_similarity(a: list[str], b: list[str]) -> float:
    """Jaccard similarity for tag-like lists (exact match after normalization)."""
    set_a = set(x.lower().strip() for x in a)
    set_b = set(x.lower().strip() for x in b)
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


# ──────────────────────────────────────────────
# Signal scorers
# ──────────────────────────────────────────────

def _score_semantic(goal_a: Goal, goal_b: Goal) -> float:
    """Semantic similarity from title + description."""
    title_sim = _text_similarity(goal_a.title, goal_b.title)
    desc_sim = _text_similarity(goal_a.description, goal_b.description)

    # Title is more important than description for duplicate detection
    # but description provides disambiguation
    if title_sim >= 0.90:
        # Very similar titles — weight description more to confirm
        return 0.60 * title_sim + 0.40 * desc_sim
    else:
        return 0.70 * title_sim + 0.30 * desc_sim


def _score_structural(goal_a: Goal, goal_b: Goal, config: dict) -> float:
    """Structural similarity from steps, resources, outputs."""
    w_steps = config["structural_weight_steps"]
    w_resources = config["structural_weight_resources"]
    w_outputs = config["structural_weight_outputs"]

    steps_ratio, steps_count = _list_overlap(goal_a.steps, goal_b.steps)
    resources_ratio, resources_count = _list_overlap(goal_a.resources, goal_b.resources)
    outputs_ratio, outputs_count = _list_overlap(goal_a.expected_outputs, goal_b.expected_outputs)

    # Penalize if lists are empty (no signal) vs genuinely overlapping
    # If both goals have no steps, that's not evidence of duplication
    step_score = steps_ratio if (goal_a.steps and goal_b.steps) else 0.0
    resource_score = resources_ratio if (goal_a.resources and goal_b.resources) else 0.0
    output_score = outputs_ratio if (goal_a.expected_outputs and goal_b.expected_outputs) else 0.0

    # If all structural lists are empty, return a neutral score
    has_any = any([goal_a.steps, goal_b.steps,
                   goal_a.resources, goal_b.resources,
                   goal_a.expected_outputs, goal_b.expected_outputs])
    if not has_any:
        return 0.5  # neutral — no structural evidence either way

    return (w_steps * step_score +
            w_resources * resource_score +
            w_outputs * output_score)


def _score_temporal(goal_a: Goal, goal_b: Goal) -> float:
    """Temporal overlap from urgency level."""
    urgency_order = {
        "idle-opportunistic": 0,
        "background": 1,
        "normal": 2,
        "elevated": 3,
        "urgent": 4,
        "critical": 5,
    }

    u_a = urgency_order.get(goal_a.urgency, 2)
    u_b = urgency_order.get(goal_b.urgency, 2)

    # Exact match → 1.0, adjacent → 0.7, far apart → lower
    diff = abs(u_a - u_b)
    if diff == 0:
        return 1.0
    elif diff == 1:
        return 0.7
    elif diff == 2:
        return 0.4
    else:
        return 0.1


def _score_domain(goal_a: Goal, goal_b: Goal) -> float:
    """Domain match score, including tag overlap."""
    domain_match = 1.0 if goal_a.domain == goal_b.domain else 0.0
    tag_sim = _jaccard_similarity(goal_a.tags, goal_b.tags)

    # Domain exact match is primary; tags provide secondary signal
    if goal_a.tags or goal_b.tags:
        return 0.70 * domain_match + 0.30 * tag_sim
    return domain_match


# ──────────────────────────────────────────────
# Main detector
# ──────────────────────────────────────────────

class DuplicateDetector:
    """Detects duplicate goals using multi-signal scoring.

    Usage:
        detector = DuplicateDetector()
        result = detector.compare(goal_a, goal_b)
        if result.is_duplicate:
            print(f"Duplicate detected: {result.explanation}")

        # Or scan all pairs in a collection:
        results = detector.scan_all(goals)
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = {**DEFAULT_DUPLICATE_CONFIG, **(config or {})}

    def compare(self, goal_a: Goal, goal_b: Goal) -> DuplicateResult:
        """Compare two goals and return a duplicate detection result."""
        # Don't compare a goal with itself
        if goal_a.id == goal_b.id:
            return DuplicateResult(
                severity=ConflictSeverity.INFO,
                goal_a_id=goal_a.id,
                goal_b_id=goal_b.id,
                confidence=1.0,
                explanation="Same goal compared with itself.",
                is_duplicate=False,
            )

        # Compute individual signals
        semantic = _score_semantic(goal_a, goal_b)
        structural = _score_structural(goal_a, goal_b, self.config)
        temporal = _score_temporal(goal_a, goal_b)
        domain = _score_domain(goal_a, goal_b)

        # Weighted combination
        confidence = (
            self.config["weight_semantic"] * semantic +
            self.config["weight_structural"] * structural +
            self.config["weight_temporal"] * temporal +
            self.config["weight_domain"] * domain
        )

        # Determine if duplicate
        is_duplicate = confidence >= self.config["threshold_duplicate"]
        is_likely = (not is_duplicate) and (confidence >= self.config["threshold_likely"])

        # Strong semantic signal alone can trigger duplicate
        if (not is_duplicate and
                semantic >= self.config["threshold_semantic_strong"] and
                domain >= 0.7):
            is_duplicate = True
            confidence = max(confidence, self.config["threshold_duplicate"])

        # Determine severity
        if is_duplicate:
            severity = ConflictSeverity.HIGH
            suggested = "Merge or cancel one of these goals."
        elif is_likely:
            severity = ConflictSeverity.MEDIUM
            suggested = "Review for potential duplication."
        elif confidence >= 0.45:
            severity = ConflictSeverity.LOW
            suggested = "Monitor — some overlap detected."
        else:
            severity = ConflictSeverity.INFO
            suggested = ""

        # Build explanation
        signals = {
            "semantic": round(semantic, 3),
            "structural": round(structural, 3),
            "temporal": round(temporal, 3),
            "domain": round(domain, 3),
            "confidence": round(confidence, 3),
        }

        explanation = self._build_explanation(
            goal_a, goal_b, signals, is_duplicate, is_likely
        )

        return DuplicateResult(
            severity=severity,
            goal_a_id=goal_a.id,
            goal_b_id=goal_b.id,
            confidence=round(confidence, 3),
            explanation=explanation,
            suggested_action=suggested,
            signals=signals,
            is_duplicate=is_duplicate,
            is_likely=is_likely,
        )

    def scan_all(self, goals: list[Goal]) -> list[DuplicateResult]:
        """Scan all pairs of goals for duplicates.

        Returns only results where is_duplicate or is_likely is True.
        """
        results = []
        n = len(goals)
        for i in range(n):
            for j in range(i + 1, n):
                result = self.compare(goals[i], goals[j])
                if result.is_duplicate or result.is_likely:
                    results.append(result)
        return results

    def find_duplicates_of(self, target: Goal, candidates: list[Goal]) -> list[DuplicateResult]:
        """Find all goals that are duplicates of a specific target goal."""
        results = []
        for candidate in candidates:
            if candidate.id == target.id:
                continue
            result = self.compare(target, candidate)
            if result.is_duplicate or result.is_likely:
                results.append(result)
        return results

    def _build_explanation(
        self, goal_a: Goal, goal_b: Goal,
        signals: dict, is_dup: bool, is_likely: bool
    ) -> str:
        """Human-readable explanation of the detection result."""
        status = "DUPLICATE" if is_dup else ("LIKELY DUPLICATE" if is_likely else "partial overlap")

        parts = [
            f"[{status}] '{goal_a.title}' ↔ '{goal_b.title}'",
            f"  Confidence: {signals['confidence']:.1%}",
            f"  Semantic: {signals['semantic']:.1%} | "
            f"Structural: {signals['structural']:.1%} | "
            f"Temporal: {signals['temporal']:.1%} | "
            f"Domain: {signals['domain']:.1%}",
        ]

        # Add specific signal explanations
        if signals["semantic"] > 0.8:
            parts.append(f"  → Titles/descriptions are highly similar")
        if signals["structural"] > 0.6:
            parts.append(f"  → Significant overlap in steps, resources, or outputs")
        if signals["domain"] >= 1.0 and goal_a.domain == goal_b.domain:
            parts.append(f"  → Same domain: '{goal_a.domain}'")

        return "\n".join(parts)
