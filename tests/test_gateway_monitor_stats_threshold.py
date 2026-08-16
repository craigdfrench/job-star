"""Tests for GatewayMonitor statistics + circuit-threshold follow-ups (Vikunja #1717).

Two small monitor fixes:
  #2: record_failure() should increment total_requests, same as record_success()
      (statistics were undercounting probe + failure calls).
  #3: ModelState.is_available() must honor the GatewayMonitor's per-instance
      failure_threshold instead of the module DEFAULT_FAILURE_THRESHOLD, so a
      custom circuit threshold actually takes effect.
"""

from __future__ import annotations

import pytest

from job_star.gatehouse.monitor import (
    DEFAULT_FAILURE_THRESHOLD,
    GatewayMonitor,
)


# --------------------------------------------------------------------------- #
# Fix #2: record_failure undercounts total_requests
# --------------------------------------------------------------------------- #


def test_record_failure_increments_total_requests():
    mon = GatewayMonitor()
    mon.record_failure("model=a", "HTTP 500")
    assert mon.state("model=a").total_requests == 1


def test_total_requests_counts_success_and_failure():
    mon = GatewayMonitor()
    mon.record_success("model=a")
    mon.record_success("model=a", tokens=100)
    mon.record_failure("model=a", "HTTP 500")
    # 2 successes + 1 failure — total_requests must count them all.
    assert mon.state("model=a").total_requests == 3
    assert mon.state("model=a").total_tokens == 100


def test_record_failure_does_not_reset_success_tokens():
    mon = GatewayMonitor()
    mon.record_success("model=a", tokens=50)
    mon.record_failure("model=a", "transient")
    assert mon.state("model=a").total_tokens == 50  # failure adds no tokens


# --------------------------------------------------------------------------- #
# Fix #3: per-instance failure_threshold honored by is_available
# --------------------------------------------------------------------------- #


def test_is_available_honors_custom_failure_threshold():
    # DEFAULT_FAILURE_THRESHOLD is 3; set the monitor to 5 and trip 4 failures.
    assert DEFAULT_FAILURE_THRESHOLD == 3
    mon = GatewayMonitor(failure_threshold=5)
    for _ in range(4):  # 4 < 5 -> still available
        mon.record_failure("model=a", "HTTP 500")
    assert mon.is_available("model=a") is True

    mon.record_failure("model=a", "HTTP 500")  # 5 == 5 -> circuit opens
    assert mon.is_available("model=a") is False


def test_is_available_uses_module_default_when_unset():
    mon = GatewayMonitor()  # default threshold = DEFAULT_FAILURE_THRESHOLD (3)
    for _ in range(DEFAULT_FAILURE_THRESHOLD):
        mon.record_failure("model=a", "HTTP 500")
    assert mon.is_available("model=a") is False


def test_failure_threshold_applies_to_refresh_created_states(monkeypatch):
    """States created inside refresh() inherit the monitor's threshold."""
    from job_star.gatehouse import monitor as _mon

    class _FakeResponse:
        status_code = 200
        def json(self):
            return {"data": [{"id": "model=x", "capabilities": {}}]}

    class _FakeClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *exc):
            return False
        async def get(self, url, headers=None):
            return _FakeResponse()

    monkeypatch.setattr(_mon.httpx, "AsyncClient", lambda **kw: _FakeClient())

    mon = GatewayMonitor(failure_threshold=5)
    import asyncio
    asyncio.run(mon.refresh(force=True))

    for _ in range(4):
        mon.record_failure("model=x", "HTTP 500")
    # 4 < 5 -> state created by refresh() must honor the monitor's threshold=5
    assert mon.is_available("model=x") is True
