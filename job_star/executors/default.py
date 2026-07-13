"""Default executor: generic AI model via gatehouse.

This is the fallback executor for goals with no expert assignment.
It uses the router to pick a model and calls the gatehouse AI client.
"""

from __future__ import annotations

from typing import Optional

from ..models import ExecutionResult, Goal, Step
from ..router import route
from ..gatehouse import execute as execute_ai
from ..gatehouse import GatewayMonitor
from ..supervisor import Supervisor, SupervisionDecision
from ..models import Urgency


class DefaultExecutor:
    """Generic AI model executor via gatehouse."""

    name = "default"
    description = "Generic AI model via gatehouse (router-selected)"

    def __init__(self, gateway_monitor: GatewayMonitor | None = None):
        self.gateway_monitor = gateway_monitor or GatewayMonitor()

    async def execute(
        self,
        goal: Goal,
        step: Step,
        context: dict | None = None,
        model_override: str | None = None,
    ) -> ExecutionResult:
        """Execute a step using a generic AI model."""
        context = context or {}
        prev_outputs = context.get("prev_outputs", [])

        allow_expensive = bool(model_override)
        routing = await route(
            urgency=goal.urgency,
            request_type="feature",
            description=step.description or step.title,
            model_override=model_override,
            allow_expensive=allow_expensive,
            gateway_monitor=self.gateway_monitor,
        )

        if not routing.model:
            return ExecutionResult(
                success=False,
                error=f"No model available: {routing.reason}",
                model="none",
            )

        prev_context = context.get("prev_context", "")
        system = context.get("system_prompt", "You are Job-Star, working on a step of a goal.")
        user = context.get("user_prompt", f"Goal: {goal.title}\nStep: {step.title}\n{step.description or ''}")

        result = await execute_ai(user, model=routing.model, system_prompt=system)
        if result.success:
            self.gateway_monitor.record_success(
                routing.model,
                result.input_tokens + result.output_tokens,
                x_gatehouse=result.x_gatehouse,
            )
        else:
            self.gateway_monitor.record_failure(routing.model, result.error or "Unknown error")

        return result

    def curated_context(self) -> str:
        return ""
