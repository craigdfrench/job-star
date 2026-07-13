"""Unit tests for RoutingStrategy."""
import pytest

from router.strategy import RoutingStrategy, RoutingDecision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_task(complexity="moderate", urgency="normal",
              budget=None, estimated_tokens=1000):
    """Build a lightweight task dict for routing."""
    return {
        "complexity": complexity,      # simple | moderate | complex
        "urgency": urgency,            # low | normal | high | critical
        "budget": budget,              # max USD spend, None = unlimited
        "estimated_tokens": estimated_tokens,
    }


# ---------------------------------------------------------------------------
# Complexity-based selection
# ---------------------------------------------------------------------------

class TestComplexityRouting:
    def test_simple_task_picks_cheapest_capable(self, strategy):
        task = make_task(complexity="simple", budget=1.0)
        decision = strategy.route(task)
        # Simple tasks should route to the cheapest model that meets
        # a minimum capability threshold.  gpt-4o-mini / gemini-flash tier.
        assert decision.model_name in ("gpt-4o-mini", "gemini-1.5-flash",
                                       "claude-3-haiku")
        # Must be cheaper than premium models
        chosen = strategy.registry.get_model(decision.model_name)
        assert chosen.cost_per_1k_tokens < 0.001

    def test_complex_task_picks_capable_model(self, strategy):
        task = make_task(complexity="complex", budget=10.0)
        decision = strategy.route(task)
        chosen = strategy.registry.get_model(decision.model_name)
        # Complex tasks need high capability (>= 9)
        assert chosen.capability_score >= 9
        assert decision.model_name in ("claude-3-opus", "claude-3.5-sonnet",
                                        "gpt-4-turbo")

    def test_moderate_task_picks_mid_tier(self, strategy):
        task = make_task(complexity="moderate", budget=5.0)
        decision = strategy.route(task)
        chosen = strategy.registry.get_model(decision.model_name)
        # Moderate tasks should land in the mid tier
        assert chosen.capability_score >= 7
        assert chosen.cost_per_1k_tokens < 0.01

    def test_simple_never_uses_premium(self, strategy):
        """A simple task should never pick the most expensive model."""
        task = make_task(complexity="simple", budget=100.0)
        decision = strategy.route(task)
        assert decision.model_name != "claude-3-opus"

    def test_complex_never_uses_cheapest(self, strategy):
        """A complex task should never pick the lowest-capability model."""
        task = make_task(complexity="complex", budget=100.0)
        decision = strategy.route(task)
        chosen = strategy.registry.get_model(decision.model_name)
        assert chosen.capability_score > 5


# ---------------------------------------------------------------------------
# Urgency-based selection
# ---------------------------------------------------------------------------

class TestUrgencyRouting:
    def test_urgent_task_prefers_fast_model(self, strategy):
        task = make_task(complexity="simple", urgency="critical", budget=1.0)
        decision = strategy.route(task)
        chosen = strategy.registry.get_model(decision.model_name)
        # Critical urgency should bias toward high speed score
        assert chosen.speed_score >= 8

    def test_low_urgency_allows_slow_premium(self, strategy):
        """When urgency is low and task is complex, a slow premium model
        is acceptable."""
        task = make_task(complexity="complex", urgency="low", budget=10.0)
        decision = strategy.route(task)
        chosen = strategy.registry.get_model(decision.model_name)
        assert chosen.capability_score >= 9

    def test_urgent_complex_balances_speed_and_capability(self, strategy):
        """Urgent + complex: must still be capable but prefer faster of
        the capable options."""
        task = make_task(complexity="complex", urgency="high", budget=10.0)
        decision = strategy.route(task)
        chosen = strategy.registry.get_model(decision.model_name)
        assert chosen.capability_score >= 9
        # Among capability>=9 models, prefer higher speed
        capable = [m for m in strategy.registry.get_available_models()
                   if strategy.registry.get_model(m).capability_score >= 9]
        fastest_capable = max(
            capable,
            key=lambda n: strategy.registry.get_model(n).speed_score,
        )
        assert decision.model_name == fastest_capable


# ---------------------------------------------------------------------------
# Budget enforcement
# ---------------------------------------------------------------------------

class TestBudgetEnforcement:
    def test_never_exceeds_budget(self, strategy):
        """The chosen model's estimated cost must never exceed the budget."""
        task = make_task(complexity="complex", urgency="low",
                         budget=0.001, estimated_tokens=1000)
        decision = strategy.route(task)
        est_cost = strategy.registry.estimate_cost(
            decision.model_name, 1000, 500)
        assert est_cost <= 0.001

    def test_budget_too_small_returns_none_or_cheapest(self, strategy):
        """If no model fits the budget, return None or the cheapest
        available with a flag."""
        task = make_task(complexity="complex", budget=0.000001,
                         estimated_tokens=1_000_000)
        decision = strategy.route(task)
        if decision is not None:
            est_cost = strategy.registry.estimate_cost(
                decision.model_name, 1_000_000, 500_000)
            assert est_cost <= 0.000001
        # decision.model_name should be None or decision.within_budget False
        # Either way, the caller must be able to detect infeasibility.

    def test_budget_filters_out_expensive_models(self, strategy):
        """With a tight budget, premium models are excluded even if
        complexity is high."""
        task = make_task(complexity="complex", urgency="low",
                         budget=0.004, estimated_tokens=1000)
        decision = strategy.route(task)
        chosen = strategy.registry.get_model(decision.model_name)
        # claude-3-opus at $0.015/1k would cost ~$0.0225 for 1.5k tokens
        # — exceeds $0.004 budget, so it must not be chosen.
        assert decision.model_name != "claude-3-opus"
        est_cost = strategy.registry.estimate_cost(
            decision.model_name, 1000, 500)
        assert est_cost <= 0.004

    def test_unlimited_budget_allows_premium(self, strategy):
        task = make_task(complexity="complex", urgency="low",
                         budget=None, estimated_tokens=1000)
        decision = strategy.route(task)
        chosen = strategy.registry.get_model(decision.model_name)
        assert chosen.capability_score >= 9


# ---------------------------------------------------------------------------
# Availability filtering
# ---------------------------------------------------------------------------

class TestAvailabilityFiltering:
    def test_unavailable_model_not_selected(self, strategy, registry):
        registry.set_availability("claude-3-opus", False)
        task = make_task(complexity="complex", urgency="low", budget=10.0)
        decision = strategy.route(task)
        assert decision.model_name != "claude-3-opus"

    def test_falls_to_next_best_when_top_unavailable(self, strategy, registry):
        """If the top-ranked model is down, the strategy should pick the
        next-best available model rather than failing."""
        registry.set_availability("claude-3-opus", False)
        task = make_task(complexity="complex", urgency="low", budget=10.0)
        decision = strategy.route(task)
        assert decision is not None
        chosen = registry.get_model(decision.model_name)
        assert chosen.capability_score >= 9  # still capable
        assert chosen.available is True

    def test_all_models_unavailable_returns_none(self, strategy, registry):
        for name in registry.list_models():
            registry.set_availability(name, False)
        task = make_task(complexity="simple", budget=1.0)
        decision = strategy.route(task)
        assert decision is None


# ---------------------------------------------------------------------------
# Ranked candidates (for executor fallback)
# ---------------------------------------------------------------------------

class TestRankedCandidates:
    def test_route_returns_ranked_candidates(self, strategy):
        """The strategy should return a ranked list so the executor can
        fall back in order."""
        task = make_task(complexity="moderate", urgency="normal", budget=5.0)
        decision = strategy.route(task)
        assert isinstance(decision, RoutingDecision)
        assert hasattr(decision, "candidates")
        assert len(decision.candidates) >= 1
        # Primary model is candidates[0]
        assert decision.model_name == decision.candidates[0]

    def test_candidates_excluded_by_budget(self, strategy):
        task = make_task(complexity="complex", budget=0.001,
                         estimated_tokens=1000)
        decision = strategy.route(task)
        for name in decision.candidates:
            cost = strategy.registry.estimate_cost(name, 1000, 500)
            assert cost <= 0.001

    def test_candidates_excluded_by_availability(self, strategy, registry):
        registry.set_availability("gpt-4o", False)
        task = make_task(complexity="moderate", budget=5.0)
        decision = strategy.route(task)
        assert "gpt-4o" not in decision.candidates
