"""Model availability checking with TTL caching.

Provides a HealthChecker that can determine whether a given LiteLLM model
is currently usable. Two strategies are supported:

1. ``ping`` — issue a tiny LiteLLM completion (max_tokens=1) to confirm the
   model responds. Costs a fraction of a cent but is authoritative.
2. ``key`` — check that the relevant API key environment variable is set.
   Free and instant, but only confirms *configuration*, not *reachability*.

Results are cached per-model for ``cache_ttl`` seconds so repeated routing
decisions don't re-ping.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger("job_star.router.health")

# Mapping from LiteLLM model prefixes to the env var that must be set.
# This is a pragmatic subset; extend as needed.
_MODEL_KEY_MAP: dict[str, str] = {
    "gpt": "OPENAI_API_KEY",
    "o1": "OPENAI_API_KEY",
    "o3": "OPENAI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "command": "COHERE_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
}


@dataclass
class HealthStatus:
    model: str
    available: bool
    reason: str  # "ok", "missing_key", "ping_failed:<detail>", "not_checked"
    checked_at: float  # unix timestamp
    latency_ms: float | None = None


def _key_for_model(model: str) -> str | None:
    """Return the env var name that authorizes ``model``, or None if unknown."""
    lower = model.lower()
    for prefix, key in _MODEL_KEY_MAP.items():
        if lower.startswith(prefix):
            return key
    return None


class HealthChecker:
    """Cached availability checker for LiteLLM models."""

    def __init__(
        self,
        mode: Literal["ping", "key"] = "key",
        cache_ttl: float = 60.0,
    ) -> None:
        self.mode = mode
        self.cache_ttl = cache_ttl
        self._cache: dict[str, HealthStatus] = {}

    def _check_key(self, model: str) -> HealthStatus:
        key_name = _key_for_model(model)
        if key_name is None:
            return HealthStatus(
                model=model,
                available=False,
                reason="unknown_provider",
                checked_at=time.time(),
            )
        if not os.getenv(key_name):
            return HealthStatus(
                model=model,
                available=False,
                reason=f"missing_key:{key_name}",
                checked_at=time.time(),
            )
        return HealthStatus(
            model=model,
            available=True,
            reason="ok",
            checked_at=time.time(),
        )

    def _check_ping(self, model: str) -> HealthStatus:
        try:
            import litellm  # local import to keep optional at module load time
            import time as _t

            start = _t.time()
            litellm.completion(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=1,
                temperature=0,
            )
            latency_ms = (_t.time() - start) * 1000
            return HealthStatus(
                model=model,
                available=True,
                reason="ok",
                checked_at=time.time(),
                latency_ms=latency_ms,
            )
        except Exception as exc:  # noqa: BLE001
            detail = type(exc).__name__
            return HealthStatus(
                model=model,
                available=False,
                reason=f"ping_failed:{detail}",
                checked_at=time.time(),
            )

    def check(self, model: str, force: bool = False) -> HealthStatus:
        """Return cached health status for ``model`` or perform a fresh check."""
        cached = self._cache.get(model)
        now = time.time()
        if cached is not None and not force and (now - cached.checked_at) < self.cache_ttl:
            return cached

        if self.mode == "ping":
            status = self._check_ping(model)
        else:
            status = self._check_key(model)

        self._cache[model] = status
        logger.debug(
            "health_check model=%s available=%s reason=%s",
            model,
            status.available,
            status.reason,
        )
        return status

    def invalidate(self, model: str | None = None) -> None:
        """Clear cache for one model or all models."""
        if model is None:
            self._cache.clear()
        else:
            self._cache.pop(model, None)
