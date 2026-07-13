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

