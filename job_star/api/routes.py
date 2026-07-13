"""FastAPI routes for the Job-Star API."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import HTMLResponse

from job_star.db import (
    create_goal,
    get_goal,
    get_steps,
    list_goals,
    update_goal_status,
    update_goal_progress,
    get_unresolved_conflicts,
    audit,
    enqueue_job,
)
from job_star.models import GoalStatus

from .auth import get_current_user
from .events import publish
from .models import (
    AskRequest,
    AskResponse,
    AnswerRequest,
    CompleteRequest,
    GoalListResponse,
    GoalResponse,
    GoalSummary,
    IntakeRequest,
    WorkRequest,
)


router = APIRouter()


# In-memory question store until a table is added.
# TODO: move to DB schema.
_asks: dict[str, dict] = {}


def _goal_to_summary(g) -> GoalSummary:
    return GoalSummary(
        id=g.id,
        title=g.title,
        description=g.description,
        domain=g.domain.value,
        status=g.status.value,
        urgency=g.urgency.value,
        progress=g.progress,
        created_at=g.created_at,
        updated_at=g.updated_at,
    )


async def _goal_with_steps(goal_id: str) -> dict:
    g = await get_goal(goal_id)
    if not g:
        raise HTTPException(status_code=404, detail="Goal not found")
    steps = await get_steps(goal_id)
    conflicts = await get_unresolved_conflicts(goal_id)
    return {
        "id": g.id,
        "title": g.title,
        "description": g.description,
        "domain": g.domain.value,
        "status": g.status.value,
        "urgency": g.urgency.value,
        "progress": g.progress,
        "created_at": g.created_at,
        "updated_at": g.updated_at,
        "steps": [s.__dict__ for s in steps],
        "conflicts": conflicts,
    }


@router.post("/intake", response_model=GoalSummary, status_code=status.HTTP_201_CREATED)
async def intake(
    req: IntakeRequest,
    user=Depends(get_current_user),
):
    """Create a new goal from an intake request."""
    goal = await create_goal(
        title=req.title,
        description=req.description,
        domain=req.domain,
        urgency=req.urgency,
        source=req.source,
        metadata=req.metadata,
    )
    await publish("goal.created", {"goal_id": goal.id, "title": goal.title})
    return _goal_to_summary(goal)


@router.get("/goals", response_model=GoalListResponse)
async def list_goals_api(
    status: str | None = None,
    domain: str | None = None,
    urgency: str | None = None,
    user=Depends(get_current_user),
):
    """List goals with optional filters."""
    from job_star.models import GoalStatus, Domain, Urgency
    status_obj = GoalStatus(status) if status else None
    domain_obj = Domain(domain) if domain else None
    urgency_obj = Urgency(urgency) if urgency else None

    goals = await list_goals(status=status_obj, domain=domain_obj, urgency=urgency_obj)
    return GoalListResponse(
        goals=[_goal_to_summary(g) for g in goals],
        total=len(goals),
    )


@router.get("/goals/{goal_id}", response_model=GoalResponse)
async def get_goal_api(
    goal_id: str,
    user=Depends(get_current_user),
):
    """Get a single goal with steps and conflicts."""
    data = await _goal_with_steps(goal_id)
    return GoalResponse(**data)


@router.post("/goals/{goal_id}/work")
async def work_on_goal_api(
    goal_id: str,
    req: WorkRequest,
    user=Depends(get_current_user),
):
    """Enqueue a goal for worker execution.

    The independent job-star worker service (`python -m job_star.worker`) picks
    up the job, plans the goal if needed, and executes the steps asynchronously.
    """
    g = await get_goal(goal_id)
    if not g:
        raise HTTPException(status_code=404, detail="Goal not found")

    priority = 0
    if g.urgency.value == "imperative":
        priority = 100
    elif g.urgency.value == "soon":
        priority = 50

    job_id = await enqueue_job(goal_id, kind="plan", priority=priority, payload={"model": req.model})
    await publish("goal.work_started", {"goal_id": goal_id, "job_id": job_id, "model": req.model})

    return {
        "success": True,
        "task_id": job_id,
        "status": "queued",
        "message": "Goal queued for worker execution.",
    }


@router.post("/goals/{goal_id}/complete")
async def complete_goal_api(
    goal_id: str,
    req: CompleteRequest,
    user=Depends(get_current_user),
):
    """Mark a goal as completed."""
    g = await get_goal(goal_id)
    if not g:
        raise HTTPException(status_code=404, detail="Goal not found")
    await update_goal_status(goal_id, GoalStatus.COMPLETED)
    await update_goal_progress(goal_id, 1.0)
    await audit("goal_completed", {"manually": False, "via": "api"}, goal_id)
    await publish("goal.completed", {"goal_id": goal_id})
    return {"success": True, "goal_id": goal_id, "status": "completed"}


@router.post("/ask", response_model=AskResponse, status_code=status.HTTP_201_CREATED)
async def ask_user(
    req: AskRequest,
    user=Depends(get_current_user),
):
    """Ask the user a question and return a question ID."""
    qid = str(uuid4())
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    ask = {
        "id": qid,
        "question": req.question,
        "goal_id": req.goal_id,
        "status": "pending",
        "created_at": now,
    }
    _asks[qid] = ask
    await publish("question.asked", {"question_id": qid, "question": req.question, "goal_id": req.goal_id})
    return AskResponse(
        question_id=qid,
        question=req.question,
        goal_id=req.goal_id,
        status="pending",
        created_at=now,
    )


@router.post("/answer/{question_id}")
async def answer_question(
    question_id: str,
    req: AnswerRequest,
    user=Depends(get_current_user),
):
    """Answer a pending question."""
    ask = _asks.get(question_id)
    if not ask:
        raise HTTPException(status_code=404, detail="Question not found")
    if ask["status"] != "pending":
        raise HTTPException(status_code=409, detail="Question already answered")
    ask["status"] = "answered"
    ask["answer"] = req.answer
    await publish("question.answered", {"question_id": question_id, "answer": req.answer, "goal_id": ask.get("goal_id")})
    return {"success": True, "question_id": question_id, "status": "answered"}


@router.get("/events")
async def events_stream(
    since: str | None = None,
    token: str | None = None,
    user=Depends(get_current_user),
):
    """SSE stream of job-star events, polled from the Postgres events table."""
    from fastapi.responses import StreamingResponse
    from .events import sse_generator

    return StreamingResponse(sse_generator(since_id=since), media_type="text/event-stream")


@router.get("/panel", response_class=HTMLResponse)
async def panel():
    """Lightweight live dashboard — no auth required (tailnet boundary)."""
    from pathlib import Path
    html = Path(__file__).parent / "panel.html"
    return HTMLResponse(html.read_text())


@router.get("/events/recent")
async def recent_events(
    limit: int = 20,
    user=Depends(get_current_user),
):
    """Get recent events as JSON (for panel polling)."""
    from job_star.db import get_events_since
    events = await get_events_since(None, limit=limit)
    # Reverse to newest-first
    events.reverse()
    return {"events": events}


# ============================================================================
# CHECK-IN routes — structured progress dialogue
# ============================================================================

@router.get("/check-ins")
async def list_check_ins_api(
    goal_id: str | None = None,
    status: str | None = None,
    type: str | None = None,
    limit: int = 50,
    user=Depends(get_current_user),
):
    """List check-ins with optional filters."""
    from job_star.checkin import list_check_ins, CheckInStatus, CheckInType
    status_filter = CheckInStatus(status) if status else None
    type_filter = CheckInType(type) if type else None
    check_ins = await list_check_ins(
        goal_id=goal_id, status=status_filter, type=type_filter, limit=limit,
    )
    return {
        "check_ins": [
            {
                "id": ci.id,
                "goal_id": ci.goal_id,
                "step_id": ci.step_id,
                "type": ci.type.value,
                "status": ci.status.value,
                "progress_summary": ci.progress_summary,
                "next_steps": ci.next_steps,
                "results": ci.results,
                "questions": [q.to_dict() for q in ci.questions],
                "response": ci.response,
                "decisions": ci.decisions,
                "responded_at": ci.responded_at,
                "created_at": ci.created_at,
                "is_pending": ci.is_pending,
            }
            for ci in check_ins
        ],
        "total": len(check_ins),
    }


@router.get("/check-ins/{check_in_id}")
async def get_check_in_api(
    check_in_id: str,
    user=Depends(get_current_user),
):
    """Get a single check-in with full details."""
    from job_star.checkin import get_check_in
    ci = await get_check_in(check_in_id)
    if not ci:
        raise HTTPException(status_code=404, detail="Check-in not found")
    return {
        "id": ci.id,
        "goal_id": ci.goal_id,
        "step_id": ci.step_id,
        "type": ci.type.value,
        "status": ci.status.value,
        "progress_summary": ci.progress_summary,
        "next_steps": ci.next_steps,
        "results": ci.results,
        "questions": [q.to_dict() for q in ci.questions],
        "response": ci.response,
        "decisions": ci.decisions,
        "responded_at": ci.responded_at,
        "created_at": ci.created_at,
        "updated_at": ci.updated_at,
        "is_pending": ci.is_pending,
    }


@router.post("/check-ins/{check_in_id}/respond")
async def respond_to_check_in_api(
    check_in_id: str,
    body: dict,
    user=Depends(get_current_user),
):
    """Respond to a check-in with feedback and/or answers to questions.

    Body:
    {
        "response": "free-text feedback",
        "decisions": [
            {"question_id": "abc123", "answer": "Accept"}
        ]
    }
    """
    from job_star.checkin import respond_to_check_in
    from job_star.checkin.engine import CheckInEngine

    response_text = body.get("response", "")
    decisions = body.get("decisions", [])

    if not response_text and not decisions:
        raise HTTPException(status_code=400, detail="Provide response text or decisions")

    updated = await respond_to_check_in(check_in_id, response_text, decisions)

    # Process the response (take action based on answers)
    engine = CheckInEngine()
    result = await engine.process_response(updated.id)

    await publish("checkin.responded", {
        "check_in_id": check_in_id,
        "goal_id": updated.goal_id,
        "actions": result.get("actions", []),
    })

    return {
        "success": True,
        "check_in_id": updated.id,
        "status": updated.status.value,
        "actions": result.get("actions", []),
    }


@router.post("/goals/{goal_id}/check-in")
async def create_check_in_api(
    goal_id: str,
    body: dict,
    user=Depends(get_current_user),
):
    """Create a check-in for a goal.

    Body: {"type": "progress|clarification|milestone|completion", "issue": "..."}
    """
    from job_star.checkin import CheckInType
    from job_star.checkin.engine import CheckInEngine
    from job_star.db import get_goal, get_steps

    goal = await get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    ci_type_str = body.get("type", "progress")
    try:
        ci_type = CheckInType(ci_type_str)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid type: {ci_type_str}")

    steps = await get_steps(goal_id)
    engine = CheckInEngine()

    if ci_type == CheckInType.PROGRESS:
        ci = await engine.create_progress_check_in(goal, steps)
    elif ci_type == CheckInType.CLARIFICATION:
        ci = await engine.create_clarification_check_in(goal, steps, issue=body.get("issue", ""))
    elif ci_type == CheckInType.MILESTONE:
        ci = await engine.create_milestone_check_in(goal, steps, body.get("description", ""))
    elif ci_type == CheckInType.COMPLETION:
        ci = await engine.create_completion_check_in(goal, steps)

    return {
        "success": True,
        "check_in_id": ci.id,
        "type": ci.type.value,
        "status": ci.status.value,
        "questions": len(ci.questions),
    }

