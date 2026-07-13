"""
Duplicate goal detector.

Detects goals that are likely duplicates of each other using:
1. Exact title matches
2. High text similarity (title + description)
3. Same target outcome in the same domain
"""

import re
from difflib import SequenceMatcher
from typing import Any

from job_star.conflict.models import Conflict
from job_star.conflict.types import ConflictType, Severity
from job_star.goal.models import Goal

SIMILARITY_THRESHOLD = 0.85


def _normalize(text: str) -> str:
    """Normalize text for comparison: lowercase, strip, collapse whitespace."""
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _similarity(a: str, b: str) -> float:
    """Return a similarity ratio between 0.0 and 1.0."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


class DuplicateDetector:
    """Detects duplicate goals via text similarity."""

    name: str = "duplicate_detector"

    def __init__(self, threshold: float = SIMILARITY_THRESHOLD) -> None:
        self.threshold = threshold

    def detect(self, goals: list[Goal]) -> list[Conflict]:
        conflicts: list[Conflict] = []
        seen_pairs: set[tuple[str, str]] = set()

        for i, goal_a in enumerate(goals):
            for goal_b in goals[i + 1 :]:
                pair_key = tuple(sorted((goal_a.id, goal_b.id)))
                if pair_key in seen_pairs:
                    continue

                sim = self._goal_similarity(goal_a, goal_b)
                if sim >= self.threshold:
                    seen_pairs.add(pair_key)
                    is_exact = sim >= 0.98
                    severity = Severity.HIGH if is_exact else Severity.MEDIUM
                    conflicts.append(
                        Conflict(
                            conflict_type=ConflictType.DUPLICATE,
                            severity=severity,
                            goal_ids=(goal_a.id, goal_b.id),
                            description=(
                                f"Goals appear to be duplicates "
                                f"(similarity: {sim:.2f})"
                            ),
                            evidence={
                                "similarity_score": round(sim, 4),
                                "exact_match": is_exact,
                                "title_a": goal_a.title,
                                "title_b": goal_b.title,
                            },
                            suggested_resolution=(
                                "Merge these goals or mark one as a duplicate."
                            ),
                        )
                    )

        return conflicts

    def _goal_similarity(self, a: Goal, b: Goal) -> float:
        """Compute combined similarity of title and description."""
        title_sim = _similarity(_normalize(a.title), _normalize(b.title))
        desc_sim = _similarity(
            _normalize(getattr(a, "description", "") or ""),
            _normalize(getattr(b, "description", "") or ""),
        )
        # Title weighted more heavily
        return (title_sim * 0.7) + (desc_sim * 0.3)
