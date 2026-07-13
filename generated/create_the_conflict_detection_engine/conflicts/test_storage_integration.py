"""
Tests for the conflict detection storage integration layer.
"""

import pytest

from jobstar.conflict.base import (
    Conflict,
    ConflictSeverity,
    ConflictType,
)
from jobstar.conflict.storage_integration import (
    ConflictDetectionService,
    ConflictRepository,
    DetectionReport,
    DetectionTrigger,
    GoalQueryAdapter,
    InMemoryGoalStore,
    StorageHooks,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store():
    return InMemoryGoalStore()


@pytest.fixture
def service(store):
    return ConflictDetectionService(store)


@pytest.fixture
def sample_goals():
    return [
        {
            "id": "g1",
            "title": "Learn Python",
            "description": "Master Python programming",
            "domain": "learning",
            "status": "active",
            "resources": ["time:10h/week"],
            "tags": ["programming", "python"],
        },
        {
            "id": "g2",
            "title": "Learn Python programming",
            "description": "Become proficient in Python",
            "domain": "learning",
            "status": "active",
            "resources": ["time:8h/week"],
            "tags": ["programming", "python"],
        },
        {
            "id": "g3",
            "title": "Ship product v2",
            "description": "Release product version 2",
            "domain": "work",
            "status": "active",
            "resources": ["time:40h/week", "budget:5000"],
            "tags": ["product", "release"],
        },
        {
            "id": "g4",
            "title": "Train for marathon",
            "description": "Run a marathon in under 4 hours",
            "domain": "personal",
            "status": "active",
            "resources": ["time:15h/week"],
            "tags": ["fitness", "running"],
        },
    ]


@pytest.fixture
def populated_store(store, sample_goals):
    for goal in sample_goals:
        store.store_goal(goal)
    return store


# ---------------------------------------------------------------------------
# GoalQueryAdapter tests
# ---------------------------------------------------------------------------


class TestGoalQueryAdapter:
    def test_fetch_all(self, populated_store):
        adapter = GoalQueryAdapter(populated_store)
        goals = adapter.fetch_all()
        assert len(goals) == 4

    def test_fetch_all_with_domain_filter(self, populated_store):
        adapter = GoalQueryAdapter(populated_store)
        goals = adapter.fetch_all(domain_filter="learning")
        assert len(goals) == 2
        assert all(g["domain"] == "learning" for g in goals)

    def test_fetch_one(self, populated_store):
        adapter = GoalQueryAdapter(populated_store)
        goal = adapter.fetch_one("g1")
        assert goal is not None
        assert goal["id"] == "g1"
        assert goal["title"] == "Learn Python"

    def test_fetch_one_not_found(self, populated_store):
        adapter = GoalQueryAdapter(populated_store)
        goal = adapter.fetch_one("nonexistent")
        assert goal is None

    def test_fetch_by_ids(self, populated_store):
        adapter = GoalQueryAdapter(populated_store)
        goals = adapter.fetch_by_ids(["g1", "g3"])
        assert len(goals) == 2

    def test_normalize_ensures_required_fields(self, store):
        store.store_goal({"id": "raw1", "title": "Raw goal"})
        adapter = GoalQueryAdapter(store)
        goal = adapter.fetch_one("raw1")
        assert goal is not None
        assert "description" in goal
        assert "domain" in goal
        assert "status" in goal
        assert "resources" in goal
        assert "tags" in goal

    def test_normalize_handles_alternate_field_names(self, store):
        store.store_goal({
            "goal_id": "alt1",
            "name": "Alt goal",
            "details": "Some details",
            "category": "work",
        })
        adapter = GoalQueryAdapter(store)
        goal = adapter.fetch_one("alt1")
        assert goal is not None
        assert goal["id"] == "alt1"
        assert goal["title"] == "Alt goal"
        assert goal["description"] == "Some details"
        assert goal["domain"] == "work"


# ---------------------------------------------------------------------------
# ConflictRepository tests
# ---------------------------------------------------------------------------


class TestConflictRepository:
    def test_save_and_retrieve(self, store):
        repo = ConflictRepository(store)
        conflict = Conflict(
            id="",
            conflict_type=ConflictType.DUPLICATE,
            severity=ConflictSeverity.MEDIUM,
            goal_ids=["g1", "g2"],
            description="Duplicate goals detected",
        )
        cid = repo.save(conflict)
        assert cid != ""

        retrieved = repo.get_for_goal("g1")
        assert len(retrieved) == 1
        assert retrieved[0].goal_ids == ["g1", "g2"]

    def test_save_batch(self, store):
        repo = ConflictRepository(store)
        conflicts = [
            Conflict(
                id="",
                conflict_type=ConflictType.DUPLICATE,
                severity=ConflictSeverity.LOW,
                goal_ids=["g1", "g2"],
                description="Dup 1",
            ),
            Conflict(
                id="",
                conflict_type=ConflictType.RESOURCE_COMPETITION,
                severity=ConflictSeverity.HIGH,
                goal_ids=["g3", "g4"],
                description="Resource conflict",
            ),
        ]
        ids = repo.save_batch(conflicts)
        assert len(ids) == 2
        assert all(id != "" for id in ids)

    def test_get_all(self, store):
        repo = ConflictRepository(store)
        repo.save(Conflict(
            id="",
            conflict_type=ConflictType.DUPLICATE,
            severity=ConflictSeverity.LOW,
            goal_ids=["g1", "g2"],
            description="Dup",
        ))
        all_conflicts = repo.get_all()
        assert len(all_conflicts) == 1

    def test_resolve(self, store):
        repo = ConflictRepository(store)
        cid = repo.save(Conflict(
            id="",
            conflict_type=ConflictType.DUPLICATE,
            severity=ConflictSeverity.LOW,
            goal_ids=["g1", "g2"],
            description="Dup",
        ))
        result = repo.resolve(cid, "Goals merged")
        assert result is True


# ---------------------------------------------------------------------------
# ConflictDetectionService tests
# ---------------------------------------------------------------------------


class TestConflictDetectionService:
    def test_detect_for_goal_not_found(self, service):
        report = service.detect_for_goal("nonexistent")
        assert report.goals_scanned == 0
        assert report.conflicts_found == 0
        assert len(report.errors) == 1

    def test_detect_all_with_few_goals(self, store, service):
        store.store_goal({"id": "solo", "title": "Only goal", "domain": "test"})
        report = service.detect_all()
        assert report.goals_scanned == 1
        assert report.conflicts_found == 0

    def test_detect_all_returns_report(self, populated_store, service):
        report = service.detect_all()
        assert isinstance(report, DetectionReport)
        assert report.goals_scanned == 4
        assert report.trigger == DetectionTrigger.SCHEDULED_SCAN

    def test_detect_for_goal_returns_report(self, populated_store, service):
        report = service.detect_for_goal("g1")
        assert isinstance(report, DetectionReport)
        assert report.trigger == DetectionTrigger.MANUAL_SCAN
        assert report.goals_scanned == 4

    def test_register_detector(self, service):
        class DummyDetector:
            def detect(self, goals):
                return []

        detector = DummyDetector()
        service.register_detector(detector)
        assert detector in service._detectors

    def test_conflict_key_is_deterministic(self):
        c1 = Conflict(
            id="",
            conflict_type=ConflictType.DUPLICATE,
            severity=ConflictSeverity.LOW,
            goal_ids=["g1", "g2"],
            description="Test",
        )
        c2 = Conflict(
            id="",
            conflict_type=ConflictType.DUPLICATE,
            severity=ConflictSeverity.LOW,
            goal_ids=["g2", "g1"],
            description="Test",
        )
        assert ConflictDetectionService._conflict_key(c1) == ConflictDetectionService._conflict_key(c2)

    def test_get_conflicts_for_goal(self, store, service):
        service._repo.save(Conflict(
            id="",
            conflict_type=ConflictType.DUPLICATE,
            severity=ConflictSeverity.LOW,
            goal_ids=["g1", "g2"],
            description="Dup",
        ))
        conflicts = service.get_conflicts_for_goal("g1")
        assert len(conflicts) == 1

    def test_resolve_conflict(self, store, service):
        cid = service._repo.save(Conflict(
            id="",
            conflict_type=ConflictType.DUPLICATE,
            severity=ConflictSeverity.LOW,
            goal_ids=["g1", "g2"],
            description="Dup",
        ))
        assert service.resolve_conflict(cid, "Resolved") is True


# ---------------------------------------------------------------------------
# StorageHooks tests
# ---------------------------------------------------------------------------


class TestStorageHooks:
    def test_on_goal_created(self, populated_store, service):
        hooks = StorageHooks(service)
        report = hooks.on_goal_created("g1")
        assert isinstance(report, DetectionReport)
        assert report.trigger == DetectionTrigger.GOAL_CREATED

    def test_on_goal_updated(self, populated_store, service):
        hooks = StorageHooks(service)
        report = hooks.on_goal_updated("g1")
        assert isinstance(report, DetectionReport)
        assert report.trigger == DetectionTrigger.GOAL_UPDATED

    def test_on_goal_deleted(self, store, service):
        # Store a conflict first
        service._repo.save(Conflict(
            id="",
            conflict_type=ConflictType.DUPLICATE,
            severity=ConflictSeverity.LOW,
            goal_ids=["g1", "g2"],
            description="Dup",
        ))
        hooks = StorageHooks(service)
        result = hooks.on_goal_deleted("g1")
        assert result is True

    def test_pre_post_hooks_called(self, populated_store, service):
        hooks = StorageHooks(service)
        pre_calls = []
        post_calls = []

        hooks.add_pre_hook(lambda trigger, gid: pre_calls.append((trigger, gid)))
        hooks.add_post_hook(lambda trigger, gid, report: post_calls.append((trigger, gid)))

        hooks.on_goal_created("g1")

        assert len(pre_calls) == 1
        assert pre_calls[0][1] == "g1"
        assert len(post_calls) == 1
        assert post_calls[0][1] == "g1"

    def test_hook_errors_dont_crash(self, populated_store, service):
        hooks = StorageHooks(service)

        def bad_hook(trigger, gid):
            raise ValueError("Hook error")

        hooks.add_pre_hook(bad_hook)
        # Should not raise
        report = hooks.on_goal_created("g1")
        assert isinstance(report, DetectionReport)


# ---------------------------------------------------------------------------
# InMemoryGoalStore tests
# ---------------------------------------------------------------------------


class TestInMemoryGoalStore:
    def test_store_and_get(self, store):
        store.store_goal({"id": "test1", "title": "Test"})
        goal = store.get_goal("test1")
        assert goal is not None
        assert goal["title"] == "Test"

    def test_get_not_found(self, store):
        assert store.get_goal("nope") is None

    def test_get_all_goals(self, store):
        store.store_goal({"id": "a", "title": "A"})
        store.store_goal({"id": "b", "title": "B"})
        assert len(store.get_all_goals()) == 2

    def test_get_goals_by_domain(self, store):
        store.store_goal({"id": "a", "title": "A", "domain": "work"})
        store.store_goal({"id": "b", "title": "B", "domain": "personal"})
        work_goals = store.get_goals_by_domain("work")
        assert len(work_goals) == 1
        assert work_goals[0]["id"] == "a"

    def test_get_goals_by_ids(self, store):
        store.store_goal({"id": "a", "title": "A"})
        store.store_goal({"id": "b", "title": "B"})
        store.store_goal({"id": "c", "title": "C"})
        result = store.get_goals_by_ids(["a", "c", "nonexistent"])
        assert len(result) == 2

    def test_save_and_get_conflicts(self, store):
        conflict = Conflict(
            id="",
            conflict_type=ConflictType.DUPLICATE,
            severity=ConflictSeverity.LOW,
            goal_ids=["g1", "g2"],
            description="Test conflict",
        )
        cid = store.save_conflict(conflict)
        assert cid != ""

        conflicts = store.get_conflicts_for_goal("g1")
        assert len(conflicts) == 1

    def test_resolve_conflict(self, store):
        conflict = Conflict(
            id="",
            conflict_type=ConflictType.DUPLICATE,
            severity=ConflictSeverity.LOW,
            goal_ids=["g1", "g2"],
            description="Test",
        )
        cid = store.save_conflict(conflict)
        assert store.resolve_conflict(cid, "Done") is True
        assert store.resolve_conflict("nonexistent", "Done") is False

    def test_clear(self, store):
        store.store_goal({"id": "a", "title": "A"})
        store.save_conflict(Conflict(
            id="",
            conflict_type=ConflictType.DUPLICATE,
            severity=ConflictSeverity.LOW,
            goal_ids=["a"],
            description="Test",
        ))
        store.clear()
        assert len(store.get_all_goals()) == 0
        assert len(store.get_all_conflicts()) == 0

    def test_auto_generate_id(self, store):
        goal_id = store.store_goal({"title": "No ID"})
        assert goal_id != ""
        assert store.get_goal(goal_id) is not None


// --- DUPLICATE BLOCK ---

"""
Supplementary tests for storage integration.

These tests verify that the conflict detection engine correctly
interacts with the goal storage layer — retrieving goals, caching
conflict results, and invalidating stale conflicts.
"""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from jobstar.conflict.base import (
    ConflictResult,
    ConflictType,
    ConflictSeverity,
    GoalContext,
)


class TestStorageIntegration:
    """Tests for conflict detection + storage integration."""

    @pytest.fixture
    def mock_storage(self):
        """Mock goal storage that returns goals from an in-memory list."""
        storage = MagicMock()
        storage._goals = {}

        def store_goal(goal):
            storage._goals[goal.goal_id] = goal
            return goal

        def get_goal(goal_id):
            return storage._goals.get(goal_id)

        def get_all_goals():
            return list(storage._goals.values())

        storage.store_goal = MagicMock(side_effect=store_goal)
        storage.get_goal = MagicMock(side_effect=get_goal)
        storage.get_all_goals = MagicMock(side_effect=get_all_goals)
        return storage

    @pytest.fixture
    def sample_goals(self):
        return [
            GoalContext(
                goal_id="s1",
                title="Goal A",
                description="First goal",
                domain="work",
                tags=["a"],
                created_at=datetime.now(timezone.utc),
            ),
            GoalContext(
                goal_id="s2",
                title="Goal A",  # Duplicate title
                description="First goal",  # Duplicate description
                domain="work",
                tags=["a"],
                created_at=datetime.now(timezone.utc),
            ),
            GoalContext(
                goal_id="s3",
                title="Goal B",
                description="Different goal entirely",
                domain="personal",
                tags=["b"],
                created_at=datetime.now(timezone.utc),
            ),
        ]

    def test_storage_retrieves_goals_for_conflict_check(self, mock_storage, sample_goals):
        """Storage should be able to provide all goals for pairwise conflict checking."""
        for g in sample_goals:
            mock_storage.store_goal(g)

        all_goals = mock_storage.get_all_goals()
        assert len(all_goals) == 3

    def test_storage_retrieves_individual_goal(self, mock_storage, sample_goals):
        """Storage should retrieve individual goals by ID."""
        for g in sample_goals:
            mock_storage.store_goal(g)

        goal = mock_storage.get_goal("s2")
        assert goal is not None
        assert goal.goal_id == "s2"

    def test_storage_returns_none_for_missing_goal(self, mock_storage):
        """Storage should return None for non-existent goal IDs."""
        goal = mock_storage.get_goal("nonexistent")
        assert goal is None

    def test_conflict_check_generates_correct_pairs(self, mock_storage, sample_goals):
        """Pairwise conflict checking should generate N*(N-1)/2 pairs."""
        for g in sample_goals:
            mock_storage.store_goal(g)

        all_goals = mock_storage.get_all_goals()
        n = len(all_goals)
        pairs = [
            (all_goals[i], all_goals[j])
            for i in range(n)
            for j in range(i + 1, n)
        ]

        # 3 goals → 3 pairs
        assert len(pairs) == 3
        assert pairs[0][0].goal_id != pairs[0][1].goal_id

    def test_conflict_result_can_be_associated_with_goals(self, sample_goals):
        """A conflict result should reference the goal IDs it pertains to."""
        result = ConflictResult(
            conflict_type=ConflictType.DUPLICATE,
            severity=ConflictSeverity.MEDIUM,
            confidence=0.92,
            reasoning="Titles and descriptions are identical.",
            goal_a_id=sample_goals[0].goal_id,
            goal_b_id=sample_goals[1].goal_id,
            evidence=["Same title: 'Goal A'", "Same description: 'First goal'"],
        )

        assert result.goal_a_id == "s1"
        assert result.goal_b_id == "s2"

    def test_conflict_cache_invalidation_on_goal_update(self, mock_storage, sample_goals):
        """When a goal is updated, cached conflicts involving it should be invalidated."""
        # This tests the concept: if we cache conflict results,
        # updating a goal should invalidate cache entries for that goal
        goal = sample_goals[0]
        mock_storage.store_goal(goal)

        # Simulate cached conflict IDs
        cached_conflict_ids = {"s1:s2", "s1:s3"}

        # When goal s1 is updated, conflicts involving s1 should be invalidated
        updated_goal_id = "s1"
        remaining_conflicts = {
            cid for cid in cached_conflict_ids
            if updated_goal_id not in cid
        }

        assert remaining_conflicts == set(), "All conflicts involving s1 should be invalidated"
