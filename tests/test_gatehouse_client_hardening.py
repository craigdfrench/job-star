"""Tests for gatehouse client hardening (pre-existing findings from PR #14's review).

The code-review-debate gate on PR #14 surfaced several pre-existing issues in
client.py beyond the empty-content fix. This file pins the cleanup:

  - trailing-slash GATEHOUSE_API_URL no longer produces a double-slash path
  - check_health strips only a *trailing* /v1 (not every "/v1" substring)
  - a 200 with an empty/malformed choices array is a clear failure (no IndexError
    into the broad except)
  - a 200 with a non-JSON body is a clear failure carrying the raw body snippet
  - the JOB_STAR_MODEL default is used when the model argument is empty
  - connection establishment is capped separately from the read timeout
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from job_star.gatehouse.client import _api_url, _health_url, execute


def _make_patched_client(transport: httpx.MockTransport) -> type:
    """An AsyncClient subclass that always uses the given mock transport."""
    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)
    return _PatchedAsyncClient


# ---------------------------------------------------------------------------
# URL construction (pure functions)
# ---------------------------------------------------------------------------

def test_api_url_strips_trailing_slash():
    assert _api_url("http://gw.example/v1") == "http://gw.example/v1/chat/completions"
    assert _api_url("http://gw.example/v1/") == "http://gw.example/v1/chat/completions"


def test_health_url_strips_only_trailing_v1():
    # Trailing /v1 is stripped
    assert _health_url("http://gw.example/v1") == "http://gw.example/health"
    assert _health_url("http://gw.example/v1/") == "http://gw.example/health"
    # A "/v1" substring elsewhere in the path must NOT be touched (the old
    # .replace("/v1", "") corrupted these)
    assert _health_url("http://gw.example/v1proxy") == "http://gw.example/v1proxy/health"
    assert _health_url("https://host/v1api/v1") == "https://host/v1api/health"
    # No /v1 at all: used as-is
    assert _health_url("http://gw.example:8090") == "http://gw.example:8090/health"


# ---------------------------------------------------------------------------
# execute() response-shape hardening
# ---------------------------------------------------------------------------

def _ok_body(content: str = "ok") -> dict:
    return {
        "choices": [{"finish_reason": "stop", "index": 0,
                     "message": {"content": content}}],
        "model": "model=glm-5.2&prov=ollama",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }


@pytest.mark.asyncio
async def test_execute_no_double_slash_with_trailing_slash_base(monkeypatch):
    """A trailing slash in GATEHOUSE_API_URL must not produce //chat/completions."""
    monkeypatch.setenv("GATEHOUSE_API_URL", "http://gateway.example/v1/")
    monkeypatch.setenv("GATEHOUSE_API_KEY", "test-key")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json=_ok_body())

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", _make_patched_client(transport))

    result = await execute(prompt="hi", model="model=glm-5.2&prov=ollama", max_tokens=10)

    assert result.success
    assert captured["url"] == "http://gateway.example/v1/chat/completions"
    assert "//chat" not in captured["url"]


@pytest.mark.asyncio
async def test_execute_empty_choices_200_is_clear_failure(monkeypatch):
    """A 200 with an empty choices array fails clearly (no opaque IndexError)."""
    monkeypatch.setenv("GATEHOUSE_API_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEHOUSE_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [], "model": "x", "usage": {}})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", _make_patched_client(transport))

    result = await execute(prompt="hi", model="model=glm-5.2&prov=ollama", max_tokens=10)

    assert not result.success
    assert "choices" in (result.error or "")
    assert "IndexError" not in (result.error or "")


@pytest.mark.asyncio
async def test_execute_non_dict_choice_200_is_clear_failure(monkeypatch):
    """A 200 with a non-dict choices[0] fails clearly (no AttributeError)."""
    monkeypatch.setenv("GATEHOUSE_API_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEHOUSE_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": ["not-a-dict"], "model": "x", "usage": {}})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", _make_patched_client(transport))

    result = await execute(prompt="hi", model="model=glm-5.2&prov=ollama", max_tokens=10)

    assert not result.success
    assert "choices" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_non_json_200_carries_body_snippet(monkeypatch):
    """A 200 with a non-JSON body fails clearly, including the raw body text.

    Previously the JSONDecodeError fell into the generic except and discarded
    the response body needed for diagnosis (e.g. a proxy HTML error page).
    """
    monkeypatch.setenv("GATEHOUSE_API_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEHOUSE_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>502 Bad Gateway proxy page</html>",
                              headers={"Content-Type": "text/html"})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", _make_patched_client(transport))

    result = await execute(prompt="hi", model="model=glm-5.2&prov=ollama", max_tokens=10)

    assert not result.success
    assert "non-JSON" in (result.error or "")
    assert "proxy page" in (result.error or "")  # raw body preserved


@pytest.mark.asyncio
async def test_execute_empty_model_falls_back_to_default(monkeypatch):
    """An empty model argument uses the JOB_STAR_MODEL default (previously dead config)."""
    monkeypatch.setenv("GATEHOUSE_API_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEHOUSE_API_KEY", "test-key")
    monkeypatch.setenv("JOB_STAR_MODEL", "model=default-model&prov=ollama")
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json
        captured["body"] = _json.loads(request.content.decode() or "{}")
        return httpx.Response(200, json=_ok_body())

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", _make_patched_client(transport))

    result = await execute(prompt="hi", model="", max_tokens=10)

    assert result.success
    assert captured["body"]["model"] == "model=default-model&prov=ollama"


@pytest.mark.asyncio
async def test_execute_connect_timeout_capped(monkeypatch):
    """The client is constructed with a bounded connect timeout, not the bare float.

    A bare-float timeout applies the (300s) generation timeout to connection
    establishment too, so a dead host hangs for 5 minutes. The client must use
    httpx.Timeout(timeout, connect=...).
    """
    monkeypatch.setenv("GATEHOUSE_API_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEHOUSE_API_KEY", "test-key")
    captured: dict[str, Any] = {}

    class _CapturingClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            captured["timeout"] = kwargs.get("timeout")
            super().__init__(*args, **kwargs)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_ok_body())

    transport = httpx.MockTransport(handler)

    class _Patched(_CapturingClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _Patched)

    result = await execute(prompt="hi", model="model=glm-5.2&prov=ollama",
                           max_tokens=10, timeout=300.0)

    assert result.success
    t = captured["timeout"]
    assert isinstance(t, httpx.Timeout), f"expected httpx.Timeout, got {type(t)}"
    assert t.connect == 15.0
    assert t.read == 300.0


# ---------------------------------------------------------------------------
# Malformed-but-valid-JSON 200 hardening (usage: null, non-dict root/message,
# null token fields) -- the adjudicator's most_important_fix follow-up.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_null_usage_200_no_crash(monkeypatch):
    """A 200 with explicit "usage": null must not raise AttributeError.

    data.get("usage", {}) only substitutes the default when the key is ABSENT;
    an explicit null previously passed through and usage.get(...) raised into
    the broad except as an opaque failure.
    """
    monkeypatch.setenv("GATEHOUSE_API_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEHOUSE_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "index": 0,
                         "message": {"content": "ok"}}],
            "model": "model=glm-5.2&prov=ollama",
            "usage": None,
        })

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", _make_patched_client(transport))

    result = await execute(prompt="hi", model="model=glm-5.2&prov=ollama", max_tokens=10)

    assert result.success
    assert result.input_tokens == 0
    assert result.output_tokens == 0
    assert result.x_gatehouse == {}


@pytest.mark.asyncio
async def test_execute_non_dict_root_200_is_clear_failure(monkeypatch):
    """A 200 whose JSON root is not an object (e.g. a list) fails clearly."""
    monkeypatch.setenv("GATEHOUSE_API_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEHOUSE_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", _make_patched_client(transport))

    result = await execute(prompt="hi", model="model=glm-5.2&prov=ollama", max_tokens=10)

    assert not result.success
    assert "non-object" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_non_dict_message_200_is_empty_failure(monkeypatch):
    """A 200 with a non-dict message yields empty content -> empty-response
    failure (not an AttributeError into the broad except)."""
    monkeypatch.setenv("GATEHOUSE_API_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEHOUSE_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "index": 0,
                         "message": "not-a-dict"}],
            "model": "model=glm-5.2&prov=ollama",
            "usage": {},
        })

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", _make_patched_client(transport))

    result = await execute(prompt="hi", model="model=glm-5.2&prov=ollama", max_tokens=10)

    assert not result.success
    assert "empty" in (result.error or "")


@pytest.mark.asyncio
async def test_execute_null_token_fields_coerce_to_zero(monkeypatch):
    """Explicitly-null prompt/completion tokens coerce to 0, not None."""
    monkeypatch.setenv("GATEHOUSE_API_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEHOUSE_API_KEY", "test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "index": 0,
                         "message": {"content": "ok"}}],
            "model": "model=glm-5.2&prov=ollama",
            "usage": {"prompt_tokens": None, "completion_tokens": None},
        })

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(httpx, "AsyncClient", _make_patched_client(transport))

    result = await execute(prompt="hi", model="model=glm-5.2&prov=ollama", max_tokens=10)

    assert result.success
    assert result.input_tokens == 0
    assert result.output_tokens == 0


def test_get_config_empty_string_env_falls_back_to_default(monkeypatch):
    """An explicitly-empty env var falls back to the default (not "").

    os.environ.get(k, default) only substitutes when the var is UNSET; an
    empty-string override previously won through as "".
    """
    from job_star.gatehouse.client import _get_config
    monkeypatch.setenv("GATEHOUSE_API_URL", "")
    monkeypatch.setenv("JOB_STAR_MODEL", "")
    base_url, _, default_model = _get_config()
    assert base_url, "empty env must fall back to the default URL"
    assert default_model, "empty env must fall back to the default model"
    assert base_url == "http://100.64.158.87:8090/v1"
