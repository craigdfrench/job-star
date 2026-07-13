"""The orchestrator: ties the entire core loop together.

Intake → Context Gather → Triage → Conflict Check → Goal Registry
→ Router → Supervisor → AI Provider → Result → Follow-up
"""

from __future__ import annotations

import json
import re
from typing import Optional

from .models import (
    Domain, ExecutionResult, Goal, GoalStatus, IntakeRequest,
    RoutingDecision, Step, StepStatus, TriageResult, Urgency,
)
from .db import (
    audit, close_pool, create_goal, create_step, get_goal, get_steps,
    claim_next_step, claim_next_step_any_goal, list_goals, update_goal_progress, update_goal_status,
    update_step_status, record_decision,
)
from .triage import triage as run_triage
from .router import route, MODEL_REGISTRY
from .gatehouse import execute as execute_ai, check_health, GatewayMonitor
from .supervisor import Supervisor, SupervisionDecision
from .followup import FollowUpEngine
from .conflict import detect_conflicts
from .intake import intake as do_intake
from .executors import get_executor, register_defaults
from .checkin import CheckInEngine, CheckInType, CheckInStatus


class Orchestrator:
    """The main job-star orchestrator. Wires all components together.

    Usage:
        orch = Orchestrator()
        goal, triage = await orch.add_goal("Fix the bug", "description")
        result = await orch.work_on_goal(goal.id)
    """

    def __init__(
        self,
        supervisor: Supervisor | None = None,
        followup: FollowUpEngine | None = None,
        gateway_monitor: GatewayMonitor | None = None,
    ):
        self.supervisor = supervisor or Supervisor()
        self.followup = followup or FollowUpEngine()
        self.gateway_monitor = gateway_monitor or GatewayMonitor()
        self.checkin_engine = CheckInEngine(self.gateway_monitor)
        register_defaults()  # register default + gatehouse-ai executors

    # ===================================================================
    # INTAKE — add a new goal through the full pipeline
    # ===================================================================

    async def add_goal(
        self,
        title: str,
        description: str = "",
        source: str = "manual",
        urgency_override: Urgency | None = None,
        domain_override: Domain | None = None,
        metadata: dict | None = None,
        requested_by: str = "",
    ) -> tuple[Goal | None, TriageResult]:
        """Add a goal through the full intake pipeline."""
        return await do_intake(
            title=title,
            description=description,
            source=source,
            urgency_override=urgency_override,
            domain_override=domain_override,
            metadata=metadata,
            requested_by=requested_by,
        )

    # ===================================================================
    # PLAN — ask AI to break a goal into steps
    # ===================================================================

    async def plan_goal(self, goal_id: str, model: str | None = None) -> list[Step]:
        """Ask the AI to break a goal into steps."""
        goal = await get_goal(goal_id)
        if not goal:
            raise ValueError(f"Goal not found: {goal_id}")

        # Check for existing steps
        existing = await get_steps(goal_id)
        if existing:
            return existing

        # Build plan prompt
        system = """You are Job-Star, a system that helps build software projects through constrained, supervised AI orchestration.

Break the goal into concrete, executable steps. Each step should be something an AI coding agent could reasonably complete in one session.

Output format: List each step as a numbered item with a title and optional description.
Use this format:
1. Step Title - Brief description
2. Step Title - Brief description
"""

        user = f"""Goal: {goal.title}

{goal.description or ''}

Domain: {goal.domain.value}
Urgency: {goal.urgency.value}

Break this goal into concrete steps."""

        # Route to a model, with fallback retry (a single model 404/error
        # must not kill the whole plan).
        allow_expensive = bool(model)
        tried: set[str] = set()
        result = None
        for attempt in range(3):
            routing = await route(
                urgency=goal.urgency,
                request_type="feature",
                description=goal.description or goal.title,
                model_override=model if attempt == 0 else None,
                allow_expensive=allow_expensive,
                gateway_monitor=self.gateway_monitor,
            )
            if not routing.model:
                break
            result = await execute_ai(user, model=routing.model, system_prompt=system)
            if result.success:
                break
            # Record failure so the monitor excludes this model, then retry
            self.gateway_monitor.record_failure(routing.model, result.error or "error")
            tried.add(routing.model)
            fallback = self.gateway_monitor.pick_fallback(
                routing.model, required_capability=None,
                prefer_free=goal.urgency != Urgency.IMPERATIVE,
                allow_expensive=allow_expensive,
            )
            if not fallback or fallback in tried:
                break
            model = fallback

        if not result or not result.success:
            raise RuntimeError(f"AI planning failed after retries: {result.error if result else 'no model'}")

        await audit("ai_called", {
            "purpose": "plan",
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        }, goal_id, model=result.model)

        # Parse steps from AI output
        steps = _parse_plan_output(result.content)

        if not steps:
            await audit("plan_parse_failed", {"output": result.content[:500]}, goal_id)
            raise RuntimeError("Could not parse steps from AI output")

        # Save steps to database
        created: list[Step] = []
        for i, (title, desc) in enumerate(steps):
            step = await create_step(goal_id, title, desc, order_index=i + 1)
            created.append(step)

        await audit("goal_planned", {"step_count": len(created), "model": result.model}, goal_id)

        await record_decision(
            goal_id=goal_id,
            decision=f"Planned {len(created)} steps using {result.model}",
            reasoning=f"AI planned steps for goal: {goal.title}",
            alternatives=[{"model": m.name, "provider": m.provider} for m in
                          [m for m in MODEL_REGISTRY
                           if m.name != result.model][:3]],
        )

        return created

    # ===================================================================
    # WORK — execute the next pending step of a goal
    # ===================================================================

    async def work_on_goal(self, goal_id: str, model_override: str | None = None) -> ExecutionResult:
        """Work on a goal: plan if needed, then execute the next step."""
        goal = await get_goal(goal_id)
        if not goal:
            raise ValueError(f"Goal not found: {goal_id}")

        # If goal is completed, skip
        if goal.status == GoalStatus.COMPLETED:
            return ExecutionResult(success=True, content="Goal already completed", model="none")

        # If no steps, plan first
        steps = await get_steps(goal_id)
        if not steps:
            print(f"  No steps yet. Planning...")
            steps = await self.plan_goal(goal_id)

        # Check for an already-claimed (in_progress) step first — this happens
        # when a distributed worker claimed the step and then called work_on_goal.
        step = None
        in_progress = [s for s in steps if s.status == StepStatus.IN_PROGRESS]
        if in_progress:
            step = in_progress[0]
        else:
            # Atomically claim the next pending step so distributed workers
            # don't collide on the same step.
            step = await claim_next_step(goal_id)

        if not step:
            # All steps done?
            all_done = all(s.status == StepStatus.COMPLETED for s in steps)
            if all_done:
                await update_goal_status(goal_id, GoalStatus.COMPLETED)
                await audit("goal_completed", {}, goal_id)
                return ExecutionResult(success=True, content="All steps completed! Goal marked complete.", model="none")
            return ExecutionResult(success=True, content="No pending steps", model="none")

        # Get previous step outputs for context
        prev_steps = [s for s in steps if s.status == StepStatus.COMPLETED and s.order_index < step.order_index]
        prev_outputs = [s.result.get("content", "") if s.result else "" for s in prev_steps]

        # Pre-execution supervision check
        pre_check = await self.supervisor.check_before_execute(goal, step, prev_outputs)
        if pre_check.decision != SupervisionDecision.APPROVE:
            await audit("step_blocked", {
                "step_id": step.id,
                "reason": pre_check.reason,
                "violations": pre_check.violations,
            }, goal_id, step.id)
            # Reset the step to pending so it can be retried later
            # (e.g., after budget is raised). Leaving it in_progress
            # would orphan it — no worker can claim an in_progress step.
            await update_step_status(step.id, StepStatus.PENDING)
            return ExecutionResult(
                success=False,
                error=f"Supervisor blocked execution: {pre_check.reason}",
                model="none",
            )

        # Determine if the model override is allowed. If an override is
        # provided, it is treated as explicit user request. Otherwise we never
        # allow expensive silent fallbacks.
        allow_expensive = bool(model_override)

        # Build work prompt with context from previous steps
        prev_context = _build_prev_context(prev_steps)

        # Dispatch to the appropriate executor. If the goal has an expert,
        # use that expert's executor (which has curated context). Otherwise
        # use the default generic executor.
        executor = get_executor(goal.expert)
        if executor.name != "default" and goal.expert:
            print(f"  Expert:     {goal.expert}", flush=True)

        # Execute, with fallback retry if the first model fails
        print(f"  Working on: {goal.title}")
        print(f"  Step:       {step.title}")

        result: ExecutionResult | None = None
        attempts = 0
        max_attempts = 3
        tried_models: set[str] = set()

        while attempts < max_attempts:
            # On attempt 0, use the original model_override (if any).
            # On retry attempts, use the fallback model we picked.
            result = await executor.execute(
                goal=goal,
                step=step,
                context={
                    "prev_outputs": prev_outputs,
                    "prev_context": prev_context,
                },
                model_override=model_override,
            )

            if result.success:
                break

            # Record failure; monitor will mark quota hold and exclude it
            self.gateway_monitor.record_failure(result.model, result.error or "Unknown error")
            tried_models.add(result.model)
            attempts += 1
            print(f"  Model {result.model} failed: {result.error[:80]}", flush=True)

            if attempts < max_attempts:
                # Pick a fallback model for the next iteration
                fallback = self.gateway_monitor.pick_fallback(
                    result.model,
                    required_capability=None,
                    prefer_free=goal.urgency != Urgency.IMPERATIVE,
                    allow_expensive=allow_expensive,
                )
                if fallback and fallback not in tried_models:
                    print(f"  Retrying with fallback model: {fallback}", flush=True)
                    model_override = fallback  # actually used on the next attempt
                else:
                    print(f"  No fallback available. Deferring step.", flush=True)
                    break
            else:
                print(f"  All attempts failed.", flush=True)

        if not result or not result.success:
            error = result.error if result else "No model available"
            await update_step_status(
                step.id, StepStatus.FAILED,
                model=result.model if result else "none",
                result={"error": error},
            )
            self.supervisor.budget.record_failure(step.id)
            await self.followup.emit(goal, "step_failed", error, step.id)
            # If a step fails repeatedly, create a clarification check-in
            all_steps = await get_steps(goal_id)
            failed_steps = [s for s in all_steps if s.status == StepStatus.FAILED]
            if len(failed_steps) >= 2:
                try:
                    check_in = await self.checkin_engine.maybe_create_clarification_check_in(
                        goal, all_steps, step=step,
                        issue=f"Step '{step.title}' failed: {error}",
                    )
                    await audit("clarification_checkin_created", {
                        "check_in_id": check_in.id,
                    }, goal_id, step.id)
                except Exception:
                    pass  # check-in failure should not block error reporting
            return result or ExecutionResult(success=False, error="No model available", model="none")

        # Post-execution supervision check
        post_check = self.supervisor.check_after_execute(
            goal, step, result.content, prev_outputs,
            tokens_used=result.input_tokens + result.output_tokens,
            cost=result.cost,
        )

        warnings = []
        if post_check.decision == SupervisionDecision.REQUIRE_ESCALATION:
            warnings = post_check.violations
            await audit("step_warnings", {
                "step_id": step.id,
                "violations": post_check.violations,
            }, goal_id, step.id)
            await self.followup.emit(goal, "constraint_violated",
                                     "; ".join(post_check.violations), step.id)

        # Save result
        step_result = {"content": result.content}
        if warnings:
            step_result["warnings"] = warnings

        await update_step_status(
            step.id, StepStatus.COMPLETED,
            result=step_result,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost=result.cost,
        )

        # Update goal progress
        all_steps = await get_steps(goal_id)
        completed = sum(1 for s in all_steps if s.status == StepStatus.COMPLETED)
        progress = completed / len(all_steps) if all_steps else 0.0
        await update_goal_progress(goal_id, progress)

        if progress >= 1.0:
            # Check if a completion check-in already exists
            from .checkin import should_create_completion_check_in
            if await should_create_completion_check_in(goal, all_steps):
                check_in = await self.checkin_engine.create_completion_check_in(goal, all_steps)
                await self.followup.emit(goal, "goal_completed",
                    f"Goal ready for review: {goal.title}. Check-in {check_in.id[:8]} created.")
                await audit("completion_checkin_created", {
                    "check_in_id": check_in.id,
                }, goal_id)
            else:
                await update_goal_status(goal_id, GoalStatus.COMPLETED)
                await audit("goal_completed", {}, goal_id)
                await self.followup.emit(goal, "goal_completed", f"Goal completed: {goal.title}")
        else:
            # Maybe create a progress check-in (after every N steps)
            try:
                check_in = await self.checkin_engine.maybe_create_progress_check_in(goal, all_steps)
                if check_in:
                    await audit("progress_checkin_created", {
                        "check_in_id": check_in.id,
                    }, goal_id, step.id)
            except Exception:
                pass  # check-in generation failure should not block step completion

        await self.followup.emit(goal, "step_completed", f"Completed: {step.title}", step.id)

        return result

    # ===================================================================
    # CONFLICT CHECK — detect conflicts among all goals
    # ===================================================================

    async def check_conflicts(self) -> list[tuple[str, str, str, str]]:
        """Run conflict detection across all goals."""
        return await detect_conflicts(save=True)

    # ===================================================================
    # IDLE — run one cycle of the idle loop
    # ===================================================================

    async def run_idle_cycle(self) -> dict:
        """Execute one cycle of the idle loop."""
        from .idle import IdleLoop
        loop = IdleLoop(
            supervisor=self.supervisor,
            followup=self.followup,
            gateway_monitor=self.gateway_monitor,
        )
        return await loop.run_once()

    # ===================================================================
    # STATUS — get system status
    # ===================================================================

    async def status(self) -> dict:
        """Get overall system status."""
        goals = await list_goals()
        active = [g for g in goals if g.status == GoalStatus.ACTIVE]
        completed = [g for g in goals if g.status == GoalStatus.COMPLETED]
        blocked = [g for g in goals if g.status == GoalStatus.BLOCKED]
        gateway_healthy = await check_health()

        await self.gateway_monitor.refresh()
        unavailable_models = {
            name: s.is_in_quota_hold
            for name, s in self.gateway_monitor._states.items()
            if not s.is_available
        }
        # Models with observed quota/cost info from x_gatehouse
        observed_models = {
            name: self.gateway_monitor.quota_status(name)
            for name, s in self.gateway_monitor._states.items()
            if s.observed_cost_class
        }

        return {
            "total_goals": len(goals),
            "active": len(active),
            "completed": len(completed),
            "blocked": len(blocked),
            "gateway_healthy": gateway_healthy,
            "followup_batch": len(self.followup.batch),
            "unavailable_models": unavailable_models,
            "observed_models": observed_models,
        }


# ============================================================================
# Helpers
# ============================================================================

def _parse_plan_output(content: str) -> list[tuple[str, str]]:
    """Parse AI output into (title, description) pairs."""
    steps: list[tuple[str, str]] = []
    lines = content.strip().split("\n")
    current_title: str | None = None
    current_desc: list[str] = []

    for line in lines:
        # Match numbered list items: "1. Title" or "1. Title — Description"
        match = re.match(r"^\s*\d+[\.\)]\s+(.+)", line)
        if match:
            # Save previous step
            if current_title:
                steps.append((current_title, " ".join(current_desc).strip()))

            text = match.group(1).strip()
            # Split title and description on "—" or " - " or ":"
            for sep in [" — ", " - ", ": "]:
                if sep in text:
                    parts = text.split(sep, 1)
                    current_title = parts[0].strip()
                    current_desc = [parts[1].strip()] if len(parts) > 1 else []
                    break
            else:
                current_title = text
                current_desc = []
        elif current_title and line.strip():
            current_desc.append(line.strip())

    # Save last step
    if current_title:
        steps.append((current_title, " ".join(current_desc).strip()))

    return steps


def _build_prev_context(prev_steps: list[Step]) -> str:
    """Build context string from previous completed steps."""
    if not prev_steps:
        return ""

    lines = [f"\nPREVIOUS STEPS COMPLETED ({len(prev_steps)}):"]
    file_list: list[str] = []

    for ps in prev_steps:
        content = ps.result.get("content", "") if ps.result else ""
        # Extract file paths
        file_matches = re.findall(r"`([^`]+\.(?:py|rs|ts|js|go|yaml|yml|json|toml|md|sql|sh|html|css))`", content)
        file_list.extend(file_matches)

        summary = content[:200].replace("\n", " ")
        lines.append(f"  - {ps.title} [{ps.model or '?'}]: {summary}...")

    if file_list:
        lines.append(f"\nFILES ALREADY CREATED ({len(file_list)}):")
        for f in file_list:
            lines.append(f"  {f}")
        lines.append("\nMANDATORY CONSTRAINT: You MUST use the EXACT file paths from previous steps. DO NOT create new module trees.")

    return "\n".join(lines)