"""Gatehouse AI client — executes AI calls through the gatehouse gateway.

This is the execution layer. It talks to the gatehouse-ai HTTP API
(OpenAI-compatible) to actually run AI models.
"""

from __future__ import annotations

import os
from typing import Optional

from ..models import ExecutionResult


def _get_config() -> tuple[str, str, str]:
    """Get gateway URL, API key, and default model from environment."""
    base_url = os.environ.get("GATEHOUSE_API_URL", "http://100.64.158.87:8090/v1")
    api_key = os.environ.get("GATEHOUSE_API_KEY", "no-key-needed")
    default_model = os.environ.get("JOB_STAR_MODEL", "ollama/glm-5.2")
    return base_url, api_key, default_model


def _api_url(base_url: str) -> str:
    """Build the chat-completions URL, tolerating a trailing slash in the base.

    A trailing slash in GATEHOUSE_API_URL previously produced a double-slash
    path (".../v1//chat/completions") which some gateways reject.
    """
    return f"{base_url.rstrip('/')}/chat/completions"


def _health_url(base_url: str) -> str:
    """Build the health URL, stripping only a *trailing* /v1 suffix.

    The previous `base_url.replace("/v1", "")` was a global substring replace
    that corrupted any URL containing "/v1" elsewhere (e.g.
    "https://host/v1proxy/v1" -> "https://hostproxy"). Suffix-anchored strip
    only removes the API version segment.
    """
    base = base_url.rstrip("/")
    if base.endswith("/v1"):
        base = base[: -len("/v1")]
    return f"{base}/health"


async def execute(
    prompt: str,
    model: str,
    system_prompt: str | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    timeout: float = 300.0,
) -> ExecutionResult:
    """Execute an AI call through the gatehouse gateway.

    Args:
        prompt: The user prompt to send.
        model: The model identifier (e.g., "ollama/glm-5.2"). Falls back to the
            JOB_STAR_MODEL env default when empty/None.
        system_prompt: Optional system prompt.
        max_tokens: Maximum output tokens.
        temperature: Sampling temperature.
        timeout: HTTP read/write timeout in seconds. Default 300 (5 min) because
            reasoning models generating large code outputs (16k tokens) can take
            well over the previous 120s default, causing ReadTimeout failures.
            Connection establishment is capped separately (15s) so a dead host
            fails fast instead of hanging for the full read timeout.

    Returns:
        ExecutionResult with the AI's response.
    """
    import httpx

    base_url, api_key, default_model = _get_config()
    # Make the documented JOB_STAR_MODEL default effective: an empty/None model
    # previously passed through as-is (the config value was computed then
    # discarded by its only caller).
    model = model or default_model

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=15.0)
        ) as client:
            response = await client.post(
                _api_url(base_url),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    # Bypass the gateway's rescue layer. job-star needs the
                    # real upstream result (a transient 404/503 stays a failure)
                    # so the router's retry/fallback recovers from transient
                    # provider outages (e.g. nvidia 404s that come and go).
                    # Without this the rescue layer returns 200 + a
                    # session-continuity message, which execute() treats as
                    # success -- poisoning planning/execution with rescue
                    # garbage and defeating the retry loop. (#104 opt-out.)
                    "X-Gatehouse-No-Rescue": "true",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
            )

        if response.status_code != 200:
            return ExecutionResult(
                success=False,
                error=f"HTTP {response.status_code}: {response.text[:500]}",
                model=model,
            )

        try:
            data = response.json()
        except ValueError as e:
            # A 200 with a non-JSON body (e.g. a proxy HTML error page)
            # previously raised into the generic except, discarding the raw
            # body needed for diagnosis.
            return ExecutionResult(
                success=False,
                error=f"non-JSON 200 response ({type(e).__name__}): {response.text[:200]}",
                model=model,
            )

        # Guard the choices indexing: a spec-legal 200 can carry an empty
        # choices list (error/filter conditions), which previously raised
        # IndexError into the broad except as an opaque failure.
        choices = data.get("choices") or []
        if not choices or not isinstance(choices[0], dict):
            return ExecutionResult(
                success=False,
                error="empty or malformed choices array in 200 response",
                model=model,
            )
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        usage = data.get("usage", {})
        x_gatehouse = usage.get("x_gatehouse", {}) or {}

        # Treat an empty/whitespace 200 as a failure. Some providers return
        # 200 with zero output tokens (notably `model=glm-5.2&prov=cline`
        # returns empty for glm-5.2) -- a false-advertised / confounded-upstream
        # variant. Without this, execute() reports success and the router's
        # retry/fallback never rotates to a model that actually produces
        # output, so the planner dies at `_parse_plan_output` with empty
        # content. Returning success=False lets the retry loop recover.
        #
        # Guard the strip: OpenAI-compatible responses can carry non-string
        # content (null for tool_calls, or a list of structured parts). Stripping
        # those would raise AttributeError and fall into the broad except as an
        # opaque generic failure. job-star's text-generation calls never produce
        # those shapes, but treat non-string content as an explicit failure
        # rather than crashing.
        if not isinstance(content, str):
            return ExecutionResult(
                success=False,
                error=f"non-string content from model: {type(content).__name__}",
                model=data.get("model", model),
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                x_gatehouse=x_gatehouse,
            )
        if not content.strip():
            return ExecutionResult(
                success=False,
                error="empty response from model",
                model=data.get("model", model),
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
                x_gatehouse=x_gatehouse,
            )

        return ExecutionResult(
            content=content,
            model=data.get("model", model),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            success=True,
            x_gatehouse=x_gatehouse,
        )

    except Exception as e:
        return ExecutionResult(
            success=False,
            error=f"{type(e).__name__}: {e}" if str(e) else type(e).__name__,
            model=model,
        )


async def check_health() -> bool:
    """Check if the gatehouse gateway is reachable."""
    import httpx

    base_url, _, _ = _get_config()
    health_url = _health_url(base_url)
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(health_url)
            return resp.status_code == 200
    except Exception:
        return False
