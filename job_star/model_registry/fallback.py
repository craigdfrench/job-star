"""
Runtime model fallback logic.

When a step's primary model fails (rate limit hit, timeout, error),
this module provides the logic to rotate to the next fallback in the
chain returned by the selector.

Usage from executors / worker loop:

    from job_star.model_registry.fallback import ModelFallbackChain

    chain = ModelFallbackChain.from_step(step)
    for model_info in chain:
        try:
            result = call_model(model_info, prompt)
            chain.mark_success(model_info)
            break
        except ModelError as exc:
            chain.mark_failure(model_info, exc)
            continue
    else:
        raise chain.final_error()
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger("job_star.model_registry.fallback")


@dataclass
class FallbackAttempt:
    """Record of a single model attempt within the fallback chain."""

    model_name: str
    platform: str
    status: str = "pending"  # pending, success, failed
    error: Optional[str] = None
    error_type: Optional[str] = None


@dataclass
class ModelFallbackChain:
    """
    An ordered list of models to try for a single step execution.

    The chain is built from the selector's top-3 results (or from
    step metadata if the selector already stored fallbacks there).
    """

    models: list[dict[str, Any]] = field(default_factory=list)
    attempts: list[FallbackAttempt] = field(default_factory=list)
    step_id: Optional[str] = None
    goal_id: Optional[str] = None
    task_type: Optional[str] = None

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_step(cls, step: dict[str, Any]) -> "ModelFallbackChain":
        """
        Build a fallback chain from a step dict.

        The step may carry fallback models in several places:
          - step['metadata']['fallback_models']  (list of model dicts)
          - step['result']['fallback_models']    (same, if re-planned)
          - step['metadata']['model_chain']      (alias)

        If no fallbacks are stored, we try to compute them on the fly
        via the selector.  If that also fails, we return a chain with
        just the step's primary model (or empty).
        """
        meta = step.get("metadata") or {}
        fallback_models: list[dict[str, Any]] = []

        # Try metadata first
        fallback_models = (
            meta.get("fallback_models")
            or meta.get("model_chain")
            or []
        )

        # If still empty, try result (re-planned steps)
        if not fallback_models:
            result = step.get("result") or {}
            fallback_models = (
                result.get("fallback_models")
                or result.get("model_chain")
                or []
            )

        # If still empty, try to compute via selector
        if not fallback_models:
            fallback_models = cls._compute_from_selector(step)

        # Ensure primary model is first
        primary = meta.get("model") or step.get("model")
        if primary:
            # Remove primary if already in list, then prepend
            fallback_models = [
                m for m in fallback_models
                if _model_name(m) != primary
            ]
            fallback_models.insert(0, {"model": primary, "platform": meta.get("platform", "unknown")})

        return cls(
            models=fallback_models[:3],  # top 3 only
            step_id=str(step.get("id")) if step.get("id") else None,
            goal_id=str(step.get("goal_id")) if step.get("goal_id") else None,
            task_type=meta.get("task_type") or step.get("task_type"),
        )

    @staticmethod
    def _compute_from_selector(step: dict[str, Any]) -> list[dict[str, Any]]:
        """Try to compute fallback models using the selector."""
        try:
            from job_star.model_registry.selector import select_models

            meta = step.get("metadata") or {}
            task_type = meta.get("task_type") or step.get("task_type") or "general"
            constraints = meta.get("constraints") or {}
            volume = meta.get("volume") or 100

            results = select_models(
                task_type=task_type,
                volume=volume,
                constraints=constraints,
                top_k=3,
            )
            return [
                {
                    "model": r.get("model") or r.get("name"),
                    "platform": r.get("platform"),
                    "task_score": r.get("task_score"),
                    "cost_tier": r.get("cost_tier"),
                }
                for r in results
            ]
        except Exception as exc:
            logger.debug("Could not compute fallback from selector: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Iteration
    # ------------------------------------------------------------------

    def __iter__(self):
        """Iterate over models, tracking attempts."""
        for model_info in self.models:
            attempt = FallbackAttempt(
                model_name=_model_name(model_info),
                platform=model_info.get("platform", "unknown"),
            )
            self.attempts.append(attempt)
            logger.info(
                "Attempting model %s on %s for step %s (attempt %d/%d)",
                attempt.model_name,
                attempt.platform,
                self.step_id,
                len(self.attempts),
                len(self.models),
            )
            yield model_info

    def __len__(self) -> int:
        return len(self.models)

    # ------------------------------------------------------------------
    # Status tracking
    # ------------------------------------------------------------------

    def mark_success(self, model_info: dict[str, Any]) -> None:
        """Mark the most recent attempt as successful."""
        if self.attempts:
            attempt = self.attempts[-1]
            attempt.status = "success"
            logger.info(
                "Model %s succeeded for step %s",
                attempt.model_name,
                self.step_id,
            )

    def mark_failure(
        self,
        model_info: dict[str, Any],
        error: Exception,
    ) -> None:
        """Mark the most recent attempt as failed and log the rotation."""
        if self.attempts:
            attempt = self.attempts[-1]
            attempt.status = "failed"
            attempt.error = str(error)
            attempt.error_type = type(error).__name__

            remaining = len(self.models) - len(self.attempts)
            if remaining > 0:
                next_model = _model_name(self.models[len(self.attempts)])
                logger.warning(
                    "Model %s failed for step %s (%s: %s). "
                    "Rotating to fallback %s (%d remaining).",
                    attempt.model_name,
                    self.step_id,
                    attempt.error_type,
                    attempt.error,
                    next_model,
                    remaining,
                )
            else:
                logger.error(
                    "Model %s failed for step %s — no more fallbacks.",
                    attempt.model_name,
                    self.step_id,
                )

    # ------------------------------------------------------------------
    # Final error
    # ------------------------------------------------------------------

    def final_error(self) -> Exception:
        """
        Build a clear error message when all models in the chain have failed.

        Includes suggestions for what the user/operator can do.
        """
        failed = [a for a in self.attempts if a.status == "failed"]
        lines = [
            f"All {len(self.models)} model(s) failed for step {self.step_id}.",
            "",
            "Failed models:",
        ]
        for a in failed:
            lines.append(
                f"  • {a.model_name} ({a.platform}): "
                f"{a.error_type}: {a.error}"
            )

        lines.append("")
        lines.append("Suggestions:")
        lines.append("  1. Check rate limits for the affected platforms.")
        lines.append("  2. Update model-registry.json if models have changed.")
        lines.append("  3. Retry the step later when rate limits reset.")
        lines.append("  4. Consider a paid model tier if free-tier limits are consistently hit.")

        return ModelFallbackExhaustedError("\n".join(lines), chain=self)


class ModelFallbackExhaustedError(Exception):
    """Raised when all models in a fallback chain have failed."""

    def __init__(self, message: str, chain: ModelFallbackChain):
        super().__init__(message)
        self.chain = chain
        self.attempts = chain.attempts


def _model_name(model_info: dict[str, Any]) -> str:
    """Extract the model name from a model info dict."""
    return (
        model_info.get("model")
        or model_info.get("name")
        or model_info.get("id")
        or "unknown"
    )