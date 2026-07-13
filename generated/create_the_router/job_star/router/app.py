"""
Job-Star Router API.

Exposes a routing service that picks the right AI model based on:
  - task complexity (1-10)
  - urgency (now | soon | later)
  - cost budget (cents per call or per session)
  - model availability (live health checks via LiteLLM)

The router returns a routing decision and can optionally proxy the
completion through LiteLLM so callers get a single integration point.
"""

from __future__ import annotations

import os
import time
import logging
from typing import Optional, Literal, Any
from dataclasses import dataclass, field, asdict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

# LiteLLM is the underlying model gateway.
try:
    import litellm
    litellm.drop_params = True  # tolerate provider-specific quirks
except ImportError as exc:  # pragma: no cover
    raise RuntimeError("litellm is required: pip install litellm") from exc

logger = logging.getLogger("job_star.router")
logging.basicConfig(level=os.getenv("JOB_STAR_LOG_LEVEL", "INFO"))

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# Model catalog. Each entry maps a logical tier to a LiteLLM model string.
# Tiers are ordered from cheapest/simplest to most capable/expensive.
DEFAULT_MODEL_CATALOG: dict[str, dict[str, Any]] = {
    "nano": {
        "model": "groq/llama-3.1-8b-instant",
        "cost_per_1k_input": 0.00005,
        "cost_per_1k_output": 0.00008,
        "complexity_floor": 1,
        "complexity_ceil": 3,
        "context_window": 128_000,
    },
    "micro": {
        "model": "openai/gpt-4o-mini",
        "cost_per_1k_input": 0.00015,
        "cost_per_1k_output": 0.0006,
        "complexity_floor": 3,
        "complexity_ceil": 5,
        "context_window": 128_000,
    },
    "standard": {
        "model": "anthropic/claude-3-5-sonnet-20240620",
        "cost_per_1k_input": 0.003,
        "cost_per_1k_output": 0.015,
        "complexity_floor": 5,
        "complexity_ceil": 7,
        "context_window": 200_000,
    },
    "heavy": {
        "model": "openai/gpt-4o",
        "cost_per_1k_input": 0.005,
        "cost_per_1k_output": 0.015,
        "complexity_floor": 7,
        "complexity_ceil": 10,
        "context_window": 128_000,
    },
}

# Urgency shifts the tier selection: urgent tasks prefer faster (lower) tiers
# when the complexity band allows it; non-urgent tasks can afford the heavier
# tier for higher quality.
URGENCY_BIAS: dict[str, int] = {
    "now": -1,   # prefer faster/cheaper within band
    "soon": 0,   # neutral
    "later": 1,  # prefer higher quality within band
}

# Health-check cache (seconds). Avoids hammering providers on every request.
HEALTH_TTL = int(os.getenv("JOB_STAR_HEALTH_TTL", "60"))


# --------------------------------------------------------------------------- #
# Router core
# --------------------------------------------------------------------------- #

@dataclass
class RoutingDecision:
    tier: str
    model: str
    rationale: str
    estimated_cost_cents: float
    alternatives: list[str] = field(default_factory=list)
    routed_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class Router:
    """Picks a model tier based on task signals and live availability."""

    def __init__(
        self,
        catalog: Optional[dict[str, dict[str, Any]]] = None,
        cost_budget_cents: float = 5.0,
    ) -> None:
        self.catalog = catalog or DEFAULT_MODEL_CATALOG
        self.default_budget = cost_budget_cents
        self._tiers = list(self.catalog.keys())
        self._health: dict[str, tuple[bool, float]] = {}

    # -- public API --------------------------------------------------------- #

    def route(
        self,
        complexity: int,
        urgency: Literal["now", "soon", "later"] = "soon",
        cost_budget_cents: Optional[float] = None,
        prefer_quality: bool = False,
    ) -> RoutingDecision:
        budget = cost_budget_cents if cost_budget_cents is not None else self.default_budget
        bias = URGENCY_BIAS.get(urgency, 0)
        if prefer_quality:
            bias += 1

        # Find candidate tiers whose complexity band contains the task.
        candidates = [
            name for name, cfg in self.catalog.items()
            if cfg["complexity_floor"] <= complexity <= cfg["complexity_ceil"]
        ]
        if not candidates:
            # Fall back to nearest tier by distance to band midpoint.
            def band_distance(name: str) -> float:
                cfg = self.catalog[name]
                mid = (cfg["complexity_floor"] + cfg["complexity_ceil"]) / 2
                return abs(mid - complexity)
            candidates = sorted(self._tiers, key=band_distance)

        # Apply urgency/quality bias to pick within candidates.
        ordered = self._apply_bias(candidates, bias)

        # Filter by budget and availability.
        chosen: Optional[str] = None
        rationale_parts: list[str] = []
        for name in ordered:
            cfg = self.catalog[name]
            est_cost = self._estimate_cost_cents(cfg)
            if est_cost > budget:
                rationale_parts.append(f"{name} over budget ({est_cost:.4f}c > {budget:.4f}c)")
                continue
            if not self._is_available(name):
                rationale_parts.append(f"{name} unavailable")
                continue
            chosen = name
            break

        if chosen is None:
            # Last resort: cheapest available regardless of budget.
            chosen = next(
                (n for n in self._tiers if self._is_available(n)),
                self._tiers[0],
            )
            rationale_parts.append(f"budget/availability exhausted; fell back to {chosen}")

        cfg = self.catalog[chosen]
        est = self._estimate_cost_cents(cfg)
        alts = [n for n in ordered if n != chosen and self._is_available(n)][:3]
        rationale = (
            f"complexity={complexity}, urgency={urgency}, bias={bias}; "
            + ("; ".join(rationale_parts) if rationale_parts else f"selected {chosen}")
        )
        return RoutingDecision(
            tier=chosen,
            model=cfg["model"],
            rationale=rationale,
            estimated_cost_cents=est,
            alternatives=alts,
        )

    def complete(
        self,
        decision: RoutingDecision,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Execute a completion via LiteLLM using the routed model."""
        try:
            response = litellm.completion(
                model=decision.model,
                messages=messages,
                **kwargs,
            )
            return {
                "ok": True,
                "model": decision.model,
                "tier": decision.tier,
                "content": response.choices[0].message.content,
                "usage": getattr(response, "usage", None),
                "raw": str(response),
            }
        except Exception as exc:  # noqa: BLE001
            logger.exception("LiteLLM completion failed for %s", decision.model)
            return {"ok": False, "error": str(exc), "model": decision.model, "tier": decision.tier}

    # -- internals ---------------------------------------------------------- #

    def _apply_bias(self, candidates: list[str], bias: int) -> list[str]:
        """Reorder candidate tiers by applying an integer bias.

        Positive bias shifts toward heavier tiers; negative toward lighter.
        """
        indexed = {name: i for i, name in enumerate(self._tiers)}
        # Sort candidates by their position in the global tier order, then
        # apply bias by shifting the sort key.
        def key(name: str) -> int:
            return indexed[name] - bias
        return sorted(candidates, key=key)

    def _estimate_cost_cents(self, cfg: dict[str, Any]) -> float:
        # Assume a nominal 800 input / 200 output tokens for estimation.
        in_tok = 800
        out_tok = 200
        cost_usd = (
            cfg["cost_per_1k_input"] * (in_tok / 1000)
            + cfg["cost_per_1k_output"] * (out_tok / 1000)
        )
        return round(cost_usd * 100, 4)

    def _is_available(self, tier: str) -> bool:
        now = time.time()
        cached = self._health.get(tier)
        if cached and (now - cached[1]) < HEALTH_TTL:
            return cached[0]
        # Lightweight availability check: we treat presence of required env
        # keys as a proxy. A real deployment would ping the provider.
        cfg = self.catalog.get(tier)
        if not cfg:
            return False
        provider = cfg["model"].split("/", 1)[0]
        required_env = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "groq": "GROQ_API_KEY",
        }.get(provider)
        available = bool(not required_env or os.getenv(required_env))
        self._health[tier] = (available, now)
        return available


# --------------------------------------------------------------------------- #
# API models
# --------------------------------------------------------------------------- #

class RouteRequest(BaseModel):
    complexity: int = Field(..., ge=1, le=10, description="Task complexity 1 (trivial) to 10 (very hard).")
    urgency: Literal["now", "soon", "later"] = Field("soon", description="How soon the result is needed.")
    cost_budget_cents: Optional[float] = Field(None, ge=0, description="Max cost in cents for this call.")
    prefer_quality: bool = Field(False, description="Bias toward higher-quality tier when ambiguous.")
    domain: Optional[str] = Field(None, description="Logical domain hint (e.g. 'meta', 'ops'). Not yet used for routing.")

    @field_validator("complexity")
    @classmethod
    def _clamp_complexity(cls, v: int) -> int:
        return max(1, min(10, v))


class RouteResponse(BaseModel):
    tier: str
    model: str
    rationale: str
    estimated_cost_cents: float
    alternatives: list[str]
    routed_at: float


class CompleteRequest(BaseModel):
    complexity: int = Field(..., ge=1, le=10)
    urgency: Literal["now", "soon", "later"] = "soon"
    cost_budget_cents: Optional[float] = None
    prefer_quality: bool = False
    domain: Optional[str] = None
    messages: list[dict[str, str]] = Field(..., min_length=1)
    params: dict[str, Any] = Field(default_factory=dict, description="Extra LiteLLM kwargs (temperature, etc.).")


class CompleteResponse(BaseModel):
    ok: bool
    tier: Optional[str] = None
    model: Optional[str] = None
    content: Optional[str] = None
    usage: Optional[Any] = None
    error: Optional[str] = None
    decision: Optional[RouteResponse] = None


class HealthResponse(BaseModel):
    status: str
    tiers: dict[str, bool]


# --------------------------------------------------------------------------- #
# App
# --------------------------------------------------------------------------- #

def create_app(router: Optional[Router] = None) -> FastAPI:
    app = FastAPI(title="Job-Star Router", version="0.1.0")
    state_router = router or Router()

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error")
        return JSONResponse(status_code=500, content={"error": str(exc)})

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        tiers = {name: state_router._is_available(name) for name in state_router._tiers}
        overall = "ok" if any(tiers.values()) else "degraded"
        return HealthResponse(status=overall, tiers=tiers)

    @app.get("/tiers", tags=["meta"])
    async def list_tiers() -> dict:
        return {name: {k: v for k, v in cfg.items()} for name, cfg in state_router.catalog.items()}

    @app.post("/route", response_model=RouteResponse, tags=["routing"])
    async def route(req: RouteRequest) -> RouteResponse:
        decision = state_router.route(
            complexity=req.complexity,
            urgency=req.urgency,
            cost_budget_cents=req.cost_budget_cents,
            prefer_quality=req.prefer_quality,
        )
        return RouteResponse(**decision.to_dict())

    @app.post("/complete", response_model=CompleteResponse, tags=["routing"])
    async def complete(req: CompleteRequest) -> CompleteResponse:
        decision = state_router.route(
            complexity=req.complexity,
            urgency=req.urgency,
            cost_budget_cents=req.cost_budget_cents,
            prefer_quality=req.prefer_quality,
        )
        result = state_router.complete(decision, req.messages, **req.params)
        return CompleteResponse(
            ok=result.get("ok", False),
            tier=result.get("tier"),
            model=result.get("model"),
            content=result.get("content"),
            usage=result.get("usage"),
            error=result.get("error"),
            decision=RouteResponse(**decision.to_dict()),
        )

    # Expose for tests / direct access.
    app.state.router = state_router
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "job_star.router.app:app",
        host=os.getenv("JOB_STAR_ROUTER_HOST", "127.0.0.1"),
        port=int(os.getenv("JOB_STAR_ROUTER_PORT", "8100")),
        reload=False,
    )
