"""Regression test for orchestrator gate-status preservation.

When all steps of a goal are COMPLETED, `work_on_goal` used to unconditionally
set the goal status to COMPLETED. That clobbered gate verdicts
(ci_pass/ci_fail/review_pass/review_block/review_error) set by a CI/review
executor, which stranded the deploy gate (it queries job-star for
ci_pass/review_pass and found `completed` instead).

The fix guards the COMPLETED transition so it only applies when the goal isn't
already carrying a gate verdict. This test verifies both directions:

  - a goal already in a gate status (review_pass) is NOT overwritten to COMPLETED
  - a goal in a non-gate status (active) IS transitioned to COMPLETED as before
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import job_star.orchestrator as orch
from job_star.models import Goal, GoalStatus, Step, StepStatus


def _completed_step(goal_id: str) -> Step:
    return Step(id="step-1", goal_id=goal_id, title="Run review gate",
                status=StepStatus.COMPLETED, order_index=1)


def _exercise_work_on_goal(monkeypatch, goal_status: GoalStatus) -> list:
    """Drive `work_on_goal` with all steps COMPLETED and return the list of
    `update_goal_status` call args recorded."""
    goal_id = "g-112"

    goal = Goal(id=goal_id, title="Review: PR #112", status=goal_status,
                expert="review")

    monkeypatch.setattr(orch, "get_goal", AsyncMock(return_value=goal))
    monkeypatch.setattr(orch, "get_steps", AsyncMock(return_value=[_completed_step(goal_id)]))
    monkeypatch.setattr(orch, "claim_next_step", AsyncMock(return_value=None))
    status_calls = []
    async def _record_status(gid, status):
        status_calls.append((gid, status))
    monkeypatch.setattr(orch, "update_goal_status", _record_status)
    monkeypatch.setattr(orch, "audit", AsyncMock(return_value=None))

    o = orch.Orchestrator()
    # Avoid touching real supervisor/gateway/registry machinery; work_on_goal's
    # all_done path only calls get_goal, get_steps, claim_next_step,
    # update_goal_status, audit — all patched above.
    asyncio.run(o.work_on_goal(goal_id))
    return status_calls


def test_gate_status_review_pass_is_preserved(monkeypatch):
    """A goal already at review_pass must NOT be overwritten to COMPLETED."""
    calls = _exercise_work_on_goal(monkeypatch, GoalStatus.REVIEW_PASS)
    # No transition to COMPLETED; the gate verdict is preserved.
    assert (GoalStatus.COMPLETED, ) not in [(c[1],) for c in calls]
    assert all(c[1] != GoalStatus.COMPLETED for c in calls), \
        f"gate verdict review_pass was clobbered: {calls}"
    # Specifically: update_goal_status should not have been called at all here.
    assert calls == [], f"expected no status update, got {calls}"


def test_gate_status_ci_pass_is_preserved(monkeypatch):
    """A goal already at ci_pass must NOT be overwritten to COMPLETED."""
    calls = _exercise_work_on_goal(monkeypatch, GoalStatus.CI_PASS)
    assert all(c[1] != GoalStatus.COMPLETED for c in calls), \
        f"gate verdict ci_pass was clobbered: {calls}"


def test_non_gate_status_active_is_completed(monkeypatch):
    """A non-gate goal (active) with all steps done IS transitioned to COMPLETED."""
    calls = _exercise_work_on_goal(monkeypatch, GoalStatus.ACTIVE)
    assert any(c[1] == GoalStatus.COMPLETED for c in calls), \
        f"active goal should transition to COMPLETED, got {calls}"