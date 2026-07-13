"""
Model registry loader.

Loads model-registry.json from disk and caches it in memory.
The file is the source of truth — call reload() to pick up changes.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("job_star.model_registry.loader")

# Default location — can be overridden by env var
DEFAULT_REGISTRY_PATH = os.environ.get(
    "JOB_STAR_MODEL_REGISTRY",
    "/home/craig/model-registry.json",
)

_registry_cache: Optional[list[dict[str, Any]]] = None
_registry_path: str = DEFAULT_REGISTRY_PATH


def load_registry(path: Optional[str] = None) -> list[dict[str, Any]]:
    """
    Load the model registry from a JSON file.

    Parameters
    ----------
    path : str, optional
        Path to the registry JSON file. Defaults to the env var
        JOB_STAR_MODEL_REGISTRY or /home/craig/model-registry.json.

    Returns
    -------
    list of dict
        The list of model entries from the registry.
    """
    global _registry_cache, _registry_path

    if path is not None:
        _registry_path = path

    p = Path(_registry_path)
    if not p.exists():
        logger.warning("Model registry not found at %s — returning empty list.", _registry_path)
        _registry_cache = []
        return _registry_cache

    try:
        with open(p, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load model registry from %s: %s", _registry_path, exc)
        _registry_cache = []
        return _registry_cache

    # The registry may be a list or a dict with a "models" key
    if isinstance(data, dict) and "models" in data:
        _registry_cache = data["models"]
    elif isinstance(data, list):
        _registry_cache = data
    else:
        logger.error("Unexpected registry format in %s", _registry_path)
        _registry_cache = []

    logger.info("Loaded %d models from %s", len(_registry_cache), _registry_path)
    return _registry_cache


def get_registry() -> list[dict[str, Any]]:
    """
    Get the cached registry, loading it if necessary.

    Returns
    -------
    list of dict
        The cached list of model entries.
    """
    global _registry_cache
    if _registry_cache is None:
        load_registry()
    return _registry_cache or []


def reload_registry() -> list[dict[str, Any]]:
    """Force a reload of the registry from disk."""
    return load_registry()


def get_model(name: str) -> Optional[dict[str, Any]]:
    """
    Look up a single model by name.

    Parameters
    ----------
    name : str
        The model name (e.g., "glm-5.2").

    Returns
    -------
    dict or None
        The model entry, or None if not found.
    """
    for model in get_registry():
        if model.get("model") == name or model.get("name") == name:
            return model
    return None