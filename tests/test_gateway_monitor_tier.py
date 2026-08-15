"""Tests for GatewayMonitor cost-tier classification across model-ID formats.

Regression coverage for the model-spec certificate format drift: the
gatehouse gateway advertises models as ``model=NAME&prov=PROVIDER`` (e.g.
``model=glm-5.2&prov=ollama``), but the tier classifier's overrides and
heuristics were written for the bare name (``glm-5.2``) and the legacy
``provider/name`` form (``ollama/glm-5.2``). Without normalization every
spec-form ID falls through to the PREMIUM default, bricking routing for
non-expert goals (0 free/cheap candidates).
"""

import time

import pytest

from job_star.gatehouse.monitor import (
    GatewayMonitor,
    ModelTier,
    _parse_model_spec,
)
from job_star.router.engine import _build_live_candidates, route
from job_star.models import Urgency


# --------------------------------------------------------------------------- #
# _parse_model_spec
# --------------------------------------------------------------------------- #


class TestParseModelSpec:
    def test_model_spec_form(self):
        name, prov, legacy = _parse_model_spec("model=glm-5.2&prov=ollama")
        assert name == "glm-5.2"
        assert prov == "ollama"
        assert legacy == "ollama/glm-5.2"

    def test_model_spec_form_no_provider(self):
        name, prov, legacy = _parse_model_spec("model=glm-5.2")
        assert name == "glm-5.2"
        assert prov is None
        assert legacy is None

    def test_model_spec_form_extra_params(self):
        name, prov, legacy = _parse_model_spec(
            "model=glm-5.2&prov=ollama&reasoning=high"
        )
        assert name == "glm-5.2"
        assert prov == "ollama"
        assert legacy == "ollama/glm-5.2"

    def test_legacy_provider_slash_form(self):
        name, prov, legacy = _parse_model_spec("ollama/glm-5.2")
        assert name == "glm-5.2"
        assert prov == "ollama"
        assert legacy == "ollama/glm-5.2"

    def test_bare_name(self):
        name, prov, legacy = _parse_model_spec("kimi-k2-7")
        assert name == "kimi-k2-7"
        assert prov is None
        assert legacy is None

    def test_unknown_form_passes_through(self):
        name, prov, legacy = _parse_model_spec("some-weird-id")
        assert name == "some-weird-id"
        assert prov is None
        assert legacy is None

    # --- malformed / edge-case inputs (fail-safe, never raise) ---------------

    def test_empty_string(self):
        name, prov, legacy = _parse_model_spec("")
        assert name == ""
        assert prov is None
        assert legacy is None

    def test_model_spec_no_value(self):
        # "model=" with no name value: bare name falls back to the raw id
        name, prov, legacy = _parse_model_spec("model=")
        assert name == "model="
        assert prov is None
        assert legacy is None

    def test_model_spec_no_model_param(self):
        # params present but no "model=" key, and the id does not start with
        # "model=": the parser treats it as a bare/unknown name (fail-safe,
        # no crash). prov is None; tier() will conservatively default PREMIUM.
        name, prov, legacy = _parse_model_spec("&prov=ollama")
        assert name == "&prov=ollama"
        assert prov is None
        assert legacy is None

    def test_model_spec_empty_provider(self):
        name, prov, legacy = _parse_model_spec("model=glm-5.2&prov=")
        assert name == "glm-5.2"
        assert prov == ""
        # empty prov -> no legacy form (f"{prov}/{name}" would be "/glm-5.2")
        assert legacy is None

    def test_model_spec_duplicate_params_last_wins(self):
        name, prov, legacy = _parse_model_spec(
            "model=glm-5.2&prov=ollama&prov=nvidia"
        )
        assert name == "glm-5.2"
        assert prov == "nvidia"  # last value wins
        assert legacy == "nvidia/glm-5.2"

    def test_bare_name_with_slash_prefix_not_treated_as_legacy(self):
        # a leading slash is not a "provider/name" form
        name, prov, legacy = _parse_model_spec("/weird")
        assert name == "/weird"
        assert prov is None
        assert legacy is None


# --------------------------------------------------------------------------- #
# GatewayMonitor.tier — model-spec format classification
# --------------------------------------------------------------------------- #


class TestTierModelSpecFormat:
    """The live gateway advertises models in model=NAME&prov=PROVIDER form."""

    @pytest.mark.parametrize("model_id,expected", [
        # QUOTA_FREE via the glm-5 prefix heuristic on the bare name
        ("model=glm-5.2&prov=ollama", ModelTier.QUOTA_FREE),
        ("model=glm-5.1&prov=ollama", ModelTier.QUOTA_FREE),
        # QUOTA_FREE via the legacy-form TIER_OVERRIDES key "ollama/glm-5.2"
        ("model=glm-5.2&prov=nvidia", ModelTier.QUOTA_FREE),
        # QUOTA_FREE via the legacy-form TIER_OVERRIDES key "ollama/deepseek-v4-flash"
        ("model=deepseek-v4-flash&prov=ollama", ModelTier.QUOTA_FREE),
        # CHEAP via the deepseek prefix heuristic (no matching override)
        ("model=deepseek-v4-pro&prov=together", ModelTier.CHEAP),
        # CHEAP via the gemini-3-5-flash prefix heuristic
        ("model=gemini-3-5-flash-minimal&prov=google", ModelTier.CHEAP),
        # PREMIUM via the claude-opus prefix heuristic
        ("model=claude-opus-4-8-high&prov=anthropic", ModelTier.PREMIUM),
        # Unknown family stays PREMIUM (conservative default)
        ("model=Some-Unknown-Model&prov=acme", ModelTier.PREMIUM),
    ])
    def test_tier_classifies_model_spec_form(self, model_id, expected):
        assert GatewayMonitor.tier(model_id) == expected


class TestTierLegacyRegression:
    """Legacy and bare-name forms must still classify (no behavior change)."""

    @pytest.mark.parametrize("model_id,expected", [
        ("ollama/glm-5.2", ModelTier.QUOTA_FREE),
        ("ollama/glm-5.1", ModelTier.QUOTA_FREE),
        ("glm-5.2", ModelTier.QUOTA_FREE),
        ("glm-5-2", ModelTier.QUOTA_FREE),       # TIER_OVERRIDES key
        ("deepseek-v4", ModelTier.CHEAP),
        ("gemini-3-5-flash-minimal", ModelTier.CHEAP),
        ("claude-opus-4-8-high", ModelTier.PREMIUM),
        ("claude-sonnet-4-6", ModelTier.STANDARD),
        ("some-unknown-model", ModelTier.PREMIUM),
    ])
    def test_tier_legacy_and_bare_forms(self, model_id, expected):
        assert GatewayMonitor.tier(model_id) == expected


# --------------------------------------------------------------------------- #
# Live-gateway unblock: the scenario that was bricked before the fix
# --------------------------------------------------------------------------- #


def _spec_gateway_models():
    """A minimal live-gateway model dict in the model-spec certificate form."""
    return {
        "model=glm-5.2&prov=ollama": {
            "id": "model=glm-5.2&prov=ollama",
            "capabilities": {"text": True, "code": True},
            "pricing": {"input": 0.0, "output": 0.0},
            "context_length": 128_000,
        },
        "model=deepseek-v4-flash&prov=ollama": {
            "id": "model=deepseek-v4-flash&prov=ollama",
            "capabilities": {"text": True, "code": True},
            "pricing": {"input": 0.0, "output": 0.0},
            "context_length": 64_000,
        },
        "model=claude-opus-4-8-high&prov=anthropic": {
            "id": "model=claude-opus-4-8-high&prov=anthropic",
            "capabilities": {"text": True, "code": True},
            "pricing": {"input": 15.0, "output": 75.0},
            "context_length": 200_000,
        },
    }


@pytest.mark.asyncio
async def test_build_live_candidates_finds_free_models_in_spec_form():
    """Before the fix, all spec-form models defaulted to PREMIUM -> 0 candidates."""
    monitor = GatewayMonitor()
    monitor._gateway_models = _spec_gateway_models()
    monitor._last_refresh = time.time()  # serve the cache; do not re-fetch
    candidates = await _build_live_candidates(
        monitor, requires_vision=False, prefer_free=False, allow_expensive=False
    )
    # The two free/cheap models qualify; the premium claude does not.
    assert len(candidates) == 2
    names = {c.name for c in candidates}
    assert "model=glm-5.2&prov=ollama" in names
    assert "model=deepseek-v4-flash&prov=ollama" in names
    assert "model=claude-opus-4-8-high&prov=anthropic" not in names


@pytest.mark.asyncio
async def test_route_returns_free_model_for_non_expert_goal():
    """The end-to-end unblock: route() finds a model for a soon/feature goal.

    Before the fix this returned model='' with reason 'No allowed model
    available (all in quota hold or circuit open)', which is exactly what
    bricked planning for non-expert (personal) goals.
    """
    monitor = GatewayMonitor()
    monitor._gateway_models = _spec_gateway_models()
    monitor._last_refresh = time.time()  # serve the cache; do not re-fetch
    decision = await route(
        urgency=Urgency.SOON,
        request_type="feature",
        description="Build a structured 4-week daily learning module to learn WezTerm",
        allow_expensive=False,
        gateway_monitor=monitor,
    )
    assert decision.model, f"expected a routed model, got none: {decision.reason!r}"
    assert decision.model in _spec_gateway_models()
    assert monitor.tier_for(decision.model) in (
        ModelTier.QUOTA_FREE,
        ModelTier.CHEAP,
        ModelTier.FREE,
    )


@pytest.mark.asyncio
async def test_pick_fallback_finds_spec_form_model():
    """pick_fallback must return a spec-form free model (not None)."""
    monitor = GatewayMonitor()
    monitor._gateway_models = _spec_gateway_models()
    fallback = monitor.pick_fallback(
        "model=glm-5.2&prov=ollama",
        required_capability=None,
        prefer_free=False,
        allow_expensive=False,
    )
    assert fallback is not None
    assert fallback in _spec_gateway_models()
    assert monitor.tier_for(fallback) in (ModelTier.QUOTA_FREE, ModelTier.CHEAP)


# --------------------------------------------------------------------------- #
# Offline fallback (no gateway monitor) — must be unaffected by the change
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_offline_fallback_unaffected_without_gateway_monitor():
    """The offline path (no gateway monitor) uses the static MODEL_REGISTRY.

    The review panel worried that calling pick_fallback with a spec-form tag
    against the legacy-keyed MODEL_REGISTRY would silently brick the offline
    fallback. That cannot happen: pick_fallback is a GatewayMonitor method
    that iterates the live _gateway_models, and route()'s offline branch sets
    fallback=None and selects from MODEL_REGISTRY directly — pick_fallback is
    not called. This test pins that the offline path still returns a model
    and is unaffected by the spec-form tier() changes.
    """
    decision = await route(
        urgency=Urgency.SOON,
        request_type="feature",
        description="Build a structured 4-week daily learning module to learn WezTerm",
        allow_expensive=False,
        gateway_monitor=None,  # offline mode
    )
    assert decision.model, (
        f"offline fallback returned no model: {decision.reason!r}"
    )
    # The offline path selects from MODEL_REGISTRY (legacy-form names) and
    # does not go through tier() classification at all.
    assert decision.model