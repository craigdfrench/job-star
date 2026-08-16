"""Tests for the #1717 item-1 monitor.py review findings.

Covers the pre-existing review findings deferred out of PR #16/inc to #1717:

  1. ``_apply_x_gatehouse`` holds an exhausted-quota model until the LATEST of
     its exhausted pools' resets, not the upcoming/soonest one (a model with
     several exhausted pools is unusable until ALL of them clear).
  2. Observed ``cost_class`` (and ``routing_advice``/``reason``/``retail_value``)
     are only overwritten when an x_gatehouse payload actually reports them, so a
     request that omits an attribute doesn't drop the last-known-good value.
     ``retail_value`` parsing is guarded against non-numeric values.
  3. ``time_until_available`` accounts for circuit-open / stale-quota (returns a
     positive retry backoff instead of falsely claiming "available now").
  4. ``cost_kind`` / ``_get_cost_kind_from_config`` do provider-normalized
     lookups so spec-form IDs match config entries keyed by the bare name.
  5. ``pick_fallback`` tolerates a null ``context_length`` on a candidate.
"""

from __future__ import annotations

import datetime
import time

import pytest

from job_star.gatehouse.monitor import (
    CostKind,
    DEFAULT_UNKNOWN_WAIT_SECONDS,
    GatewayMonitor,
    QuotaWindow,
)


# --------------------------------------------------------------------------- #
# Fix 1: _apply_x_gatehouse holds for the LATEST reset of exhausted pools
# --------------------------------------------------------------------------- #


def test_exhausted_quota_holds_until_latest_reset():
    now = datetime.datetime.now(datetime.timezone.utc)
    daily_reset = now + datetime.timedelta(hours=2)   # the soonest reset
    weekly_reset = now + datetime.timedelta(days=3)   # the latest reset
    xg = {
        "cost_class": "included_quota",
        "quota_windows": [
            {"pool_id": "daily", "dimension": "dollars", "window": "daily",
             "limit": 10, "used": 10, "remaining": 0, "remaining_pct": 0.0,
             "resets_at": daily_reset.isoformat(), "hours_until_reset": 2},
            {"pool_id": "weekly", "dimension": "dollars", "window": "weekly",
             "limit": 70, "used": 70, "remaining": 0, "remaining_pct": 0.0,
             "resets_at": weekly_reset.isoformat(), "hours_until_reset": 72},
        ],
    }
    mon = GatewayMonitor()
    mon.record_success("model=m", x_gatehouse=xg)
    s = mon.state("model=m")

    soonest_wait = (daily_reset - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
    latest_wait = (weekly_reset - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
    observed_wait = s.quota_hold_until - time.time()

    # Must NOT have held only until the soonest reset.
    assert observed_wait > soonest_wait
    # Must hold until the latest reset (tolerance for the time between the
    # assert and the hold computation inside _apply_x_gatehouse).
    assert abs(observed_wait - latest_wait) < 5


# --------------------------------------------------------------------------- #
# Fix 2: observed cost_class / the rest preserved + guarded retail_value
# --------------------------------------------------------------------------- #


def test_cost_class_preserved_when_payload_omits_it():
    mon = GatewayMonitor()
    mon.record_success("model=m", x_gatehouse={"cost_class": "retail"})
    assert mon.state("model=m").observed_cost_class == "retail"
    # A later payload that omits cost_class must NOT wipe the last-known-good.
    mon.record_success("model=m", x_gatehouse={"quota_windows": []})
    assert mon.state("model=m").observed_cost_class == "retail"


def test_retail_value_guarded_against_non_numeric():
    mon = GatewayMonitor()
    xg = {"cost_class": "retail", "retail_value_this_request": "not-a-number"}
    mon.record_success("model=m", x_gatehouse=xg)  # must not raise
    assert mon.state("model=m").observed_retail_value == 0.0


def test_retail_value_preserved_when_omitted():
    mon = GatewayMonitor()
    mon.record_success("model=m", x_gatehouse={"retail_value_this_request": 3.5})
    assert mon.state("model=m").observed_retail_value == 3.5
    mon.record_success("model=m", x_gatehouse={"quota_windows": []})
    assert mon.state("model=m").observed_retail_value == 3.5


# --------------------------------------------------------------------------- #
# Fix 3: time_until_available accounts for circuit-open / stale-quota
# --------------------------------------------------------------------------- #


def test_time_until_available_zero_when_available():
    mon = GatewayMonitor()
    mon.record_success("model=m")
    assert mon.time_until_available("model=m") == 0.0


def test_time_until_available_quota_hold_returns_remaining():
    mon = GatewayMonitor()
    mon.record_failure("model=m", "quota exceeded")  # genuine quota -> hold
    assert mon.is_available("model=m") is False
    wait = mon.time_until_available("model=m")
    assert 0 < wait <= mon.quota_hold_seconds


@pytest.mark.parametrize("err", ["HTTP 500", "model not found 404", "connection reset by peer"])
def test_time_until_available_circuit_open_returns_backoff(err):
    # These are NOT _is_quota_error, so no quota hold is entered — the model is
    # unavailable only because the circuit opened.
    mon = GatewayMonitor()
    for _ in range(mon.failure_threshold):
        mon.record_failure("model=m", err)
    assert mon.is_available("model=m") is False
    assert mon.state("model=m").is_in_quota_hold is False
    # Must NOT claim "available now" (0.0) for a circuit-open model; report the
    # positive unknown-wait backoff instead.
    assert mon.time_until_available("model=m") == DEFAULT_UNKNOWN_WAIT_SECONDS


def test_time_until_available_exhausted_window_uses_reset():
    mon = GatewayMonitor()
    s = mon.state("model=m")
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=10)
    s.observed_quota_windows = [
        QuotaWindow(pool_id="p", dimension="dollars", window="daily",
                    limit=10, used=10, remaining=0, remaining_pct=0.0,
                    resets_at=future.isoformat(), hours_until_reset=None),
    ]
    # Not in quota hold, but the exhausted window makes it unavailable.
    assert s.is_in_quota_hold is False
    assert mon.is_available("model=m") is False
    wait = mon.time_until_available("model=m")
    assert 0 < wait < 600  # tracks the future reset, not 0 and not the backoff


# --------------------------------------------------------------------------- #
# Fix 4: cost_kind provider-normalized lookups
# --------------------------------------------------------------------------- #


def test_cost_kind_normalizes_model_spec_lookup(monkeypatch):
    import job_star.gatehouse.monitor as mm
    monkeypatch.setattr(mm, "_load_gatehouse_config", lambda: {})
    monkeypatch.setattr(mm, "_gatehouse_cost_cache", {"glm-5.2": CostKind.QUOTA_BEARING})

    mon = GatewayMonitor()
    # Spec-form ID normalizes to bare "glm-5.2" -> matches the config entry.
    assert mon.cost_kind("model=glm-5.2&prov=ollama") == CostKind.QUOTA_BEARING
    # Legacy prov/name form too.
    assert mon.cost_kind("ollama/glm-5.2") == CostKind.QUOTA_BEARING
    # Unrelated model falls through to UNKNOWN (not a crash / wrong match).
    assert mon.cost_kind("something-else") == CostKind.UNKNOWN


def test_is_quota_bearing_uses_normalized_lookup(monkeypatch):
    import job_star.gatehouse.monitor as mm
    monkeypatch.setattr(mm, "_load_gatehouse_config", lambda: {})
    monkeypatch.setattr(mm, "_gatehouse_cost_cache", {"ollama/gemma3:4b": CostKind.PROMOTIONAL_FREE})

    mon = GatewayMonitor()
    assert mon.is_quota_bearing("model=gemma3:4b&prov=ollama") is True
    assert mon.is_included_unlimited("model=gemma3:4b&prov=ollama") is False


# --------------------------------------------------------------------------- #
# Fix 5: pick_fallback tolerates a null context_length
# --------------------------------------------------------------------------- #


def test_pick_fallback_null_context_length():
    mon = GatewayMonitor()
    mon._gateway_models = {
        "preferred": {"capabilities": {"text": True}, "context_length": 100000},
        # null context_length — must not raise in the scoring below.
        "gemini-3-5-flash-low": {"capabilities": {"text": True}, "context_length": None},
    }
    assert mon.pick_fallback("preferred") == "gemini-3-5-flash-low"
