"""AI-generated running commentary for job-star.

Produces a natural-language summary of what the system is doing, what it has
done, and what needs user attention. Designed to give the user a clear grip on
the system without digging through goals, steps, and audit trails.

Usage:
    python -m job_star commentary          # Full commentary
    python -m job_star commentary --brief  # One-paragraph summary
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from .db import get_pool, close_pool, audit, publish_event
from .gatehouse import execute as execute_ai, check_health, GatewayMonitor
from .router import route
from .models import Urgency
from .checkin import get_pending_check_ins


BRIEF_SYSTEM_PROMPT = """You are Job-Star's narrator. Give a concise one-paragraph summary of what the system is doing. Write in plain English — no jargon, no bullet points, just a natural paragraph that a busy person can read in 10 seconds and understand what's happening."""

FULL_SYSTEM_PROMPT = """You are Job-Star's narrator. Your job is to give the user a clear, honest picture of what their AI orchestration system is doing right now.

Write in plain English. Be honest — if something is stuck, say so. If nothing is happening, say that. The user is busy and needs to quickly understand:

1. What's the system doing right now? (active work, idle, stuck)
2. What has it done recently? (recent completions)
3. What needs the user's attention? (pending check-ins, blocked goals, failed steps)
4. What's queued up? (pending work, by priority)

Format as a short narrative (not bullet points). Use natural language. Be specific — mention actual goal names and what happened. Don't sugarcoat.

Keep it under 400 words."""


async def _gather_state() -> dict:
    """Gather the current system state from the database."""
    pool = await get_pool()
    state: dict[str, Any] = {}

    async with pool.acquire() as conn:
        # Goal counts
        state["goals"] = {}
        for status in ("active", "completed", "abandoned", "blocked"):
            state["goals"][status] = await conn.fetchval(
                "SELECT count(*) FROM goals WHERE status = $1", status
            )

        # Active goals with details
        rows = await conn.fetch("""
            SELECT g.id, g.title, g.urgency, g.progress, g.expert, g.domain,
                   (SELECT count(*) FROM goal_steps s WHERE s.goal_id = g.id AND s.status = 'pending') as pending_steps,
                   (SELECT count(*) FROM goal_steps s WHERE s.goal_id = g.id AND s.status = 'in_progress') as active_steps,
                   (SELECT count(*) FROM goal_steps s WHERE s.goal_id = g.id AND s.status = 'completed') as done_steps,
                   (SELECT count(*) FROM goal_steps s WHERE s.goal_id = g.id AND s.status = 'failed') as failed_steps,
                   (SELECT max(s.completed_at) FROM goal_steps s WHERE s.goal_id = g.id AND s.status = 'completed') as last_activity
            FROM goals g
            WHERE g.status = 'active'
            ORDER BY CASE g.urgency
                WHEN 'imperative' THEN 0 WHEN 'soon' THEN 1
                WHEN 'idle-opportunistic' THEN 2 ELSE 3 END,
                g.updated_at DESC
        """)
        state["active_goals"] = [dict(r) for r in rows]

        # Recent completions (last 24 hours)
        rows = await conn.fetch("""
            SELECT g.title as goal_title, s.title as step_title, s.model,
                   s.completed_at, g.urgency, g.expert
            FROM goal_steps s
            JOIN goals g ON s.goal_id = g.id
            WHERE s.status = 'completed' AND s.completed_at > NOW() - INTERVAL '24 hours'
            ORDER BY s.completed_at DESC
            LIMIT 20
        """)
        state["recent_completions"] = [dict(r) for r in rows]

        # Failed steps
        rows = await conn.fetch("""
            SELECT g.title as goal_title, s.title as step_title, s.model
            FROM goal_steps s
            JOIN goals g ON s.goal_id = g.id
            WHERE s.status = 'failed'
            ORDER BY s.attempted_at DESC
        """)
        state["failed_steps"] = [dict(r) for r in rows]

        # Blocked goals
        rows = await conn.fetch("""
            SELECT title, blockers FROM goals WHERE status = 'blocked'
        """)
        state["blocked_goals"] = [dict(r) for r in rows]

        # Step summary
        state["steps"] = {}
        for status in ("pending", "in_progress", "completed", "failed"):
            state["steps"][status] = await conn.fetchval(
                "SELECT count(*) FROM goal_steps WHERE status = $1", status
            )

        # Pending check-ins
        state["pending_checkins"] = await conn.fetchval(
            "SELECT count(*) FROM check_ins WHERE status = 'sent'"
        )

        # Worker activity
        rows = await conn.fetch("""
            SELECT worker_id, generation, draining, last_heartbeat,
                   current_step_id IS NOT NULL as has_step
            FROM worker_registry
            WHERE last_heartbeat > NOW() - INTERVAL '5 minutes'
        """)
        state["active_workers"] = [dict(r) for r in rows]

        # Gateway health
        state["gateway_healthy"] = await check_health()

        # Total tokens used
        state["total_tokens"] = await conn.fetchval(
            "SELECT COALESCE(SUM(COALESCE(input_tokens,0) + COALESCE(output_tokens,0)), 0) FROM goal_steps"
        )

        # Total cost
        state["total_cost"] = await conn.fetchval(
            "SELECT COALESCE(SUM(cost), 0) FROM goal_steps"
        )

    await close_pool()
    return state


def _format_state_for_ai(state: dict) -> str:
    """Format the system state into a text prompt for the AI."""
    lines = []

    # System overview
    lines.append(f"SYSTEM STATE as of {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"Goals: {state['goals']['active']} active, {state['goals']['completed']} completed, {state['goals']['abandoned']} abandoned, {state['goals']['blocked']} blocked")
    lines.append(f"Steps: {state['steps']['completed']} completed, {state['steps']['pending']} pending, {state['steps']['in_progress']} in progress, {state['steps']['failed']} failed")
    lines.append(f"Total tokens used: {state['total_tokens']:,}")
    lines.append(f"Total cost: ${state['total_cost']:.4f}")
    lines.append(f"Gateway healthy: {state['gateway_healthy']}")
    lines.append(f"Active workers: {len(state['active_workers'])}")
    lines.append(f"Pending check-ins (need user response): {state['pending_checkins']}")
    lines.append("")

    # Active goals
    if state["active_goals"]:
        lines.append("ACTIVE GOALS:")
        for g in state["active_goals"]:
            progress = int(float(g["progress"]) * 100)
            step_info = f"{g['done_steps']} done, {g['pending_steps']} pending"
            if g["active_steps"]:
                step_info += f", {g['active_steps']} in progress"
            if g["failed_steps"]:
                step_info += f", {g['failed_steps']} failed"
            expert = f" [{g['expert']}]" if g["expert"] else ""
            lines.append(f"  [{g['urgency']}] {g['title']}{expert} — {progress}% ({step_info})")
    lines.append("")

    # Recent completions
    if state["recent_completions"]:
        lines.append(f"RECENT COMPLETIONS (last 24h, showing {min(len(state['recent_completions']), 10)} of {len(state['recent_completions'])}):")
        for c in state["recent_completions"][:10]:
            model = f" [{c['model']}]" if c["model"] else ""
            lines.append(f"  {c['goal_title'][:50]} → {c['step_title'][:40]}{model}")
    else:
        lines.append("RECENT COMPLETIONS: none in the last 24 hours")
    lines.append("")

    # Failed steps
    if state["failed_steps"]:
        lines.append(f"FAILED STEPS ({len(state['failed_steps'])}):")
        for f in state["failed_steps"]:
            lines.append(f"  {f['goal_title'][:50]} → {f['step_title'][:40]}")
    lines.append("")

    # Blocked goals
    if state["blocked_goals"]:
        lines.append("BLOCKED GOALS:")
        for b in state["blocked_goals"]:
            lines.append(f"  {b['title'][:60]} — blockers: {b['blockers']}")
    lines.append("")

    # Workers
    if state["active_workers"]:
        lines.append("ACTIVE WORKERS:")
        for w in state["active_workers"]:
            status = "draining" if w["draining"] else ("working" if w["has_step"] else "idle")
            lines.append(f"  {w['worker_id']} (gen {w['generation']}): {status}")
    else:
        lines.append("ACTIVE WORKERS: none (system is stopped)")

    return "\n".join(lines)


async def generate_commentary(brief: bool = False, gateway_monitor: GatewayMonitor | None = None) -> str:
    """Generate an AI commentary on the current system state.

    Args:
        brief: If True, produce a one-paragraph summary. Otherwise, full commentary.
        gateway_monitor: Optional gateway monitor for model routing.

    Returns:
        The AI-generated commentary as a string.
    """
    state = await _gather_state()
    state_text = _format_state_for_ai(state)

    system_prompt = BRIEF_SYSTEM_PROMPT if brief else FULL_SYSTEM_PROMPT

    # Route to a model (prefer free/cheap)
    monitor = gateway_monitor or GatewayMonitor()
    allow_expensive = False
    result = None
    tried: set[str] = set()

    for attempt in range(3):
        routing = await route(
            urgency=Urgency.SOON,
            request_type="docs",
            description="commentary generation",
            allow_expensive=allow_expensive,
            gateway_monitor=monitor,
        )
        if not routing.model:
            break

        result = await execute_ai(state_text, model=routing.model, system_prompt=system_prompt)
        if result.success:
            break

        monitor.record_failure(routing.model, result.error or "error")
        tried.add(routing.model)
        fallback = monitor.pick_fallback(
            routing.model, required_capability=None,
            prefer_free=True, allow_expensive=False,
        )
        if not fallback or fallback in tried:
            break

    # Fallback: generate a basic commentary without AI
    if not result or not result.success:
        return _fallback_commentary(state)

    return result.content.strip()


def _fallback_commentary(state: dict) -> str:
    """Generate a basic commentary without AI (when AI is unavailable)."""
    active = state["active_goals"]
    recent = state["recent_completions"]
    pending_checkins = state["pending_checkins"]
    workers = state["active_workers"]

    lines = [
        f"Job-Star Commentary — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
    ]

    if workers:
        lines.append(f"System is running with {len(workers)} active worker(s).")
    else:
        lines.append("System is stopped — no workers running.")

    if recent:
        lines.append(f"\n{len(recent)} step(s) completed in the last 24 hours.")
    else:
        lines.append("\nNo steps completed in the last 24 hours.")

    if active:
        lines.append(f"\n{len(active)} active goal(s):")
        for g in active:
            progress = int(float(g["progress"]) * 100)
            lines.append(f"  [{g['urgency']}] {g['title']} — {progress}%")

    if pending_checkins:
        lines.append(f"\n⚠ {pending_checkins} check-in(s) need your response. Run: python3 -m job_star checkin pending")

    if state["failed_steps"]:
        lines.append(f"\n{len(state['failed_steps'])} failed step(s) need attention.")

    return "\n".join(lines)