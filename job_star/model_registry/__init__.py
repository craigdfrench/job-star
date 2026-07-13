"""
Model registry package — intelligent model selection from a structured
free-tier model registry.
"""

from job_star.model_registry.loader import load_registry, get_registry
from job_star.model_registry.selector import select_models, select_for_step
from job_star.model_registry.step_mapping import map_step_type, infer_task_type
from job_star.model_registry.fallback import (
    ModelFallbackChain,
    ModelFallbackExhaustedError,
    FallbackAttempt,
)

__all__ = [
    "load_registry",
    "get_registry",
    "select_models",
    "select_for_step",
    "map_step_type",
    "infer_task_type",
    "ModelFallbackChain",
    "ModelFallbackExhaustedError",
    "FallbackAttempt",
]