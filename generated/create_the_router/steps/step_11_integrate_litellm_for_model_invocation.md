# Job-Star Router: LiteLLM Executor Integration

## What I'm Building

The executor module wraps LiteLLM's `completion()` / `acompletion()` so the router can actually execute requests against the selected model. Key design decisions:

1. **Thin abstraction** — Callers pass an `ExecutionRequest` and get an `ExecutionResponse`. They never touch LiteLLM directly or know which provider was chosen.
2. **Environment-based API keys** — LiteLLM reads provider keys from env vars (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `AZURE_API_KEY`, etc.). We validate that the required key exists before invoking.
3. **Sync + async** — `run()` for blocking calls, `run_async()` for async workflows.
4. **Structured responses** — Usage stats, latency, model used, and raw response are captured for cost tracking and observability.
5. **Error normalization** — LiteLLM raises various provider-specific exceptions; we normalize them into a clear `ExecutionError`.

## File: `job_star/router/executor.py`

```python
"""
LiteLLM-backed executor for the Job-Star router.

This module provides a thin abstraction over LiteLLM's completion() and
acompletion() functions. Callers pass an ExecutionRequest and receive an
ExecutionResponse — they never need to know which provider or model was
selected by the router.

Provider API keys are read from environment variables as expected by LiteLLM:
  - OpenAI:      OPENAI_API_KEY
  - Anthropic:   ANTHROPIC_API_KEY
  - Azure:       AZURE_API_KEY, AZURE_API_BASE, AZURE_API_VERSION
  - Google:      GEMINI_API_KEY or GOOGLE_API_KEY
  - Cohere:      COHERE_API_KEY
  - AWS Bedrock: AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_REGION_NAME
  - Groq:        GROQ_API_KEY
  - Mistral:     MISTRAL_API_KEY
  - Together:    TOGETHERAI_API_KEY
  - OpenRouter:  OPENROUTER_API_KEY

See LiteLLM docs for the full list and naming conventions:
https://docs.litellm.ai/docs/providers
"""

from __future__ import annotations

import os
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Optional, Union, Dict, List

try:
    import litellm
except ImportError as exc:  # pragma: no cover - import guard
    raise ImportError(
        "LiteLLM is required for the executor. Install it with: pip install litellm"
    ) from exc

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ExecutionRequest:
    """
    A request to execute a completion against a specific model.

    Either `prompt` (str) or `messages` (list of message dicts) must be
    provided. If both are given, `messages` takes precedence.

    Attributes:
        model: LiteLLM model string, e.g. "gpt-4o", "claude-3-5-sonnet-20240620",
               "groq/llama-3.1-70b-versatile". See LiteLLM model naming docs.
        prompt: A simple string prompt. Converted to a single user message.
        messages: A list of message dicts with "role" and "content" keys.
        temperature: Sampling temperature. Defaults to 0.7.
        max_tokens: Maximum tokens to generate. None means provider default.
        top_p: Nucleus sampling parameter. None means provider default.
        stop: Stop sequences. None means no stop sequences.
        stream: If True, returns a streaming response. The executor will raise
                if streaming is requested but not yet supported by the caller's
                code path (see notes in run()).
        timeout: Request timeout in seconds. None means LiteLLM default.
        num_retries: Number of retries on transient failures. LiteLLM handles
                     this internally; we pass it through.
        metadata: Arbitrary metadata to attach (e.g. request_id, task_type).
                  Passed through to LiteLLM and included in the response.
        extra_params: Additional LiteLLM / provider-specific parameters passed
                      directly to completion(). Use for things like
                      response_format, tools, tool_choice, etc.
    """

    model: str
    prompt: Optional[str] = None
    messages: Optional[List[Dict[str, Any]]] = None
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    stop: Optional[Union[str, List[str]]] = None
    stream: bool = False
    timeout: Optional[float] = None
    num_retries: Optional[int] = None
    metadata: Optional[Dict[str, Any]] = None
    extra_params: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.prompt is None and self.messages is None:
            raise ValueError(
                "ExecutionRequest requires either 'prompt' or 'messages'."
            )
        if self.messages is None and self.prompt is not None:
            self.messages = [{"role": "user", "content": self.prompt}]

    def to_litellm_kwargs(self) -> Dict[str, Any]:
        """Build the kwargs dict for litellm.completion / acompletion."""
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
        }
        # Only include optional params if they're set (avoid overriding
        # LiteLLM / provider defaults with None).
        optional = {
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            "stop": self.stop,
            "stream": self.stream,
            "timeout": self.timeout,
            "num_retries": self.num_retries,
            "metadata": self.metadata,
        }
        for key, value in optional.items():
            if value is not None:
                kwargs[key] = value
        # Merge in any extra provider-specific params.
        kwargs.update(self.extra_params)
        return kwargs


@dataclass
class ExecutionResponse:
    """
    The result of executing a completion request.

    Attributes:
        content: The generated text content.
        model: The model string that was actually used (may differ from the
               request if LiteLLM resolved an alias).
        provider: The provider name, extracted from the model string or
                 response. Useful for logging and cost tracking.
        usage: Token usage dict, typically with 'prompt_tokens',
               'completion_tokens', and 'total_tokens'.
        finish_reason: Why generation stopped (e.g. 'stop', 'length').
        latency_ms: Wall-clock time for the request, in milliseconds.
        raw_response: The raw LiteLLM ModelResponse object, for advanced use.
        metadata: The metadata that was attached to the request, if any.
        extra: Additional provider-specific response fields (e.g. tool calls).
    """

    content: str
    model: str
    provider: str
    usage: Dict[str, Any]
    finish_reason: Optional[str]
    latency_ms: float
    raw_response: Any
    metadata: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class ExecutionError(Exception):
    """Raised when a model invocation fails. Wraps the underlying error."""

    def __init__(self, message: str, *, model: Optional[str] = None,
                 provider: Optional[str] = None, cause: Optional[Exception] = None):
        super().__init__(message)
        self.model = model
        self.provider = provider
        self.cause = cause


# ---------------------------------------------------------------------------
# Provider key management
# ---------------------------------------------------------------------------

# Mapping of provider prefix (as used in LiteLLM model strings) to the
# environment variable(s) that must be set for that provider. A model string
# like "groq/llama-3.1-70b-versatile" has provider prefix "groq". For models
# without a prefix (e.g. "gpt-4o"), we infer the provider from the model name.
_PROVIDER_KEY_ENVVARS: Dict[str, List[str]] = {
    "openai": ["OPENAI_API_KEY"],
    "azure": ["AZURE_API_KEY", "AZURE_API_BASE", "AZURE_API_VERSION"],
    "anthropic": ["ANTHROPIC_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "google": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "cohere": ["COHERE_API_KEY"],
    "bedrock": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION_NAME"],
    "groq": ["GROQ_API_KEY"],
    "mistral": ["MISTRAL_API_KEY"],
    "together_ai": ["TOGETHERAI_API_KEY"],
    "together": ["TOGETHERAI_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY"],
    "huggingface": ["HUGGINGFACE_API_KEY"],
    "ai21": ["AI21_API_KEY"],
    "perplexity": ["PERPLEXITYAI_API_KEY"],
    "anyscale": ["ANYSCALE_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "fireworks_ai": ["FIREWORKS_API_KEY"],
    "fireworks": ["FIREWORKS_API_KEY"],
    "vertex_ai": ["GOOGLE_APPLICATION_CREDENTIALS", "VERTEX_PROJECT",
                  "VERTEX_LOCATION"],
}

# Models that don't use a "provider/" prefix in LiteLLM but still need keys.
# Maps model name prefix to provider key.
_IMPLICIT_PROVIDER_PREFIXES: Dict[str, str] = {
    "gpt-": "openai",
    "o1-": "openai",
    "o3-": "openai",
    "text-embedding-": "openai",
    "claude-": "anthropic",
    "gemini-": "gemini",
    "command-": "cohere",
    "mixtral-": "mistral",
    "mistral-": "mistral",
    "deepseek-": "deepseek",
}


def _extract_provider(model: str) -> str:
    """
    Extract the provider name from a LiteLLM model string.

    Examples:
        "gpt-4o"                        -> "openai"
        "groq/llama-3.1-70b-versatile"   -> "groq"
        "anthropic/claude-3-5-sonnet..." -> "anthropic"
        "azure/gpt-4o"                  -> "azure"
        "claude-3-5-sonnet-20240620"    -> "anthropic"
    """
    if "/" in model:
        provider = model.split("/", 1)[0].lower()
        return provider
    # No prefix — try to infer from the model name.
    for prefix, provider in _IMPLICIT_PROVIDER_PREFIXES.items():
        if model.lower().startswith(prefix):
            return provider
    # Fallback: LiteLLM defaults to OpenAI for unprefixed models.
    return "openai"


def _check_provider_keys(model: str) -> Dict[str, str]:
    """
    Verify that the required environment variables for the model's provider
    are set. Returns a dict of env var name -> value for the keys that are
    present. Raises ExecutionError if a required key is missing.

    For providers with multiple possible key env vars (e.g. Gemini supports
    both GEMINI_API_KEY and GOOGLE_API_KEY), only one needs to be set.
    """
    provider = _extract_provider(model)
    required_vars = _PROVIDER_KEY_ENVVARS.get(provider, [])

    if not required_vars:
        # Unknown provider — let LiteLLM handle it. It may use a default
        # or raise its own error.
        logger.debug("No known env var requirements for provider '%s'", provider)
        return {}

    present: Dict[str, str] = {}
    missing: List[str] = []
    for var in required_vars:
        value = os.environ.get(var)
        if value:
            present[var] = value
        else:
            missing.append(var)

    # For providers where any one of several keys suffices, we treat the
    # whole set as satisfied if at least one is present. We detect this by
    # checking if all required vars share a common "key group". For
    # simplicity, providers like gemini/google list alternatives and we
    # accept if at least one is set.
    if provider in ("gemini", "google"):
        if present:
            return present
        raise ExecutionError(
            f"Missing API key for provider '{provider}'. Set one of: "
            f"{', '.join(required_vars)}",
            model=model,
            provider=provider,
        )

    if provider == "bedrock":
        # Bedrock needs all three (access key, secret key, region).
        if len(missing) > 0:
            raise ExecutionError(
                f"Missing AWS Bedrock credentials for provider '{provider}': "
                f"{', '.join(missing)}",
                model=model,
                provider=provider,
            )
        return present

    if provider == "vertex_ai":
        # Vertex needs credentials file + project + location.
        if len(missing) > 0:
            raise ExecutionError(
                f"Missing Vertex AI configuration for provider '{provider}': "
                f"{', '.join(missing)}",
                model=model,
                provider=provider,
            )
        return present

    # Default: all required vars must be present.
    if missing:
        raise ExecutionError(
            f"Missing API key(s) for provider '{provider}': {', '.join(missing)}. "
            f"Set the environment variable(s) and try again.",
            model=model,
            provider=provider,
        )
    return present


# ---------------------------------------------------------------------------
# Response extraction
# ---------------------------------------------------------------------------

def _extract_response(raw: Any, request: ExecutionRequest,
                      latency_ms: float) -> ExecutionResponse:
    """
    Convert a LiteLLM ModelResponse into an ExecutionResponse.

    LiteLLM responses follow the OpenAI shape:
        raw.choices[0].message.content
        raw.model
        raw.usage
        raw.choices[0].finish_reason
    """
    provider = _extract_provider(request.model)
    model_used = getattr(raw, "model", request.model)

    # Content extraction — handle both dict-style and attribute-style access.
    content = ""
    finish_reason = None
    try:
        choices = getattr(raw, "choices", None) or raw.get("choices", [])
        if choices:
            choice = choices[0]
            message = getattr(choice, "message", None) or choice.get("message", {})
            content = getattr(message, "content", None) or message.get("content", "") or ""
            finish_reason = getattr(choice, "finish_reason", None) or choice.get("finish_reason")
    except Exception as exc:
        logger.warning("Failed to extract content from response: %s", exc)

    # Usage extraction.
    usage: Dict[str, Any] = {}
    try:
        raw_usage = getattr(raw, "usage", None) or raw.get("usage", {})
        if raw_usage:
            usage = dict(raw_usage) if isinstance(raw_usage, dict) else {
                "prompt_tokens": getattr(raw_usage, "prompt_tokens", None),
                "completion_tokens": getattr(raw_usage, "completion_tokens", None),
                "total_tokens": getattr(raw_usage, "total_tokens", None),
            }
    except Exception as exc:
        logger.warning("Failed to extract usage from response: %s", exc)

    # Extra fields (tool calls, function calls, etc.)
    extra: Dict[str, Any] = {}
    try:
        choices = getattr(raw, "choices", None) or raw.get("choices", [])
        if choices:
            choice = choices[0]
            message = getattr(choice, "message", None) or choice.get("message", {})
            tool_calls = getattr(message, "tool_calls", None) or message.get("tool_calls")
            if tool_calls:
                extra["tool_calls"] = tool_calls
    except Exception:
        pass

    return ExecutionResponse(
        content=content,
        model=model_used,
        provider=provider,
        usage=usage,
        finish_reason=finish_reason,
        latency_ms=latency_ms,
        raw_response=raw,
        metadata=request.metadata,
        extra=extra,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run(request: ExecutionRequest) -> ExecutionResponse:
    """
    Execute a completion request synchronously using LiteLLM.

    Args:
        request: The ExecutionRequest describing what to run.

    Returns:
        An ExecutionResponse with the generated content and metadata.

    Raises:
        ExecutionError: If the provider API key is missing or the request
                        fails for any reason.
    """
    _check_provider_keys(request.model)

    kwargs = request.to_litellm_kwargs()
    logger.debug(
        "Executing completion: model=%s, messages=%d, temperature=%s, max_tokens=%s",
        request.model,
        len(kwargs["messages"]),
        request.temperature,
        request.max_tokens,
    )

    start = time.monotonic()
    try:
        raw = litellm.completion(**kwargs)
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000
        logger.error(
            "LiteLLM completion failed for model=%s after %.1fms: %s",
            request.model, latency_ms, exc,
        )
        raise ExecutionError(
            f"Model invocation failed for '{request.model}': {exc}",
            model=request.model,
            provider=_extract_provider(request.model),
            cause=exc,
        ) from exc

    latency_ms = (time.monotonic() - start) * 1000
    response = _extract_response(raw, request, latency_ms)
    logger.info(
        "Completion succeeded: model=%s, provider=%s, latency=%.1fms, "
        "prompt_tokens=%s,