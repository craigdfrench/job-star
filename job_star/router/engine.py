"""Router: selects the best AI model for a task.

Routes based on:
- Urgency (imperative → best model, idle → free model)
- Complexity (trivial → cheap, complex → capable)
- Cost tier (never silently fall back to expensive models)
- Model availability (real-time from gatehouse monitor)

Uses the gatehouse-ai gateway for actual model access. The static registry is
kept as fallback/defaults; the live model list from the gateway is authoritative.

Cost protection:
- FREE and CHEAP models are eligible for automatic routing and fallback.
- STANDARD and PREMIUM models are only used when explicitly requested by the
  user (model_override or allow_expensive=True).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..models import RoutingDecision, Urgency
from ..gatehouse import GatewayMonitor


# ============================================================================
# Static registry — fallback defaults for offline/no-monitor scenarios
# ============================================================================

@dataclass
class ModelInfo:
    """Information about an available AI model."""
    name: str  # model identifier for the API
    provider: str
    complexity_ceiling: float  # 0-1, max complexity this model can handle
    cost_per_1k_input: float  # USD
    cost_per_1k_output: float  # USD
    availability: float  # 0-1, uptime estimate
    context_window: int  # max tokens
    tier: str = "free"  # free, cheap, standard, premium
    capabilities: list[str] = field(default_factory=list)  # e.g., ["text", "vision", "code"]

    @property
    def is_free(self) -> bool:
        return self.cost_per_1k_input == 0 and self.cost_per_1k_output == 0

    @property
    def is_allowed_for_routing(self) -> bool:
        """Whether this model can be selected automatically."""
        return self.tier in ("free", "quota_free", "cheap")


MODEL_REGISTRY: list[ModelInfo] = [
    ModelInfo(
        name="ollama/glm-5.2",
        provider="ollama",
        complexity_ceiling=0.75,
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        availability=0.90,
        context_window=128_000,
        tier="quota_free",
        capabilities=["text", "code"],
    ),
    ModelInfo(
        name="glm-5-2",
        provider="z-ai",
        complexity_ceiling=0.95,
        cost_per_1k_input=0.001,
        cost_per_1k_output=0.004,
        availability=0.95,
        context_window=1_000_000,
        tier="quota_free",
        capabilities=["text", "code", "reasoning"],
    ),
    ModelInfo(
        name="ollama/gemini-3-flash-preview",
        provider="ollama",
        complexity_ceiling=0.70,
        cost_per_1k_input=0.0,
        cost_per_1k_output=0.0,
        availability=0.85,
        context_window=128_000,
        tier="quota_free",
        capabilities=["text", "vision", "code"],
    ),
]


# ============================================================================
# Live model discovery from gateway
# ============================================================================

async def _build_live_candidates(
    gateway_monitor: GatewayMonitor,
    requires_vision: bool,
    prefer_free: bool,
    allow_expensive: bool,
) -> list[ModelInfo]:
    """Build candidate list from the live gateway model list."""
    gateway_models = await gateway_monitor.refresh()
    candidates: list[ModelInfo] = []

    for model_id, model in gateway_models.items():
        caps = model.get("capabilities", {})
        if requires_vision and not caps.get("vision"):
            continue

        # Skip expensive models unless explicitly allowed
        tier = gateway_monitor.tier(model_id)
        if not allow_expensive and tier not in ("free", "quota_free", "cheap"):
            continue

        if not gateway_monitor.is_available(model_id):
            continue

        pricing = model.get("pricing", {})
        cost_input = pricing.get("input", 0.0) or pricing.get("input_per_1k", 0.0)
        cost_output = pricing.get("output", 0.0) or pricing.get("output_per_1k", 0.0)
        is_free = cost_input == 0 and cost_output == 0

        if prefer_free and not is_free:
            continue

        # Complexity ceiling derived from context window / reasoning / capabilities
        context = model.get("context_length", 0)
        if caps.get("reasoning"):
            complexity = 0.95
        elif caps.get("structured_output"):
            complexity = 0.85
        elif context > 100_000:
            complexity = 0.80
        elif context > 50_000:
            complexity = 0.70
        else:
            complexity = 0.60

        provider = model.get("provider", "unknown")
        candidates.append(ModelInfo(
            name=model_id,
            provider=provider,
            complexity_ceiling=complexity,
            cost_per_1k_input=float(cost_input),
            cost_per_1k_output=float(cost_output),
            availability=model.get("availability", 0.95),
            context_window=context,
            tier=tier.value,
            capabilities=[k for k, v in caps.items() if v],
        ))

    return candidates


# ============================================================================
# Routing logic
# ============================================================================

def _estimate_complexity(request_type: str, description: str) -> float:
    """Estimate task complexity on a 0-1 scale."""
    base = {
        "bug": 0.5,
        "feature": 0.6,
        "refactor": 0.7,
        "question": 0.3,
        "chore": 0.2,
        "docs": 0.3,
        "research": 0.8,
        "code": 0.7,          # code editing/generation
        "code_review": 0.5,
        "planning": 0.4,
    }.get(request_type, 0.5)

    desc_len = len(description)
    if desc_len > 2000:
        base = min(1.0, base + 0.15)
    elif desc_len > 500:
        base = min(1.0, base + 0.05)

    return base


def _complexity_bucket(score: float) -> str:
    if score < 0.2:
        return "trivial"
    elif score < 0.4:
        return "simple"
    elif score < 0.7:
        return "moderate"
    else:
        return "complex"


def _score_model_for_urgency(
    model: ModelInfo,
    urgency: Urgency,
    complexity: float,
    prefer_free: bool,
) -> float:
    """Score a model for a given urgency. Higher is better."""
    if urgency == Urgency.IMPERATIVE:
        return model.complexity_ceiling * 1000 - model.cost_per_1k_input * 10

    elif urgency == Urgency.SOON:
        if model.complexity_ceiling < complexity:
            return model.complexity_ceiling * 100 - 1000
        cost = model.cost_per_1k_input + model.cost_per_1k_output
        score = model.complexity_ceiling * 100
        if prefer_free and model.is_free:
            score += 1000
        else:
            score -= cost * 1000
        return score

    else:  # idle-opportunistic or timed
        cost = model.cost_per_1k_input + model.cost_per_1k_output
        if model.is_free:
            score = 1000 + model.complexity_ceiling * 100
        else:
            score = -cost * 1000 + model.complexity_ceiling * 10
        return score


async def route(
    urgency: Urgency,
    request_type: str = "feature",
    description: str = "",
    requires_vision: bool = False,
    prefer_free: bool = False,
    model_override: str | None = None,
    allow_expensive: bool = False,
    gateway_monitor: GatewayMonitor | None = None,
) -> RoutingDecision:
    """Select the best AI model for a task, respecting gateway availability and cost tiers.

    Args:
        urgency: How urgent the task is.
        request_type: Type of request (bug, feature, etc.)
        description: Task description (for complexity estimation).
        requires_vision: Whether the task needs image understanding.
        prefer_free: Whether to prefer free models.
        model_override: Explicitly use this model. If unavailable, falls back to
            a model of the same tier or cheaper. If allow_expensive is False, the
            fallback will never be a premium/standard model.
        allow_expensive: Whether premium/standard models are eligible for routing.
        gateway_monitor: Optional monitor for real-time model availability/quota.

    Returns:
        RoutingDecision with the selected model and rationale.
    """
    complexity = _estimate_complexity(request_type, description)
    bucket = _complexity_bucket(complexity)

    if gateway_monitor:
        await gateway_monitor.refresh()

    # Model override: use it if available and allowed, otherwise fall back
    if model_override:
        if not allow_expensive and gateway_monitor and gateway_monitor.is_expensive(model_override):
            return RoutingDecision(
                model="",
                provider="none",
                reason=f"Override model {model_override} is expensive and allow_expensive=False",
                complexity=bucket,
            )

        if gateway_monitor and not gateway_monitor.is_available(model_override):
            fallback = gateway_monitor.pick_fallback(
                model_override,
                required_capability="vision" if requires_vision else None,
                prefer_free=prefer_free,
                allow_expensive=allow_expensive,
            )
            if fallback:
                return RoutingDecision(
                    model=fallback,
                    provider="fallback",
                    reason=f"Override model {model_override} unavailable → {fallback} fallback",
                    complexity=bucket,
                )
            else:
                return RoutingDecision(
                    model="",
                    provider="none",
                    reason=f"Override model {model_override} unavailable and no allowed fallback",
                    complexity=bucket,
                )

        return RoutingDecision(
            model=model_override,
            provider="override",
            reason="Explicitly specified by user",
            complexity=bucket,
        )

    # Build candidates from live gateway if available, else static registry
    if gateway_monitor:
        candidates = await _build_live_candidates(
            gateway_monitor, requires_vision, prefer_free, allow_expensive
        )
        fallback = gateway_monitor.pick_fallback(
            "ollama/glm-5.2",
            required_capability="vision" if requires_vision else None,
            prefer_free=prefer_free,
            allow_expensive=allow_expensive,
        )
    else:
        candidates = [m for m in MODEL_REGISTRY if m.is_allowed_for_routing]
        if requires_vision:
            candidates = [m for m in candidates if "vision" in m.capabilities]
        fallback = None

    if not candidates:
        if fallback:
            return RoutingDecision(
                model=fallback,
                provider="fallback",
                reason=f"No allowed model available → fallback (complexity={bucket})",
                complexity=bucket,
            )
        # If we have a gateway monitor and it returned no candidates, the
        # gateway is likely down or all models are unavailable. Do NOT fall
        # back to the static MODEL_REGISTRY — those models are also served
        # through the gateway and would fail at execution time. Fail fast.
        if gateway_monitor:
            gateway_models = gateway_monitor._gateway_models
            if not gateway_models:
                return RoutingDecision(
                    model="",
                    provider="none",
                    reason="Gateway unreachable and no live model list available — failing fast instead of returning an unexecutable static fallback",
                    complexity=bucket,
                )
            return RoutingDecision(
                model="",
                provider="none",
                reason="No allowed model available (all in quota hold or circuit open) and no fallback configured",
                complexity=bucket,
            )
        # No gateway monitor — use static registry as last resort (offline mode)
        candidates = [m for m in MODEL_REGISTRY if m.is_allowed_for_routing]
        if not candidates:
            return RoutingDecision(
                model="",
                provider="none",
                reason="No allowed model available and no fallback configured",
                complexity=bucket,
            )

    # Score and select best model
    scored = [
        (m, _score_model_for_urgency(m, urgency, complexity, prefer_free))
        for m in candidates
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    best = scored[0][0]

    reason = f"{urgency.value} urgency → {best.name} ({best.tier}, complexity={bucket}, context={best.context_window})"

    # Estimate cost
    est_input_tokens = min(2000, len(description) + 500)
    est_output_tokens = 1000
    est_cost = (est_input_tokens / 1000 * best.cost_per_1k_input +
                est_output_tokens / 1000 * best.cost_per_1k_output)

    return RoutingDecision(
        model=best.name,
        provider=best.provider,
        reason=reason,
        estimated_cost=est_cost,
        complexity=bucket,
    )
