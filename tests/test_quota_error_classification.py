"""Tests for _is_quota_error classification (Vikunja #1717 follow-up).

_is_quota_error decides whether record_failure() imposes a LONG 3-hour quota
hold in addition to the circuit breaker. Over-broad matching ("timeout",
"404", "500", "401", "403") caused false 3h holds that stranded models from
the liveness-probe recovery path (probe_free_pool skips quota-hold models).

Only genuine quota/rate-limit and upstream-capacity signals should hold;
transient/config errors are left to the circuit breaker + probe.
"""

from job_star.gatehouse.monitor import _is_quota_error


# -------- genuine quota / rate-limit -> True (3h hold) --------
def test_quota_word():
    assert _is_quota_error("insufficient quota on daily pool")
    assert _is_quota_error("exceeded prompt quota for windsurf_daily")


def test_rate_limit_429():
    assert _is_quota_error("too many requests")
    assert _is_quota_error("rate limit exceeded, try again later")
    assert _is_quota_error("HTTP 429: rate limited")


def test_capacity_503_502():
    assert _is_quota_error("HTTP 503: service unavailable")
    assert _is_quota_error("HTTP 502: bad gateway")
    assert _is_quota_error("model not loaded, retry later")


def test_unavailable():
    assert _is_quota_error("upstream unavailable")


# -------- transient / config errors -> False (NO 3h hold) --------
def test_timeout_not_quota():
    assert not _is_quota_error("ReadTimeout: connection timed out")
    assert not _is_quota_error("gateway timeout while streaming")


def test_404_not_quota():
    assert not _is_quota_error("HTTP 404: model not found")

def test_generic_5xx_not_quota():
    assert not _is_quota_error("HTTP 500: internal server error")


def test_auth_not_quota():
    assert not _is_quota_error("HTTP 401: unauthorized")
    assert not _is_quota_error("HTTP 403: forbidden")
    assert not _is_quota_error("unauthorized: missing credential")


def test_empty_content_not_quota():
    assert not _is_quota_error("empty response from model")
    assert not _is_quota_error("non-string content from model: list")
