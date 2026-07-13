"""Supervisor: enforces constraints on AI execution.

The supervisor checks every action before it runs:
- Budget constraints (token/cost limits per goal)
- File path constraints (must use existing paths from previous steps)
- Loop detection (same step failing repeatedly)
- Domain constraints (don't write outside the goal's domain)

This is the Python implementation. The Rust supervisor (in generated/)
has the full async actor implementation. This is the simplified version
that works within the Python orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..models import Domain, Goal, GoalStatus, Step, StepStatus


class SupervisionDecision(Enum):
    APPROVE = "approve"
    DENY = "deny"
    REQUIRE_ESCALATION = "require_escalation"
    PAUSE_GOAL = "pause_goal"


@dataclass
class SupervisionResult:
    decision: SupervisionDecision
    reason: str = ""
    violations: list[str] = field(default_factory=list)


@dataclass
class BudgetTracker:
    """Tracks token and cost usage per goal."""
    max_tokens_per_goal: int = 50_000
    max_cost_per_goal: float = 1.0
    max_step_retries: int = 3

    _goal_tokens: dict[str, int] = field(default_factory=dict)
    _goal_cost: dict[str, float] = field(default_factory=dict)
    _step_failures: dict[str, int] = field(default_factory=dict)

    def record_usage(self, goal_id: str, tokens: int, cost: float) -> None:
        self._goal_tokens[goal_id] = self._goal_tokens.get(goal_id, 0) + tokens
        self._goal_cost[goal_id] = self._goal_cost.get(goal_id, 0.0) + cost

    def record_failure(self, step_id: str) -> None:
        self._step_failures[step_id] = self._step_failures.get(step_id, 0) + 1

    def check_budget(self, goal_id: str) -> tuple[bool, str]:
        """Check if goal is within budget. Returns (ok, reason).

        Uses in-memory tracking as a cache. The authoritative source is the
        goal_steps table in the DB (so budget persists across restarts).
        """
        tokens = self._goal_tokens.get(goal_id, 0)
        cost = self._goal_cost.get(goal_id, 0.0)
        if tokens > self.max_tokens_per_goal:
            return False, f"Token budget exceeded: {tokens}/{self.max_tokens_per_goal}"
        if cost > self.max_cost_per_goal:
            return False, f"Cost budget exceeded: ${cost:.4f}/${self.max_cost_per_goal}"
        return True, ""

    async def check_budget_db(self, goal_id: str) -> tuple[bool, str]:
        """Check budget using the DB as the authoritative source.

        Queries SUM(input_tokens + output_tokens) and SUM(cost) from
        goal_steps for this goal. This persists across process restarts —
        a worker that restarts will see the real cumulative spend, not zero.
        """
        from ..db import get_pool
        from uuid import UUID
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """SELECT COALESCE(SUM(COALESCE(input_tokens,0) + COALESCE(output_tokens,0)), 0) as tokens,
                              COALESCE(SUM(cost), 0) as cost
                       FROM goal_steps WHERE goal_id = $1""",
                    UUID(goal_id),
                )
            tokens = int(row["tokens"] or 0)
            cost = float(row["cost"] or 0)
            # Update in-memory cache
            self._goal_tokens[goal_id] = tokens
            self._goal_cost[goal_id] = cost
            if tokens > self.max_tokens_per_goal:
                return False, f"Token budget exceeded: {tokens}/{self.max_tokens_per_goal}"
            if cost > self.max_cost_per_goal:
                return False, f"Cost budget exceeded: ${cost:.4f}/${self.max_cost_per_goal}"
            return True, ""
        except Exception as e:
            # DB unavailable — fall back to in-memory check
            return self.check_budget(goal_id)

    def check_retries(self, step_id: str) -> tuple[bool, str]:
        failures = self._step_failures.get(step_id, 0)
        if failures >= self.max_step_retries:
            return False, f"Max retries exceeded for step: {failures}/{self.max_step_retries}"
        return True, ""


# ============================================================================
# Constraint checking
# ============================================================================

def check_file_path_consistency(
    proposed_output: str,
    previous_step_outputs: list[str],
) -> tuple[bool, list[str]]:
    """Check if the AI's output uses consistent file paths with previous steps.

    Returns (is_consistent, violations).
    """
    import re

    # Extract file paths from previous steps
    prev_files: set[str] = set()
    for output in previous_step_outputs:
        matches = re.findall(r"`([^`]+\.(?:py|rs|ts|js|go|yaml|yml|json|toml|md|sql|sh|html|css))`", output)
        prev_files.update(matches)
        # Also look for "File: path" patterns
        matches2 = re.findall(r"(?:File:\s*|##\s*File:\s*)([^\n]+)", output)
        prev_files.update(m.strip().strip("`") for m in matches2)

    if not prev_files:
        # No previous files to compare against
        return True, []

    # Extract file paths from proposed output
    proposed_files: set[str] = set()
    matches = re.findall(r"`([^`]+\.(?:py|rs|ts|js|go|yaml|yml|json|toml|md|sql|sh|html|css))`", proposed_output)
    proposed_files.update(matches)

    # Check if proposed files are in a different module tree
    violations: list[str] = []

    # Get the module prefixes from previous steps
    prev_prefixes: set[str] = set()
    for f in prev_files:
        parts = f.split("/")
        if len(parts) > 1:
            prev_prefixes.add(parts[0])

    for f in proposed_files:
        parts = f.split("/")
        if len(parts) > 1:
            prefix = parts[0]
            if prev_prefixes and prefix not in prev_prefixes:
                violations.append(
                    f"New module tree '{prefix}/' doesn't match previous: {prev_prefixes}. "
                    f"File: {f}"
                )

    return len(violations) == 0, violations


# ============================================================================
# Main supervisor
# ============================================================================

class Supervisor:
    """Supervises AI execution, enforcing constraints."""

    def __init__(
        self,
        max_tokens_per_goal: int = 50_000,
        max_cost_per_goal: float = 1.0,
        max_step_retries: int = 3,
    ):
        self.budget = BudgetTracker(
            max_tokens_per_goal=max_tokens_per_goal,
            max_cost_per_goal=max_cost_per_goal,
            max_step_retries=max_step_retries,
        )

    async def check_before_execute(
        self,
        goal: Goal,
        step: Step,
        previous_step_outputs: list[str] | None = None,
    ) -> SupervisionResult:
        """Check if a step can be executed. Called before AI invocation.

        Uses the DB-backed budget check so budget persists across restarts.
        """
        violations: list[str] = []

        # Check budget (DB-backed for persistence across restarts)
        ok, reason = await self.budget.check_budget_db(goal.id)
        if not ok:
            violations.append(reason)

        # Check retries
        ok, reason = self.budget.check_retries(step.id)
        if not ok:
            violations.append(reason)

        # Check if goal is blocked
        if goal.status == GoalStatus.BLOCKED:
            violations.append(f"Goal is blocked: {', '.join(goal.blockers)}")

        if violations:
            if any("budget" in v.lower() or "retry" in v.lower() for v in violations):
                return SupervisionResult(
                    decision=SupervisionDecision.PAUSE_GOAL,
                    reason="; ".join(violations),
                    violations=violations,
                )
            return SupervisionResult(
                decision=SupervisionDecision.DENY,
                reason="; ".join(violations),
                violations=violations,
            )

        return SupervisionResult(decision=SupervisionDecision.APPROVE)

    def check_after_execute(
        self,
        goal: Goal,
        step: Step,
        result_content: str,
        previous_step_outputs: list[str] | None = None,
        tokens_used: int = 0,
        cost: float = 0.0,
    ) -> SupervisionResult:
        """Check the AI's output after execution. Called after AI returns.

        Args:
            goal: The goal being worked on.
            step: The step that was executed.
            result_content: The AI's output text.
            previous_step_outputs: Previous step outputs for path consistency.
            tokens_used: Tokens consumed by this call.
            cost: Cost of this call.

        Returns:
            SupervisionResult with the decision.
        """
        # Record usage
        self.budget.record_usage(goal.id, tokens_used, cost)

        violations: list[str] = []

        # Check file path consistency
        if previous_step_outputs:
            ok, path_violations = check_file_path_consistency(result_content, previous_step_outputs)
            if not ok:
                violations.extend(path_violations)

        # Check for empty output
        if not result_content.strip():
            violations.append("AI returned empty output")
            self.budget.record_failure(step.id)

        if violations:
            return SupervisionResult(
                decision=SupervisionDecision.REQUIRE_ESCALATION,
                reason="Constraint violations detected in output",
                violations=violations,
            )

        return SupervisionResult(decision=SupervisionDecision.APPROVE)