"""
Job-Star router: selects the best AI model for a task based on complexity,
urgency, cost budget, and model availability. Uses LiteLLM for execution.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

try:
    import litellm  # noqa: F401
    _HAS_LITELLM = True
except ImportError:  # pragma: no cover
    _HAS_LITELLM = False

from job_star.router.logging import (
    DecisionLogger,
    ModelCandidate,
    RoutingDecision,
    correlation_id_var,
    get_decision_logger,
    timer_ms,
    utc_now,
)

# ---------------------------------------------------------------------------
# Model registry: capabilities + economics.
# ---------------------------------------------------------------------------
# cost_per_1k_tokens is (input, output) in USD.
MODEL_REGISTRY: list[dict[str, Any]] = [
    {
        "model": "gpt-4o-mini", "provider": "openai",
        "complexity_ceiling": 0.35, "cost_per_1k": (0.00015, 0.0006),
        "availability": 0.99,
    },
    {
        "model": "gpt-4o", "provider": "openai",
        "complexity_ceiling": 0.85, "cost_per_1k": (0.0025, 0.01),
        "availability": 0.98,
    },
    {
        "model": "claude-3-5-sonnet", "provider": "anthropic",
        "complexity_ceiling": 0.9, "cost_per_1k": (0.003, 0.015),
        "availability": 0.97,
    },
    {
        "model": "o1-mini", "provider": "openai",
        "complexity_ceiling": 1.0, "cost_per_1k": (0.003, 0.012),
        "availability": 0.95,
    },
]

URGENCY_WEIGHTS = {"now": 0.5, "soon": 0.3, "whenever": 0.1}
COMPLEXITY_BUCKETS = [
    (0.0, 0.2, "trivial"),
    (0.2, 0.4, "simple"),
    (0.4, 0.7, "moderate"),
    (0.7, 1.01, "complex"),
]


@dataclass
class RoutingRequest:
    task_id: str
    task_description: str
    domain: str = "general"
    urgency: str = "soon"          # now | soon | whenever
    complexity: Optional[float] = None  # 0..1, may be inferred
    cost_budget_usd: float = 0.05
    estimated_input_tokens: int = 1000
    estimated_output_tokens: int = 1000
    metadata: dict[str, Any] = field(default_factory=dict)


def _bucket_complexity(score: float) -> str:
    for lo, hi, label in COMPLEXITY_BUCKETS:
        if lo <= score < hi:
            return label
    return "unknown"


def _estimate_cost(candidate: ModelCandidate, req: RoutingRequest) -> float:
    in_cost = candidate.cost_per_1k_tokens * req.estimated_input_tokens / 1000.0
    out_cost = candidate.cost_per_1k_tokens * req.estimated_output_tokens / 1000.0
    return round(in_cost + out_cost, 6)


def _score_candidate(
    cand: ModelCandidate, req: RoutingRequest, complexity: float
) -> float:
    # Hard filter: must be capable enough for the task complexity.
    if complexity > cand.complexity_score:
        cand.eligible = False
        cand.disqualify_reasons.append(
            f"complexity {complexity:.2f} exceeds ceiling {cand.complexity_score:.2f}"
        )
        return 0.0

    # Hard filter: cost must fit budget.
    est_cost = _estimate_cost(cand, req)
    if est_cost > req.cost_budget_usd:
        cand.eligible = False
        cand.disqualify_reasons.append(
            f"est_cost ${est_cost:.6f} exceeds budget ${req.cost_budget_usd:.6f}"
        )
        return 0.0

    # Composite score: capability headroom + availability - urgency penalty.
    headroom = cand.complexity_score - complexity  # room to spare
    urgency_weight = URGENCY_WEIGHTS.get(req.urgency, 0.2)
    # For urgent tasks, prefer higher availability.
    score = (0.5 * headroom) + (0.3 * cand.availability) + (0.2 * urgency_weight)
    cand.final_score = round(score, 4)
    return cand.final_score


def route(req: RoutingRequest, logger: Optional[DecisionLogger] = None) -> dict[str, Any]:
    """
    Pick the best model for the request and return a routing result dict.
    Always emits a RoutingDecision log.
    """
    start = time.perf_counter()
    logger = logger or get_decision_logger()
    correlation_id = logger.new_correlation_id()
    correlation_id_var.set(correlation_id)

    complexity = req.complexity if req.complexity is not None else 0.5
    complexity_label = _bucket_complexity(complexity)

    candidates: list[ModelCandidate] = []
    for entry in MODEL_REGISTRY:
        in_cost, out_cost = entry["cost_per_1k"]
        # cost_per_1k_tokens stored as input cost for estimate convenience.
        cand = ModelCandidate(
            model=entry["model"],
            provider=entry["provider"],
            complexity_score=entry["complexity_ceiling"],
            urgency_score=URGENCY_WEIGHTS.get(req.urgency, 0.2),
            cost_per_1k_tokens=in_cost,
            availability=entry["availability"],
            eligible=True,
        )
        _score_candidate(cand, req, complexity)
        candidates.append(cand)

    eligible = [c for c in candidates if c.eligible]
    eligible.sort(key=lambda c: c.final_score, reverse=True)
    chosen = eligible[0] if eligible else None

    if chosen:
        est_cost = _estimate_cost(chosen, req)
        rationale = (
            f"Selected {chosen.model} (score {chosen.final_score}) as best fit "
            f"for complexity {complexity_label} ({complexity:.2f}), urgency "
            f"{req.urgency}, within budget ${req.cost_budget_usd:.6f}."
        )
        status = "success"
        selected_model = chosen.model
        selected_provider = chosen.provider
        error = None
    else:
        est_cost = 0.0
        rationale = "No eligible model satisfied complexity and budget constraints."
        status = "no_model"
        selected_model = None
        selected_provider = None
        error = "no_eligible_model"

    decision = RoutingDecision(
        correlation_id=correlation_id,
        timestamp=utc_now(),
        task_id=req.task_id,
        task_description=req.task_description,
        domain=req.domain,
        urgency=req.urgency,
        complexity=complexity_label,
        cost_budget_usd=req.cost_budget_usd,
        input_request={
            "task_id": req.task_id,
            "domain": req.domain,
            "urgency": req.urgency,
            "complexity": complexity,
            "cost_budget_usd": req.cost_budget_usd,
            "estimated_input_tokens": req.estimated_input_tokens,
            "estimated_output_tokens": req.estimated_output_tokens,
            "metadata": req.metadata,
        },
        models_considered=[
            {
                "model": c.model,
                "provider": c.provider,
                "complexity_score": c.complexity_score,
                "availability": c.availability,
                "cost_per_1k_tokens": c.cost_per_1k_tokens,
                "eligible": c.eligible,
                "disqualify_reasons": c.disqualify_reasons,
                "final_score": c.final_score,
            }
            for c in candidates
        ],
        selected_model=selected_model,
        selected_provider=selected_provider,
        rationale=rationale,
        estimated_input_tokens=req.estimated_input_tokens,
        estimated_output_tokens=req.estimated_output_tokens,
        estimated_cost_usd=est_cost,
        routing_time_ms=timer_ms(start),
        status=status,
        error=error,
    )

    logger.log_decision(decision)

    return {
        "correlation_id": correlation_id,
        "model": selected_model,
        "provider": selected_provider,
        "estimated_cost_usd": est_cost,
        "rationale": rationale,
        "status": status,
        "routing_time_ms": decision.routing_time_ms,
    }


def execute(routing_result: dict[str, Any], messages: list[dict[str, str]]) -> dict[str, Any]:
    """
    Execute the selected model via LiteLLM. Returns the completion response.
    Raises RuntimeError if LiteLLM is unavailable or no model was selected.
    """
    if not _HAS_LITELLM:
        raise RuntimeError("LiteLLM is not installed; cannot execute model call.")
    model = routing_result.get("model")
    if not model:
        raise RuntimeError("No model was selected by the router; cannot execute.")
    provider = routing_result.get("provider", "openai")
    litellm_model = f"{provider}/{model}" if provider != "openai" else model
    response = litellm.completion(model=litellm_model, messages=messages)
    return response
