"""Vikunja sync adapter — two-way integration with tasks.craigdfrench.com.

Pulls undone tasks from the Job-Star Vikunja project into job-star goals,
and pushes results/status back when goals complete or check-ins are created.

Configuration (environment variables):
    VIKUNJA_API_URL    — base URL (default: https://tasks.craigdfrench.com/api/v1)
    VIKUNJA_API_TOKEN  — bearer token (required for sync to run)
    VIKUNJA_PROJECT_ID — project ID to sync (default: 134, the Job-Star project)
"""

from __future__ import annotations

import os
import logging
from datetime import datetime, timezone

import httpx

from .db import (
    create_goal,
    get_goal_by_vikunja_task,
    get_goal,
    list_goals,
    update_goal_status,
    get_pool,
)
from .models import Goal, GoalStatus, Domain, Urgency

log = logging.getLogger(__name__)

VIKUNJA_API_URL = os.environ.get("VIKUNJA_API_URL", "https://tasks.craigdfrench.com/api/v1")
VIKUNJA_PROJECT_ID = int(os.environ.get("VIKUNJA_PROJECT_ID", "134"))


def _token() -> str | None:
    return os.environ.get("VIKUNJA_API_TOKEN")


def _enabled() -> bool:
    """Sync is only active if a token is configured."""
    return bool(_token())


# ============================================================================
# Vikunja API client
# ============================================================================

async def _vikunja_get(path: str) -> dict | list | None:
    token = _token()
    if not token:
        return None
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{VIKUNJA_API_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code != 200:
            log.warning(f"Vikunja GET {path} -> {r.status_code}")
            return None
        return r.json()


async def _vikunja_post(path: str, body: dict | None = None) -> dict | None:
    token = _token()
    if not token:
        return None
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(
            f"{VIKUNJA_API_URL}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body or {},
        )
        if r.status_code not in (200, 201):
            log.warning(f"Vikunja POST {path} -> {r.status_code}: {r.text[:200]}")
            return None
        return r.json()


async def _vikunja_put(path: str, body: dict) -> dict | None:
    token = _token()
    if not token:
        return None
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.put(
            f"{VIKUNJA_API_URL}{path}",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json=body,
        )
        if r.status_code not in (200, 201):
            log.warning(f"Vikunja PUT {path} -> {r.status_code}: {r.text[:200]}")
            return None
        return r.json()


# ============================================================================
# Inbound: Vikunja -> job-star
# ============================================================================

async def sync_from_vikunja() -> int:
    """Pull undone tasks from Vikunja and create goals for new ones.

    Returns the number of new goals created. Tasks already linked to a goal
    are skipped. Done tasks in Vikunja are ignored (we only pick up open work).
    """
    if not _enabled():
        return 0

    tasks = await _vikunja_get(f"/projects/{VIKUNJA_PROJECT_ID}/tasks")
    if not tasks or not isinstance(tasks, list):
        return 0

    created = 0
    for task in tasks:
        if task.get("done"):
            continue
        task_id = task["id"]
        # Skip if already linked to a goal
        existing = await get_goal_by_vikunja_task(task_id)
        if existing:
            continue

        title = task.get("title", "").strip()
        if not title:
            continue
        description = (task.get("description") or "").strip()

        # Infer domain/urgency from the task
        domain = _infer_domain(title, description)
        urgency = _infer_urgency(task.get("priority", 0))

        goal = await create_goal(
            title=title,
            description=description,
            domain=domain,
            urgency=urgency,
            source="vikunja",
            requested_by="vikunja-sync",
            vikunja_task_id=task_id,
        )
        log.info(f"Vikunja sync: created goal {goal.id[:8]} from task {task_id} ({title[:40]})")

        # Comment back to Vikunja so the user knows it was picked up
        await _vikunja_put(
            f"/tasks/{task_id}/comments",
            {"comment": f"Job-Star picked up this task as goal `{goal.id[:8]}`. "
                        f"It will be triaged, planned, and worked on automatically. "
                        f"Check progress at the check-ins page."},
        )
        created += 1

    return created


def _infer_domain(title: str, description: str) -> Domain:
    text = (title + " " + description).lower()
    if any(k in text for k in ("server", "docker", "caddy", "network", "infra", "deploy", "nginx", "proxy")):
        return Domain.INFRA
    if any(k in text for k in ("tax", "renew", "call", "appointment", "pay ", "book ", "schedule")):
        return Domain.PERSONAL
    if any(k in text for k in ("job-star", "jobstar", "orchestrat")):
        return Domain.META
    return Domain.CODING


def _infer_urgency(priority: int) -> Urgency:
    # Vikunja: 0=none, 1=low, 2=medium, 3=high, 4=urgent, 5=DO NOW
    if priority >= 4:
        return Urgency.IMPERATIVE
    if priority >= 2:
        return Urgency.SOON
    return Urgency.IDLE_OPPORTUNISTIC


# ============================================================================
# Outbound: job-star -> Vikunja
# ============================================================================

async def comment_on_vikunja_task(goal: Goal, message: str) -> bool:
    """Add a comment to the linked Vikunja task."""
    if not goal.vikunja_task_id or not _enabled():
        return False
    result = await _vikunja_put(
        f"/tasks/{goal.vikunja_task_id}/comments",
        {"comment": message},
    )
    return result is not None


async def mark_vikunja_task_done(goal: Goal, done: bool = True) -> bool:
    """Mark the linked Vikunja task done (or undone) via the update endpoint."""
    if not goal.vikunja_task_id or not _enabled():
        return False
    result = await _vikunja_post(f"/tasks/{goal.vikunja_task_id}", {"done": done})
    return result is not None


async def push_completion_to_vikunja(goal: Goal, results: str = "", pr_url: str = "") -> None:
    """When a goal completes, update the linked Vikunja task.

    Adds a comment with the results/PR link, and marks the task done.
    """
    if not goal.vikunja_task_id or not _enabled():
        return
    lines = [f"✅ Goal completed: {goal.title}"]
    if results:
        lines.append("")
        lines.append(results[:1500])
    if pr_url:
        lines.append("")
        lines.append(f"PR: {pr_url}")
    lines.append("")
    lines.append(f"Check-in: review at the check-ins page (goal {goal.id[:8]})")
    await comment_on_vikunja_task(goal, "\n".join(lines))
    await mark_vikunja_task_done(goal)


async def push_checkin_to_vikunja(goal: Goal, check_in_type: str, summary: str = "") -> None:
    """When a check-in is created for a Vikunja-linked goal, notify the task."""
    if not goal.vikunja_task_id or not _enabled():
        return
    icon = {"progress": "📊", "clarification": "❓", "milestone": "🏁", "completion": "✅"}.get(check_in_type, "📋")
    msg = f"{icon} Job-Star {check_in_type} check-in created for goal `{goal.id[:8]}`."
    if summary:
        msg += f"\n\n{summary[:800]}"
    msg += "\n\nRespond at the check-ins page."
    await comment_on_vikunja_task(goal, msg)


# ============================================================================
# Reconciliation: sync goal status back to Vikunja on a schedule
# ============================================================================

async def reconcile_vikunja_status() -> int:
    """For all goals linked to Vikunja tasks, sync status.

    - Completed goals -> mark the Vikunja task done
    - Abandoned goals -> add a comment, leave task open
    Returns the number of tasks updated.
    """
    if not _enabled():
        return 0
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM goals WHERE vikunja_task_id IS NOT NULL AND status IN ('completed','abandoned')"
        )
    updated = 0
    for row in rows:
        goal = Goal.from_row(dict(row))
        # Check if the Vikunja task is already done
        task = await _vikunja_get(f"/tasks/{goal.vikunja_task_id}")
        if not task:
            continue
        if goal.status == GoalStatus.COMPLETED and not task.get("done"):
            await comment_on_vikunja_task(goal, f"✅ Goal completed: {goal.title}")
            await mark_vikunja_task_done(goal)
            updated += 1
        elif goal.status == GoalStatus.ABANDONED and not task.get("done"):
            await comment_on_vikunja_task(goal, f"⏸️ Goal abandoned: {goal.title}. The task remains open for human action.")
            updated += 1
    return updated