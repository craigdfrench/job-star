"""
Integration helpers for executors and the worker loop.

Provides a convenience function `execute_with_fallback` that wraps
a caller-provided model call function with the fallback chain logic.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from job_star.model_registry.fallback import (
    ModelFallbackChain,
    ModelFallbackExhaustedError,
)

logger = logging.getLogger("job_star.model_registry.integration")


def execute_with_fallback(
    step: dict[str, Any],
    call_fn: Callable[[dict[str, Any]], Any],
    on_rotation: Optional[Callable[[dict, dict, Exception], None]] = None,
) -> tuple[Any, dict[str, Any]]:
    """
    Execute a model call with automatic fallback on failure.

    Parameters
    ----------
    step : dict
        The step dict (must contain metadata with fallback_models or
        enough info for the selector to compute them).
    call_fn : callable
        A function that takes a model_info dict and returns a result.
        Should raise an exception on failure (rate limit, timeout, error).
    on_rotation : callable, optional
        Callback invoked when rotating from a failed model to the next.
        Receives (failed_model_info, next_model_info, error).

    Returns
    -------
    tuple
        (result, model_info_that_succeeded)

    Raises
    ------
    ModelFallbackExhaustedError
        If all models in the chain fail.
    """
    chain = ModelFallbackChain.from_step(step)

    if not chain.models:
        raise ModelFallbackExhaustedError(
            f"No models available for step {step.get('id')}",
            chain=chain,
        )

    last_error: Exception | None = None
    for i, model_info in enumerate(chain):
        try:
            result = call_fn(model_info)
            chain.mark_success(model_info)
            return result, model_info
        except Exception as exc:
            chain.mark_failure(model_info, exc)
            last_error = exc
            # Notify rotation callback if there's a next model
            if i + 1 < len(chain.models) and on_rotation:
                next_model = chain.models[i + 1]
                try:
                    on_rotation(model_info, next_model, exc)
                except Exception as cb_err:
                    logger.warning("on_rotation callback failed: %s", cb_err)
            continue

    # All models failed
    raise chain.final_error()


def build_fallback_metadata(
    step: dict[str, Any],
    selected_models: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Build step metadata that includes the fallback model chain.

    Used by the orchestrator when planning steps to embed the fallback
    chain into the step so executors can use it at runtime.
    """
    meta = dict(step.get("metadata") or {})
    if selected_models:
        meta["model"] = selected_models[0].get("model") or selected_models[0].get("name")
        meta["platform"] = selected_models[0].get("platform", "unknown")
        meta["task_type"] = meta.get("task_type") or step.get("task_type")
        meta["fallback_models"] = [
            {
                "model": m.get("model") or m.get("name"),
                "platform": m.get("platform", "unknown"),
                "task_score": m.get("task_score"),
                "cost_tier": m.get("cost_tier"),
            }
            for m in selected_models[:3]
        ]
    return meta