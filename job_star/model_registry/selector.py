"""
Model selection algorithm.

Given (task_type, volume, constraints) → return best model(s) with fallbacks.

Selection logic:
  1. Filter by task_score > 0 (model can do this task)
  2. Filter by cost tier (free only for opportunistic work)
  3. Filter by rate limits (RPD sufficient for expected volume)
  4. Sort by task_score desc, then by rate limits desc
  5. Return top 3 with fallback chain
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from job_star.model_registry.loader import get_registry
from job_star.model_registry.step_mapping import infer_task_type

logger = logging.getLogger("job_star.model_registry.selector")

# Task categories in the registry
TASK_CATEGORIES = [
    "coding",
    "reasoning",
    "math",
    "writing",
    "analysis",
    "vision",
    "tools",
    "general",
]

# Cost tiers in priority order (lower = cheaper)
COST_TIER_PRIORITY = {
    "free": 0,
    "near-free": 1,
    "cheap": 2,
    "standard": 3,
    "premium": 4,
}


def select_models(
    task_type: str,
    volume: int = 100,
    constraints: Optional[dict[str, Any]] = None,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """
    Select the best models for a given task.

    Parameters
    ----------
    task_type : str
        One of the TASK_CATEGORIES (coding, reasoning, math, writing,
        analysis, vision, tools, general).
    volume : int
        Expected requests per day (RPD). Models with RPD < volume are
        filtered out (unless constraints allow it).
    constraints : dict, optional
        Additional constraints:
          - cost_tier: str or list — only include these tiers
          - min_context: int — minimum context window in tokens
          - platform: str or list — only these platforms
          - allow_near_free: bool — include near-free tier (default False)
    top_k : int
        Number of models to return (default 3).

    Returns
    -------
    list of dict
        Top-k models, each enriched with the matched task_score.
    """
    constraints = constraints or {}
    registry = get_registry()

    if not registry:
        logger.warning("Model registry is empty — cannot select models.")
        return []

    # Normalize task_type
    task_type = task_type.lower().strip()
    if task_type not in TASK_CATEGORIES:
        logger.info("Unknown task_type '%s' — defaulting to 'general'", task_type)
        task_type = "general"

    # --- Step 1: Filter by task_score > 0 ---
    candidates = []
    for model in registry:
        scores = model.get("task_scores") or {}
        score = scores.get(task_type, 0)
        if score > 0:
            enriched = dict(model)
            enriched["task_score"] = score
            candidates.append(enriched)

    if not candidates:
        logger.info("No models with task_score > 0 for '%s'", task_type)
        return []

    # --- Step 2: Filter by cost tier ---
    cost_tier_filter = constraints.get("cost_tier")
    allow_near_free = constraints.get("allow_near_free", False)

    if cost_tier_filter:
        if isinstance(cost_tier_filter, str):
            allowed_tiers = {cost_tier_filter}
        else:
            allowed_tiers = set(cost_tier_filter)
    else:
        # Default: free only (add near-free if allowed)
        allowed_tiers = {"free"}
        if allow_near_free:
            allowed_tiers.add("near-free")

    candidates = [
        m for m in candidates
        if m.get("cost_tier", "unknown") in allowed_tiers
    ]

    if not candidates:
        logger.info(
            "No models after cost tier filter (allowed: %s)", allowed_tiers
        )
        return []

    # --- Step 3: Filter by rate limits (RPD) ---
    min_rpd = volume if not constraints.get("ignore_rate_limits") else 0

    def get_rpd(model: dict) -> int:
        rate_limits = model.get("rate_limits") or {}
        rpd = rate_limits.get("rpd", 0)
        if rpd in (None, "unlimited", "Unknown"):
            return 999999  # treat as very high
        try:
            return int(rpd)
        except (ValueError, TypeError):
            return 0

    if min_rpd > 0:
        candidates = [m for m in candidates if get_rpd(m) >= min_rpd]

    if not candidates:
        logger.info("No models after rate limit filter (min RPD: %d)", min_rpd)
        return []

    # --- Step 4: Filter by platform (if specified) ---
    platform_filter = constraints.get("platform")
    if platform_filter:
        if isinstance(platform_filter, str):
            platforms = {platform_filter}
        else:
            platforms = set(platform_filter)
        candidates = [
            m for m in candidates
            if m.get("platform", "unknown") in platforms
        ]

    # --- Step 5: Filter by min context (if specified) ---
    min_context = constraints.get("min_context")
    if min_context:
        def get_context(model: dict) -> int:
            ctx = model.get("context_window", 0)
            if ctx in (None, "Unknown"):
                return 0
            try:
                return int(ctx)
            except (ValueError, TypeError):
                return 0
        candidates = [m for m in candidates if get_context(m) >= min_context]

    # --- Step 6: Sort by task_score desc, then rate limits desc ---
    candidates.sort(
        key=lambda m: (
            -m.get("task_score", 0),
            -get_rpd(m),
        )
    )

    # --- Step 7: Return top_k ---
    return candidates[:top_k]


def select_for_step(
    step: dict[str, Any],
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """
    Select the best models for a given step.

    Infers the task type from step metadata or title, then calls
    select_models with appropriate defaults.

    Parameters
    ----------
    step : dict
        The step dict. Should have a title, description, and optionally
        metadata with task_type or step_type.
    top_k : int
        Number of models to return.

    Returns
    -------
    list of dict
        Top-k models for this step.
    """
    meta = step.get("metadata") or {}
    task_type = infer_task_type(step)
    volume = meta.get("volume", 100)
    constraints = meta.get("constraints") or {}

    return select_models(
        task_type=task_type,
        volume=volume,
        constraints=constraints,
        top_k=top_k,
    )