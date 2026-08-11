"""Regression test for the re-enqueue (POST /goals/{id}/work) "plan" path.

When a goal was re-queued via the API, the worker's `_process_job_queue`
referenced an undefined `goal` variable while calling `self.orch.plan_goal(...)`,
raising `NameError: name 'goal' is not defined` and failing the job before the
goal was ever planned. The fix passes `goal_id` (which *is* in scope from the
claimed job-queue row) to `plan_goal()`, which loads the goal itself.
"""

import asyncio
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import job_star.worker_core as wc
from job_star.models import ExecutionResult


def _make_plan_job(goal_id: str) -> dict:
    return {
        "id": uuid.uuid4(),
        "goal_id": uuid.UUID(goal_id),
        "kind": "plan",
        "payload": None,
    }


def test_process_plan_job_passes_goal_id_not_undefined_goal(monkeypatch):
    """A 'plan' job must call plan_goal(goal_id, ...) — never reference `goal`."""
    goal_id = "96aa4f32-043e-4caa-ba08-f2111c51d70a"

    # Fake orchestrator: record the goal_id handed to plan_goal; work_on_goal
    # returns a success so the job completes cleanly.
    class FakeOrch:
        def __init__(self):
            self.planned_with = None

        async def plan_goal(self, gid, model=None):
            self.planned_with = gid
            return []

        async def work_on_goal(self, gid, model_override=None):
            return ExecutionResult(success=True, content="done", model="ci")

    fake_orch = FakeOrch()

    # Patch the module-level functions the plan path touches.
    monkeypatch.setattr(wc, "claim_job_queue_item", AsyncMock(return_value=_make_plan_job(goal_id)))
    monkeypatch.setattr(wc, "complete_job", AsyncMock(return_value=None))
    monkeypatch.setattr(wc, "publish_event", AsyncMock(return_value=None))
    # Avoid any DB pool use / worker registry writes.
    monkeypatch.setattr(wc, "get_pool", AsyncMock(side_effect=AssertionError("get_pool should not be called for plan path")))

    worker = wc.Worker(worker_id="test", model="ci", expert="ci")
    worker.orch = fake_orch

    rc = asyncio.run(worker._process_job_queue())

    # The plan path must reach plan_goal with the *goal_id* string — the old
    # code raised NameError before getting here.
    assert fake_orch.planned_with == goal_id, (
        f"plan_goal called with {fake_orch.planned_with!r}, expected {goal_id!r}"
    )
    # plan -> eventually complete_job("completed") on the success path
    assert rc == "worked"
