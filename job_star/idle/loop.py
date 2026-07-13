"""Idle loop: opportunistically executes idle-opportunistic goals.

Runs in the background, checking for idle-opportunistic goals that can
be worked on when resources are available. Chipping away at directional
goals during downtime is what makes job-star alive, not just reactive.

The idle loop claims steps from ANY expert (including 'research') and
dispatches to the appropriate executor. Goals with an expert (e.g.
'research', 'gatehouse-ai') are handled by their specialized executor.
Goals without an expert use the generic execution path.
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

from ..models import Goal, GoalStatus, Step, StepStatus, Urgency
from ..db import list_goals, claim_next_step_any_goal, update_step_status, audit
from ..router import route
from ..gatehouse import execute, GatewayMonitor
from ..supervisor import Supervisor, SupervisionDecision
from ..followup import FollowUpEngine
from ..executors import get_executor, register_defaults


class IdleLoop:
    """Background loop that opportunistically works on idle goals.

    Lifecycle per cycle:
    1. Sleep for configured interval
    2. Find idle-opportunistic goals with pending steps (any expert)
    3. Pick the highest-priority one
    4. Execute one step — dispatching to the expert executor if the goal
       has one, or the generic path if not
    5. Record progress
    6. Repeat

    The idle loop now uses the gateway monitor to avoid models in quota hold
    and to dynamically pick fallbacks.
    """

    def __init__(
        self,
        supervisor: Supervisor | None = None,
        followup: FollowUpEngine | None = None,
        gateway_monitor: GatewayMonitor | None = None,
        interval_s: float = 60.0,
        max_cycles: int | None = None,
    ):
        self.supervisor = supervisor or Supervisor(
            max_tokens_per_goal=10_000,
            max_cost_per_goal=0.10,
        )
        self.followup = followup or FollowUpEngine()
        self.gateway_monitor = gateway_monitor or GatewayMonitor()
        self.interval_s = interval_s
        self.max_cycles = max_cycles
        self._stop = False
        self._cycle_count = 0

        # Ensure executors are registered so expert goals can be dispatched
        register_defaults()

    def stop(self) -> None:
        self._stop = True

    async def run(self) -> None:
        """Run the idle loop until stopped or max cycles reached."""
        print("Idle loop started. Press Ctrl+C to stop.")
        while not self._stop:
            if self.max_cycles and self._cycle_count >= self.max_cycles:
                break

            try:
                await self._run_once()
            except Exception as e:
                print(f"Idle loop error: {e}")

            self._cycle_count += 1
            await asyncio.sleep(self.interval_s)

        print(f"Idle loop stopped after {self._cycle_count} cycles.")

    async def run_once(self) -> dict:
        """Execute a single idle loop cycle."""
        return await self._run_once()

    async def _run_once(self) -> dict:
        """Execute one cycle of the idle loop."""
        # Atomically claim the next pending step across all idle-opportunistic
        # goals, regardless of expert. This includes 'research' goals and
        # any other expert-tagged goals.
        claimed = await claim_next_step_any_goal(
            urgency=Urgency.IDLE_OPPORTUNISTIC,
            expert_any=True,
        )
        if not claimed:
            return {"status": "no_idle_steps", "cycle": self._cycle_count}

        goal, step = claimed
        expert_tag = f" [{goal.expert}]" if goal.expert else ""
        print(f"  [idle] Working on:{expert_tag} {goal.title} → {step.title}")
        result = await self._execute_step(goal, step)
        return {"status": "executed", "goal_id": goal.id, "step_id": step.id, **result}

    async def _execute_step(self, goal: Goal, step: Step) -> dict:
        """Execute a single step under supervision.

        If the goal has an expert (e.g. 'research'), dispatch to that expert's
        executor. Otherwise, use the generic execution path.
        """
        # Pre-execution check
        pre_check = await self.supervisor.check_before_execute(goal, step)
        if pre_check.decision != SupervisionDecision.APPROVE:
            await audit("idle_step_blocked", {
                "goal_id": goal.id,
                "step_id": step.id,
                "reason": pre_check.reason,
            })
            return {"blocked": True, "reason": pre_check.reason}

        # Dispatch to expert executor if the goal has one
        executor = get_executor(goal.expert)
        if executor.name != "default" and goal.expert:
            return await self._execute_with_expert(goal, step, executor)
        else:
            return await self._execute_generic(goal, step)

    async def _execute_with_expert(
        self, goal: Goal, step: Step, executor
    ) -> dict:
        """Execute a step using an expert executor (e.g. ResearchExecutor).

        Expert executors handle their own routing, prompting, and post-processing.
        The idle loop provides supervision (budget checks, post-execution review)
        and records the result.
        """
        print(f"  [idle] Expert executor: {executor.name}", flush=True)

        try:
            result = await executor.execute(
                goal,
                step,
                context={"gateway_monitor": self.gateway_monitor},
            )
        except Exception as e:
            print(f"  [idle] Executor error: {e}", flush=True)
            await update_step_status(step.id, StepStatus.FAILED)
            self.followup.emit(goal, "step_failed", str(e), step.id)
            return {"success": False, "error": str(e)}

        if not result.success:
            self.followup.emit(goal, "step_failed", result.error or "Unknown error", step.id)
            await update_step_status(step.id, StepStatus.FAILED)
            return {"success": False, "error": result.error}

        # Post-execution supervision check
        post_check = self.supervisor.check_after_execute(
            goal, step, result.content,
            tokens_used=result.input_tokens + result.output_tokens,
            cost=result.cost,
        )

        if post_check.decision == SupervisionDecision.REQUIRE_ESCALATION:
            await audit("idle_step_escalation", {
                "step_id": step.id,
                "violations": post_check.violations,
            })
            self.followup.emit(goal, "constraint_violated",
                               "; ".join(post_check.violations), step.id)
            await update_step_status(
                step.id, StepStatus.COMPLETED,
                result={"content": result.content, "warnings": post_check.violations},
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
        else:
            await update_step_status(
                step.id, StepStatus.COMPLETED,
                result={"content": result.content},
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )

        self.followup.emit(goal, "step_completed", f"Completed: {step.title}", step.id)

        return {
            "success": True,
            "model": result.model,
            "tokens": result.input_tokens + result.output_tokens,
            "executor": executor.name,
        }

    async def _execute_generic(self, goal: Goal, step: Step) -> dict:
        """Execute a step using the generic AI model path (no expert executor).

        This is the original idle loop execution path for goals without an expert.
        """
        # Route to a model (prefer free for idle work; never allow expensive)
        routing = await route(
            urgency=goal.urgency,
            request_type="chore",
            description=step.description or step.title,
            prefer_free=True,
            allow_expensive=False,
            gateway_monitor=self.gateway_monitor,
        )

        # Build prompt
        system = f"""You are Job-Star, working on an idle-opportunistic goal.
Goal: {goal.title}
Step: {step.title}
{step.description or ''}

Be practical and concise. Complete this step."""
        user = f"Step: {step.title}\n{step.description or ''}\n\nComplete this step."

        # Execute
        result = await execute(user, model=routing.model, system_prompt=system)

        if not result.success:
            # Record quota/availability failures in the gateway monitor
            self.gateway_monitor.record_failure(routing.model, result.error or "Unknown error")
            await update_step_status(step.id, StepStatus.FAILED)
            self.supervisor.budget.record_failure(step.id)
            self.followup.emit(goal, "step_failed", result.error or "Unknown error", step.id)
            return {"success": False, "error": result.error}

        # Record success
        self.gateway_monitor.record_success(routing.model, result.input_tokens + result.output_tokens, x_gatehouse=result.x_gatehouse)

        # Post-execution check
        post_check = self.supervisor.check_after_execute(
            goal, step, result.content, tokens_used=result.input_tokens + result.output_tokens,
            cost=result.cost,
        )

        if post_check.decision == SupervisionDecision.REQUIRE_ESCALATION:
            await audit("idle_step_escalation", {
                "step_id": step.id,
                "violations": post_check.violations,
            })
            self.followup.emit(goal, "constraint_violated",
                                     "; ".join(post_check.violations), step.id)
            # Still save the result but flag the issues
            await update_step_status(
                step.id, StepStatus.COMPLETED,
                result={"content": result.content, "warnings": post_check.violations},
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )
        else:
            await update_step_status(
                step.id, StepStatus.COMPLETED,
                result={"content": result.content},
                model=result.model,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            )

        self.followup.emit(goal, "step_completed",
                                 f"Completed: {step.title}", step.id)

        return {
            "success": True,
            "model": result.model,
            "tokens": result.input_tokens + result.output_tokens,
        }