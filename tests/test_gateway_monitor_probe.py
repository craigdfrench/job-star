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

    assert available is True  # one failure < threshold (3), still available
    s = mon.state("model=glm-5.2&prov=cline")
    assert s.consecutive_failures == 1
    assert s.last_error == "empty response from model"


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
async def test_probe_free_pool_skips_already_unavailable(monkeypatch):
    """Models already in quota hold / circuit-open are not re-probed (no wasted call)."""
    mon = GatewayMonitor()
    _seed_catalog(mon, ["ollama/glm-5.2", "deepseek-v4"])
    # Force glm-5.2 into quota hold
    mon.state("ollama/glm-5.2").enter_quota_hold(3600)
    fake = _FakeExecute({"deepseek-v4": _result(success=True, content="ok")})
    monkeypatch.setattr(monitor, "_execute_ai", fake)

    results = await mon.probe_free_pool()

    assert "ollama/glm-5.2" not in results
    assert "deepseek-v4" in results
    assert "ollama/glm-5.2" not in fake.calls  # not probed


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