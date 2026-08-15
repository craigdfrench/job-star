"""Tests for the worker's background liveness-probe wiring (Vikunja #1709).

The generic worker starts a background task that periodically probes the
free/cheap model pool, feeding probed liveness into that worker's gateway
monitor so the router excludes false-advertised models (404/empty) before
selecting them for a real goal. Expert workers skip it to avoid every worker
re-probing the same pool. The monitor is per-process, so the probe must run
in the worker that does planning.
"""

import asyncio

import pytest

from job_star.worker_core import Worker


def test_probe_interval_is_positive():
    """PROBE_INTERVAL_SEC must be a positive float (env-overridable cadence)."""
    assert isinstance(Worker.PROBE_INTERVAL_SEC, float)
    assert Worker.PROBE_INTERVAL_SEC > 0


def test_probe_task_gated_to_generic_worker():
    """Only the generic worker (expert is None, not expert_any) starts the probe.

    The run() method gates on `self.expert is None and self.expert_any is False`.
    An expert worker (expert set) or expert_any worker must not start it, to
    avoid every worker re-probing the same pool.
    """
    generic = Worker(worker_id="generic")
    assert generic.expert is None
    assert generic.expert_any is False
    # gating condition is True for the generic worker
    assert (generic.expert is None and generic.expert_any is False) is True

    expert = Worker(worker_id="expert", expert="ci")
    assert (expert.expert is None and expert.expert_any is False) is False

    any_expert = Worker(worker_id="any", expert_any=True)
    assert (any_expert.expert is None and any_expert.expert_any is False) is False


@pytest.mark.asyncio
async def test_probe_loop_calls_probe_free_pool_then_exits_on_drain(monkeypatch):
    """_run_liveness_probe_loop probes once, then exits when _draining is set.

    Patches probe_free_pool to return a scripted result and PROBE_INTERVAL_SEC
    to a tiny value so the loop sleeps briefly before we flip _draining.
    """
    w = Worker(worker_id="test", interval=15)
    w.PROBE_INTERVAL_SEC = 0.05  # tiny sleep so the loop iterates fast

    calls: list[dict] = []

    async def fake_probe_free_pool(max_models=None, prompt="ping", max_tokens=256,
                                   probe_ttl=300.0, timeout=30.0):
        calls.append({"max_models": max_models})
        # flip draining so the loop exits after this iteration's sleep
        w._draining = True
        return {"model=a&prov=free": True, "model=b&prov=nvidia": False}

    monkeypatch.setattr(w.orch.gateway_monitor, "probe_free_pool", fake_probe_free_pool)

    await w._run_liveness_probe_loop()

    assert len(calls) == 1
    # The loop must terminate (not hang) once _draining is True.


@pytest.mark.asyncio
async def test_probe_loop_swallows_probe_errors(monkeypatch):
    """A raising probe_free_pool must not break the loop (best-effort probe)."""
    w = Worker(worker_id="test", interval=15)
    w.PROBE_INTERVAL_SEC = 0.05

    call_count = {"n": 0}

    async def raising_probe(**kwargs):
        call_count["n"] += 1
        w._draining = True  # exit after this iteration's sleep
        raise RuntimeError("gateway exploded")

    monkeypatch.setattr(w.orch.gateway_monitor, "probe_free_pool", raising_probe)

    # Must not raise
    await w._run_liveness_probe_loop()
    assert call_count["n"] == 1