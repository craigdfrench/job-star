"""Conflict detection between goals.

Detects:
- Duplicates (same goal registered twice)
- Contradictions (goals that pull in opposite directions)
- Resource conflicts (both need the same limited resource)
- Tensions (goals that create friction if pursued simultaneously)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations
from typing import Optional

from ..models import Conflict, ConflictType, Goal, GoalStatus
from ..db import create_conflict, list_goals


# ============================================================================
# Detection strategies
# ============================================================================

def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z]{2,}", text.lower()))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _detect_duplicate(a: Goal, b: Goal, threshold: float = 0.70) -> Optional[str]:
    """Check if two goals are duplicates."""
    tokens_a = _tokenize(f"{a.title} {a.description or ''}")
    tokens_b = _tokenize(f"{b.title} {b.description or ''}")
    score = _jaccard(tokens_a, tokens_b)
    if score >= threshold:
        return f"Duplicate detected (similarity={score:.2f}): similar titles/descriptions"
    return None


def _detect_contradiction(a: Goal, b: Goal) -> Optional[str]:
    """Check if two goals contradict each other."""
    # Simple heuristic: look for antonym patterns
    antonym_pairs = [
        ("remove", "add"), ("delete", "create"), ("disable", "enable"),
        ("stop", "start"), ("upgrade", "downgrade"), ("refactor", "rewrite"),
        ("merge", "split"), ("increase", "decrease"), ("open", "close"),
    ]

    text_a = f"{a.title} {a.description or ''}".lower()
    text_b = f"{b.title} {b.description or ''}".lower()

    for word_a, word_b in antonym_pairs:
        if word_a in text_a and word_b in text_b:
            return f"Possible contradiction: '{word_a}' vs '{word_b}'"
        if word_b in text_a and word_a in text_b:
            return f"Possible contradiction: '{word_b}' vs '{word_a}'"

    return None


def _detect_resource_conflict(a: Goal, b: Goal) -> Optional[str]:
    """Check if two goals compete for the same resource."""
    # Resources that can be contested
    resource_patterns = [
        (r"port\s*(\d+)", "port"),
        (r"/(?:home|var|tmp|etc)/\S+", "file path"),
        (r"docker\s+container\s+(\S+)", "docker container"),
        (r"systemd\s+service\s+(\S+)", "systemd service"),
    ]

    text_a = f"{a.title} {a.description or ''}".lower()
    text_b = f"{b.title} {b.description or ''}".lower()

    for pattern, resource_type in resource_patterns:
        matches_a = set(re.findall(pattern, text_a))
        matches_b = set(re.findall(pattern, text_b))
        shared = matches_a & matches_b
        if shared:
            return f"Resource conflict on {resource_type}: {shared}"

    return None


def _detect_tension(a: Goal, b: Goal) -> Optional[str]:
    """Check if two goals create tension (not direct conflict but friction)."""
    # Same domain, different urgency → tension on attention
    if a.domain == b.domain and a.urgency != b.urgency:
        higher = max(a.urgency.value, b.urgency.value)
        lower = min(a.urgency.value, b.urgency.value)
        return f"Tension: same domain ({a.domain.value}) but different urgency ({higher} vs {lower})"

    return None


# ============================================================================
# Main conflict detection
# ============================================================================

async def detect_conflicts(
    goals: list[Goal] | None = None,
    save: bool = True,
    incremental_goal_id: str | None = None,
) -> list[tuple[str, str, ConflictType, str]]:
    """Detect conflicts among goals.

    Args:
        goals: Goals to check. If None, loads from DB.
        save: Whether to save detected conflicts to the database.
        incremental_goal_id: If provided, only check this goal against all
            others (O(n) instead of O(n²)). Use this on intake when only one
            new goal was added.

    Returns:
        List of (goal_a_id, goal_b_id, conflict_type, description) tuples.
    """
    if goals is None:
        goals = await list_goals()

    # Only check active goals
    active = [g for g in goals if g.status in (GoalStatus.ACTIVE, GoalStatus.BLOCKED, GoalStatus.PAUSED)]

    conflicts: list[tuple[str, str, ConflictType, str]] = []

    # Build pairs to check: all pairs, or just the incremental goal vs others
    if incremental_goal_id:
        target = next((g for g in active if g.id == incremental_goal_id), None)
        if not target:
            return []
        pairs = [(target, g) for g in active if g.id != incremental_goal_id]
    else:
        pairs = list(combinations(active, 2))

    # Load existing conflicts for dedup (avoid duplicate rows)
    existing_conflicts: set[tuple[str, str, str]] = set()
    if save:
        from ..db import get_pool
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT goal_a_id, goal_b_id, conflict_type FROM goal_conflicts"
                )
            for r in rows:
                # Normalize: store both orderings
                a, b = str(r["goal_a_id"]), str(r["goal_b_id"])
                ct = r["conflict_type"]
                existing_conflicts.add((a, b, ct))
                existing_conflicts.add((b, a, ct))
        except Exception:
            pass  # DB unavailable — skip dedup

    for a, b in pairs:
        for detector, conflict_type in [
            (_detect_duplicate, ConflictType.DUPLICATE),
            (_detect_contradiction, ConflictType.CONTRADICTORY),
            (_detect_resource_conflict, ConflictType.COMPETING_RESOURCE),
            (_detect_tension, ConflictType.TENSION),
        ]:
            desc = detector(a, b)
            if desc:
                # Dedup: skip if this conflict already exists
                key = (a.id, b.id, conflict_type.value)
                if key in existing_conflicts:
                    conflicts.append((a.id, b.id, conflict_type, desc))
                    continue
                conflicts.append((a.id, b.id, conflict_type, desc))
                if save:
                    await create_conflict(a.id, b.id, conflict_type, desc)
                    existing_conflicts.add(key)
                    existing_conflicts.add((b.id, a.id, conflict_type.value))

    return conflicts