"""
Step type → task type mapping.

Maps job-star step types (from triage/planning) to the task categories
used in the model registry (coding, reasoning, math, writing, analysis,
vision, tools, general).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("job_star.model_registry.step_mapping")

# Default mapping file location
MAPPING_FILE = os.environ.get(
    "JOB_STAR_STEP_TYPE_MAP",
    str(Path(__file__).parent / "step_type_map.json"),
)

# Fallback mapping if file is not found
DEFAULT_MAPPING = {
    "code": "coding",
    "coding": "coding",
    "implement": "coding",
    "fix": "coding",
    "debug": "coding",
    "refactor": "coding",
    "test": "coding",
    "research": "analysis",
    "investigate": "analysis",
    "analyze": "analysis",
    "survey": "analysis",
    "review": "analysis",
    "write": "writing",
    "draft": "writing",
    "document": "writing",
    "summarize": "writing",
    "summarise": "writing",
    "plan": "reasoning",
    "design": "reasoning",
    "reason": "reasoning",
    "decide": "reasoning",
    "calculate": "math",
    "compute": "math",
    "estimate": "math",
    "describe": "vision",
    "transcribe": "vision",
    "ocr": "vision",
    "image": "vision",
    "screenshot": "vision",
    "tool": "tools",
    "api": "tools",
    "execute": "tools",
    "run": "tools",
    "default": "general",
    "general": "general",
    "misc": "general",
}

_mapping_cache: Optional[dict[str, str]] = None


def _load_mapping() -> dict[str, str]:
    """Load the step type mapping from file, with fallback to defaults."""
    global _mapping_cache
    if _mapping_cache is not None:
        return _mapping_cache

    p = Path(MAPPING_FILE)
    if p.exists():
        try:
            with open(p, "r") as f:
                _mapping_cache = json.load(f)
            logger.info("Loaded step type mapping from %s", MAPPING_FILE)
            return _mapping_cache
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load step type map: %s — using defaults", exc)

    _mapping_cache = DEFAULT_MAPPING
    return _mapping_cache


def map_step_type(step_type: str) -> str:
    """
    Map a step type string to a task category.

    Parameters
    ----------
    step_type : str
        The step type from triage/planning (e.g., "code", "research").

    Returns
    -------
    str
        A task category: coding, reasoning, math, writing, analysis,
        vision, tools, or general.
    """
    mapping = _load_mapping()
    normalized = step_type.lower().strip()

    # Direct lookup
    if normalized in mapping:
        return mapping[normalized]

    # Partial match (step_type contains a keyword)
    for key, value in mapping.items():
        if key in normalized:
            return value

    return mapping.get("default", "general")


def infer_task_type(step: dict[str, Any]) -> str:
    """
    Infer the task type from a step dict.

    Checks in order:
      1. step.metadata.task_type
      2. step.metadata.step_type (mapped)
      3. step.task_type
      4. step.step_type (mapped)
      5. Keyword analysis of step.title + step.description

    Parameters
    ----------
    step : dict
        The step dict.

    Returns
    -------
    str
        A task category.
    """
    meta = step.get("metadata") or {}

    # 1. Explicit task_type in metadata
    if meta.get("task_type"):
        return meta["task_type"]

    # 2. step_type in metadata (needs mapping)
    if meta.get("step_type"):
        return map_step_type(meta["step_type"])

    # 3. Direct task_type on step
    if step.get("task_type"):
        return step["task_type"]

    # 4. step_type on step (needs mapping)
    if step.get("step_type"):
        return map_step_type(step["step_type"])

    # 5. Keyword analysis
    title = (step.get("title") or "").lower()
    description = (step.get("description") or "").lower()
    combined = f"{title} {description}"

    mapping = _load_mapping()
    # Check longer keys first for better matching
    for key in sorted(mapping.keys(), key=len, reverse=True):
        if key == "default":
            continue
        if key in combined:
            return mapping[key]

    return mapping.get("default", "general")