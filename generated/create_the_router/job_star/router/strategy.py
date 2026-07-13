"""
Job-Star Routing Strategy Engine

Scores and selects the best AI model for a given task based on:
  - capability match (required capabilities + complexity tier)
  - cost within budget
  - urgency vs. latency tier
  - model availability

Uses a weighted scoring approach. Hard constraints (budget ceiling,
required capabilities, enabled status) filter candidates before scoring.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

logger = logging.getLogger("job_star.router.strategy")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Urgency(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class LatencyTier(str, Enum):
    FAST = "fast"        # typically < 2s first token
    MEDIUM = "medium"    # 2–6s
    SLOW = "slow"        # > 6s, often large reasoning models


class TaskType(str, Enum):
    SIMPLE_QA = "simple_qa"
    SUMMARIZATION = "summarization"
    CODE_GENERATION = "code_generation"
    REASONING = "reasoning"
    CREATIVE_WRITING = "creative_writing"
    EMBEDDING = "embedding"
    VISION = "vision"
    TOOL_USE = "tool_use"
    GENERAL = "general"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class RoutingRequest:
    """Describes the task to be routed."""

    task_type: TaskType = TaskType.GENERAL
    complexity: float = 0.3          # 0.0 (trivial) – 1.0 (extremely complex)
    urgency: Urgency = Urgency.NORMAL
    cost_budget: Optional[float] = None  # max USD per call; None = unlimited
    required_capabilities: list[str] = field(default_factory=list)
    preferred_models: list[str] = field(default_factory=list)
    estimated_input_tokens: int = 1000
    estimated_output_tokens: int = 500
    max_latency_tier: Optional[LatencyTier] = None  # hard cap on slowness

    def __post_init__(self):
        if not 0.0 <= self.complexity <= 1.0:
            raise ValueError(
                f"complexity must be between 0.0 and 1.0, got {self.complexity}"
            )


@dataclass
class ModelEntry:
    """A model in the registry."""

    model_id: str                        # internal canonical id, e.g. "gpt-4o"
    litellm_model: str                   # LiteLLM model string, e.g. "openai/gpt-4o"
    provider: str                        # e.g. "openai", "anthropic"
    capabilities: list[str] = field(default_factory=list)
    complexity_tier: float = 0.5         # max complexity this model handles well (0–1)
    cost_per_1k_input: float = 0.0       # USD
    cost_per_1k_output: float = 0.0      # USD
    latency_tier: LatencyTier = LatencyTier.MEDIUM
    availability: float = 1.0            # 0.0–1.0, current health score
    context_window: int = 8192
    enabled: bool = True
    tags: list[str] = field(default_factory=list)

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            (input_tokens / 1000.0) * self.cost_per_1k_input
            + (output_tokens / 1000.0) * self.cost_per_1k_output
        )


@dataclass
class RoutingDecision:
    """The result of a routing operation."""

    selected_model: str
    litellm_model: str
    provider: str
    score: float
    rationale: str
    estimated_cost: float
    alternatives: list[tuple[str, float]] = field(default_factory=list)
    filtered_out: list[tuple[str, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------

WEIGHTS = {
    "capability": 0.35,
    "cost": 0.25,
    "latency": 0.25,
    "availability": 0.15,
}

# How well each latency tier satisfies each urgency level (0–1).
# High urgency strongly prefers fast models; low urgency tolerates slow.
URGENCY_LATENCY_FIT: dict[Urgency, dict[LatencyTier, float]] = {
    Urgency.CRITICAL: {LatencyTier.FAST: 1.0, LatencyTier.MEDIUM: 0.4, LatencyTier.SLOW: 0.0},
    Urgency.HIGH:     {LatencyTier.FAST: 1.0, LatencyTier.MEDIUM: 0.6, LatencyTier.SLOW: 0.2},
    Urgency.NORMAL:   {LatencyTier.FAST: 0.8, LatencyTier.MEDIUM: 1.0, LatencyTier.SLOW: 0.7},
    Urgency.LOW:      {LatencyTier.FAST: 0.6, LatencyTier.MEDIUM: 0.9, LatencyTier.SLOW: 1.0},
}

LATENCY_RANK = {LatencyTier.FAST: 0, LatencyTier.MEDIUM: 1, LatencyTier.SLOW: 2}


# ---------------------------------------------------------------------------
# Strategy engine
# ---------------------------------------------------------------------------

class RoutingStrategyEngine:
    """
    Scores candidate models and selects the best one for a RoutingRequest.

    Usage:
        engine = RoutingStrategyEngine()
        decision = engine.route(request, registry)
    """

    def route(
        self,
        request: RoutingRequest,
        registry: dict[str, ModelEntry],
    ) -> RoutingDecision:
        """Select the best model for the request from the registry."""

        filtered_out: list[tuple[str, str]] = []

        # --- Phase 1: Hard filtering -------------------------------------
        candidates: list[ModelEntry] = []
        for model_id, model in registry.items():
            reason = self._check_feasible(model, request)
            if reason is not None:
                filtered_out.append((model_id, reason))
                continue
            candidates.append(model)

        if not candidates:
            return RoutingDecision(
                selected_model="",
                litellm_model="",
                provider="",
                score=0.0,
                rationale=(
                    "No feasible model found. All candidates were filtered out. "
                    f"Reasons: {filtered_out or 'registry empty'}"
                ),
                estimated_cost=0.0,
                alternatives=[],
                filtered_out=filtered_out,
            )

        # --- Phase 2: Scoring --------------------------------------------
        scored: list[tuple[ModelEntry, float, dict[str, float]]] = []
        for model in candidates:
            sub_scores = self._score_model(model, request)
            total = (
                WEIGHTS["capability"] * sub_scores["capability"]
                + WEIGHTS["cost"] * sub_scores["cost"]
                + WEIGHTS["latency"] * sub_scores["latency"]
                + WEIGHTS["availability"] * sub_scores["availability"]
            )
            # Bonus for explicitly preferred models
            if model.model_id in request.preferred_models:
                total += 0.05
            scored.append((model, total, sub_scores))

        scored.sort(key=lambda t: t[1], reverse=True)

        best, best_score, best_subs = scored[0]
        est_cost = best.estimate_cost(
            request.estimated_input_tokens, request.estimated_output_tokens
        )
        alternatives = [(m.model_id, s) for m, s, _ in scored[1:6]]
        rationale = self._build_rationale(best, best_score, best_subs, request, est_cost)

        logger.info(
            "Routed %s task (complexity=%.2f, urgency=%s) → %s (score=%.3f, cost≈$%.5f)",
            request.task_type.value,
            request.complexity,
            request.urgency.value,
            best.model_id,
            best_score,
            est_cost,
        )

        return RoutingDecision(
            selected_model=best.model_id,
            litellm_model=best.litellm_model,
            provider=best.provider,
            score=round(best_score, 4),
            rationale=rationale,
            estimated_cost=round(est_cost, 6),
            alternatives=alternatives,
            filtered_out=filtered_out,
        )

    # -- Feasibility -------------------------------------------------------

    @staticmethod
    def _check_feasible(model: ModelEntry, request: RoutingRequest) -> Optional[str]:
        """Return a reason string if infeasible, else None."""
        if not model.enabled:
            return "model disabled"

        missing_caps = set(request.required_capabilities) - set(model.capabilities)
        if missing_caps:
            return f"missing capabilities: {sorted(missing_caps)}"

        est_cost = model.estimate_cost(
            request.estimated_input_tokens, request.estimated_output_tokens
        )
        if request.cost_budget is not None and est_cost > request.cost_budget:
            return (
                f"estimated cost ${est_cost:.5f} exceeds budget "
                f"${request.cost_budget:.5f}"
            )

        if request.max_latency_tier is not None:
            if LATENCY_RANK[model.latency_tier] > LATENCY_RANK[request.max_latency_tier]:
                return (
                    f"latency tier {model.latency_tier.value} exceeds "
                    f"max {request.max_latency_tier.value}"
                )

        if model.availability <= 0.0:
            return "availability is zero"

        return None

    # -- Scoring -----------------------------------------------------------

    def _score_model(
        self, model: ModelEntry, request: RoutingRequest
    ) -> dict[str, float]:
        """Return sub-scores in 0–1 for each dimension."""
        return {
            "capability": self._score_capability(model, request),
            "cost": self._score_cost(model, request),
            "latency": self._score_latency(model, request),
            "availability": self._score_availability(model),
        }

    @staticmethod
    def _score_capability(model: ModelEntry, request: RoutingRequest) -> float:
        """
        How well the model's complexity_tier covers the requested complexity,
        with a bonus for having the right capabilities beyond required ones.
        """
        # Coverage: model should handle at least the requested complexity.
        # If model tier >= complexity, full coverage; else partial.
        if model.complexity_tier >= request.complexity:
            coverage = 1.0
        else:
            # Linearly penalize under-tiered models, but don't go below 0.
            gap = request.complexity - model.complexity_tier
            coverage = max(0.0, 1.0 - (gap * 2.0))

        # Small penalty for over-provisioning (using a sledgehammer for a nail).
        over = model.complexity_tier - request.complexity
        if over > 0.3:
            coverage -= 0.1 * min(over, 0.5)

        # Capability bonus: having extra relevant capabilities is mildly good.
        cap_bonus = min(0.1, len(model.capabilities) * 0.01)

        return max(0.0, min(1.0, coverage + cap_bonus))

    @staticmethod
    def _score_cost(model: ModelEntry, request: RoutingRequest) -> float:
        """
        Score cost efficiency. Cheaper is better.
        Uses a diminishing-returns curve based on estimated cost relative to
        the budget (or a sensible default if no budget given).
        """
        est_cost = model.estimate_cost(
            request.estimated_input_tokens, request.estimated_output_tokens
        )

        # Reference point: the budget, or a default of $0.02 per call.
        ref = request.cost_budget if request.cost_budget is not None else 0.02

        if est_cost <= 0:
            return 1.0  # free model

        # ratio = cost / ref.  ratio=0 → 1.0, ratio=1 → ~0.37, ratio→∞ → 0
        ratio = est_cost / max(ref, 1e-9)
        score = 1.0 / (1.0 + ratio * 2.0)

        return max(0.0, min(1.0, score))

    @staticmethod
    def _score_latency(model: ModelEntry, request: RoutingRequest) -> float:
        """How well the model's latency tier fits the request urgency."""
        return URGENCY_LATENCY_FIT[request.urgency][model.latency_tier]

    @staticmethod
    def _score_availability(model: ModelEntry) -> float:
        """Direct mapping; availability is already 0–1."""
        return max(0.0, min(1.0, model.availability))

    # -- Rationale ---------------------------------------------------------

    @staticmethod
    def _build_rationale(
        model: ModelEntry,
        score: float,
        subs: dict[str, float],
        request: RoutingRequest,
        est_cost: float,
    ) -> str:
        parts = [
            f"Selected '{model.model_id}' ({model.provider}) "
            f"with overall score {score:.3f}."
        ]
        parts.append(
            f"Sub-scores — capability: {subs['capability']:.2f}, "
            f"cost: {subs['cost']:.2f}, "
            f"latency: {subs['latency']:.2f}, "
            f"availability: {subs['availability']:.2f}."
        )
        parts.append(
            f"Task: {request.task_type.value}, complexity={request.complexity:.2f}, "
            f"urgency={request.urgency.value}."
        )
        parts.append(
            f"Model complexity tier={model.complexity_tier:.2f}, "
            f"latency={model.latency_tier.value}, "
            f"est. cost≈${est_cost:.5f}."
        )
        if model.model_id in request.preferred_models:
            parts.append("Bonus applied: model was in preferred list.")
        return " ".join(parts)


# ---------------------------------------------------------------------------
# Convenience function
# ---------------------------------------------------------------------------

def route_request(
    request: RoutingRequest,
    registry: dict[str, ModelEntry],
) -> RoutingDecision:
    """Functional shortcut for routing a single request."""
    return RoutingStrategyEngine().route(request, registry)
