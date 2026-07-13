"""Unit tests for ModelRegistry."""
import pytest

from router.registry import ModelRegistry, ModelEntry, ModelNotFoundError


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestRegistration:
    def test_register_single_model(self):
        reg = ModelRegistry()
        reg.register(
            name="gpt-4o-mini",
            provider="openai",
            cost_per_1k_tokens=0.00015,
            capability_score=5,
            speed_score=9,
            available=True,
        )
        assert "gpt-4o-mini" in reg.list_models()

    def test_register_multiple_models(self, registry):
        names = registry.list_models()
        assert len(names) == 7
        assert "gpt-4o-mini" in names
        assert "claude-3-opus" in names

    def test_register_duplicate_overwrites(self):
        reg = ModelRegistry()
        reg.register("m1", "openai", 0.01, 5, 5, True)
        reg.register("m1", "openai", 0.02, 7, 7, True)
        entry = reg.get_model("m1")
        assert entry.cost_per_1k_tokens == 0.02
        assert entry.capability_score == 7


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

class TestLookup:
    def test_get_model_returns_entry(self, registry):
        entry = registry.get_model("gpt-4o")
        assert isinstance(entry, ModelEntry)
        assert entry.provider == "openai"
        assert entry.capability_score == 8

    def test_get_unknown_model_raises(self, registry):
        with pytest.raises(ModelNotFoundError):
            registry.get_model("does-not-exist")

    def test_list_models_returns_all(self, registry):
        names = set(registry.list_models())
        assert names == {m[0] for m in DEFAULT_MODELS}


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

class TestAvailability:
    def test_get_available_models_excludes_unavailable(self, registry):
        registry.set_availability("claude-3-opus", False)
        available = registry.get_available_models()
        assert "claude-3-opus" not in available
        assert "gpt-4o-mini" in available

    def test_set_availability_toggles(self, registry):
        assert registry.get_model("gpt-4o").available is True
        registry.set_availability("gpt-4o", False)
        assert registry.get_model("gpt-4o").available is False
        registry.set_availability("gpt-4o", True)
        assert registry.get_model("gpt-4o").available is True

    def test_set_availability_unknown_model_raises(self, registry):
        with pytest.raises(ModelNotFoundError):
            registry.set_availability("nope", False)

    def test_all_available_when_registered(self, registry):
        for name in registry.list_models():
            assert registry.get_model(name).available is True


# ---------------------------------------------------------------------------
# Cost queries
# ---------------------------------------------------------------------------

class TestCostQueries:
    def test_cost_per_1k_tokens(self, registry):
        assert registry.get_model("gpt-4o-mini").cost_per_1k_tokens == 0.00015
        assert registry.get_model("claude-3-opus").cost_per_1k_tokens == 0.015

    def test_estimate_cost(self, registry):
        """estimate_cost(model, input_tokens, output_tokens)."""
        # gpt-4o-mini: $0.00015 / 1k tokens
        cost = registry.estimate_cost("gpt-4o-mini",
                                       input_tokens=1000,
                                       output_tokens=500)
        # (1000 + 500) / 1000 * 0.00015 = 0.000225
        assert abs(cost - 0.000225) < 1e-9

    def test_estimate_cost_zero_tokens(self, registry):
        cost = registry.estimate_cost("gpt-4o", 0, 0)
        assert cost == 0.0
