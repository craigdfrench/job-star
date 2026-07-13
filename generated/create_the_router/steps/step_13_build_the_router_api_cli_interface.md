# Job-Star Router: API & CLI Interface

I'll build the Router as a cohesive service that ties together a model registry, routing strategy, and LiteLLM executor. Since this is the integration layer, I'll include the supporting components (registry, strategy, executor) within `router.py` so it's self-contained and immediately usable, while keeping clean separation of concerns for future extraction.

## File 1: `job_star/router/router.py`

```python
"""
Job-Star Router: Intelligent model routing service.

Picks the right AI model based on task complexity, urgency, cost budget,
and model availability. Uses LiteLLM as the unified execution backend.

Usage:
    from job_star.router.router import Router, RoutingRequest

    router = Router()
    request = RoutingRequest(
        task="Summarize this article",
        complexity="low",
        urgency="normal",
        cost_budget=0.05,
    )
    decision = router.route(request)          # returns RoutingDecision
    result = router.execute(decision, request)  # actually calls the model
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger("job_star.router")

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Complexity(str, Enum):
    """Task complexity levels, mapped to model capability tiers."""

    TRIVIAL = "trivial"    # simple formatting, short answers
    LOW = "low"            # basic reasoning, summarization
    MEDIUM = "medium"      # multi-step reasoning, code generation
    HIGH = "high"          # complex analysis, long-form writing
    CRITICAL = "critical"  # architecture, deep reasoning, safety-critical


class Urgency(str, Enum):
    """How quickly the result is needed."""

    INSTANT = "instant"    # sub-second expected (chat, autocomplete)
    SOON = "soon"          # seconds (interactive tool)
    NORMAL = "normal"      # minutes OK (batch-ish)
    BACKGROUND = "background"  # cheapest possible, latency irrelevant


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ModelInfo:
    """Metadata for a single model in the registry."""

    name: str                       # LiteLLM model string, e.g. "gpt-4o"
    provider: str                   # "openai", "anthropic", "groq", etc.
    cost_per_1k_input: float         # USD per 1K input tokens
    cost_per_1k_output: float        # USD per 1K output tokens
    max_output_tokens: int
    capability_tier: str             # one of Complexity values
    latency_tier: str               # one of Urgency values (best-case)
    available: bool = True
    context_window: int = 128_000
    tags: list[str] = field(default_factory=list)

    def estimated_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate total USD cost for a generation."""
        return (
            (input_tokens / 1000) * self.cost_per_1k_input
            + (output_tokens / 1000) * self.cost_per_1k_output
        )


@dataclass
class RoutingRequest:
    """Input to the router describing what the caller needs."""

    task: str                                # prompt / task description
    complexity: str | Complexity = Complexity.MEDIUM
    urgency: str | Urgency = Urgency.NORMAL
    cost_budget: Optional[float] = None      # USD, None = unlimited
    max_output_tokens: int = 1024
    estimated_input_tokens: int = 500        # rough estimate for cost calc
    preferred_provider: Optional[str] = None
    required_tags: list[str] = field(default_factory=list)
    system_prompt: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def normalized_complexity(self) -> Complexity:
        if isinstance(self.complexity, Complexity):
            return self.complexity
        return Complexity(self.complexity)

    def normalized_urgency(self) -> Urgency:
        if isinstance(self.urgency, Urgency):
            return self.urgency
        return Urgency(self.urgency)


@dataclass
class RoutingDecision:
    """Output of the routing strategy — what model to use and why."""

    model: str                        # chosen LiteLLM model string
    provider: str
    capability_tier: str
    estimated_cost: float
    within_budget: bool
    reason: str                       # human-readable explanation
    alternatives: list[str] = field(default_factory=list)
    request: Optional[RoutingRequest] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "capability_tier": self.capability_tier,
            "estimated_cost": round(self.estimated_cost, 6),
            "within_budget": self.within_budget,
            "reason": self.reason,
            "alternatives": self.alternatives,
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

# Tier ranking for comparison (higher = more capable / faster)
_COMPLEXITY_RANK = {
    Complexity.TRIVIAL: 0,
    Complexity.LOW: 1,
    Complexity.MEDIUM: 2,
    Complexity.HIGH: 3,
    Complexity.CRITICAL: 4,
}

_URGENCY_RANK = {
    Urgency.INSTANT: 0,
    Urgency.SOON: 1,
    Urgency.NORMAL: 2,
    Urgency.BACKGROUND: 3,
}


class ModelRegistry:
    """Holds model metadata and provides filtered lookups."""

    def __init__(self, models: list[ModelInfo] | None = None) -> None:
        self._models: dict[str, ModelInfo] = {}
        if models:
            for m in models:
                self.register(m)

    def register(self, model: ModelInfo) -> None:
        self._models[model.name] = model
        logger.debug(f"Registered model: {model.name}")

    def get(self, name: str) -> Optional[ModelInfo]:
        return self._models.get(name)

    def all(self) -> list[ModelInfo]:
        return list(self._models.values())

    def available(self) -> list[ModelInfo]:
        return [m for m in self._models.values() if m.available]

    def by_provider(self, provider: str) -> list[ModelInfo]:
        return [m for m in self.available() if m.provider == provider]

    def with_tags(self, tags: list[str]) -> list[ModelInfo]:
        if not tags:
            return self.available()
        return [
            m for m in self.available()
            if all(t in m.tags for t in tags)
        ]

    def capable_of(self, complexity: Complexity) -> list[ModelInfo]:
        """Return models whose capability tier meets or exceeds the requirement."""
        required = _COMPLEXITY_RANK[complexity]
        return [
            m for m in self.available()
            if _COMPLEXITY_RANK.get(Complexity(m.capability_tier), 0) >= required
        ]


# ---------------------------------------------------------------------------
# Default registry — sensible starter set
# ---------------------------------------------------------------------------


def default_registry() -> ModelRegistry:
    """Create a registry with a curated default model set."""
    models = [
        # --- OpenAI ---
        ModelInfo(
            name="gpt-4o",
            provider="openai",
            cost_per_1k_input=0.0025,
            cost_per_1k_output=0.01,
            max_output_tokens=16_384,
            capability_tier="high",
            latency_tier="soon",
            context_window=128_000,
            tags=["vision", "tools", "json"],
        ),
        ModelInfo(
            name="gpt-4o-mini",
            provider="openai",
            cost_per_1k_input=0.00015,
            cost_per_1k_output=0.0006,
            max_output_tokens=16_384,
            capability_tier="medium",
            latency_tier="instant",
            context_window=128_000,
            tags=["vision", "tools", "json"],
        ),
        ModelInfo(
            name="gpt-3.5-turbo",
            provider="openai",
            cost_per_1k_input=0.0005,
            cost_per_1k_output=0.0015,
            max_output_tokens=4_096,
            capability_tier="low",
            latency_tier="instant",
            context_window=16_385,
            tags=["json"],
        ),
        # --- Anthropic ---
        ModelInfo(
            name="claude-3-5-sonnet-20240620",
            provider="anthropic",
            cost_per_1k_input=0.003,
            cost_per_1k_output=0.015,
            max_output_tokens=8_192,
            capability_tier="high",
            latency_tier="soon",
            context_window=200_000,
            tags=["vision", "tools", "long-context"],
        ),
        ModelInfo(
            name="claude-3-5-haiku-20241022",
            provider="anthropic",
            cost_per_1k_input=0.0008,
            cost_per_1k_output=0.004,
            max_output_tokens=8_192,
            capability_tier="medium",
            latency_tier="instant",
            context_window=200_000,
            tags=["vision", "long-context"],
        ),
        ModelInfo(
            name="claude-3-opus-20240229",
            provider="anthropic",
            cost_per_1k_input=0.015,
            cost_per_1k_output=0.075,
            max_output_tokens=4_096,
            capability_tier="critical",
            latency_tier="normal",
            context_window=200_000,
            tags=["vision", "tools", "long-context"],
        ),
        # --- Groq (ultra-fast) ---
        ModelInfo(
            name="groq/llama-3.3-70b-versatile",
            provider="groq",
            cost_per_1k_input=0.00059,
            cost_per_1k_output=0.00079,
            max_output_tokens=8_192,
            capability_tier="medium",
            latency_tier="instant",
            context_window=128_000,
            tags=["tools"],
        ),
        ModelInfo(
            name="groq/llama-3.1-8b-instant",
            provider="groq",
            cost_per_1k_input=0.00005,
            cost_per_1k_output=0.00008,
            max_output_tokens=8_192,
            capability_tier="low",
            latency_tier="instant",
            context_window=128_000,
            tags=[],
        ),
        # --- Google ---
        ModelInfo(
            name="gemini/gemini-2.0-flash",
            provider="gemini",
            cost_per_1k_input=0.0001,
            cost_per_1k_output=0.0004,
            max_output_tokens=8_192,
            capability_tier="medium",
            latency_tier="instant",
            context_window=1_000_000,
            tags=["vision", "long-context"],
        ),
        ModelInfo(
            name="gemini/gemini-1.5-pro",
            provider="gemini",
            cost_per_1k_input=0.00125,
            cost_per_1k_output=0.005,
            max_output_tokens=8_192,
            capability_tier="high",
            latency_tier="normal",
            context_window=2_000_000,
            tags=["vision", "long-context"],
        ),
    ]
    return ModelRegistry(models)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


class RoutingStrategy:
    """
    Selects the best model from the registry given a RoutingRequest.

    Decision logic (in priority order):
      1. Filter to available models.
      2. Filter by required tags.
      3. Filter by preferred provider (if set).
      4. Filter to models capable of the requested complexity.
      5. Filter to models that fit the cost budget (if set).
      6. Among remaining candidates, pick based on urgency:
         - INSTANT/SOON: prefer lowest latency tier, then lowest cost
         - NORMAL: prefer lowest cost
         - BACKGROUND: prefer absolute lowest cost
      7. If nothing survives budget filter, relax budget and pick cheapest
         capable model, flagging within_budget=False.
    """

    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry

    def select(self, request: RoutingRequest) -> RoutingDecision:
        complexity = request.normalized_complexity()
        urgency = request.normalized_urgency()

        # Step 1-3: base filters
        candidates = self.registry.available()
        if request.required_tags:
            candidates = [
                m for m in candidates
                if all(t in m.tags for t in request.required_tags)
            ]
        if request.preferred_provider:
            candidates = [
                m for m in candidates
                if m.provider == request.preferred_provider
            ]

        if not candidates:
            return RoutingDecision(
                model="",
                provider="",
                capability_tier="",
                estimated_cost=0.0,
                within_budget=False,
                reason="No models available after applying filters "
                       f"(tags={request.required_tags}, "
                       f"provider={request.preferred_provider}).",
                request=request,
            )

        # Step 4: capability filter
        capable = self.registry.capable_of(complexity)
        # intersect with current candidates
        capable_names = {m.name for m in capable}
        capable_candidates = [m for m in candidates if m.name in capable_names]

        # Fallback: if no model meets complexity, use highest-tier candidate
        if not capable_candidates:
            capable_candidates = sorted(
                candidates,
                key=lambda m: _COMPLEXITY_RANK.get(
                    Complexity(m.capability_tier), 0
                ),
                reverse=True,
            )
            logger.warning(
                f"No model meets complexity '{complexity.value}'; "
                f"falling back to highest available: "
                f"{capable_candidates[0].name}"
            )

        # Step 5: budget filter
        def est_cost(m: ModelInfo) -> float:
            return m.estimated_cost(
                request.estimated_input_tokens,
                request.max_output_tokens,
            )

        within_budget_models = capable_candidates
        if request.cost_budget is not None:
            within_budget_models = [
                m for m in capable_candidates
                if est_cost(m) <= request.cost_budget
            ]

        # Step 6: pick based on urgency
        pool = within_budget_models if within_budget_models else capable_candidates
        within_budget = bool(within_budget_models)

        if urgency in (Urgency.INSTANT, Urgency.SOON):
            # Sort by latency tier first, then cost
            pool_sorted = sorted(
                pool,
                key=lambda m: (
                    _URGENCY_RANK.get(Urgency(m.latency_tier), 99),
                    est_cost(m),
                ),
            )
        else:
            # NORMAL / BACKGROUND: sort by cost only
            pool_sorted = sorted(pool, key=est_cost)

        chosen = pool_sorted[0]
        cost = est_cost(chosen)

        # Build alternatives (next 2 options)
        alternatives = [m.name for m in pool_sorted[1:3]]

        # Build reason string
        reasons = []
        reasons.append(f"complexity={complexity.value}")
        reasons.append(f"urgency={urgency.value}")
        if request.cost_budget is not None:
            reasons.append(f"budget=${request.cost_budget}")
        if request.preferred_provider:
            reasons.append(f"provider={request.preferred_provider}")
        if request.required_tags:
            reasons.append(f"tags={request.required_tags}")

        if not within_budget:
            reason = (
                f"WARNING: No model within budget ${request.cost_budget}. "
                f"Selected cheapest capable model. "
                f"Criteria: {', '.join(reasons)}. "
                f"Estimated cost ${cost:.6f} exceeds budget."
            )
        else:
            reason = (
                f"Selected for {', '.join(reasons)}. "
                f"Tier={chosen.capability_tier}, "