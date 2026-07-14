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


def _goal_to_summary(g, step_counts: dict | None = None, pending_checkin_id: str | None = None) -> GoalSummary:
    sc = step_counts or {}
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
        expert=g.expert,
        requested_by=g.requested_by,
        step_count=sc.get("total", 0),
        completed_steps=sc.get("completed", 0),
        failed_steps=sc.get("failed", 0),
        pending_checkin_id=pending_checkin_id,
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


@router.get("/whoami")
async def whoami(user=Depends(get_current_user)):
    """Return the authenticated user's identity (Tailscale email).

    Used by web pages to show who is logged in.
    """
    return {"authenticated": True, "email": user.email}


@router.post("/intake", response_model=GoalSummary, status_code=status.HTTP_201_CREATED)
async def intake(
    req: IntakeRequest,
    user=Depends(get_current_user),
):
    """Create a new goal through the full intake pipeline (triage + duplicate check).

    Unlike calling create_goal directly, this runs the triage engine to
    auto-assign domain/urgency and detect duplicates. If the request is
    a duplicate of an existing goal, returns 409 Conflict.
    """
    from job_star.intake import intake as do_intake
    from job_star.models import Domain, Urgency

    requested_by = req.requested_by or user.email
    goal, triage_result = await do_intake(
        title=req.title,
        description=req.description,
        source=req.source,
        domain_override=Domain(req.domain) if req.domain else None,
        urgency_override=Urgency(req.urgency) if req.urgency else None,
        metadata=req.metadata,
        requested_by=requested_by,
    )
    if goal is None:
        # Duplicate — triage detected an existing goal
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "This looks like a duplicate of an existing goal.",
                "duplicate_of": triage_result.duplicate_of,
            },
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
    from job_star.db import get_pool
    status_obj = GoalStatus(status) if status else None
    domain_obj = Domain(domain) if domain else None
    urgency_obj = Urgency(urgency) if urgency else None

    goals = await list_goals(status=status_obj, domain=domain_obj, urgency=urgency_obj)
    # Fetch step counts and pending check-ins in bulk for all goals
    counts = {}
    checkins = {}
    if goals:
        goal_ids = [g.id for g in goals]
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT goal_id,
                          count(*) AS total,
                          count(*) FILTER (WHERE status = 'completed') AS completed,
                          count(*) FILTER (WHERE status = 'failed') AS failed
                   FROM goal_steps WHERE goal_id = ANY($1::uuid[]) GROUP BY goal_id""",
                goal_ids,
            )
            for r in rows:
                counts[str(r["goal_id"])] = {"total": r["total"], "completed": r["completed"], "failed": r["failed"]}
            # Fetch pending (sent) check-ins for these goals
            ci_rows = await conn.fetch(
                """SELECT goal_id, id FROM check_ins
                   WHERE status = 'sent' AND goal_id = ANY($1::uuid[])""",
                goal_ids,
            )
            for r in ci_rows:
                checkins[str(r["goal_id"])] = str(r["id"])
    return GoalListResponse(
        goals=[_goal_to_summary(g, counts.get(g.id), checkins.get(g.id)) for g in goals],
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


@router.post("/goals/{goal_id}/abandon")
async def abandon_goal_api(
    goal_id: str,
    user=Depends(get_current_user),
):
    """Mark a goal as abandoned (cancelled). Used for personal goals that
    need human action, or goals no longer wanted."""
    g = await get_goal(goal_id)
    if not g:
        raise HTTPException(status_code=404, detail="Goal not found")
    await update_goal_status(goal_id, GoalStatus.ABANDONED)
    await audit("goal_abandoned", {"via": "api", "user": user.email}, goal_id)
    await publish("goal.abandoned", {"goal_id": goal_id})
    return {"success": True, "goal_id": goal_id, "status": "abandoned"}


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


@router.get("/checkin/{check_in_id}", response_class=HTMLResponse)
async def checkin_page(check_in_id: str):
    """Interactive check-in discussion page — no auth required (tailnet boundary).

    Serves a web page that loads the check-in, shows progress/questions,
    and provides an LLM-powered chat for the user to discuss before responding.
    """
    from pathlib import Path
    html = Path(__file__).parent / "checkin_page.html"
    return HTMLResponse(html.read_text())


@router.get("/checkins", response_class=HTMLResponse)
async def checkins_list_page():
    """List all check-ins — no auth required (tailnet boundary).

    Serves a web page showing all check-ins with status/type filters
    and links to each check-in's discussion page.
    """
    from pathlib import Path
    html = Path(__file__).parent / "checkins_page.html"
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
    # Fetch goal titles so the list page can show them
    goal_ids = {ci.goal_id for ci in check_ins}
    goal_titles = {}
    if goal_ids:
        from job_star.db import get_goal
        for gid in goal_ids:
            g = await get_goal(gid)
            if g:
                goal_titles[gid] = g.title

    return {
        "check_ins": [
            {
                "id": ci.id,
                "goal_id": ci.goal_id,
                "goal_title": goal_titles.get(ci.goal_id, "Unknown goal"),
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



# ============================================================================
# CHECK-IN DISCUSSION — LLM-powered chat for check-in pages (no auth, tailnet)
# ============================================================================

@router.post("/check-ins/{check_in_id}/discuss")
async def discuss_check_in(
    check_in_id: str,
    body: dict,
):
    """Discuss a check-in with an LLM helper. No auth required (tailnet boundary).

    The LLM (gemini-3-5-flash-minimal) has the full check-in context and can
    answer questions about the goal, progress, results, and help the user
    decide how to respond.

    Body: {"message": "user's question or comment"}
    Returns: {"response": "LLM's reply"}
    """
    from job_star.checkin import get_check_in
    from job_star.db import get_goal
    from job_star.gatehouse import execute as execute_ai

    check_in = await get_check_in(check_in_id)
    if not check_in:
        raise HTTPException(status_code=404, detail="Check-in not found")

    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")

    goal = await get_goal(check_in.goal_id)
    goal_title = goal.title if goal else "Unknown goal"
    goal_desc = goal.description if goal else ""

    # Build context for the LLM
    context_parts = [
        f"You are Job-Star's check-in assistant. The user is reviewing a check-in and wants to discuss it.",
        f"Help them understand the progress, the questions, and what the system needs from them.",
        f"Be concise and helpful. Do NOT make decisions for the user — help them decide.",
        f"",
        f"GOAL: {goal_title}",
    ]
    if goal_desc:
        context_parts.append(f"DESCRIPTION: {goal_desc}")

    context_parts.append(f"CHECK-IN TYPE: {check_in.type.value}")
    context_parts.append(f"STATUS: {check_in.status.value}")

    if check_in.progress_summary:
        context_parts.append(f"PROGRESS SUMMARY: {check_in.progress_summary}")

    if check_in.results:
        context_parts.append(f"RESULTS: {check_in.results[:1000]}")

    if check_in.next_steps:
        context_parts.append(f"NEXT STEPS: {check_in.next_steps}")

    if check_in.questions:
        q_lines = ["QUESTIONS:"]
        for i, q in enumerate(check_in.questions, 1):
            q_lines.append(f"  {i}. {q.question}")
            if q.options:
                q_lines.append(f"     Options: {', '.join(q.options)}")
        context_parts.append("\n".join(q_lines))

    system_prompt = "\n".join(context_parts)

    # Try gemini-2.5-flash first (cheap, fast, returns content). Fall back to
    # glm-5.2 if the response is empty (some gemini variants return empty).
    result = None
    # Use swe-1-6 (cognition reasoning model) on the gatehouse-ai gateway.
    # Fall back to glm-5.2 if it fails.
    for m in ("swe-1-6", "glm-5.2"):
        result = await execute_ai(message, model=m, system_prompt=system_prompt)
        if result.success and result.content.strip():
            break

    if result and result.success and result.content.strip():
        from job_star.db import audit
        await audit("checkin_discuss", {
            "check_in_id": check_in_id,
            "model": result.model,
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
        }, check_in.goal_id)
        return {"response": result.content.strip()}

    return {"response": "I'm having trouble connecting to the AI model right now. You can still respond to the check-in directly using the buttons below."}
