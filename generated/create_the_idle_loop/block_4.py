"""Tests for jobstar.idle.supervisor."""
import time
import signal
from unittest.mock import patch, MagicMock

import pytest

from jobstar.idle.supervisor import (
    Supervisor,
    SupervisedResult,
    SupervisionOutcome,
    StepAction,
)
from jobstar.idle.queue import QueueItem, QueuePriority


@pytest.fixture
def supervisor():
    return Supervisor(default_timeout_seconds=5)


@pytest.fixture
def sample_item():
    return QueueItem(
        step_id="step-1",
        job_id="job-1",
        priority=QueuePriority.NORMAL,
        resource_hints={},
        locks=[],
        enqueued_at=time.time(),
    )


class TestSupervisedResult:
    def test_success_result(self):
        r = SupervisedResult(outcome=SupervisionOutcome.SUCCESS, return_value=42, elapsed_seconds=0.5)
        assert r.outcome == SupervisionOutcome.SUCCESS
        assert r.return_value == 42
        assert r.success is True

    def test_timeout_result(self):
        r = SupervisedResult(outcome=SupervisionOutcome.TIMEOUT, return_value=None, elapsed_seconds=5.0)
        assert r.outcome == SupervisionOutcome.TIMEOUT
        assert r.success is False

    def test_error_result(self):
        r = SupervisedResult(outcome=SupervisionOutcome.ERROR, return_value=None, elapsed_seconds=0.1, error="boom")
        assert r.outcome == SupervisionOutcome.ERROR
        assert r.error == "boom"
        assert r.success is False


class TestSupervisor:
    def test_run_success(self, supervisor, sample_item):
        def action(ctx):
            return "done"

        result = supervisor.run(sample_item, action)
        assert result.outcome == SupervisionOutcome.SUCCESS
        assert result.return_value == "done"

    def test_run_with_explicit_timeout(self, supervisor, sample_item):
        def action(ctx):
            time.sleep(0.05)
            return "done"

        result = supervisor.run(sample_item, action, timeout_seconds=2)
        assert result.outcome == SupervisionOutcome.SUCCESS

    def test_run_timeout(self, supervisor, sample_item):
        def action(ctx):
            time.sleep(10)
            return "done"

        result = supervisor.run(sample_item, action, timeout_seconds=0.1)
        assert result.outcome == SupervisionOutcome.TIMEOUT
        assert result.success is False

    def test_run_error(self, supervisor, sample_item):
        def action(ctx):
            raise ValueError("boom")

        result = supervisor.run(sample_item, action)
        assert result.outcome == SupervisionOutcome.ERROR
        assert "boom" in result.error

    def test_run_cancellation(self, supervisor, sample_item):
        cancelled = {"flag": False}

        def action(ctx):
            while not ctx.cancelled:
                time.sleep(0.01)
            cancelled["flag"] = True
            return "cancelled"

        # Simulate external cancellation by setting ctx.cancelled via a side thread
        import threading

        def canceller(ctx_holder):
            time.sleep(0.05)
            ctx_holder[0].cancelled = True

        ctx_holder = [None]

        def wrapped_action(ctx):
            ctx_holder[0] = ctx
            return action(ctx)

        t = threading.Thread(target=canceller, args=(ctx_holder,))
        t.start()

        result = supervisor.run(sample_item, wrapped_action, timeout_seconds=5)
        t.join()

        assert result.outcome == SupervisionOutcome.SUCCESS
        assert cancelled["flag"] is True

    def test_run_returns_elapsed_time(self, supervisor, sample_item):
        def action(ctx):
            time.sleep(0.05)
            return None

        result = supervisor.run(sample_item, action, timeout_seconds=5)
        assert result.elapsed_seconds >= 0.04

    def test_run_step_action_object(self, supervisor, sample_item):
        class MyAction(StepAction):
            def execute(self, ctx):
                return 123

        result = supervisor.run(sample_item, MyAction())
        assert result.outcome == SupervisionOutcome.SUCCESS
        assert result.return_value == 123
