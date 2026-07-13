from jobstar.idle.conflict_checker import check_conflicts, acquire_step_locks
from jobstar.idle.locks import LockManager
from jobstar.idle.queue import peek_next_step   # from previous step
from jobstar.idle.resource_checker import check_resources  # from previous step

lock_mgr = LockManager(backend="memory")

# inside the loop:
step = peek_next_step()
if step is None:
    continue
if not check_resources(step)[0]:
    continue
ok, reasons = check_conflicts(step, running_jobs, lock_manager=lock_mgr)
if not ok:
    log.debug("skipping %s: %s", step["id"], reasons)
    continue
ok, handles, reasons = acquire_step_locks(step, lock_mgr)
if not ok:
    continue
try:
    execute(step)
finally:
    for h in handles:
        lock_mgr.release(h)


// --- DUPLICATE BLOCK ---

"""Tests for jobstar.idle.conflict_checker and locks."""
import time
import pytest

from jobstar.idle.locks import LockManager, LockState
from jobstar.idle.conflict_checker import ConflictChecker, ConflictReport
from jobstar.idle.queue import QueueItem, QueuePriority


@pytest.fixture
def lock_manager(tmp_path):
    return LockManager(lock_dir=tmp_path)


@pytest.fixture
def conflict_checker(lock_manager):
    return ConflictChecker(lock_manager=lock_manager)


class TestLockManager:
    def test_acquire_and_release(self, lock_manager):
        assert lock_manager.acquire("lock-A", owner="worker-1", ttl_seconds=60) is True
        assert lock_manager.release("lock-A", owner="worker-1") is True

    def test_acquire_blocked_by_existing(self, lock_manager):
        lock_manager.acquire("lock-A", owner="worker-1", ttl_seconds=60)
        assert lock_manager.acquire("lock-A", owner="worker-2", ttl_seconds=60) is False

    def test_release_wrong_owner_fails(self, lock_manager):
        lock_manager.acquire("lock-A", owner="worker-1", ttl_seconds=60)
        assert lock_manager.release("lock-A", owner="worker-2") is False

    def test_expired_lock_can_be_reacquired(self, lock_manager):
        lock_manager.acquire("lock-A", owner="worker-1", ttl_seconds=60)
        # Simulate expiry by backdating
        lock_manager._locks["lock-A"].acquired_at = time.time() - 120
        assert lock_manager.acquire("lock-A", owner="worker-2", ttl_seconds=60) is True

    def test_release_nonexistent_is_noop(self, lock_manager):
        assert lock_manager.release("nonexistent", owner="worker-1") is False

    def test_get_state(self, lock_manager):
        assert lock_manager.get_state("lock-X") == LockState.FREE
        lock_manager.acquire("lock-X", owner="w", ttl_seconds=60)
        assert lock_manager.get_state("lock-X") == LockState.HELD


class TestConflictChecker:
    def _make_item(self, locks, step_id="step-1"):
        return QueueItem(
            step_id=step_id,
            job_id="job-1",
            priority=QueuePriority.NORMAL,
            resource_hints={},
            locks=locks,
            enqueued_at=time.time(),
        )

    def test_no_conflict_when_locks_free(self, conflict_checker, lock_manager):
        item = self._make_item(["lock-A", "lock-B"])
        report = conflict_checker.check(item)
        assert report.has_conflict is False
        assert len(report.conflicting_locks) == 0

    def test_conflict_when_lock_held(self, conflict_checker, lock_manager):
        lock_manager.acquire("lock-A", owner="other-worker", ttl_seconds=60)
        item = self._make_item(["lock-A", "lock-B"])
        report = conflict_checker.check(item)
        assert report.has_conflict is True
        assert "lock-A" in report.conflicting_locks
        assert "lock-B" not in report.conflicting_locks

    def test_acquire_locks_on_no_conflict(self, conflict_checker, lock_manager):
        item = self._make_item(["lock-A", "lock-B"])
        report = conflict_checker.check(item)
        assert report.has_conflict is False
        conflict_checker.acquire(item, owner="worker-1", ttl_seconds=60)
        assert lock_manager.get_state("lock-A") == LockState.HELD
        assert lock_manager.get_state("lock-B") == LockState.HELD

    def test_release_locks(self, conflict_checker, lock_manager):
        item = self._make_item(["lock-A"])
        conflict_checker.acquire(item, owner="worker-1", ttl_seconds=60)
        conflict_checker.release(item, owner="worker-1")
        assert lock_manager.get_state("lock-A") == LockState.FREE

    def test_no_locks_means_no_conflict(self, conflict_checker):
        item = self._make_item([])
        report = conflict_checker.check(item)
        assert report.has_conflict is False
