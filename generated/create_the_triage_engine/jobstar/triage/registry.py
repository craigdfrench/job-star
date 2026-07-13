"""
Goal registry client and duplicate detection.
Supports an in-memory registry for development/testing
and an HTTP client for production use against a persistent store.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional, Protocol
from uuid import UUID

import httpx

from .models import DuplicateMatch, GoalRegistryEntry, IntakeRequest


def _normalize(text: str) -> set[str]:
    """Tokenize and normalize text into a set of lowercase tokens."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    # Filter very short tokens
    return {t for t in tokens if len(t) > 2}


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def _tag_overlap(tags_a: list[str], tags_b: list[str]) -> float:
    """Compute fraction of overlapping tags."""
    if not tags_a or not tags_b:
        return 0.0
    set_a = {t.lower() for t in tags_a}
    set_b = {t.lower() for t in tags_b}
    return len(set_a & set_b) / max(len(set_a | set_b), 1)


class GoalRegistryBackend(Protocol):
    """Protocol for registry backends."""

    async def list_goals(self, status: Optional[str] = "open") -> list[GoalRegistryEntry]:
        ...

    async def get_goal(self, goal_id: UUID) -> Optional[GoalRegistryEntry]:
        ...

    async def add_goal(self, entry: GoalRegistryEntry) -> GoalRegistryEntry:
        ...


@dataclass
class InMemoryRegistry:
    """In-memory goal registry for development and testing."""

    goals: dict[UUID, GoalRegistryEntry] = field(default_factory=dict)

    async def list_goals(self, status: Optional[str] = "open") -> list[GoalRegistryEntry]:
        goals = list(self.goals.values())
        if status:
            goals = [g for g in goals if g.status == status]
        return goals

    async def get_goal(self, goal_id: UUID) -> Optional[GoalRegistryEntry]:
        return self.goals.get(goal_id)

    async def add_goal(self, entry: GoalRegistryEntry) -> GoalRegistryEntry:
        self.goals[entry.id] = entry
        return entry


class HttpRegistry:
    """HTTP client for a remote goal registry service."""

    def __init__(self, base_url: str, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def list_goals(self, status: Optional[str] = "open") -> list[GoalRegistryEntry]:
        params = {}
        if status:
            params["status"] = status
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/goals",
                params=params,
                headers=self._headers(),
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return [GoalRegistryEntry(**g) for g in data.get("goals", [])]

    async def get_goal(self, goal_id: UUID) -> Optional[GoalRegistryEntry]:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.base_url}/goals/{goal_id}",
                headers=self._headers(),
                timeout=10.0,
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return GoalRegistryEntry(**resp.json())

    async def add_goal(self, entry: GoalRegistryEntry) -> GoalRegistryEntry:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.base_url}/goals",
                json=entry.model_dump(mode="json"),
                headers=self._headers(),
                timeout=10.0,
            )
            resp.raise_for_status()
            return GoalRegistryEntry(**resp.json())


class DuplicateChecker:
    """
    Checks incoming requests against the goal registry
    to find potential duplicates using text similarity and tag overlap.
    """

    def __init__(
        self,
        backend: GoalRegistryBackend,
        title_threshold: float = 0.45,
        tag_threshold: float = 0.50,
        combined_threshold: float = 0.55,
    ):
        self.backend = backend
        self.title_threshold = title_threshold
        self.tag_threshold = tag_threshold
        self.combined_threshold = combined_threshold

    async def check(self, request: IntakeRequest) -> list[DuplicateMatch]:
        """Return a list of potential duplicate matches from the registry."""
        goals = await self.backend.list_goals(status="open")

        request_tokens = _normalize(f"{request.title} {request.description}")
        request_tags = {t.lower() for t in request.tags}

        matches: list[DuplicateMatch] = []

        for goal in goals:
            goal_tokens = _normalize(f"{goal.title} {goal.description}")
            goal_tags = {t.lower() for t in goal.tags}

            title_sim = _jaccard_similarity(request_tokens, goal_tokens)
            tag_sim = _tag_overlap(request.tags, goal.tags)

            # Weighted combined score (title similarity matters more)
            combined = 0.70 * title_sim + 0.30 * tag_sim

            reasons: list[str] = []
            is_match = False

            if title_sim >= self.title_threshold:
                reasons.append(f"title similarity {title_sim:.2f}")
                is_match = True

            if tag_sim >= self.tag_threshold and request_tags:
                reasons.append(f"tag overlap {tag_sim:.2f}")
                is_match = True

            if combined >= self.combined_threshold:
                reasons.append(f"combined score {combined:.2f}")
                is_match = True

            if is_match:
                matches.append(
                    DuplicateMatch(
                        goal_id=goal.id,
                        title=goal.title,
                        similarity_score=round(combined, 3),
                        match_reasons=reasons,
                    )
                )

        # Sort by similarity descending
        matches.sort(key=lambda m: m.similarity_score, reverse=True)
        return matches
