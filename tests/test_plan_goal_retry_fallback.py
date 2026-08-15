"""Tests for Orchestrator.plan_goal retry/fallback behavior.

Regression for the dead-code retry loop (Vikunja #1708): plan_goal computed a
fallback model but never used it -- route() was re-called with
model_override=None on attempts >0, so it re-picked the same just-failed
top-scored model every attempt. The fix always passes model_override=model,
so the retry actually tries the fallback.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import job_star.orchestrator as orch_mod
from job_star.models import Goal, ExecutionResult, RoutingDecision


def _make_goal() -> Goal:
    return Goal(id="g-1708", title="Test goal", description="desc")


async def _route_top_then_override(**kwargs):
    """Fake route: honor an explicit override; otherwise return the top model 'A'."""
    override = kwargs.get("model_override")
    return RoutingDecision(model=override or "A", provider="test",
                            reason="fake", complexity="moderate")


async def _execute_fail_A_then_pass_B(prompt, model, system_prompt=None, **kw):
    """Fake execute: fail on the top model A, succeed on the fallback B."""
    if model == "A":
        return ExecutionResult(success=False, error="HTTP 404: provider not found", model="A")
    return ExecutionResult(success=True,
                            content="1. Step One - do thing one\n2. Step Two - do thing two",
                            model=model, input_tokens=1, output_tokens=2)


async def test_plan_goal_uses_fallback_model_on_retry(monkeypatch):
    """On a top-model failure, the retry must call the fallback model (not re-pick A)."""
    monkeypatch.setattr(orch_mod, "get_goal", AsyncMock(return_value=_make_goal()))
    monkeypatch.setattr(orch_mod, "get_steps", AsyncMock(return_value=[]))  # no existing steps
    monkeypatch.setattr(orch_mod, "audit", AsyncMock(return_value=None))
    monkeypatch.setattr(orch_mod, "route", _route_top_then_override)
    execute_calls: list[str] = []
    async def _capture_execute(prompt, model, system_prompt=None, **kw):
        execute_calls.append(model)
        return await _execute_fail_A_then_pass_B(prompt, model, system_prompt=system_prompt, **kw)
    monkeypatch.setattr(orch_mod, "execute_ai", _capture_execute)

    gm = MagicMock()
    gm.record_failure = MagicMock()
    gm.pick_fallback = MagicMock(return_value="B")

    o = orch_mod.Orchestrator()
    o.gateway_monitor = gm

    steps = await o.plan_goal("g-1708")

    # The fallback B must actually have been called (attempt 0 = A fails, attempt 1 = B succeeds).
    assert execute_calls == ["A", "B"], f"expected A then B, got {execute_calls!r}"
    assert len(steps) >= 1
    gm.record_failure.assert_called()       # A's failure recorded
    gm.pick_fallback.assert_called()        # B picked as the fallback


async def test_plan_goal_succeeds_first_try_does_not_call_fallback(monkeypatch):
    """When the top model succeeds immediately, no fallback is consulted."""
    monkeypatch.setattr(orch_mod, "get_goal", AsyncMock(return_value=_make_goal()))
    monkeypatch.setattr(orch_mod, "get_steps", AsyncMock(return_value=[]))
    monkeypatch.setattr(orch_mod, "audit", AsyncMock(return_value=None))
    monkeypatch.setattr(orch_mod, "route", _route_top_then_override)
    monkeypatch.setattr(orch_mod, "execute_ai",
                        AsyncMock(return_value=ExecutionResult(
                            success=True, content="1. Step One - x\n2. Step Two - y",
                            model="A", input_tokens=1, output_tokens=2)))

    gm = MagicMock()
    gm.record_failure = MagicMock()
    gm.pick_fallback = MagicMock()

    o = orch_mod.Orchestrator()
    o.gateway_monitor = gm

    steps = await o.plan_goal("g-1708")

    assert len(steps) >= 1
    gm.record_failure.assert_not_called()
    gm.pick_fallback.assert_not_called()


# pytest-asyncio config: these are bare `async def` test functions; run them on the loop.
def test_uses_fallback(monkeypatch):
    asyncio.run(test_plan_goal_uses_fallback_model_on_retry(monkeypatch))


def test_no_fallback_on_success(monkeypatch):
    asyncio.run(test_plan_goal_succeeds_first_try_does_not_call_fallback(monkeypatch))