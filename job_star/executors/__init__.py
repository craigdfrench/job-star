"""Executor registry: maps expert names to specialized execution backends.

An executor is a pluggable agent that handles goals for a specific topic/codebase.
The router dispatches to an executor instead of a generic AI model when a goal
has an `expert` field matching a registered executor.

Executors implement:
    async def execute(goal, step, context) -> ExecutionResult

The default executor (None) is the generic AI model path via gatehouse.
"""

from __future__ import annotations

from typing import Optional

from ..models import ExecutionResult, Goal, Step


class Executor:
    """Base class for expert executors."""

    name: str = "default"
    description: str = "Generic AI model via gatehouse"

    async def execute(
        self,
        goal: Goal,
        step: Step,
        context: dict | None = None,
    ) -> ExecutionResult:
        """Execute a step. Override in subclasses."""
        raise NotImplementedError

    def curated_context(self) -> str:
        """Return curated context (docs, codebase knowledge) for this expert."""
        return ""


# Registry of executors by expert name
_registry: dict[str, Executor] = {}


def register_executor(executor: Executor) -> None:
    """Register an executor by its name."""
    _registry[executor.name] = executor


def get_executor(expert: str | None) -> Executor:
    """Get the executor for an expert name. Falls back to default."""
    if expert and expert in _registry:
        return _registry[expert]
    return _registry.get("default")


def list_executors() -> dict[str, str]:
    """Return {name: description} for all registered executors."""
    return {name: ex.description for name, ex in _registry.items()}


def register_defaults() -> None:
    """Register the default executor and all built-in experts."""
    from .default import DefaultExecutor
    from .gatehouse_ai import GatehouseAIExecutor
    from .research import ResearchExecutor

    if "default" not in _registry:
        register_executor(DefaultExecutor())
    if "gatehouse-ai" not in _registry:
        register_executor(GatehouseAIExecutor())
    if "research" not in _registry:
        register_executor(ResearchExecutor())
