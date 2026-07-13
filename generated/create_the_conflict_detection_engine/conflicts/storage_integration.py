"""
Integration layer connecting the conflict detection engine to goal storage/retrieval.

This module provides:
- ConflictDetectionService: Orchestrates conflict detection against stored goals
- StorageHooks: Callbacks for goal create/update/delete events
- ConflictRepository: Persists and retrieves detected conflicts from storage
- GoalQueryAdapter: Abstracts goal storage queries for the detection engine
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional, Protocol

from jobstar.conflict.base import (
    Conflict,
    ConflictResult,
    ConflictSeverity,
    ConflictType,
)
from jobstar.conflict.duplicate import DuplicateDetector
from jobstar.conflict.cross_domain_detector import CrossDomainConflictDetector
from jobstar.conflict.domains import Domain

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocols (interfaces) for storage dependencies
# ---------------------------------------------------------------------------


class GoalStore(Protocol):
    """Protocol describing the minimum storage interface needed for conflict detection."""

    def get_goal(self, goal_id: str) -> Optional[dict[str, Any]]:
        """Retrieve a single goal by ID. Returns None if not found."""
        ...

    def get_all_goals(self) -> list[dict[str, Any]]:
        """Retrieve all stored goals."""
        ...

    def get_goals_by_domain(self, domain: str) -> list[dict[str, Any]]:
        """Retrieve goals filtered by domain."""
        ...

    def get_goals_by_ids(self, goal_ids: list[str]) -> list[dict[str, Any]]:
        """Retrieve multiple goals by their IDs."""
        ...

    def save_conflict(self, conflict: Conflict) -> str:
        """Persist a detected conflict. Returns the conflict ID."""
        ...

    def get_conflicts_for_goal(self, goal_id: str) -> list[Conflict]:
        """Retrieve all conflicts involving a given goal."""
        ...

    def get_all_conflicts(self) -> list[Conflict]:
        """Retrieve all stored conflicts."""
        ...

    def resolve_conflict(self, conflict_id: str, resolution: str) -> bool:
        """Mark a conflict as resolved. Returns True on success."""
        ...


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


class DetectionTrigger(str, Enum):
    """Events that can trigger conflict detection."""

    GOAL_CREATED = "goal_created"
    GOAL_UPDATED = "goal_updated"
    GOAL_DELETED = "goal_deleted"
    MANUAL_SCAN = "manual_scan"
    SCHEDULED_SCAN = "scheduled_scan"


@dataclass
class DetectionContext:
    """Context passed to the detection engine for a single detection run."""

    trigger: DetectionTrigger
    triggering_goal_id: Optional[str] = None
    domain_filter: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class DetectionReport:
    """Summary of a conflict detection run."""

    trigger: DetectionTrigger
    goals_scanned: int
    conflicts_found: int
    new_conflicts: int
    existing_conflicts: int
    conflicts: list[Conflict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def summary(self) -> str:
        return (
            f"DetectionReport(trigger={self.trigger.value}, "
            f"scanned={self.goals_scanned}, "
            f"found={self.conflicts_found}, "
            f"new={self.new_conflicts}, "
            f"duration={self.duration_ms:.1f}ms)"
        )


# ---------------------------------------------------------------------------
# Goal Query Adapter
# ---------------------------------------------------------------------------


class GoalQueryAdapter:
    """
    Adapts raw goal storage dicts into the format expected by conflict detectors.

    This decouples the detection engine from the exact storage schema.
    """

    def __init__(self, store: GoalStore):
        self._store = store

    def fetch_all(self, domain_filter: Optional[str] = None) -> list[dict[str, Any]]:
        """Fetch goals, optionally filtered by domain."""
        if domain_filter:
            raw_goals = self._store.get_goals_by_domain(domain_filter)
        else:
            raw_goals = self._store.get_all_goals()
        return [self.normalize(g) for g in raw_goals]

    def fetch_by_ids(self, goal_ids: list[str]) -> list[dict[str, Any]]:
        """Fetch specific goals by ID."""
        raw_goals = self._store.get_goals_by_ids(goal_ids)
        return [self.normalize(g) for g in raw_goals]

    def fetch_one(self, goal_id: str) -> Optional[dict[str, Any]]:
        """Fetch a single goal."""
        raw = self._store.get_goal(goal_id)
        if raw is None:
            return None
        return self.normalize(raw)

    @staticmethod
    def normalize(goal: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize a raw goal dict into a consistent shape for detectors.

        Ensures required fields exist with sensible defaults.
        """
        return {
            "id": goal.get("id", goal.get("goal_id", "")),
            "title": goal.get("title", goal.get("name", "")),
            "description": goal.get("description", goal.get("details", "")),
            "domain": goal.get("domain", goal.get("category", "unknown")),
            "status": goal.get("status", "active"),
            "priority": goal.get("priority", "normal"),
            "resources": goal.get("resources", []),
            "tags": goal.get("tags", []),
            "deadline": goal.get("deadline"),
            "created_at": goal.get("created_at"),
            "updated_at": goal.get("updated_at"),
            "metadata": goal.get("metadata", {}),
        }


# ---------------------------------------------------------------------------
# Conflict Repository
# ---------------------------------------------------------------------------


class ConflictRepository:
    """
    Manages persistence and retrieval of detected conflicts via the goal store.
    """

    def __init__(self, store: GoalStore):
        self._store = store

    def save(self, conflict: Conflict) -> str:
        """Persist a conflict. Returns the conflict ID."""
        conflict_id = self._store.save_conflict(conflict)
        logger.debug(f"Saved conflict {conflict_id}: {conflict.conflict_type}")
        return conflict_id

    def save_batch(self, conflicts: list[Conflict]) -> list[str]:
        """Persist multiple conflicts. Returns list of conflict IDs."""
        ids = []
        for c in conflicts:
            try:
                ids.append(self.save(c))
            except Exception as e:
                logger.error(f"Failed to save conflict: {e}")
                ids.append("")
        return ids

    def get_for_goal(self, goal_id: str) -> list[Conflict]:
        """Get all conflicts involving a goal."""
        return self._store.get_conflicts_for_goal(goal_id)

    def get_all(self) -> list[Conflict]:
        """Get all stored conflicts."""
        return self._store.get_all_conflicts()

    def resolve(self, conflict_id: str, resolution: str) -> bool:
        """Mark a conflict as resolved."""
        return self._store.resolve_conflict(conflict_id, resolution)


# ---------------------------------------------------------------------------
# Conflict Detection Service
# ---------------------------------------------------------------------------


class ConflictDetectionService:
    """
    Main service that orchestrates conflict detection against stored goals.

    Combines multiple detectors (duplicate, contradiction, resource, tension,
    cross-domain) and integrates with goal storage for retrieval and persistence.
    """

    def __init__(
        self,
        store: GoalStore,
        detectors: Optional[list[Any]] = None,
        similarity_threshold: float = 0.85,
    ):
        self._store = store
        self._query = GoalQueryAdapter(store)
        self._repo = ConflictRepository(store)

        # Register detectors
        if detectors is not None:
            self._detectors = detectors
        else:
            self._detectors = self._default_detectors(similarity_threshold)

        logger.info(
            f"ConflictDetectionService initialized with {len(self._detectors)} detectors"
        )

    @staticmethod
    def _default_detectors(similarity_threshold: float) -> list[Any]:
        """Create the default set of detectors."""
        detectors: list[Any] = []

        try:
            detectors.append(DuplicateDetector(threshold=similarity_threshold))
        except Exception as e:
            logger.warning(f"Failed to init DuplicateDetector: {e}")

        try:
            detectors.append(CrossDomainConflictDetector())
        except Exception as e:
            logger.warning(f"Failed to init CrossDomainConflictDetector: {e}")

        # Additional detectors can be added here as they are implemented
        # contradiction, resource, tension detectors are available in
        # job_star/conflict/strategies/ but follow a different interface.
        # They can be adapted via wrapper classes.

        return detectors

    def register_detector(self, detector: Any) -> None:
        """Register an additional detector at runtime."""
        self._detectors.append(detector)
        logger.info(f"Registered detector: {detector.__class__.__name__}")

    def detect_for_goal(
        self, goal_id: str, trigger: DetectionTrigger = DetectionTrigger.MANUAL_SCAN
    ) -> DetectionReport:
        """
        Run conflict detection for a single goal against all other stored goals.

        This is the primary entry point when a goal is created or updated.
        """
        start = datetime.now(timezone.utc)

        target_goal = self._query.fetch_one(goal_id)
        if target_goal is None:
            return DetectionReport(
                trigger=trigger,
                goals_scanned=0,
                conflicts_found=0,
                new_conflicts=0,
                existing_conflicts=0,
                errors=[f"Goal {goal_id} not found"],
                duration_ms=0.0,
            )

        # Fetch all other goals for comparison
        all_goals = self._query.fetch_all()
        other_goals = [g for g in all_goals if g["id"] != goal_id]

        context = DetectionContext(
            trigger=trigger,
            triggering_goal_id=goal_id,
        )

        all_conflicts = self._run_detectors([target_goal] + other_goals, context)

        # Deduplicate and classify new vs existing
        existing = self._repo.get_for_goal(goal_id)
        existing_keys = {self._conflict_key(c) for c in existing}

        new_conflicts = []
        for c in all_conflicts:
            if self._conflict_key(c) not in existing_keys:
                new_conflicts.append(c)

        # Persist new conflicts
        self._repo.save_batch(new_conflicts)

        duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000

        return DetectionReport(
            trigger=trigger,
            goals_scanned=len(other_goals) + 1,
            conflicts_found=len(all_conflicts),
            new_conflicts=len(new_conflicts),
            existing_conflicts=len(all_conflicts) - len(new_conflicts),
            conflicts=all_conflicts,
            duration_ms=duration,
        )

    def detect_all(
        self,
        domain_filter: Optional[str] = None,
        trigger: DetectionTrigger = DetectionTrigger.SCHEDULED_SCAN,
    ) -> DetectionReport:
        """
        Run a full conflict detection scan across all stored goals.

        Optionally filter by domain.
        """
        start = datetime.now(timezone.utc)

        goals = self._query.fetch_all(domain_filter=domain_filter)

        if len(goals) < 2:
            return DetectionReport(
                trigger=trigger,
                goals_scanned=len(goals),
                conflicts_found=0,
                new_conflicts=0,
                existing_conflicts=0,
                duration_ms=0.0,
            )

        context = DetectionContext(
            trigger=trigger,
            domain_filter=domain_filter,
        )

        all_conflicts = self._run_detectors(goals, context)

        # Check against existing conflicts
        existing = self._repo.get_all()
        existing_keys = {self._conflict_key(c) for c in existing}

        new_conflicts = []
        for c in all_conflicts:
            if self._conflict_key(c) not in existing_keys:
                new_conflicts.append(c)

        self._repo.save_batch(new_conflicts)

        duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000

        return DetectionReport(
            trigger=trigger,
            goals_scanned=len(goals),
            conflicts_found=len(all_conflicts),
            new_conflicts=len(new_conflicts),
            existing_conflicts=len(all_conflicts) - len(new_conflicts),
            conflicts=all_conflicts,
            duration_ms=duration,
        )

    def detect_cross_domain(
        self, trigger: DetectionTrigger = DetectionTrigger.SCHEDULED_SCAN
    ) -> DetectionReport:
        """
        Run cross-domain conflict detection specifically.

        Fetches goals from all domains and checks for cross-domain tensions.
        """
        start = datetime.now(timezone.utc)

        goals = self._query.fetch_all()
        if len(goals) < 2:
            return DetectionReport(
                trigger=trigger,
                goals_scanned=len(goals),
                conflicts_found=0,
                new_conflicts=0,
                existing_conflicts=0,
                duration_ms=0.0,
            )

        context = DetectionContext(
            trigger=trigger,
            metadata={"cross_domain_only": True},
        )

        # Only run cross-domain detector
        conflicts: list[Conflict] = []
        for detector in self._detectors:
            if isinstance(detector, CrossDomainConflictDetector):
                try:
                    result = detector.detect(goals)
                    if hasattr(result, "conflicts"):
                        conflicts.extend(result.conflicts)
                    elif isinstance(result, list):
                        conflicts.extend(result)
                except Exception as e:
                    logger.error(
                        f"Cross-domain detector error: {e}", exc_info=True
                    )

        existing = self._repo.get_all()
        existing_keys = {self._conflict_key(c) for c in existing}
        new_conflicts = [
            c for c in conflicts if self._conflict_key(c) not in existing_keys
        ]
        self._repo.save_batch(new_conflicts)

        duration = (datetime.now(timezone.utc) - start).total_seconds() * 1000

        return DetectionReport(
            trigger=trigger,
            goals_scanned=len(goals),
            conflicts_found=len(conflicts),
            new_conflicts=len(new_conflicts),
            existing_conflicts=len(conflicts) - len(new_conflicts),
            conflicts=conflicts,
            duration_ms=duration,
        )

    def _run_detectors(
        self, goals: list[dict[str, Any]], context: DetectionContext
    ) -> list[Conflict]:
        """Run all registered detectors against the given goals."""
        all_conflicts: list[Conflict] = []
        seen_keys: set[str] = set()

        for detector in self._detectors:
            detector_name = detector.__class__.__name__
            try:
                result = detector.detect(goals)

                # Handle different return types
                if hasattr(result, "conflicts"):
                    detected = result.conflicts
                elif isinstance(result, list):
                    detected = result
                else:
                    detected = []

                for conflict in detected:
                    key = self._conflict_key(conflict)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        all_conflicts.append(conflict)

            except Exception as e:
                logger.error(
                    f"Detector {detector_name} failed: {e}", exc_info=True
                )

        return all_conflicts

    @staticmethod
    def _conflict_key(conflict: Conflict) -> str:
        """
        Generate a deterministic key for a conflict to identify duplicates.

        Sorts goal IDs so that (A,B) and (B,A) produce the same key.
        """
        goal_ids = sorted(conflict.goal_ids)
        return f"{conflict.conflict_type.value}:{':'.join(goal_ids)}"

    def get_conflicts_for_goal(self, goal_id: str) -> list[Conflict]:
        """Convenience method to retrieve conflicts for a goal."""
        return self._repo.get_for_goal(goal_id)

    def resolve_conflict(self, conflict_id: str, resolution: str) -> bool:
        """Convenience method to resolve a conflict."""
        return self._repo.resolve(conflict_id, resolution)


# ---------------------------------------------------------------------------
# Storage Hooks
# ---------------------------------------------------------------------------


class StorageHooks:
    """
    Hook system for integrating conflict detection into goal storage lifecycle.

    These hooks should be called by the goal storage layer when goals are
    created, updated, or deleted.
    """

    def __init__(self, service: ConflictDetectionService):
        self._service = service
        self._pre_hooks: list[Callable] = []
        self._post_hooks: list[Callable] = []

    def add_pre_hook(self, hook: Callable) -> None:
        """Add a hook called before detection runs."""
        self._pre_hooks.append(hook)

    def add_post_hook(self, hook: Callable) -> None:
        """Add a hook called after detection completes."""
        self._post_hooks.append(hook)

    def on_goal_created(self, goal_id: str) -> DetectionReport:
        """Hook called when a new goal is created."""
        self._run_pre_hooks(DetectionTrigger.GOAL_CREATED, goal_id)
        report = self._service.detect_for_goal(
            goal_id, trigger=DetectionTrigger.GOAL_CREATED
        )
        self._run_post_hooks(DetectionTrigger.GOAL_CREATED, goal_id, report)
        logger.info(f"on_goal_created: {report.summary()}")
        return report

    def on_goal_updated(self, goal_id: str) -> DetectionReport:
        """Hook called when a goal is updated."""
        self._run_pre_hooks(DetectionTrigger.GOAL_UPDATED, goal_id)
        report = self._service.detect_for_goal(
            goal_id, trigger=DetectionTrigger.GOAL_UPDATED
        )
        self._run_post_hooks(DetectionTrigger.GOAL_UPDATED, goal_id, report)
        logger.info(f"on_goal_updated: {report.summary()}")
        return report

    def on_goal_deleted(self, goal_id: str) -> bool:
        """
        Hook called when a goal is deleted.

        This should mark conflicts involving the deleted goal as stale/resolved.
        """
        self._run_pre_hooks(DetectionTrigger.GOAL_DELETED, goal_id)
        conflicts = self._service.get_conflicts_for_goal(goal_id)
        for c in conflicts:
            self._service.resolve_conflict(
                c.id if hasattr(c, "id") else str(c),
                f"Goal {goal_id} was deleted",
            )
        self._run_post_hooks(DetectionTrigger.GOAL_DELETED, goal_id)
        logger.info(
            f"on_goal_deleted: resolved {len(conflicts)} conflicts for {goal_id}"
        )
        return True

    def _run_pre_hooks(self, trigger: DetectionTrigger, goal_id: str) -> None:
        for hook in self._pre_hooks:
            try:
                hook(trigger, goal_id)
            except Exception as e:
                logger.warning(f"Pre-hook failed: {e}")

    def _run_post_hooks(
        self,
        trigger: DetectionTrigger,
        goal_id: str,
        report: Optional[DetectionReport] = None,
    ) -> None:
        for hook in self._post_hooks:
            try:
                hook(trigger, goal_id, report)
            except Exception as e:
                logger.warning(f"Post-hook failed: {e}")


# ---------------------------------------------------------------------------
# In-Memory Store (for testing and bootstrapping)
# ---------------------------------------------------------------------------


class InMemoryGoalStore:
    """
    Simple in-memory implementation of GoalStore for testing and bootstrapping.

    Implements the GoalStore protocol without external dependencies.
    """

    def __init__(self):
        self._goals: dict[str, dict[str, Any]] = {}
        self._conflicts: dict[str, Conflict] = {}
        self._conflict_counter = 0

    def store_goal(self, goal: dict[str, Any]) -> str:
        """Store or update a goal."""
        goal_id = goal.get("id", goal.get("goal_id", ""))
        if not goal_id:
            goal_id = f"goal_{len(self._goals) + 1}"
            goal["id"] = goal_id
        self._goals[goal_id] = goal
        return goal_id

    def get_goal(self, goal_id: str) -> Optional[dict[str, Any]]:
        return self._goals.get(goal_id)

    def get_all_goals(self) -> list[dict[str, Any]]:
        return list(self._goals.values())

    def get_goals_by_domain(self, domain: str) -> list[dict[str, Any]]:
        return [
            g
            for g in self._goals.values()
            if g.get("domain", g.get("category", "")) == domain
        ]

    def get_goals_by_ids(self, goal_ids: list[str]) -> list[dict[str, Any]]:
        return [self._goals[gid] for gid in goal_ids if gid in self._goals]

    def save_conflict(self, conflict: Conflict) -> str:
        self._conflict_counter += 1
        conflict_id = (
            conflict.id
            if hasattr(conflict, "id") and conflict.id
            else f"conflict_{self._conflict_counter}"
        )
        if hasattr(conflict, "id"):
            conflict.id = conflict_id
        self._conflicts[conflict_id] = conflict
        return conflict_id

    def get_conflicts_for_goal(self, goal_id: str) -> list[Conflict]:
        results = []
        for c in self._conflicts.values():
            if goal_id in c.goal_ids:
                results.append(c)
        return results

    def get_all_conflicts(self) -> list[Conflict]:
        return list(self._conflicts.values())

    def resolve_conflict(self, conflict_id: str, resolution: str) -> bool:
        if conflict_id in self._conflicts:
            c = self._conflicts[conflict_id]
            if hasattr(c, "resolution"):
                c.resolution = resolution
            if hasattr(c, "resolved"):
                c.resolved = True
            return True
        return False

    def clear(self) -> None:
        self._goals.clear()
        self._conflicts.clear()
        self._conflict_counter = 0
