"""
Contradiction detector.

Detects goals that directly oppose each other. Uses keyword/phrase
patterns to identify opposing intents within the same domain or
across domains when they target the same subject.

Examples:
- "Increase X" vs "Decrease X"
- "Adopt technology A" vs "Remove technology A"
- "Expand team" vs "Reduce headcount"
"""

import re
from typing import Any

from job_star.conflict.models import Conflict
from job_star.conflict.types import ConflictType, Severity
from job_star.goal.models import Goal

# Opposing action pairs — if two goals use these against the same subject,
# they likely contradict.
OPPOSING_PAIRS: list[tuple[str, str]] = [
    (r"\bincrease\b", r"\bdecrease\b"),
    (r"\bgrow\b", r"\bshrink\b"),
    (r"\bexpand\b", r"\bcontract\b"),
    (r"\badd\b", r"\bremove\b"),
    (r"\badopt\b", r"\babandon\b"),
    (r"\bstart\b", r"\bstop\b"),
    (r"\blaunch\b", r"\bshut\s*down\b"),
    (r"\bhire\b", r"\blay\s*off\b"),
    (r"\bbuild\b", r"\bdismantle\b"),
    (r"\bopen\b", r"\bclose\b"),
    (r"\bupgrade\b", r"\bdowngrade\b"),
    (r"\bspeed\s*up\b", r"\bslow\s*down\b"),
    (r"\bmaximize\b", r"\bminimize\b"),
    (r"\benable\b", r"\bdisable\b"),
    (r"\bswitch\s*to\b", r"\brevert\s*from\b"),
]


def _extract_subject(text: str, action_pattern: str) -> str | None:
    """
    Try to extract the subject of an action from text.
    Looks for the action word followed by a noun phrase.
    """
    pattern = action_pattern + r"\s+(.+?)(?:\s+(?:to|for|in|by|with|from)\b|$|[,;.])"
    match = re.search(pattern, text, re.IGNORECASE)
    if match:
        subject = match.group(1).strip().lower()
        # Filter out very short or generic subjects
        if len(subject) > 2:
            return subject
    return None


def _subjects_overlap(subj_a: str, subj_b: str) -> bool:
    """Check if two subjects refer to the same thing."""
    # Simple overlap check: one contains the other or they share significant words
    words_a = set(subj_a.split())
    words_b = set(subj_b.split())
    if not words_a or not words_b:
        return False
    # If either is a substring of the other
    if subj_a in subj_b or subj_b in subj_a:
        return True
    # If they share all words of the shorter subject
    shorter = words_a if len(words_a) <= len(words_b) else words_b
    longer = words_b if len(words_a) <= len(words_b) else words_a
    return shorter.issubset(longer)


class ContradictionDetector:
    """Detects contradictory goals using opposing action patterns."""

    name: str = "contradiction_detector"

    def detect(self, goals: list[Goal]) -> list[Conflict]:
        conflicts: list[Conflict] = []

        # Build a list of (goal, action_index, subject) for all goals
        detected_actions: list[tuple[Goal, int, str]] = []
        for goal in goals:
            text = f"{goal.title} {getattr(goal, 'description', '') or ''}".lower()
            for pair_idx, (action_a, action_b) in enumerate(OPPOSING_PAIRS):
                for action_pat in (action_a, action_b):
                    if re.search(action_pat, text):
                        subject = _extract_subject(text, action_pat)
                        if subject:
                            detected_actions.append((goal, pair_idx, subject))

        # Compare detected actions for contradictions
        for i, (goal_a, pair_a, subj_a) in enumerate(detected_actions):
            for goal_b, pair_b, subj_b in detected_actions[i + 1 :]:
                # Must be same opposing pair and different goals
                if goal_a.id == goal_b.id:
                    continue
                if pair_a != pair_b:
                    continue
                if _subjects_overlap(subj_a, subj_b):
                    conflicts.append(
                        Conflict(
                            conflict_type=ConflictType.CONTRADICTION,
                            severity=Severity.BLOCKING,
                            goal_ids=(goal_a.id, goal_b.id),
                            description=(
                                f"Goals have contradictory actions on "
                                f"the same subject: '{subj_a}'"
                            ),
                            evidence={
                                "opposing_pair_index": pair_a,
                                "subject_a": subj_a,
                                "subject_b": subj_b,
                                "goal_a_title": goal_a.title,
                                "goal_b_title": goal_b.title,
                            },
                            suggested_resolution=(
                                "These goals directly contradict each other. "
                                "Choose one direction or find a compromise."
                            ),
                        )
                    )

        return conflicts
