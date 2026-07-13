ok, snapshot = check_resources(config.thresholds, job_counter=registry.active_count)
if not ok:
    sleep(config.idle_interval_seconds)
    continue
# ... proceed to pick next step from idle-opportunistic queue


// --- DUPLICATE BLOCK ---

"""Tests for jobstar.idle.queue."""
import time
from pathlib import Path

import pytest

from jobstar.idle.queue import (
    IdleQueue,
    QueueItem,
    QueuePriority,
    QueueBackend,
)


@pytest.fixture
def tmp_queue_path(tmp_path):
    return tmp_path / "idle_queue.json"


@pytest.fixture
def queue(tmp_queue_path):
    return IdleQueue(backend=QueueBackend.FILE, path=tmp_queue_path)


class TestQueueItem:
    def test_item_creation(self):
        item = QueueItem(
            step_id="step-1",
            job_id="job-1",
            priority=QueuePriority.LOW,
            resource_hints={"cpu": "light"},
            locks=["file-A"],
            enqueued_at=time.time(),
        )
        assert item.step_id == "step-1"
        assert item.priority == QueuePriority.LOW

    def test_priority_ordering(self):
        assert QueuePriority.HIGH.value < QueuePriority.NORMAL.value < QueuePriority.LOW.value


class TestIdleQueue:
    def test_empty_queue_peek_returns_none(self, queue):
        assert queue.peek() is None

    def test_empty_queue_pop_returns_none(self, queue):
        assert queue.pop() is None

    def test_push_and_peek(self, queue):
        item = QueueItem(
            step_id="step-1",
            job_id="job-1",
            priority=QueuePriority.LOW,
            resource_hints={},
            locks=[],
            enqueued_at=time.time(),
        )
        queue.push(item)
        peeked = queue.peek()
        assert peeked is not None
        assert peeked.step_id == "step-1"

    def test_push_and_pop(self, queue):
        item = QueueItem(
            step_id="step-1",
            job_id="job-1",
            priority=QueuePriority.LOW,
            resource_hints={},
            locks=[],
            enqueued_at=time.time(),
        )
        queue.push(item)
        popped = queue.pop()
        assert popped is not None
        assert popped.step_id == "step-1"
        assert queue.peek() is None

    def test_priority_ordering_high_first(self, queue):
        low = QueueItem(step_id="low", job_id="j", priority=QueuePriority.LOW, resource_hints={}, locks=[], enqueued_at=time.time())
        high = QueueItem(step_id="high", job_id="j", priority=QueuePriority.HIGH, resource_hints={}, locks=[], enqueued_at=time.time())
        normal = QueueItem(step_id="normal", job_id="j", priority=QueuePriority.NORMAL, resource_hints={}, locks=[], enqueued_at=time.time())

        queue.push(low)
        queue.push(high)
        queue.push(normal)

        assert queue.pop().step_id == "high"
        assert queue.pop().step_id == "normal"
        assert queue.pop().step_id == "low"

    def test_fifo_within_same_priority(self, queue):
        t = time.time()
        first = QueueItem(step_id="first", job_id="j", priority=QueuePriority.NORMAL, resource_hints={}, locks=[], enqueued_at=t)
        second = QueueItem(step_id="second", job_id="j", priority=QueuePriority.NORMAL, resource_hints={}, locks=[], enqueued_at=t + 1)

        queue.push(first)
        queue.push(second)

        assert queue.pop().step_id == "first"
        assert queue.pop().step_id == "second"

    def test_remove_specific_item(self, queue):
        item = QueueItem(step_id="step-1", job_id="j", priority=QueuePriority.NORMAL, resource_hints={}, locks=[], enqueued_at=time.time())
        queue.push(item)
        queue.remove("step-1")
        assert queue.peek() is None

    def test_remove_nonexistent_is_noop(self, queue):
        queue.remove("does-not-exist")
        assert queue.peek() is None

    def test_len(self, queue):
        assert len(queue) == 0
        queue.push(QueueItem(step_id="s1", job_id="j", priority=QueuePriority.NORMAL, resource_hints={}, locks=[], enqueued_at=time.time()))
        queue.push(QueueItem(step_id="s2", job_id="j", priority=QueuePriority.NORMAL, resource_hints={}, locks=[], enqueued_at=time.time()))
        assert len(queue) == 2

    def test_persistence_across_instances(self, tmp_queue_path):
        q1 = IdleQueue(backend=QueueBackend.FILE, path=tmp_queue_path)
        q1.push(QueueItem(step_id="persisted", job_id="j", priority=QueuePriority.NORMAL, resource_hints={}, locks=[], enqueued_at=time.time()))

        q2 = IdleQueue(backend=QueueBackend.FILE, path=tmp_queue_path)
        assert q2.peek().step_id == "persisted"
