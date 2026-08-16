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

from .client import _get_config, execute as _execute_ai


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
    FREE = "free"              # truly free, no quota, no cost (e.g. local model)
    QUOTA_FREE = "quota_free"  # zero-rated but consumes quota pools (included_quota)
    CHEAP = "cheap"            # very low cost, safe for idle work
    STANDARD = "standard"      # normal cost
    PREMIUM = "premium"        # expensive, only use when requested


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
    # Ollama-hosted models (consume ollama_session/ollama_weekly quota pools)
    "ollama/glm-5.2": ModelTier.QUOTA_FREE,
    "ollama/glm-5": ModelTier.QUOTA_FREE,
    "ollama/glm-5.1": ModelTier.QUOTA_FREE,
    "ollama/glm-4.7": ModelTier.QUOTA_FREE,
    "ollama/gemini-3-flash-preview": ModelTier.QUOTA_FREE,
    "ollama/deepseek-v4-flash": ModelTier.QUOTA_FREE,
    "ollama/deepseek-v4-pro": ModelTier.QUOTA_FREE,
    "ollama/deepseek-v3.2": ModelTier.QUOTA_FREE,
    "ollama/deepseek-v3.1:671b": ModelTier.QUOTA_FREE,
    "ollama/gemma3:4b": ModelTier.QUOTA_FREE,
    "ollama/gemma3:12b": ModelTier.QUOTA_FREE,
    "ollama/gemma3:27b": ModelTier.QUOTA_FREE,
    "ollama/gemma4:31b": ModelTier.QUOTA_FREE,
    "ollama/gpt-oss:20b": ModelTier.QUOTA_FREE,
    "ollama/gpt-oss:120b": ModelTier.QUOTA_FREE,
    "ollama/kimi-k2.5": ModelTier.QUOTA_FREE,
    "ollama/kimi-k2.6": ModelTier.QUOTA_FREE,
    "ollama/kimi-k2.7-code": ModelTier.QUOTA_FREE,
    "ollama/minimax-m2.1": ModelTier.QUOTA_FREE,
    "ollama/minimax-m2.5": ModelTier.QUOTA_FREE,
    "ollama/minimax-m2.7": ModelTier.QUOTA_FREE,
    "ollama/minimax-m3": ModelTier.QUOTA_FREE,
    "ollama/ministral-3:3b": ModelTier.QUOTA_FREE,
    "ollama/ministral-3:14b": ModelTier.QUOTA_FREE,
    "ollama/devstral-2:123b": ModelTier.QUOTA_FREE,
    "ollama/devstral-small-2:24b": ModelTier.QUOTA_FREE,
    # Z-AI included_quota models (windsurf quota pools)

    "glm-5-2": ModelTier.QUOTA_FREE,
    "glm-5-2-1m": ModelTier.QUOTA_FREE,
    "glm-5-2-max": ModelTier.QUOTA_FREE,
    "glm-5-2-max-1m": ModelTier.QUOTA_FREE,
    "glm-5-2-none": ModelTier.QUOTA_FREE,
    "glm-5-2-none-1m": ModelTier.QUOTA_FREE,
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
    # Last time this model was actively probed for liveness (epoch seconds).
    # Used by probe_free_pool to throttle re-probing. Distinct from last_seen
    # (which is set on any successful real call) so a probe doesn't reset the
    # probe-throttle clock confusingly.
    last_probe: float | None = None

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


def _parse_model_spec(model_id: str) -> tuple[str, Optional[str], Optional[str]]:
    """Normalize a model ID into (bare_name, provider, legacy_form).

    The gatehouse gateway advertises models in the model-spec certificate
    format ``model=NAME&prov=PROVIDER`` (e.g. ``model=glm-5.2&prov=ollama``).
    The tier classifier's ``TIER_OVERRIDES`` keys and family prefix
    heuristics were written for the bare name (``glm-5.2``) and the legacy
    ``provider/name`` form (``ollama/glm-5.2``). Without normalization every
    spec-form ID starts with ``model=``, matches none of the
    ``startswith(...)`` heuristics, and falls through to the conservative
    PREMIUM default — which silently bricks routing for every non-expert
    goal (0 free/cheap candidates).

    Returns ``(bare_name, provider, legacy_form)`` where ``legacy_form`` is
    ``f"{prov}/{name}"`` when a provider is present, else ``None``. For a
    non-spec, non-``prov/name`` ID, ``bare_name == model_id`` and the other
    two are ``None``.
    """
    # Model-spec certificate format: "model=NAME&prov=PROVIDER[&...]"
    if model_id.startswith("model="):
        params: dict[str, str] = {}
        for part in model_id.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                params[k] = v
        name = params.get("model") or model_id
        prov = params.get("prov")
        legacy = f"{prov}/{name}" if prov else None
        return name, prov, legacy
    # Legacy "provider/name" form (e.g. "ollama/glm-5.2").
    if "/" in model_id and not model_id.startswith("/"):
        prov, _, name = model_id.partition("/")
        if prov and name:
            return name, prov, model_id
    # Bare name (e.g. "glm-5.2", "kimi-k2-7").
    return model_id, None, None


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
        # Normalize the model-spec certificate format ("model=NAME&prov=PROVIDER")
        # and the legacy "provider/name" form so the config lookup, exact
        # overrides, and family prefix heuristics below match against the bare
        # name and the legacy form as well as the raw ID. Without this, every
        # spec-form ID starts with "model=", matches no heuristic, and defaults
        # to PREMIUM — bricking routing for non-expert goals.
        name, _prov, legacy = _parse_model_spec(model_id)
        forms = [mid for mid in (model_id, name, legacy) if mid is not None]

        # Authoritative source: gatehouse config (first non-UNKNOWN kind wins)
        for mid in forms:
            kind = _get_cost_kind_from_config(mid)
            if kind == CostKind.INCLUDED_UNLIMITED:
                return ModelTier.FREE
            if kind == CostKind.PROMOTIONAL_FREE:
                return ModelTier.FREE
            if kind == CostKind.QUOTA_BEARING:
                return ModelTier.PREMIUM

        # Exact override (try the raw ID, bare name, and legacy form)
        for mid in forms:
            if mid in TIER_OVERRIDES:
                return TIER_OVERRIDES[mid]

        # Family prefix match — check the bare name and legacy form
        for mid in (name, legacy):
            if mid is None:
                continue
            if mid.startswith("ollama/"):
                return ModelTier.QUOTA_FREE
            if mid.startswith("claude-opus") or mid.startswith("claude-5-fable"):
                return ModelTier.PREMIUM
            if mid.startswith("claude-sonnet-"):
                return ModelTier.STANDARD
            if mid.startswith("glm-5"):
                return ModelTier.QUOTA_FREE
            if mid.startswith("gemini-3-5-flash") or mid.startswith("deepseek"):
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
        # FREE (truly free, no quota) is the best fallback -- include it even
        # without allow_expensive. Previously FREE was excluded, leaving truly
        # free models unroutable as fallbacks.
        return tier in (ModelTier.FREE, ModelTier.QUOTA_FREE, ModelTier.CHEAP)

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
        """Fetch the model list from gatehouse. Cached for 60s. Fail-open.

        On a failed fetch (network error, non-200 status, or an unparseable
        payload) the **last-known-good** catalog is PRESERVED rather than wiped
        to ``{}``, and ``_last_refresh`` is NOT advanced. A transient /models blip
        therefore never fail-closes routing/fallback/liveness (which previously
        left an empty catalog cached for the full 60s TTL), and an empty/absent
        catalog is never stuck for the TTL -- the next ``refresh()`` retries.

        Only a successful fetch (HTTP 200 + parseable, non-empty ``data`` list)
        replaces the catalog and stamps ``_last_refresh``.
        """
        now = time.time()
        if not force and self._gateway_models and (now - self._last_refresh) < self._cache_ttl:
            return self._gateway_models

        base_url, api_key, _ = _get_config()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{base_url.rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                if resp.status_code != 200:
                    # Transient failure: keep last-known-good catalog, retry sooner.
                    return self._gateway_models
                fetched = {m["id"]: m for m in resp.json().get("data", []) if "id" in m}
                if not fetched:
                    # A 200 with empty/missing data is indistinguishable from a
                    # degraded gateway (and in practice gatehouse returns the full
                    # model list even when most of the pool is down). Treat it like
                    # a failure: preserve last-known-good and DO NOT stamp
                    # _last_refresh, so an empty catalog is never cached-and-stuck
                    # for the TTL fail-closing routing.
                    return self._gateway_models
        except Exception:
            # Network error / bad payload: preserve prior catalog; retry next time.
            return self._gateway_models

        self._gateway_models = fetched
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

    # ------------------------------------------------------------------
    # Liveness probing (Vikunja #1709)
    # ------------------------------------------------------------------
    # The reactive circuit breaker (record_failure on real execute() failures,
    # now including empty/non-string 200s via PR #14) excludes false-advertised
    # models *after* they waste goal attempts. These probes add a *proactive*
    # path: a minimal call to each free/cheap-tier model classifies it alive /
    # dead *before* the router selects it for a real goal, feeding liveness
    # back into the routable set (is_available) via the same record_success /
    # record_failure path. False-advertised models (nvidia 404, cline empty
    # content) trip the circuit on the probe instead of on a real plan.

    async def probe_liveness(
        self,
        model_id: str,
        prompt: str = "Reply with the single word: ready",
        max_tokens: int = 256,
        timeout: float = 30.0,
    ) -> bool:
        """Actively probe a model with a minimal call and record the result.

        Sends a tiny prompt and classifies the response:
          - success (200 with non-empty string content) -> record_success
          - failure / empty / non-string / HTTP error   -> record_failure

        The default max_tokens (256) is sized for **reasoning models**: a
        reasoning model can burn ~64-80 tokens on thinking before emitting any
        visible content, so a too-small budget (e.g. 16) yields an empty 200 --
        a false-negative that would trip the circuit on a working model. 256
        covers the free/cheap pool comfortably; the actual output is tiny.

        Returns the **probe outcome** (True if the call succeeded with
        content, False if it failed). This is the signal callers want: did the
        model actually respond? Post-probe *availability* (whether the circuit
        / quota hold has tripped after recording) is a separate question answered
        by is_available(); a single failed probe returns False here while the
        model may still be is_available (the circuit opens after
        failure_threshold consecutive failures).
        """
        s = self.state(model_id)
        # Reserve the probe slot BEFORE the await so a concurrent
        # probe_free_pool caller's throttle check skips this model (avoids
        # duplicate billable probes under overlapping invocations -- finding
        # #6/#20). _execute_ai never raises (returns ExecutionResult on
        # failure), so last_probe is always set even on a failed probe.
        s.last_probe = time.time()
        result = await _execute_ai(
            prompt=prompt, model=model_id, max_tokens=max_tokens, timeout=timeout,
        )
        if result.success:
            self.record_success(
                model_id, tokens=result.output_tokens, x_gatehouse=result.x_gatehouse,
            )
        else:
            self.record_failure(model_id, result.error or "liveness probe failed")
        return result.success

    async def probe_free_pool(
        self,
        max_models: int | None = None,
        prompt: str = "Reply with the single word: ready",
        max_tokens: int = 256,
        probe_ttl: float = 300.0,
        reprobe_ttl: float | None = None,
        timeout: float = 30.0,
    ) -> dict[str, bool]:
        """Probe all free/cheap-tier catalog models for liveness.

        Probes only models in the routable free set (FREE / QUOTA_FREE /
        CHEAP tiers) and skips:
          - models in quota hold (genuinely unavailable until a known reset
            time -- no point probing; the reset clears the hold)
          - models probed within ``probe_ttl`` seconds (throttle)

        Circuit-open models (consecutive_failures >= threshold but not in
        quota hold) ARE probed -- this is their recovery path. A transient
        404/empty that tripped the circuit clears when a later probe succeeds
        (record_success resets consecutive_failures). They use the longer
        ``reprobe_ttl`` (default 4x probe_ttl) to bound calls on
        permanently-dead models (finding #7/#16).

        Records success/failure so the circuit breaker + quota holds exclude
        false-advertised models from the routable set. Returns a mapping of
        probed model_id -> probe-outcome (True = responded with content,
        False = failed/empty/errored).
        """
        if reprobe_ttl is None:
            reprobe_ttl = probe_ttl * 4
        await self.refresh()
        results: dict[str, bool] = {}
        for model_id in list(self._gateway_models.keys()):
            tier = self.tier(model_id)
            if tier not in (ModelTier.FREE, ModelTier.QUOTA_FREE, ModelTier.CHEAP):
                continue
            s = self.state(model_id)
            # Skip quota-hold models (known reset time -- the hold clears on
            # its own). Circuit-open models are NOT skipped: they need a probe
            # to recover when a transient issue clears.
            if s.is_in_quota_hold:
                continue
            # Available models use probe_ttl; circuit-open models use the
            # longer reprobe_ttl (recovery checks, bounded).
            ttl = probe_ttl if s.is_available else reprobe_ttl
            if s.last_probe is not None and (time.time() - s.last_probe) < ttl:
                continue
            results[model_id] = await self.probe_liveness(
                model_id, prompt=prompt, max_tokens=max_tokens, timeout=timeout,
            )
            if max_models is not None and len(results) >= max_models:
                break
        return results

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
                ModelTier.FREE: 1500,
                ModelTier.QUOTA_FREE: 1000,
                ModelTier.CHEAP: 500,
                ModelTier.STANDARD: 100,
                ModelTier.PREMIUM: 0,
            }[tier]
            if prefer_free and tier in (ModelTier.FREE, ModelTier.QUOTA_FREE):
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
    """Detect whether an error indicates genuine quota/capacity exhaustion.

    record_failure() uses this to decide whether a failure ALSO enters the
    long quota hold (3h). The hold is only appropriate for conditions that
    reliably mean "resource exhausted for a sustained period":

      - explicit quota / rate-limit language and HTTP 429
      - upstream capacity: HTTP 502/503, "unavailable", "model not loaded"

    Everything else -- model-not-found (404), timeouts, generic 5xx, and
    401/403 auth -- is NOT quota. Transient/config errors are handled by the
    per-step circuit breaker (consecutive_failures >= threshold) and, since PR
    #15, by the liveness probe's recovery path; they must NOT impose a 3h hold
    that would strand the model from probing. The old indicators list over-matched
    on "timeout"/"404"/"500"/"401"/"403", causing false 3h holds.
    """
    lower = str(error_str).lower()

    # Genuine quota/rate-limit signals.
    quota_words = (
        "quota", "rate limit", "too many requests", "capacity",
        "exhausted", "limit reached", "try again later", "429",
    )
    if any(w in lower for w in quota_words):
        return True

    # Upstream capacity / unavailable (server-side resource exhaustion).
    capacity_words = (
        "bad gateway", "service unavailable", "502", "503", "unavailable",
        "model not loaded",
    )
    if any(w in lower for w in capacity_words):
        return True

    return False


def _cost_class_to_tier(cost_class: str) -> ModelTier:
    """Map a gatehouse cost_class (from x_gatehouse) to a ModelTier."""
    cc = (cost_class or "").lower()
    # Included quota = zero-rated but consumes quota pools (windsurf, ollama, etc)
    # These are free in dollar terms but NOT unlimited — quota can exhaust.
    if cc in ("included_quota", "zero_rated"):
        return ModelTier.QUOTA_FREE
    # Truly unlimited / promotional free
    if cc in ("included_unlimited", "promotional_free", "free"):
        return ModelTier.FREE
    # Retail / paid = premium
    if cc in ("retail", "paid", "premium", "standard"):
        return ModelTier.PREMIUM
    if cc in ("cheap", "discount"):
        return ModelTier.CHEAP
    # Unknown: conservative
    return ModelTier.PREMIUM
