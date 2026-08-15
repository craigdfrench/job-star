"""Tests for GatewayMonitor liveness probing (Vikunja #1709).

The probe is the proactive complement to the reactive circuit breaker (which
#14 fixed to include empty/non-string 200s). It sends a minimal call to each
free/cheap-tier model and feeds the result back into the routable set via
record_success/record_failure, so false-advertised models (nvidia 404, cline
empty content) trip the circuit on the probe instead of on a real goal's
planning attempt.
"""

from __future__ import annotations

import time

import pytest

from job_star.gatehouse import monitor
from job_star.gatehouse.monitor import GatewayMonitor, ModelTier
from job_star.models import ExecutionResult


def _result(*, success: bool, content: str = "", error: str | None = None,
            output_tokens: int = 0, x_gatehouse: dict | None = None) -> ExecutionResult:
    return ExecutionResult(
        content=content,
        model="",
        output_tokens=output_tokens,
        success=success,
        error=error,
        x_gatehouse=x_gatehouse or {},
    )


class _FakeExecute:
    """Replaces monitor._execute_ai with a scripted async callable."""

    def __init__(self, responses: dict[str, ExecutionResult]):
        self.responses = responses
        self.calls: list[str] = []

    async def __call__(self, *, prompt: str, model: str, max_tokens: int, timeout: float) -> ExecutionResult:
        self.calls.append(model)
        return self.responses.get(model, _result(success=False, error="HTTP 404: not found"))


def _seed_catalog(mon: GatewayMonitor, model_ids: list[str]) -> None:
    """Populate the monitor's cached catalog without a network refresh."""
    mon._gateway_models = {mid: {"id": mid, "capabilities": {}} for mid in model_ids}
    mon._last_refresh = time.time()
    for mid in model_ids:
        if mid not in mon._states:
            mon._states[mid] = monitor.ModelState(name=mid)


@pytest.mark.asyncio
async def test_probe_liveness_records_success(monkeypatch):
    """A successful probe (non-empty content) records success and keeps the model available."""
    mon = GatewayMonitor()
    fake = _FakeExecute({"model=a&prov=free": _result(success=True, content="ok", output_tokens=3)})
    monkeypatch.setattr(monitor, "_execute_ai", fake)

    available = await mon.probe_liveness("model=a&prov=free")

    assert available is True
    assert mon.state("model=a&prov=free").consecutive_failures == 0
    assert mon.state("model=a&prov=free").total_requests == 1
    assert mon.state("model=a&prov=free").last_probe is not None
    assert available is True  # probe succeeded


@pytest.mark.asyncio
async def test_probe_liveness_records_failure_on_empty(monkeypatch):
    """An empty-content 200 (the cline/glm-5.2 case) surfaces as a probe failure.

    execute() (PR #14) returns success=False for empty content; the probe must
    record that as a failure so the circuit breaker excludes the model.
    """
    mon = GatewayMonitor()
    fake = _FakeExecute({"model=glm-5.2&prov=cline": _result(success=False, error="empty response from model")})
    monkeypatch.setattr(monitor, "_execute_ai", fake)

    available = await mon.probe_liveness("model=glm-5.2&prov=cline")

    # Probe outcome is False (the call failed), even though the model is still
    # is_available (one failure < threshold). The CLI/caller distinguishes
    # probe-outcome from post-probe availability.
    assert available is False
    s = mon.state("model=glm-5.2&prov=cline")
    assert s.consecutive_failures == 1
    assert s.last_error == "empty response from model"
    assert mon.is_available("model=glm-5.2&prov=cline") is True


@pytest.mark.asyncio
async def test_probe_liveness_opens_circuit_after_threshold(monkeypatch):
    """Repeated probe failures trip the circuit (threshold=3) -> unavailable."""
    mon = GatewayMonitor(failure_threshold=3)
    fake = _FakeExecute({"model=x&prov=nvidia": _result(success=False, error="HTTP 404: not found")})
    monkeypatch.setattr(monitor, "_execute_ai", fake)

    for _ in range(3):
        await mon.probe_liveness("model=x&prov=nvidia")

    assert mon.is_available("model=x&prov=nvidia") is False


@pytest.mark.asyncio
async def test_probe_free_pool_filters_to_free_cheap_tiers(monkeypatch):
    """probe_free_pool only probes FREE/QUOTA_FREE/CHEAP-tier catalog models."""
    mon = GatewayMonitor()
    # Mix tiers: free, quota_free, cheap, premium
    _seed_catalog(mon, [
        "ollama/glm-5.2",          # QUOTA_FREE (TIER_OVERRIDES)
        "nvidia/nemotron-free",    # unknown -> PREMIUM default (not probed)
        "deepseek-v4",             # CHEAP (family prefix heuristic)
    ])
    # Make deepseek tier CHEAP via the family heuristic; glm-5.2 is QUOTA_FREE.
    # nvidia/nemotron-free is unknown -> PREMIUM (skipped).
    fake = _FakeExecute({
        "ollama/glm-5.2": _result(success=True, content="ok"),
        "deepseek-v4": _result(success=True, content="ok"),
    })
    monkeypatch.setattr(monitor, "_execute_ai", fake)

    results = await mon.probe_free_pool()

    assert set(results.keys()) == {"ollama/glm-5.2", "deepseek-v4"}
    assert "nvidia/nemotron-free" not in results
    assert fake.calls == ["ollama/glm-5.2", "deepseek-v4"]


@pytest.mark.asyncio
async def test_probe_free_pool_skips_quota_hold_models(monkeypatch):
    """Models in quota hold (known reset time) are not re-probed (no wasted call)."""
    mon = GatewayMonitor()
    _seed_catalog(mon, ["ollama/glm-5.2", "deepseek-v4"])
    # Force glm-5.2 into quota hold (not circuit-open)
    mon.state("ollama/glm-5.2").enter_quota_hold(3600)
    fake = _FakeExecute({"deepseek-v4": _result(success=True, content="ok")})
    monkeypatch.setattr(monitor, "_execute_ai", fake)

    results = await mon.probe_free_pool()

    assert "ollama/glm-5.2" not in results
    assert "deepseek-v4" in results
    assert "ollama/glm-5.2" not in fake.calls  # not probed


@pytest.mark.asyncio
async def test_probe_free_pool_probes_circuit_open_models_for_recovery(monkeypatch):
    """Circuit-open models (failures >= threshold, NOT in quota hold) ARE probed.

    This is their recovery path: a transient 404/empty that tripped the circuit
    clears when a later probe succeeds (record_success resets
    consecutive_failures). Skipping them would strand them unavailable forever
    (finding #7/#16).
    """
    mon = GatewayMonitor(failure_threshold=3)
    _seed_catalog(mon, ["model=a&prov=free", "model=b&prov=nvidia"])
    # Trip model=b's circuit (3 failures), no quota hold
    s = mon.state("model=b&prov=nvidia")
    for _ in range(3):
        s.record_failure("HTTP 404")
    assert not s.is_in_quota_hold  # circuit-open, not quota hold
    assert not mon.is_available("model=b&prov=nvidia")

    # Probe where model=b recovers (returns success)
    fake = _FakeExecute({
        "model=a&prov=free": _result(success=True, content="ok"),
        "model=b&prov=nvidia": _result(success=True, content="ok"),
    })
    monkeypatch.setattr(monitor, "_execute_ai", fake)

    results = await mon.probe_free_pool(reprobe_ttl=0.0)  # ignore throttle for the test

    assert "model=b&prov=nvidia" in results  # circuit-open model WAS probed
    assert results["model=b&prov=nvidia"] is True
    # record_success reset the circuit -> available again
    assert mon.is_available("model=b&prov=nvidia") is True
    assert mon.state("model=b&prov=nvidia").consecutive_failures == 0


@pytest.mark.asyncio
async def test_probe_free_pool_circuit_open_uses_longer_reprobe_ttl(monkeypatch):
    """Circuit-open models are throttled by reprobe_ttl (longer than probe_ttl)."""
    mon = GatewayMonitor(failure_threshold=3)
    _seed_catalog(mon, ["model=a&prov=free", "model=b&prov=nvidia"])
    # Trip model=b's circuit
    for _ in range(3):
        mon.state("model=b&prov=nvidia").record_failure("HTTP 404")
    # Mark both as probed just now (within probe_ttl but outside a tiny reprobe_ttl)
    now = time.time()
    mon.state("model=a&prov=free").last_probe = now
    mon.state("model=b&prov=nvidia").last_probe = now
    fake = _FakeExecute({
        "model=a&prov=free": _result(success=True, content="ok"),
        "model=b&prov=nvidia": _result(success=True, content="ok"),
    })
    monkeypatch.setattr(monitor, "_execute_ai", fake)

    # probe_ttl=300 (recently probed -> available model skipped),
    # reprobe_ttl=0  (circuit-open model NOT throttled -> probed)
    results = await mon.probe_free_pool(probe_ttl=300.0, reprobe_ttl=0.0)

    assert "model=a&prov=free" not in results  # throttled by probe_ttl
    assert "model=b&prov=nvidia" in results     # circuit-open, reprobe_ttl=0 -> probed


@pytest.mark.asyncio
async def test_probe_liveness_sets_last_probe_before_the_call(monkeypatch):
    """last_probe is set BEFORE the execute call so concurrent callers skip.

    Guards against duplicate billable probes under overlapping invocations
    (finding #6/#20). The fake asserts last_probe is already set when it runs.
    """
    mon = GatewayMonitor()
    seen = {}

    class _CheckFake:
        async def __call__(self, *, prompt, model, max_tokens, timeout):
            seen[model] = mon.state(model).last_probe
            return _result(success=True, content="ok")

    monkeypatch.setattr(monitor, "_execute_ai", _CheckFake())

    await mon.probe_liveness("model=a&prov=free")

    assert seen["model=a&prov=free"] is not None  # set before the call ran


@pytest.mark.asyncio
async def test_probe_free_pool_throttles_recently_probed(monkeypatch):
    """A model probed within probe_ttl is not re-probed."""
    mon = GatewayMonitor()
    _seed_catalog(mon, ["ollama/glm-5.2"])
    # Mark as probed just now
    mon.state("ollama/glm-5.2").last_probe = time.time()
    fake = _FakeExecute({"ollama/glm-5.2": _result(success=True, content="ok")})
    monkeypatch.setattr(monitor, "_execute_ai", fake)

    results = await mon.probe_free_pool(probe_ttl=300.0)

    assert results == {}  # throttled, nothing probed
    assert fake.calls == []

    # After the TTL window, it gets probed
    mon.state("ollama/glm-5.2").last_probe = time.time() - 301
    results = await mon.probe_free_pool(probe_ttl=300.0)
    assert "ollama/glm-5.2" in results


@pytest.mark.asyncio
async def test_probe_free_pool_respects_max_models(monkeypatch):
    """max_models caps the number of probes."""
    mon = GatewayMonitor()
    _seed_catalog(mon, ["ollama/glm-5.2", "deepseek-v4", "ollama/glm-5"])
    fake = _FakeExecute({
        "ollama/glm-5.2": _result(success=True, content="ok"),
        "deepseek-v4": _result(success=True, content="ok"),
        "ollama/glm-5": _result(success=True, content="ok"),
    })
    monkeypatch.setattr(monitor, "_execute_ai", fake)

    results = await mon.probe_free_pool(max_models=2)

    assert len(results) == 2
    assert len(fake.calls) == 2