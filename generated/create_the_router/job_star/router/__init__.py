"""
Job-Star Router Package
=======================
AI model routing service that picks the right model based on:
- Task complexity
- Urgency
- Cost budget
- Model availability

Built on LiteLLM for unified provider access.
"""

from job_star.router.model_registry import (
    ModelTier,
    TaskType,
    ModelInfo,
    get_model,
    get_all_models,
    get_enabled_models,
    get_models_by_tier,
    get_models_for_task,
    get_cheapest_model_for_task,
    get_fastest_model_for_task,
    get_best_model_for_task,
    set_model_enabled,
    list_models_summary,
)

__all__ = [
    "ModelTier",
    "TaskType",
    "ModelInfo",
    "get_model",
    "get_all_models",
    "get_enabled_models",
    "get_models_by_tier",
    "get_models_for_task",
    "get_cheapest_model_for_task",
    "get_fastest_model_for_task",
    "get_best_model_for_task",
    "set_model_enabled",
    "list_models_summary",
]


// --- DUPLICATE BLOCK ---

"""Job-Star routing service."""

from .app import Router, RoutingDecision, create_app, app, DEFAULT_MODEL_CATALOG

__all__ = ["Router", "RoutingDecision", "create_app", "app", "DEFAULT_MODEL_CATALOG"]


// --- DUPLICATE BLOCK ---

"""Job-Star Router package — model selection via LiteLLM."""

from job_star.router.strategy import (
    LatencyTier,
    ModelEntry,
    RoutingDecision,
    RoutingRequest,
    RoutingStrategyEngine,
    TaskType,
    Urgency,
    route_request,
)

__all__ = [
    "LatencyTier",
    "ModelEntry",
    "RoutingDecision",
    "RoutingRequest",
    "RoutingStrategyEngine",
    "TaskType",
    "Urgency",
    "route_request",
]


// --- DUPLICATE BLOCK ---

"""
Job-Star Router Package
=======================
AI model routing service that picks the right model based on:
- Task complexity
- Urgency
- Cost budget
- Model availability

Built on LiteLLM for unified provider access.
"""

from job_star.router.model_registry import (
    ModelTier,
    TaskType,
    ModelInfo,
    get_model,
    get_all_models,
    get_enabled_models,
    get_models_by_tier,
    get_models_for_task,
    get_cheapest_model_for_task,
    get_fastest_model_for_task,
    get_best_model_for_task,
    set_model_enabled,
    list_models_summary,
)

__all__ = [
    "ModelTier",
    "TaskType",
    "ModelInfo",
    "get_model",
    "get_all_models",
    "get_enabled_models",
    "get_models_by_tier",
    "get_models_for_task",
    "get_cheapest_model_for_task",
    "get_fastest_model_for_task",
    "get_best_model_for_task",
    "set_model_enabled",
    "list_models_summary",
]


// --- DUPLICATE BLOCK ---

"""Job-Star routing service."""

from .app import Router, RoutingDecision, create_app, app, DEFAULT_MODEL_CATALOG

__all__ = ["Router", "RoutingDecision", "create_app", "app", "DEFAULT_MODEL_CATALOG"]


// --- DUPLICATE BLOCK ---

"""Job-Star Router package — model selection via LiteLLM."""

from job_star.router.strategy import (
    LatencyTier,
    ModelEntry,
    RoutingDecision,
    RoutingRequest,
    RoutingStrategyEngine,
    TaskType,
    Urgency,
    route_request,
)

__all__ = [
    "LatencyTier",
    "ModelEntry",
    "RoutingDecision",
    "RoutingRequest",
    "RoutingStrategyEngine",
    "TaskType",
    "Urgency",
    "route_request",
]
