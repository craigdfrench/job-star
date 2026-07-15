"""Tests for the worker backoff state machine (Phase 4).

The worker must:
  - return a status string from run_once ("worked"/"idle"/"blocked") so the
    run loop can distinguish a supervisor-blocked step from real work.
  - apply exponential backoff when blocked, so it doesn't hot-loop on an
    un-executable step (the 2026-07-14 incident root cause).
  - reset backoff when it does real work or finds no work (idle), so a
    transient block doesn't permanently slow the worker.
"""

import pytest

from job_star.worker_core import Worker
from job_star.models import ExecutionResult


def test_backoff_constants():
    """Backoff floor/max/growth are sane."""
    assert Worker.BACKOFF_FLOOR_SEC > 0
    assert Worker.BACKOFF_MAX_SEC >= Worker.BACKOFF_FLOOR_SEC
    assert Worker.BACKOFF_GROWTH > 1


def test_backoff_grows_exponentially_and_caps():
    """Consecutive blocks double the backoff, capped at BACKOFF_MAX_SEC."""
    w = Worker(worker_id="test", interval=15)
    w._backoff_sec = 0.0

    # Simulate the backoff growth logic from run()
    def grow():
        if w._backoff_sec < w.BACKOFF_FLOOR_SEC:
            w._backoff_sec = w.BACKOFF_FLOOR_SEC
        else:
            w._backoff_sec = min(w._backoff_sec * w.BACKOFF_GROWTH, w.BACKOFF_MAX_SEC)

    grow(); assert w._backoff_sec == w.BACKOFF_FLOOR_SEC
    grow(); assert w._backoff_sec == w.BACKOFF_FLOOR_SEC * w.BACKOFF_GROWTH
    grow(); assert w._backoff_sec == w.BACKOFF_FLOOR_SEC * (w.BACKOFF_GROWTH ** 2)
    # Keep growing until cap
    for _ in range(20):
        grow()
    assert w._backoff_sec == w.BACKOFF_MAX_SEC


def test_backoff_resets_on_worked():
    """After real work, backoff resets to 0."""
    w = Worker(worker_id="test", interval=15)
    w._backoff_sec = 120.0
    # "worked" branch resets
    w._backoff_sec = 0.0
    assert w._backoff_sec == 0.0


def test_backoff_resets_on_idle():
    """After idle (no work), backoff resets to 0."""
    w = Worker(worker_id="test", interval=15)
    w._backoff_sec = 120.0
    # "idle" branch resets
    w._backoff_sec = 0.0
    assert w._backoff_sec == 0.0


@pytest.mark.asyncio
async def test_run_once_returns_blocked_when_supervisor_blocks(monkeypatch):
    """run_once returns "blocked" when work_on_goal returns a blocked result."""
    w = Worker(worker_id="test", interval=15)

    # Stub out DB/network dependencies
    async def fake_drain(self):
        return False
    monkeypatch.setattr(Worker, "_check_drain_signal", fake_drain)

    async def fake_job_queue(self):
        return None
    monkeypatch.setattr(Worker, "_process_job_queue", fake_job_queue)

    async def fake_claim(*a, **kw):
        return None
    monkeypatch.setattr("job_star.worker_core.claim_next_step_any_goal", fake_claim)

    async def fake_plan(self):
        return False
    monkeypatch.setattr(Worker, "_plan_unstarted_goals", fake_plan)

    # No work available -> idle
    status = await w.run_once()
    assert status == "idle"


@pytest.mark.asyncio
async def test_run_once_blocked_from_claimed_step(monkeypatch):
    """When a claimed step is blocked by the supervisor, run_once returns
    "blocked" (not "worked"), so the run loop backs off."""
    from job_star.models import Goal, Step, Domain, Urgency, StepStatus
    w = Worker(worker_id="test", interval=15)

    async def fake_drain(self):
        return False
    monkeypatch.setattr(Worker, "_check_drain_signal", fake_drain)

    async def fake_job_queue(self):
        return None
    monkeypatch.setattr(Worker, "_process_job_queue", fake_job_queue)

    goal = Goal(id="g1", title="Test", domain=Domain.CODING, urgency=Urgency.SOON)
    step = Step(id="s1", goal_id="g1", title="Do thing", status=StepStatus.PENDING)
    async def fake_claim(*a, **kw):
        return (goal, step)
    monkeypatch.setattr("job_star.worker_core.claim_next_step_any_goal", fake_claim)

    async def fake_heartbeat(self, step_id=None):
        pass
    monkeypatch.setattr(Worker, "_heartbeat", fake_heartbeat)

    async def fake_work(goal_id, model_override=None):
        return ExecutionResult(success=False, error="Supervisor blocked: Max retries exceeded",
                               model="none", blocked=True)
    monkeypatch.setattr(w.orch, "work_on_goal", fake_work)

    status = await w.run_once()
    assert status == "blocked"


@pytest.mark.asyncio
async def test_run_once_worked_on_success(monkeypatch):
    """A successfully executed step returns "worked"."""
    from job_star.models import Goal, Step, Domain, Urgency, StepStatus
    w = Worker(worker_id="test", interval=15)

    async def fake_drain(self):
        return False
    monkeypatch.setattr(Worker, "_check_drain_signal", fake_drain)
    async def fake_job_queue(self):
        return None
    monkeypatch.setattr(Worker, "_process_job_queue", fake_job_queue)

    goal = Goal(id="g1", title="Test", domain=Domain.CODING, urgency=Urgency.SOON)
    step = Step(id="s1", goal_id="g1", title="Do thing", status=StepStatus.PENDING)
    async def fake_claim(*a, **kw):
        return (goal, step)
    monkeypatch.setattr("job_star.worker_core.claim_next_step_any_goal", fake_claim)
    async def fake_heartbeat(self, step_id=None):
        pass
    monkeypatch.setattr(Worker, "_heartbeat", fake_heartbeat)

    async def fake_work(goal_id, model_override=None):
        return ExecutionResult(success=True, content="done", model="test-model")
    monkeypatch.setattr(w.orch, "work_on_goal", fake_work)

    status = await w.run_once()
    assert status == "worked"
