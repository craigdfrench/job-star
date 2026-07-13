"""
Input parser for the Job-Star router.

Takes raw, loosely-typed input (dicts, kwargs, partial values)
and produces a validated RoutingInput. Handles:

- Coercing string enums to proper Enum values
- Accepting numeric scores and mapping them to categories
- Filling in defaults for missing signals
- Validating constraints (non-negative costs, valid weights, etc.)
- Producing clear error messages for misconfigured calls
"""

from __future__ import annotations

from typing import Any, Optional, Union

from jobstar.router.models import (
    CostBudget,
    ModelAvailability,
    RoutingInput,
    TaskComplexity,
    Urgency,
)


class RoutingInputError(ValueError):
    """Raised when routing input cannot be parsed or validated."""

    def __init__(self, message: str, field: Optional[str] = None):
        self.field = field
        super().__init__(message)


class InputParser:
    """Parses raw input into a validated RoutingInput.

    Usage:
        parser = InputParser()
        routing_input = parser.parse(
            complexity="moderate",
            urgency=0.4,  # score -> Urgency.SOON
            cost_budget={"max_cost_usd": 0.05, "prefer_cheapest": True},
        )
    """

    def parse(
        self,
        complexity: Union[str, TaskComplexity, float, int, None] = None,
        urgency: Union[str, Urgency, float, int, None] = None,
        cost_budget: Union[dict[str, Any], CostBudget, None] = None,
        availability: Union[dict[str, Any], ModelAvailability, None] = None,
        task_id: Optional[str] = None,
        task_description: Optional[str] = None,
        estimated_input_tokens: Optional[int] = None,
        estimated_output_tokens: Optional[int] = None,
    ) -> RoutingInput:
        """Parse and validate all routing signals.

        Args:
            complexity: Task complexity as enum, string label,
                or 0.0–1.0 numeric score. Required.
            urgency: Urgency as enum, string label, or max wait
                seconds (numeric). Required.
            cost_budget: CostBudget instance or dict with keys
                max_cost_usd, prefer_cheapest, cost_weight.
                Defaults to unconstrained.
            availability: ModelAvailability instance or dict.
                Defaults to "check live" (empty available set).
            task_id: Optional identifier for tracing.
            task_description: Optional human-readable task summary.
            estimated_input_tokens: Optional token estimate for
                cost calculation.
            estimated_output_tokens: Optional token estimate for
                cost calculation.

        Returns:
            Validated RoutingInput.

        Raises:
            RoutingInputError: If any signal is missing, invalid,
                or out of range.
        """

        parsed_complexity = self._parse_complexity(complexity)
        parsed_urgency = self._parse_urgency(urgency)
        parsed_cost = self._parse_cost_budget(cost_budget)
        parsed_availability = self._parse_availability(availability)

        try:
            return RoutingInput(
                complexity=parsed_complexity,
                urgency=parsed_urgency,
                cost_budget=parsed_cost,
                availability=parsed_availability,
                task_id=task_id,
                task_description=task_description,
                estimated_input_tokens=estimated_input_tokens,
                estimated_output_tokens=estimated_output_tokens,
            )
        except ValueError as e:
            raise RoutingInputError(str(e)) from e

    def _parse_complexity(
        self, value: Union[str, TaskComplexity, float, int, None]
    ) -> TaskComplexity:
        """Parse complexity from enum, string, or numeric score."""

        if value is None:
            raise RoutingInputError(
                "complexity is required — provide a label, "
                "TaskComplexity enum, or 0.0–1.0 score",
                field="complexity",
            )

        if isinstance(value, TaskComplexity):
            return value

        if isinstance(value, str):
            normalized = value.strip().lower()
            try:
                return TaskComplexity(normalized)
            except ValueError:
                # Maybe it's a numeric string score
                try:
                    score = float(normalized)
                    return TaskComplexity.from_score(score)
                except ValueError:
                    valid = [c.value for c in TaskComplexity]
                    raise RoutingInputError(
                        f"complexity '{value}' is not valid. "
                        f"Use one of {valid} or a 0.0–1.0 score.",
                        field="complexity",
                    ) from None

        if isinstance(value, (int, float)):
            try:
                return TaskComplexity.from_score(float(value))
            except ValueError as e:
                raise RoutingInputError(
                    str(e), field="complexity"
                ) from e

        raise RoutingInputError(
            f"complexity must be a string, TaskComplexity, or number, "
            f"got {type(value).__name__}",
            field="complexity",
        )

    def _parse_urgency(
        self, value: Union[str, Urgency, float, int, None]
    ) -> Urgency:
        """Parse urgency from enum, string, or max-wait-seconds."""

        if value is None:
            raise RoutingInputError(
                "urgency is required — provide a label, "
                "Urgency enum, or max wait seconds",
                field="urgency",
            )

        if isinstance(value, Urgency):
            return value

        if isinstance(value, str):
            normalized = value.strip().lower()
            try:
                return Urgency(normalized)
            except ValueError:
                try:
                    seconds = float(normalized)
                    return Urgency.from_seconds(seconds)
                except ValueError:
                    valid = [u.value for u in Urgency]
                    raise RoutingInputError(
                        f"urgency '{value}' is not valid. "
                        f"Use one of {valid} or max wait seconds.",
                        field="urgency",
                    ) from None

        if isinstance(value, (int, float)):
            try:
                return Urgency.from_seconds(float(value))
            except ValueError as e:
                raise RoutingInputError(
                    str(e), field="urgency"
                ) from e

        raise RoutingInputError(
            f"urgency must be a string, Urgency, or number, "
            f"got {type(value).__name__}",
            field="urgency",
        )

    def _parse_cost_budget(
        self, value: Union[dict[str, Any], CostBudget, None]
    ) -> CostBudget:
        """Parse cost budget from dict or CostBudget instance."""

        if value is None:
            return CostBudget()

        if isinstance(value, CostBudget):
            return value

        if isinstance(value, dict):
            try:
                return CostBudget(
                    max_cost_usd=value.get("max_cost_usd"),
                    prefer_cheapest=value.get("prefer_cheapest", False),
                    cost_weight=value.get("cost_weight", 0.5),
                )
            except ValueError as e:
                raise RoutingInputError(
                    str(e), field="cost_budget"
                ) from e

        raise RoutingInputError(
            f"cost_budget must be a dict or CostBudget, "
            f"got {type(value).__name__}",
            field="cost_budget",
        )

    def _parse_availability(
        self, value: Union[dict[str, Any], ModelAvailability, None]
    ) -> ModelAvailability:
        """Parse model availability from dict or ModelAvailability."""

        if value is None:
            return ModelAvailability()

        if isinstance(value, ModelAvailability):
            return value

        if isinstance(value, dict):
            available = value.get("available_models", [])
            excluded = value.get("excluded_models", [])
            health = value.get("provider_health", {})

            # Coerce to sets if lists/tuples were passed
            available_set = set(available) if available else set()
            excluded_set = set(excluded) if excluded else set()

            try:
                return ModelAvailability(
                    available_models=available_set,
                    excluded_models=excluded_set,
                    provider_health=dict(health),
                )
            except ValueError as e:
                raise RoutingInputError(
                    str(e), field="availability"
                ) from e

        raise RoutingInputError(
            f"availability must be a dict or ModelAvailability, "
            f"got {type(value).__name__}",
            field="availability",
        )


# Convenience module-level function
_default_parser = InputParser()


def parse_routing_input(
    complexity: Union[str, TaskComplexity, float, int, None] = None,
    urgency: Union[str, Urgency, float, int, None] = None,
    cost_budget: Union[dict[str, Any], CostBudget, None] = None,
    availability: Union[dict[str, Any], ModelAvailability, None] = None,
    task_id: Optional[str] = None,
    task_description: Optional[str] = None,
    estimated_input_tokens: Optional[int] = None,
    estimated_output_tokens: Optional[int] = None,
) -> RoutingInput:
    """Parse routing input using the default InputParser.

    See InputParser.parse for full documentation.
    """
    return _default_parser.parse(
        complexity=complexity,
        urgency=urgency,
        cost_budget=cost_budget,
        availability=availability,
        task_id=task_id,
        task_description=task_description,
        estimated_input_tokens=estimated_input_tokens,
        estimated_output_tokens=estimated_output_tokens,
    )
