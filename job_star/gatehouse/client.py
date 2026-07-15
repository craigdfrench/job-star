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
        model: The model identifier (e.g., "ollama/glm-5.2").
        system_prompt: Optional system prompt.
        max_tokens: Maximum output tokens.
        temperature: Sampling temperature.
        timeout: HTTP timeout in seconds. Default 300 (5 min) because reasoning
            models generating large code outputs (16k tokens) can take well
            over the previous 120s default, causing ReadTimeout failures.

    Returns:
        ExecutionResult with the AI's response.
    """
    import httpx

    base_url, api_key, _ = _get_config()

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
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

        data = response.json()
        content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        x_gatehouse = usage.get("x_gatehouse", {}) or {}

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
    # Strip /v1 for health check
    health_url = base_url.replace("/v1", "") + "/health"
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(health_url)
            return resp.status_code == 200
    except Exception:
        return False