"""Gateway monitor: tracks model availability, quota state, and cost tiers.

This is the integration point between the job-star scheduler/router and the
gatehouse AI gateway. It answers:
- Which models are available right now?
- Which models are in quota hold?
- How many consecutive failures has a model had?
- Which models are expensive and should not be used as silent fallbacks?
- When should we retry a model that hit quota?

Usage:
    monitor = GatewayMonitor()
    await monitor.refresh()
    if not monitor.is_available("ollama/gemini-3-flash-preview"):
        model = monitor.pick_fallback(
            "ollama/gemini-3-flash-preview",
            required_capability="vision",
        )
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import httpx

from .client import _get_config


# How long to hold a model after a quota/availability error
DEFAULT_QUOTA_HOLD_SECONDS = 3 * 60 * 60  # 3 hours

# Circuit breaker: after N consecutive failures, open the circuit
DEFAULT_FAILURE_THRESHOLD = 3

# Gatehouse config paths to read for actual model_costs metadata
GATEHOUSE_CONFIG_PATHS = [
    "/etc/gatehouse/config.json",
    "/opt/gatehouse/config.json",
    "config.json",
]


class ModelTier(str, Enum):
    """Cost tier of a model."""
    FREE = "free"             # free to use, no quota impact
    CHEAP = "cheap"           # very low cost, safe for idle work
    STANDARD = "standard"     # normal cost
    PREMIUM = "premium"       # expensive, only use when requested


class CostKind(str, Enum):
    """How a model is billed, from gatehouse config."""
    INCLUDED_UNLIMITED = "included_unlimited"
    PROMOTIONAL_FREE = "promotional_free"
    QUOTA_BEARING = "quota_bearing"
    UNKNOWN = "unknown"


# Tier assignment for known model families. Gatehouse does not expose pricing
# reliably in the /v1/models endpoint, so we maintain a conservative map. The
# monitor also attempts to load /etc/gatehouse/config.json for authoritative
# `free_kind`/`quota_pools` metadata. Updates are cheap.
TIER_OVERRIDES: dict[str, ModelTier] = {
    # Ollama-hosted models are free
    "ollama/glm-5.2": ModelTier.FREE,
    "ollama/glm-5": ModelTier.FREE,
    "ollama/glm-5.1": ModelTier.FREE,
    "ollama/glm-4.7": ModelTier.FREE,
    "ollama/gemini-3-flash-preview": ModelTier.FREE,
    "ollama/deepseek-v4-flash": ModelTier.FREE,
    "ollama/deepseek-v4-pro": ModelTier.FREE,
    "ollama/deepseek-v3.2": ModelTier.FREE,
    "ollama/deepseek-v3.1:671b": ModelTier.FREE,
    "ollama/gemma3:4b": ModelTier.FREE,
    "ollama/gemma3:12b": ModelTier.FREE,
    "ollama/gemma3:27b": ModelTier.FREE,
    "ollama/gemma4:31b": ModelTier.FREE,
    "ollama/gpt-oss:20b": ModelTier.FREE,
    "ollama/gpt-oss:120b": ModelTier.FREE,
    "ollama/kimi-k2.5": ModelTier.FREE,
    "ollama/kimi-k2.6": ModelTier.FREE,
    "ollama/kimi-k2.7-code": ModelTier.FREE,
    "ollama/minimax-m2.1": ModelTier.FREE,
    "ollama/minimax-m2.5": ModelTier.FREE,
    "ollama/minimax-m2.7": ModelTier.FREE,
    "ollama/minimax-m3": ModelTier.FREE,
    "ollama/ministral-3:3b": ModelTier.FREE,
    "ollama/ministral-3:14b": ModelTier.FREE,
    "ollama/devstral-2:123b": ModelTier.FREE,
    "ollama/devstral-small-2:24b": ModelTier.FREE,
    # Z-AI free-ish models
    "glm-5-2": ModelTier.FREE,
    "glm-5-2-1m": ModelTier.FREE,
    "glm-5-2-max": ModelTier.FREE,
    "glm-5-2-max-1m": ModelTier.FREE,
    "glm-5-2-none": ModelTier.FREE,
    "glm-5-2-none-1m": ModelTier.FREE,
    # Gemini flash is cheap
    "gemini-3-5-flash-high": ModelTier.CHEAP,
    "gemini-3-5-flash-low": ModelTier.CHEAP,
    "gemini-3-5-flash-medium": ModelTier.CHEAP,
    "gemini-3-5-flash-minimal": ModelTier.CHEAP,
    "gemini-3-1-pro-high": ModelTier.STANDARD,
    "gemini-3-1-pro-low": ModelTier.STANDARD,
    "deepseek-v4": ModelTier.CHEAP,
    "deepseek-ai/deepseek-v4-flash": ModelTier.CHEAP,
    "deepseek-ai/deepseek-v4-pro": ModelTier.CHEAP,
    # Claude premium models
    "claude-opus-4-6": ModelTier.PREMIUM,
    "claude-opus-4-6-1m": ModelTier.PREMIUM,
    "claude-opus-4-6-thinking": ModelTier.PREMIUM,
    "claude-opus-4-6-thinking-1m": ModelTier.PREMIUM,
    "claude-opus-4-7-high": ModelTier.PREMIUM,
    "claude-opus-4-7-high-fast": ModelTier.PREMIUM,
    "claude-opus-4-7-low": ModelTier.PREMIUM,
    "claude-opus-4-7-low-fast": ModelTier.PREMIUM,
    "claude-opus-4-7-max": ModelTier.PREMIUM,
    "claude-opus-4-7-max-fast": ModelTier.PREMIUM,
    "claude-opus-4-7-medium": ModelTier.PREMIUM,
    "claude-opus-4-7-medium-fast": ModelTier.PREMIUM,
    "claude-opus-4-7-xhigh": ModelTier.PREMIUM,
    "claude-opus-4-7-xhigh-fast": ModelTier.PREMIUM,
    "claude-opus-4-8-high": ModelTier.PREMIUM,
    "claude-opus-4-8-high-fast": ModelTier.PREMIUM,
    "claude-opus-4-8-low": ModelTier.PREMIUM,
    "claude-opus-4-8-low-fast": ModelTier.PREMIUM,
    "claude-opus-4-8-max": ModelTier.PREMIUM,
    "claude-opus-4-8-max-fast": ModelTier.PREMIUM,
    "claude-opus-4-8-medium": ModelTier.PREMIUM,
    "claude-opus-4-8-medium-fast": ModelTier.PREMIUM,
    "claude-opus-4-8-xhigh": ModelTier.PREMIUM,
    "claude-opus-4-8-xhigh-fast": ModelTier.PREMIUM,
    "claude-5-fable-high": ModelTier.PREMIUM,
    "claude-5-fable-low": ModelTier.PREMIUM,
    "claude-5-fable-max": ModelTier.PREMIUM,
    "claude-5-fable-medium": ModelTier.PREMIUM,
    "claude-5-fable-xhigh": ModelTier.PREMIUM,
    "claude-sonnet-4-6": ModelTier.STANDARD,
    "claude-sonnet-4-6-1m": ModelTier.STANDARD,
    "claude-sonnet-4-6-thinking": ModelTier.STANDARD,
    "claude-sonnet-4-6-thinking-1m": ModelTier.STANDARD,
    "claude-sonnet-5-high": ModelTier.STANDARD,
    "claude-sonnet-5-low": ModelTier.STANDARD,
    "claude-sonnet-5-max": ModelTier.STANDARD,
    "claude-sonnet-5-medium": ModelTier.STANDARD,
    "claude-sonnet-5-xhigh": ModelTier.STANDARD,
}


@dataclass
class QuotaWindow:
    """A quota pool window reported by gatehouse (from x_gatehouse.quota_windows)."""
    pool_id: str
    dimension: str          # e.g. "dollars", "quota_units"
    window: str             # e.g. "daily", "weekly"
    limit: float
    used: float
    remaining: float
    remaining_pct: float
    resets_at: str | None   # ISO timestamp
    hours_until_reset: float | None = None


@dataclass
class ModelState:
    """Health state of a single model."""
    name: str
    last_seen: float | None = None
    consecutive_failures: int = 0
    quota_hold_until: float = 0.0  # epoch seconds
    last_error: str | None = None
    total_requests: int = 0
    total_tokens: int = 0
    # Observed from x_gatehouse in the last response
    observed_cost_class: str | None = None     # e.g. "included_quota", "retail"
    observed_routing_advice: str | None = None  # e.g. "harvest", "switch"
    observed_quota_windows: list[QuotaWindow] = field(default_factory=list)
    observed_retail_value: float = 0.0
    observed_reason: str | None = None

    @property
    def is_available(self) -> bool:
        if time.time() <= self.quota_hold_until:
            return False
        if self.consecutive_failures >= DEFAULT_FAILURE_THRESHOLD:
            return False
        # If any quota window is exhausted, the model is unavailable until reset
        for w in self.observed_quota_windows:
            if w.remaining_pct <= 0:
                return False
        return True

    @property
    def is_in_quota_hold(self) -> bool:
        return time.time() <= self.quota_hold_until

    def enter_quota_hold(self, duration_seconds: float) -> None:
        self.quota_hold_until = time.time() + duration_seconds

    def record_failure(self, error: str) -> None:
        self.consecutive_failures += 1
        self.last_error = error

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.last_error = None


class GatewayMonitor:
    """Monitor gatehouse model availability, quota state, and cost tiers.

    Lightweight, no background task required. Refresh on demand or let
    the scheduler call it periodically.
    """

    def __init__(
        self,
        quota_hold_seconds: float = DEFAULT_QUOTA_HOLD_SECONDS,
        failure_threshold: int = DEFAULT_FAILURE_THRESHOLD,
    ):
        self.quota_hold_seconds = quota_hold_seconds
        self.failure_threshold = failure_threshold
        self._states: dict[str, ModelState] = {}
        self._gateway_models: dict[str, dict] = {}
        self._last_refresh: float = 0.0
        self._cache_ttl: float = 60.0

    @staticmethod
    def tier(model_id: str) -> ModelTier:
        """Return the cost tier for a model.

        Uses observed cost_class from x_gatehouse when available (via
        instance method tier_for), then gatehouse config `free_kind`, then
        the hardcoded `TIER_OVERRIDES` map, then family prefix heuristics.
        """
        # Authoritative source: gatehouse config
        kind = _get_cost_kind_from_config(model_id)
        if kind == CostKind.INCLUDED_UNLIMITED:
            return ModelTier.FREE
        if kind == CostKind.PROMOTIONAL_FREE:
            return ModelTier.FREE
        if kind == CostKind.QUOTA_BEARING:
            return ModelTier.PREMIUM

        # Exact override
        if model_id in TIER_OVERRIDES:
            return TIER_OVERRIDES[model_id]

        # Family prefix match
        if model_id.startswith("ollama/"):
            return ModelTier.FREE
        if model_id.startswith("claude-opus") or model_id.startswith("claude-5-fable"):
            return ModelTier.PREMIUM
        if model_id.startswith("claude-sonnet-"):
            return ModelTier.STANDARD
        if model_id.startswith("glm-5"):
            return ModelTier.FREE
        if model_id.startswith("gemini-3-5-flash") or model_id.startswith("deepseek"):
            return ModelTier.CHEAP
        # Unknown model: conservative default
        return ModelTier.PREMIUM

    def tier_for(self, model_id: str) -> ModelTier:
        """Return the cost tier, preferring observed x_gatehouse cost_class."""
        s = self.state(model_id)
        if s.observed_cost_class:
            return _cost_class_to_tier(s.observed_cost_class)
        return self.tier(model_id)

    def is_expensive(self, model_id: str) -> bool:
        """Return True if the model is premium/standard and should not be a silent fallback."""
        return self.tier_for(model_id) in (ModelTier.PREMIUM, ModelTier.STANDARD)

    def is_allowed_fallback(self, model_id: str, allow_expensive: bool = False) -> bool:
        """Check if a model is acceptable as a fallback."""
        if allow_expensive:
            return True
        tier = self.tier_for(model_id)
        return tier in (ModelTier.FREE, ModelTier.CHEAP)

    def cost_kind(self, model_id: str) -> CostKind:
        """Return the gatehouse cost kind for a model."""
        return _get_cost_kind_from_config(model_id)

    def is_quota_bearing(self, model_id: str) -> bool:
        """Return True if the model consumes quota when used."""
        kind = self.cost_kind(model_id)
        return kind in (CostKind.QUOTA_BEARING, CostKind.PROMOTIONAL_FREE)

    def is_included_unlimited(self, model_id: str) -> bool:
        """Return True if the model is included_unlimited (does not consume quota)."""
        return self.cost_kind(model_id) == CostKind.INCLUDED_UNLIMITED

    async def refresh(self, force: bool = False) -> dict[str, dict]:
        """Fetch the model list from gatehouse. Cached for 60s."""
        now = time.time()
        if not force and self._gateway_models and (now - self._last_refresh) < self._cache_ttl:
            return self._gateway_models

        base_url, api_key, _ = _get_config()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{base_url}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    self._gateway_models = {m["id"]: m for m in data.get("data", []) if "id" in m}
                else:
                    self._gateway_models = {}
        except Exception:
            self._gateway_models = {}

        self._last_refresh = now
        for model_id in self._gateway_models:
            if model_id not in self._states:
                self._states[model_id] = ModelState(name=model_id)

        return self._gateway_models

    def state(self, model_id: str) -> ModelState:
        """Get or create state for a model."""
        if model_id not in self._states:
            self._states[model_id] = ModelState(name=model_id)
        return self._states[model_id]

    def is_available(self, model_id: str) -> bool:
        """Check if a model is currently available (not in quota hold and circuit is closed)."""
        return self.state(model_id).is_available

    def record_success(self, model_id: str, tokens: int = 0, x_gatehouse: dict | None = None) -> None:
        """Record a successful model invocation.

        If x_gatehouse is provided (from usage.x_gatehouse in the response),
        update the model's observed cost_class, routing_advice, quota windows,
        and retail value. This is the authoritative source for tiering.
        """
        s = self.state(model_id)
        s.record_success()
        s.total_requests += 1
        s.total_tokens += tokens
        s.last_seen = time.time()
        if x_gatehouse:
            self._apply_x_gatehouse(model_id, x_gatehouse)

    def _apply_x_gatehouse(self, model_id: str, xg: dict) -> None:
        """Parse x_gatehouse and update model state + quota holds."""
        s = self.state(model_id)
        s.observed_cost_class = xg.get("cost_class")
        s.observed_routing_advice = xg.get("routing_advice")
        s.observed_reason = xg.get("reason")
        s.observed_retail_value = float(xg.get("retail_value_this_request", 0.0) or 0.0)

        # Parse quota windows
        windows: list[QuotaWindow] = []
        for w in xg.get("quota_windows", []) or []:
            try:
                windows.append(QuotaWindow(
                    pool_id=w.get("pool_id", ""),
                    dimension=w.get("dimension", ""),
                    window=w.get("window", ""),
                    limit=float(w.get("limit", 0) or 0),
                    used=float(w.get("used", 0) or 0),
                    remaining=float(w.get("remaining", 0) or 0),
                    remaining_pct=float(w.get("remaining_pct", 0) or 0),
                    resets_at=w.get("resets_at"),
                    hours_until_reset=w.get("hours_until_reset"),
                ))
            except (TypeError, ValueError):
                continue
        s.observed_quota_windows = windows

        # If any window is exhausted, enter quota hold until the earliest reset
        exhausted = [w for w in windows if w.remaining_pct <= 0]
        if exhausted:
            # Use the soonest reset time to compute hold duration
            import datetime as _dt
            soonest = None
            for w in exhausted:
                if w.resets_at:
                    try:
                        reset_dt = _dt.datetime.fromisoformat(w.resets_at.replace("Z", "+00:00"))
                        if soonest is None or reset_dt < soonest:
                            soonest = reset_dt
                    except ValueError:
                        continue
            if soonest:
                hold = (soonest - _dt.datetime.now(_dt.timezone.utc)).total_seconds()
                if hold > 0:
                    s.enter_quota_hold(hold)
                else:
                    # Reset already passed; use default
                    s.enter_quota_hold(self.quota_hold_seconds)
            else:
                s.enter_quota_hold(self.quota_hold_seconds)

    def record_failure(self, model_id: str, error: str) -> None:
        """Record a failed model invocation."""
        s = self.state(model_id)
        s.record_failure(error)
        if _is_quota_error(error):
            s.enter_quota_hold(self.quota_hold_seconds)

    def time_until_available(self, model_id: str) -> float:
        """Return seconds until a model is available again (0 if available now)."""
        s = self.state(model_id)
        if s.is_available:
            return 0.0
        return max(0.0, s.quota_hold_until - time.time())

    def pick_fallback(
        self,
        preferred_model_id: str,
        required_capability: str | None = None,
        prefer_free: bool = False,
        allow_expensive: bool = False,
    ) -> str | None:
        """Pick a fallback model when the preferred model is unavailable.

        Args:
            preferred_model_id: The model that was originally requested.
            required_capability: e.g., "vision" or "text".
            prefer_free: Prefer free models over cheap ones.
            allow_expensive: Allow premium/standard models to be selected.

        Returns:
            A model ID or None if no fallback is available.
        """
        preferred = self._gateway_models.get(preferred_model_id, {})
        preferred_capabilities = set(preferred.get("capabilities", {}).keys())
        if required_capability:
            preferred_capabilities.add(required_capability)

        preferred_tier = self.tier_for(preferred_model_id)

        candidates: list[tuple[str, dict, float]] = []
        for model_id, model in self._gateway_models.items():
            if model_id == preferred_model_id:
                continue
            if not self.is_available(model_id):
                continue
            if not self.is_allowed_fallback(model_id, allow_expensive):
                continue
            caps = model.get("capabilities", {})
            if required_capability and not caps.get(required_capability):
                continue

            tier = self.tier_for(model_id)
            # Score: tier priority, then capability overlap, then context length
            tier_score = {
                ModelTier.FREE: 1000,
                ModelTier.CHEAP: 500,
                ModelTier.STANDARD: 100,
                ModelTier.PREMIUM: 0,
            }[tier]
            if prefer_free and tier == ModelTier.FREE:
                tier_score += 1000

            overlap = len(preferred_capabilities & set(caps.keys()))
            context = model.get("context_length", 0)
            context_score = min(context / 100000, 5.0)

            # Prefer staying close to the original tier if possible
            tier_match_bonus = 50 if tier == preferred_tier else 0

            # Respect routing_advice: if gatehouse advised "harvest" for this
            # model, boost it (it's free retail value). If "switch", penalize.
            s = self.state(model_id)
            advice_bonus = 0
            if s.observed_routing_advice == "harvest":
                advice_bonus += 200
            elif s.observed_routing_advice == "switch":
                advice_bonus -= 200

            score = tier_score + overlap * 10 + context_score + tier_match_bonus + advice_bonus
            candidates.append((model_id, model, score))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates[0][0]

    def quota_status(self, model_id: str) -> dict | None:
        """Return observed quota status for a model, or None if not observed."""
        s = self.state(model_id)
        if not s.observed_quota_windows and not s.observed_cost_class:
            return None
        return {
            "cost_class": s.observed_cost_class,
            "routing_advice": s.observed_routing_advice,
            "reason": s.observed_reason,
            "retail_value": s.observed_retail_value,
            "quota_windows": [
                {
                    "pool_id": w.pool_id,
                    "dimension": w.dimension,
                    "remaining_pct": w.remaining_pct,
                    "resets_at": w.resets_at,
                    "hours_until_reset": w.hours_until_reset,
                }
                for w in s.observed_quota_windows
            ],
        }


# ============================================================================
# Helpers
# ============================================================================

# In-memory cache for gatehouse config model_costs
_gatehouse_cost_cache: dict[str, CostKind] = {}
_gatehouse_config_mtime: float = 0.0


def _load_gatehouse_config() -> dict:
    """Load the gatehouse config file if accessible."""
    global _gatehouse_config_mtime
    for path in GATEHOUSE_CONFIG_PATHS:
        if not os.path.exists(path):
            continue
        try:
            mtime = os.path.getmtime(path)
            if mtime != _gatehouse_config_mtime:
                with open(path) as f:
                    config = json.load(f)
                _gatehouse_config_mtime = mtime
                # Build cost cache from config
                _gatehouse_cost_cache.clear()
                for provider, cfg in config.get("providers", {}).items():
                    for mc in cfg.get("account", {}).get("model_costs", []):
                        model = mc.get("model")
                        if model:
                            kind = mc.get("free_kind", "unknown")
                            try:
                                _gatehouse_cost_cache[model] = CostKind(kind)
                            except ValueError:
                                _gatehouse_cost_cache[model] = CostKind.UNKNOWN
                return config
        except (json.JSONDecodeError, OSError, PermissionError):
            continue
    return {}


def _get_cost_kind_from_config(model_id: str) -> CostKind:
    """Look up the cost kind for a model from the gatehouse config."""
    _load_gatehouse_config()
    return _gatehouse_cost_cache.get(model_id, CostKind.UNKNOWN)


def _is_quota_error(error_str: str) -> bool:
    """Detect whether an error string indicates quota/availability issues."""
    indicators = [
        "quota", "rate limit", "too many requests", "capacity",
        "exhausted", "limit reached", "try again later", "unavailable",
        "model not loaded", "model not found", "timeout", "gateway timeout",
        "404", "429", "503", "502", "500", "bad gateway", "service unavailable",
        "unauthorized", "401", "forbidden", "403",
    ]
    lower = str(error_str).lower()
    return any(k in lower for k in indicators)


def _cost_class_to_tier(cost_class: str) -> ModelTier:
    """Map a gatehouse cost_class (from x_gatehouse) to a ModelTier."""
    cc = (cost_class or "").lower()
    # Zero-rated / included quota / promotional = free to use
    if cc in ("included_quota", "included_unlimited", "promotional_free", "zero_rated", "free"):
        return ModelTier.FREE
    # Retail / paid = premium
    if cc in ("retail", "paid", "premium", "standard"):
        return ModelTier.PREMIUM
    if cc in ("cheap", "discount"):
        return ModelTier.CHEAP
    # Unknown: conservative
    return ModelTier.PREMIUM
