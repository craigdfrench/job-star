"""Tests for the gatehouse client's X-Gatehouse-No-Rescue opt-out.

job-star must bypass the gateway's rescue layer so transient upstream
failures (a 404/503) surface as real failures and the router's retry/fallback
can recover. Without the header the rescue layer returns 200 + a
session-continuity message, which execute() treats as success — poisoning
planning/execution with rescue garbage and defeating the retry loop.
(gatehouse-ai PR #104 added the opt-out; this test pins that job-star sends it.)
"""

from typing import Any

import httpx
import pytest

from job_star.gatehouse.client import execute


def _make_patched_client(transport: httpx.MockTransport) -> type:
    """An AsyncClient subclass that always uses the given mock transport."""
    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)
    return _PatchedAsyncClient


def _capture_handler(captured: dict[str, Any], *, status: int = 200, body: dict | None = None):
    body = body or {
        "choices": [{"finish_reason": "stop", "index": 0,
                     "message": {"content": "ok"}}],
        "model": "model=glm-5.2&prov=ollama",
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["url"] = str(request.url)
        import json as _json
        captured["body"] = _json.loads(request.content.decode() or "{}")
        return httpx.Response(status, json=body)

    return handler


def _header(headers: dict, name: str) -> str | None:
    for k, v in headers.items():
        if k.lower() == name:
            return v
    return None


@pytest.mark.asyncio
async def test_execute_sends_no_rescue_header(monkeypatch):
    """execute() must send X-Gatehouse-No-Rescue: true on every AI call."""
    monkeypatch.setenv("GATEHOUSE_API_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEHOUSE_API_KEY", "test-key")
    captured: dict[str, Any] = {}
    transport = httpx.MockTransport(_capture_handler(captured))
    monkeypatch.setattr(httpx, "AsyncClient", _make_patched_client(transport))

    result = await execute(prompt="hi", model="model=glm-5.2&prov=ollama", max_tokens=10)

    assert result.success, f"expected success, got {result.error!r}"
    assert _header(captured["headers"], "x-gatehouse-no-rescue") == "true"
    assert _header(captured["headers"], "authorization") == "Bearer test-key"
    assert "/chat/completions" in captured["url"]


@pytest.mark.asyncio
async def test_execute_surfaces_upstream_failure_without_rescue(monkeypatch):
    """With No-Rescue, a 503 upstream surfaces as success=False (not 200+rescue).

    This is the load-bearing behavior: the router's retry loop only fires when
    execute() returns success=False. If the rescue layer masked the 503 as a
    200, execute() would return success=True with garbage content and the
    retry loop would never run.
    """
    monkeypatch.setenv("GATEHOUSE_API_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEHOUSE_API_KEY", "test-key")
    captured: dict[str, Any] = {}
    transport = httpx.MockTransport(_capture_handler(captured, status=503, body={"error": "provider 503"}))
    monkeypatch.setattr(httpx, "AsyncClient", _make_patched_client(transport))

    result = await execute(prompt="hi", model="model=deepseek-v4-flash-0731&prov=nvidia", max_tokens=10)

    assert not result.success
    assert "503" in (result.error or "")
    assert _header(captured["headers"], "x-gatehouse-no-rescue") == "true"


@pytest.mark.asyncio
async def test_execute_treats_empty_content_200_as_failure(monkeypatch):
    """A 200 with empty/whitespace content must surface as success=False.

    Some providers return 200 with zero output tokens (notably
    `model=glm-5.2&prov=cline` returns empty for glm-5.2). Without this guard
    execute() reports success=True with empty content, the router's
    retry/fallback never rotates, and the planner dies at `_parse_plan_output`
    with `output: ""`. Treating empty 200 as failure lets the retry loop recover.
    """
    monkeypatch.setenv("GATEHOUSE_API_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEHOUSE_API_KEY", "test-key")
    captured: dict[str, Any] = {}
    body = {
        "choices": [{"finish_reason": "stop", "index": 0,
                     "message": {"content": ""}}],
        "model": "model=glm-5.2&prov=cline",
        "usage": {"prompt_tokens": 5, "completion_tokens": 0},
    }
    transport = httpx.MockTransport(_capture_handler(captured, body=body))
    monkeypatch.setattr(httpx, "AsyncClient", _make_patched_client(transport))

    result = await execute(prompt="hi", model="model=glm-5.2&prov=cline", max_tokens=10)

    assert not result.success
    assert "empty" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_execute_treats_whitespace_content_200_as_failure(monkeypatch):
    """A 200 with only-whitespace content must also surface as success=False."""
    monkeypatch.setenv("GATEHOUSE_API_URL", "http://gateway.example/v1")
    monkeypatch.setenv("GATEHOUSE_API_KEY", "test-key")
    captured: dict[str, Any] = {}
    body = {
        "choices": [{"finish_reason": "stop", "index": 0,
                     "message": {"content": "   \n\t  "}}],
        "model": "model=glm-5.2&prov=cline",
        "usage": {"prompt_tokens": 5, "completion_tokens": 0},
    }
    transport = httpx.MockTransport(_capture_handler(captured, body=body))
    monkeypatch.setattr(httpx, "AsyncClient", _make_patched_client(transport))

    result = await execute(prompt="hi", model="model=glm-5.2&prov=cline", max_tokens=10)

    assert not result.success
    assert "empty" in (result.error or "").lower()