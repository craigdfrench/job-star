"""Check-in generation and response handling.

The CheckInEngine:
  1. Generates check-ins using AI (summarize progress, formulate questions)
  2. Decides WHEN to trigger a check-in (step count, failures, milestones, completion)
  3. Processes user responses (save answers, action decisions)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from ..models import Goal, GoalStatus, Step, StepStatus, Urgency, ExecutionResult
from ..db import (
    audit, get_pool, get_goal, get_steps, publish_event,
    update_goal_status, update_goal_progress, record_decision,
)
from ..gatehouse import execute as execute_ai, GatewayMonitor
from ..router import route
from . import CheckIn, CheckInType, CheckInStatus, CheckInQuestion

DEFAULT_CHECK_IN_INTERVAL = 3  # steps between progress check-ins
DEFAULT_CHECK_IN_COOLDOWN_HOURS = 168  # 7 days — minimum time between progress check-ins


# ============================================================================
# DB operations for check-ins
# ============================================================================

async def create_check_in(
    goal_id: str,
    type: CheckInType = CheckInType.PROGRESS,
    step_id: str | None = None,
    progress_summary: str = "",
    next_steps: str = "",
    results: str = "",
    questions: list[CheckInQuestion] | None = None,
    status: CheckInStatus = CheckInStatus.SENT,
    created_by: str = "system",
) -> CheckIn:
    """Create a check-in record in the database."""
    pool = await get_pool()
    q_json = json.dumps([q.to_dict() for q in (questions or [])])
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO check_ins
               (goal_id, step_id, type, status, progress_summary, next_steps,
                results, questions, created_by)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
               RETURNING *""",
            goal_id, step_id, type.value, status.value,
            progress_summary, next_steps, results, q_json, created_by,
        )
    check_in = CheckIn.from_row(dict(row))
    await audit("checkin_created", {
        "check_in_id": check_in.id, "type": type.value, "goal_id": goal_id,
    }, goal_id, step_id)
    await publish_event("checkin.created", {
        "check_in_id": check_in.id, "goal_id": goal_id, "type": type.value,
    })
    return check_in


async def get_check_in(check_in_id: str) -> CheckIn | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM check_ins WHERE id = $1", check_in_id)
    return CheckIn.from_row(dict(row)) if row else None


async def list_check_ins(
    goal_id: str | None = None,
    status: CheckInStatus | None = None,
    type: CheckInType | None = None,
    limit: int = 50,
) -> list[CheckIn]:
    pool = await get_pool()
    conditions = []
    params: list = []
    idx = 1

    if goal_id:
        conditions.append(f"goal_id = ${idx}")
        params.append(goal_id)
        idx += 1
    if status:
        conditions.append(f"status = ${idx}")
        params.append(status.value)
        idx += 1
    if type:
        conditions.append(f"type = ${idx}")
        params.append(type.value)
        idx += 1

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT * FROM check_ins{where} ORDER BY created_at DESC LIMIT {limit}"

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [CheckIn.from_row(dict(r)) for r in rows]


async def get_pending_check_ins(goal_id: str | None = None) -> list[CheckIn]:
    """Get all check-ins awaiting a user response."""
    return await list_check_ins(
        goal_id=goal_id,
        status=CheckInStatus.SENT,
    )


async def respond_to_check_in(
    check_in_id: str,
    response: str,
    decisions: list[dict] | None = None,
) -> CheckIn:
    """Record a user's response to a check-in."""
    pool = await get_pool()
    decisions = decisions or []

    # Also update question answers
    check_in = await get_check_in(check_in_id)
    if not check_in:
        raise ValueError(f"Check-in not found: {check_in_id}")

    # Map decisions to question answers
    for d in decisions:
        qid = d.get("question_id")
        for q in check_in.questions:
            if q.id == qid:
                q.answer = d.get("answer", "")

    q_json = json.dumps([q.to_dict() for q in check_in.questions])

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """UPDATE check_ins
               SET status = 'responded', response = $2, decisions = $3,
                   questions = $4, responded_at = NOW()
               WHERE id = $1
               RETURNING *""",
            check_in_id, response, json.dumps(decisions), q_json,
        )

    updated = CheckIn.from_row(dict(row))
    await audit("checkin_responded", {
        "check_in_id": check_in_id,
        "goal_id": check_in.goal_id,
        "response": response[:200],
        "decisions": decisions,
    }, check_in.goal_id)
    await publish_event("checkin.responded", {
        "check_in_id": check_in_id, "goal_id": check_in.goal_id,
    })
    return updated


async def action_check_in(check_in_id: str) -> CheckIn:
    """Mark a check-in as actioned (the system has processed the user's response)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "UPDATE check_ins SET status = 'actioned' WHERE id = $1 RETURNING *",
            check_in_id,
        )
    updated = CheckIn.from_row(dict(row))
    await audit("checkin_actioned", {
        "check_in_id": check_in_id, "goal_id": updated.goal_id,
    }, updated.goal_id)
    return updated


# ============================================================================
# Check-in trigger logic — when to create check-ins
# ============================================================================

async def should_create_progress_check_in(goal: Goal, steps: list[Step]) -> bool:
    """Determine if a progress check-in should be created.

    Triggers when BOTH:
      - At least N steps completed since the last check-in (default N=3)
      - At least cooldown_hours have passed since the last check-in (default 168h = 1 week)

    The cooldown is the dominant constraint for fast-cycling workers.
    Configurable per goal via metadata:
      - check_in_interval: step count threshold (default 3)
      - check_in_cooldown_hours: minimum hours between check-ins (default 168)
    """
    if goal.status != GoalStatus.ACTIVE:
        return False

    completed = [s for s in steps if s.status == StepStatus.COMPLETED]
    if not completed:
        return False

    # Configurable thresholds
    interval = goal.metadata.get("check_in_interval", DEFAULT_CHECK_IN_INTERVAL)
    cooldown_hours = goal.metadata.get("check_in_cooldown_hours", DEFAULT_CHECK_IN_COOLDOWN_HOURS)

    # Find the last check-in for this goal
    recent_check_ins = await list_check_ins(goal_id=goal.id, limit=1)
    if not recent_check_ins:
        # No previous check-in — trigger if we have enough completed steps
        return len(completed) >= interval

    last = recent_check_ins[0]
    last_created = last.created_at
    if hasattr(last_created, 'tzinfo') and last_created.tzinfo is None:
        last_created = last_created.replace(tzinfo=timezone.utc)

    # Time cooldown: must have passed at least cooldown_hours since last check-in
    hours_since_last = (datetime.now(timezone.utc) - last_created).total_seconds() / 3600
    if hours_since_last < cooldown_hours:
        return False

    # Step count: must have completed at least N new steps since last check-in
    new_completed = [
        s for s in completed
        if s.completed_at and _is_after(s.completed_at, last_created)
    ]
    return len(new_completed) >= interval


def _is_after(a: datetime, b: datetime) -> bool:
    """Compare two datetimes, handling timezone-aware vs naive."""
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return a > b


async def should_create_completion_check_in(goal: Goal, steps: list[Step]) -> bool:
    """Determine if a completion check-in should be created.

    Triggers when all steps are completed but no completion check-in exists.
    """
    if goal.status == GoalStatus.COMPLETED:
        return False  # already completed

    if not steps:
        return False

    all_done = all(s.status == StepStatus.COMPLETED for s in steps)
    if not all_done:
        return False

    # Check if a completion check-in already exists
    completion_check_ins = await list_check_ins(
        goal_id=goal.id, type=CheckInType.COMPLETION, limit=1,
    )
    return len(completion_check_ins) == 0


# ============================================================================
# AI-powered check-in generation
# ============================================================================

SYSTEM_PROMPT = """You are Job-Star's check-in generator. Your job is to create a structured progress update for the user based on the goal's current state.

You must output a JSON object with these fields:
{
  "progress_summary": "2-4 sentence summary of what has been accomplished so far",
  "next_steps": "1-2 sentence description of what's planned next",
  "results": "If this is a milestone or completion check-in, summarize the key deliverables. Otherwise, leave empty.",
  "questions": [
    {
      "question": "A specific, actionable question for the user",
      "type": "choice" | "text" | "approval",
      "options": ["option 1", "option 2"],
      "required": true
    }
  ]
}

Rules for questions:
- Ask ONLY questions where the answer would genuinely improve the outcome.
- For a progress check-in: 0-2 questions. Usually about direction or priorities.
- For a clarification: 1-3 questions. The system is blocked or uncertain.
- For a milestone: 0-1 question. Usually "Does this match what you expected?"
- For a completion: 1 question. Always an approval question: "Do you accept this result, or does it need revision?"
- If no questions are needed, return an empty questions array.
- Use "choice" type with 2-4 options when the question has discrete answers.
- Use "text" type for open-ended questions.
- Use "approval" type for completion reviews (options: "Accept", "Needs revision").

Output ONLY the JSON object. No markdown, no explanation."""


async def generate_check_in_content(
    goal: Goal,
    steps: list[Step],
    type: CheckInType,
    gateway_monitor: GatewayMonitor | None = None,
    extra_context: str = "",
) -> dict:
    """Use AI to generate the structured content of a check-in.

    Returns a dict with keys: progress_summary, next_steps, results, questions
    """
    # Build context from steps
    completed = [s for s in steps if s.status == StepStatus.COMPLETED]
    in_progress = [s for s in steps if s.status == StepStatus.IN_PROGRESS]
    pending = [s for s in steps if s.status == StepStatus.PENDING]
    failed = [s for s in steps if s.status == StepStatus.FAILED]

    steps_summary = []
    for s in completed:
        content_preview = ""
        if s.result:
            content = s.result.get("content", "")
            content_preview = content[:300] if content else ""
        steps_summary.append(
            f"  ✓ [{s.model or '?'}] {s.title}: {content_preview}"
        )
    for s in failed:
        steps_summary.append(f"  ✗ [FAILED] {s.title}")
    for s in in_progress:
        steps_summary.append(f"  ◉ [IN PROGRESS] {s.title}")
    for s in pending:
        steps_summary.append(f"  ○ [PENDING] {s.title}")

    steps_text = "\n".join(steps_summary) if steps_summary else "  (no steps yet)"

    user_prompt = f"""GOAL: {goal.title}
DESCRIPTION: {goal.description or '(none)'}
DOMAIN: {goal.domain.value}
URGENCY: {goal.urgency.value}
PROGRESS: {int(goal.progress * 100)}%
CHECK-IN TYPE: {type.value}

STEPS:
{steps_text}

{f"ADDITIONAL CONTEXT:{chr(10)}{extra_context}" if extra_context else ""}

Generate the check-in content as a JSON object."""

    # Route to a model (prefer free/cheap for check-in generation)
    allow_expensive = False
    result = None
    tried: set[str] = set()

    for attempt in range(3):
        routing = await route(
            urgency=Urgency.SOON,  # check-ins are "soon" priority
            request_type="feature",
            description=f"check-in generation for {goal.title}",
            allow_expensive=allow_expensive,
            gateway_monitor=gateway_monitor,
        )
        if not routing.model:
            break

        result = await execute_ai(
            user_prompt, model=routing.model, system_prompt=SYSTEM_PROMPT,
        )
        if result.success:
            break

        if gateway_monitor:
            gateway_monitor.record_failure(routing.model, result.error or "error")
        tried.add(routing.model)
        if gateway_monitor:
            fallback = gateway_monitor.pick_fallback(
                routing.model, required_capability=None,
                prefer_free=True, allow_expensive=False,
            )
            if not fallback or fallback in tried:
                break

    # Fallback: if AI generation fails, create a basic check-in from step data
    if not result or not result.success:
        return _fallback_check_in_content(goal, steps, type)

    # Parse the AI output as JSON
    content = result.content.strip()
    # Strip markdown code fences if present
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return _fallback_check_in_content(goal, steps, type)

    # Convert questions to CheckInQuestion objects
    questions_data = parsed.get("questions", [])
    questions = []
    for qd in questions_data:
        questions.append(CheckInQuestion(
            question=qd.get("question", ""),
            type=qd.get("type", "text"),
            options=qd.get("options", []),
            required=qd.get("required", True),
        ))

    return {
        "progress_summary": parsed.get("progress_summary", ""),
        "next_steps": parsed.get("next_steps", ""),
        "results": parsed.get("results", ""),
        "questions": questions,
    }


def _fallback_check_in_content(
    goal: Goal, steps: list[Step], type: CheckInType,
) -> dict:
    """Generate a basic check-in without AI (when AI is unavailable)."""
    completed = [s for s in steps if s.status == StepStatus.COMPLETED]
    pending = [s for s in steps if s.status == StepStatus.PENDING]
    failed = [s for s in steps if s.status == StepStatus.FAILED]

    summary = f"{len(completed)} of {len(steps)} steps completed ({int(goal.progress * 100)}%)."
    if failed:
        summary += f" {len(failed)} step(s) failed."
    if pending:
        summary += f" {len(pending)} step(s) remaining."

    next_steps = "; ".join(s.title for s in pending[:3])
    if not next_steps:
        next_steps = "No pending steps."

    results = ""
    questions = []

    if type == CheckInType.COMPLETION:
        results = "; ".join(s.title for s in completed)
        questions.append(CheckInQuestion(
            question="Do you accept this result, or does it need revision?",
            type="approval",
            options=["Accept", "Needs revision"],
            required=True,
        ))
    elif type == CheckInType.CLARIFICATION:
        questions.append(CheckInQuestion(
            question="The system encountered issues. How would you like to proceed?",
            type="text",
            required=True,
        ))

    return {
        "progress_summary": summary,
        "next_steps": next_steps,
        "results": results,
        "questions": questions,
    }


# ============================================================================
# Check-in engine — orchestrates generation, triggers, and response processing
# ============================================================================

class CheckInEngine:
    """Manages the check-in lifecycle: trigger, generate, send, process responses."""

    def __init__(self, gateway_monitor: GatewayMonitor | None = None):
        self.gateway_monitor = gateway_monitor or GatewayMonitor()

    async def maybe_create_progress_check_in(
        self, goal: Goal, steps: list[Step],
    ) -> CheckIn | None:
        """Check if a progress check-in is due and create one if so."""
        if not await should_create_progress_check_in(goal, steps):
            return None
        return await self.create_progress_check_in(goal, steps)

    async def create_progress_check_in(
        self, goal: Goal, steps: list[Step],
    ) -> CheckIn:
        """Generate and create a progress check-in for a goal."""
        content = await generate_check_in_content(
            goal, steps, CheckInType.PROGRESS, self.gateway_monitor,
        )
        return await create_check_in(
            goal_id=goal.id,
            type=CheckInType.PROGRESS,
            progress_summary=content["progress_summary"],
            next_steps=content["next_steps"],
            questions=content["questions"],
        )

    async def create_clarification_check_in(
        self, goal: Goal, steps: list[Step],
        step: Step | None = None,
        issue: str = "",
    ) -> CheckIn:
        """Generate and create a clarification check-in when the system is blocked."""
        content = await generate_check_in_content(
            goal, steps, CheckInType.CLARIFICATION, self.gateway_monitor,
            extra_context=f"ISSUE: {issue}" if issue else "",
        )
        return await create_check_in(
            goal_id=goal.id,
            step_id=step.id if step else None,
            type=CheckInType.CLARIFICATION,
            progress_summary=content["progress_summary"],
            next_steps=content["next_steps"],
            questions=content["questions"],
        )

    async def create_milestone_check_in(
        self, goal: Goal, steps: list[Step],
        milestone_description: str = "",
    ) -> CheckIn:
        """Generate and create a milestone check-in."""
        content = await generate_check_in_content(
            goal, steps, CheckInType.MILESTONE, self.gateway_monitor,
            extra_context=f"MILESTONE: {milestone_description}" if milestone_description else "",
        )
        return await create_check_in(
            goal_id=goal.id,
            type=CheckInType.MILESTONE,
            progress_summary=content["progress_summary"],
            next_steps=content["next_steps"],
            results=content["results"],
            questions=content["questions"],
        )

    async def create_completion_check_in(
        self, goal: Goal, steps: list[Step],
    ) -> CheckIn:
        """Generate and create a completion check-in for goal acceptance."""
        content = await generate_check_in_content(
            goal, steps, CheckInType.COMPLETION, self.gateway_monitor,
        )
        return await create_check_in(
            goal_id=goal.id,
            type=CheckInType.COMPLETION,
            progress_summary=content["progress_summary"],
            next_steps=content["next_steps"],
            results=content["results"],
            questions=content["questions"],
        )

    async def process_response(self, check_in_id: str) -> dict:
        """Process a user's response to a check-in and take action.

        Returns a dict describing the action taken.
        """
        check_in = await get_check_in(check_in_id)
        if not check_in:
            raise ValueError(f"Check-in not found: {check_in_id}")

        if check_in.status != CheckInStatus.RESPONDED:
            raise ValueError(f"Check-in is not in 'responded' state (current: {check_in.status.value})")

        goal = await get_goal(check_in.goal_id)
        if not goal:
            raise ValueError(f"Goal not found: {check_in.goal_id}")

        actions = []

        # Extract decisions
        for d in check_in.decisions:
            qid = d.get("question_id", "")
            answer = d.get("answer", "")

            # Find the matching question
            for q in check_in.questions:
                if q.id == qid and q.type == "approval":
                    if check_in.type == CheckInType.COMPLETION:
                        if "accept" in answer.lower():
                            # Accept: mark goal as completed
                            await update_goal_status(goal.id, GoalStatus.COMPLETED)
                            await update_goal_progress(goal.id, 1.0)
                            await audit("goal_accepted", {
                                "check_in_id": check_in_id,
                            }, goal.id)
                            actions.append("goal_accepted")
                        elif "revision" in answer.lower() or "reject" in answer.lower():
                            # Reject: reopen the goal, create a new step for revision
                            await audit("goal_rejected", {
                                "check_in_id": check_in_id,
                                "user_feedback": check_in.response or "",
                            }, goal.id)
                            actions.append("goal_rejected_revision_needed")
                    elif q.type == "approval":
                        actions.append(f"approved: {answer}")

        # Record the user's decision in the decisions log
        await record_decision(
            goal_id=goal.id,
            decision=f"Check-in response ({check_in.type.value}): {check_in.response or '(no text)'}",
            reasoning=json.dumps(check_in.decisions) if check_in.decisions else "",
            alternatives=[{"question": q.question, "answer": q.answer} for q in check_in.questions if q.answer],
            decided_by="user",
        )

        # Mark the check-in as actioned
        await action_check_in(check_in_id)

        # If this was a completion check-in and user accepted, publish event
        if check_in.type == CheckInType.COMPLETION and "goal_accepted" in actions:
            await publish_event("goal.accepted", {
                "goal_id": goal.id, "check_in_id": check_in_id,
            })

        return {
            "check_in_id": check_in_id,
            "goal_id": goal.id,
            "actions": actions,
            "user_response": check_in.response,
            "decisions": check_in.decisions,
        }