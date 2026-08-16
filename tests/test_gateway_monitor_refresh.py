"""Tests for GatewayMonitor.refresh() fail-open behavior (Vikunja #1717).

Regression coverage for the review's ``most_important_fix`` on the planning
chain: ``GatewayMonitor.refresh()`` swallowed every error, replaced
``_gateway_models`` with ``{}`` on a transient /models failure, and cached that
empty catalog for the full 60s TTL -- disabling routing, fallback, and liveness
probing on any brief network hiccup.

Expected behavior (`refresh` is fail-open):
  (a) first fetch succeeds          -> catalog populated
  (b) transient failure AFTER a good fetch -> last-known-good catalog PRESERVED,
      not wiped to {}
  (c) repeated failures             -> an empty/absent catalog is NOT cached-and-
      stuck for the TTL (each retry actually re-hits the endpoint)
  (d) recovery                      -> catalog refreshes normally
"""

from __future__ import annotations

import time

import pytest

from job_star.gatehouse import monitor
from job_star.gatehouse.monitor import GatewayMonitor


class _FakeResponse:
    """Minimal stand-in for httpx.Response: status_code + json()."""

    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _ScriptedClient:
    """Replaces ``httpx.AsyncClient`` inside refresh() with a scripted fake.

    ``refresh()`` does ``async with httpx.AsyncClient(timeout=10) as client``
    then ``client.get(...)``. We monkeypatch ``monitor.httpx.AsyncClient`` with
    a factory returning this instance so the network never actually runs.

    ``responses`` is a list of callables ``(status_code, payload)`` played back
    in order. Passing ``status_code=None`` makes ``get`` raise (network error).
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[int | None, object]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, headers=None):
        assert self.responses, f"scripted client exhausted; url={url}"
        status, payload = self.responses.pop(0)
        self.calls.append((status, payload))
        if status is None:
            raise OSError("simulated transient network error")
        return _FakeResponse(status, payload)


def _patch_refresh_client(monkeypatch, responses) -> _ScriptedClient:
    """Point refresh()'s httpx.AsyncClient at a scripted fake; return the fake."""
    fake = _ScriptedClient(responses)
    monkeypatch.setattr(monitor.httpx, "AsyncClient", lambda **kw: fake)
    return fake


def _catalog(*ids: str):
    return {"data": [{"id": mid, "capabilities": {}} for mid in ids]}


@pytest.fixture
def mon() -> GatewayMonitor:
    return GatewayMonitor()


# --------------------------------------------------------------------------- #
# (a) first fetch succeeds -> catalog populated
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_refresh_first_fetch_success_populates_catalog(monkeypatch, mon):
    _patch_refresh_client(
        monkeypatch,
        [(200, _catalog("model=glm-5.2&prov=ollama", "model=kimi-k2-7&prov=ollama"))],
    )

    catalog = await mon.refresh()

    assert set(catalog) == {
        "model=glm-5.2&prov=ollama",
        "model=kimi-k2-7&prov=ollama",
    }
    # Catalog is live in the monitor, stamped as recently refreshed, and each
    # model got a state slot.
    assert set(mon._gateway_models) == set(catalog)
    assert mon._last_refresh > 0
    assert "model=glm-5.2&prov=ollama" in mon._states


# --------------------------------------------------------------------------- #
# (b) transient failure AFTER a good fetch -> catalog preserved, not wiped
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_refresh_transient_failure_preserves_last_known_good(monkeypatch, mon):
    fake = _patch_refresh_client(
        monkeypatch,
        [
            (200, _catalog("model=glm-5.2&prov=ollama")),  # good fetch
            (None, None),  # transient network error
        ],
    )

    good = await mon.refresh()
    assert set(good) == {"model=glm-5.2&prov=ollama"}

    # Age the cache past the 60s TTL so the next call actually re-hits the
    # endpoint (the realistic fail-open scenario: a scheduled refresh after TTL
    # expiry lands on a gateway blip).
    mon._last_refresh = time.time() - 999

    # Second refresh hits a gateway blip; refresh must FAIL-OPEN, not wipe.
    result = await mon.refresh()

    assert set(result) == {"model=glm-5.2&prov=ollama"}
    assert set(mon._gateway_models) == {"model=glm-5.2&prov=ollama"}
    assert fake.calls[1][0] is None  # the second call really was the failure


@pytest.mark.asyncio
async def test_refresh_non200_failure_preserves_last_known_good(monkeypatch, mon):
    _patch_refresh_client(
        monkeypatch,
        [
            (200, _catalog("model=glm-5.2&prov=ollama")),
            (500, None),  # gateway internal error, not a network exception
        ],
    )

    await mon.refresh()
    result = await mon.refresh()

    assert set(result) == {"model=glm-5.2&prov=ollama"}
    assert set(mon._gateway_models) == {"model=glm-5.2&prov=ollama"}


# --------------------------------------------------------------------------- #
# (c) repeated failures -> does NOT cache-and-stick the empty result
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_refresh_repeated_failures_do_not_cache_and_stick_empty(monkeypatch, mon):
    fake = _patch_refresh_client(
        monkeypatch,
        [
            (None, None),  # first fetch fails (no last-known-good yet)
            (None, None),  # second fetch fails again -> must still re-attempt
            (200, _catalog("model=glm-5.2&prov=ollama")),  # third recovers
        ],
    )

    first = await mon.refresh()
    assert first == {}

    # With fail-open, an empty/absent result is NOT stamped into _last_refresh,
    # so this call must NOT be served from a 60s "empty cache" -- it must
    # actually re-hit the endpoint (a second scripted response is consumed).
    second = await mon.refresh()
    assert second == {}
    assert len(fake.calls) == 2  # both attempts did real network work

    recovered = await mon.refresh()
    assert set(recovered) == {"model=glm-5.2&prov=ollama"}


# --------------------------------------------------------------------------- #
# (d) recovery -> catalog refreshes normally
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_refresh_recovers_after_failure(monkeypatch, mon):
    fake = _patch_refresh_client(
        monkeypatch,
        [
            (200, _catalog("model=glm-5.2&prov=ollama")),  # good
            (None, None),  # blip -> preserved
            (200, _catalog("model=glm-5.2&prov=ollama", "model=new-model&prov=ollama")),  # recovery
        ],
    )

    await mon.refresh()          # good
    mon._last_refresh = time.time() - 999  # TTL expired -> next will re-hit
    await mon.refresh()          # blip, preserved
    mon._last_refresh = time.time() - 999  # TTL expired -> next will re-hit
    recovered = await mon.refresh()  # recovery

    assert set(recovered) == {
        "model=glm-5.2&prov=ollama",
        "model=new-model&prov=ollama",
    }
    assert "model=new-model&prov=ollama" in mon._states
    assert len(fake.calls) == 3


@pytest.mark.asyncio
async def test_refresh_caches_success_for_ttl(monkeypatch, mon):
    """A SUCCESSFUL fetch stamps _last_refresh, so a follow-up within the TTL
    is served from cache (no re-hit). Confirms the cache still works on success."""
    fake = _patch_refresh_client(
        monkeypatch,
        [(200, _catalog("model=glm-5.2&prov=ollama"))],
    )
    await mon.refresh()
    mon._last_refresh = time.time()  # ensure within TTL

    again = await mon.refresh()

    assert set(again) == {"model=glm-5.2&prov=ollama"}
    assert len(fake.calls) == 1  # second call did NOT re-hit the endpoint
