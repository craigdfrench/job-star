"""Simplified dashboard: one-command view of everything that matters.

Shows what needs attention, what's happening, and what to do next.
Designed to give the user a grip on the system without digging through
goals, steps, and audit trails.
"""

from __future__ import annotations

from datetime import datetime, timezone

from .db import get_pool, close_pool
from .checkin import get_pending_check_ins
from .gatehouse import check_health


async def render_dashboard() -> str:
    """Render the dashboard as a string."""
    pool = await get_pool()
    lines: list[str] = []

    async with pool.acquire() as conn:
        # Pending check-ins
        pending_cis = await conn.fetch("""
            SELECT ci.id, ci.type, ci.goal_id, ci.progress_summary, g.title as goal_title
            FROM check_ins ci
            LEFT JOIN goals g ON ci.goal_id = g.id
            WHERE ci.status = 'sent'
            ORDER BY ci.created_at DESC
        """)

        # In-progress steps
        in_progress = await conn.fetch("""
            SELECT s.title, g.title as goal_title, s.attempted_at, g.expert
            FROM goal_steps s
            JOIN goals g ON s.goal_id = g.id
            WHERE s.status = 'in_progress'
            ORDER BY s.attempted_at DESC
        """)

        # Active goals summary
        goal_stats = await conn.fetchrow("""
            SELECT
                count(*) FILTER (WHERE status = 'active') as active,
                count(*) FILTER (WHERE status = 'completed') as completed,
                count(*) FILTER (WHERE status = 'abandoned') as abandoned,
                count(*) FILTER (WHERE status = 'blocked') as blocked
            FROM goals
        """)

        # Active goals that need attention (0% with no steps at all, or blocked)
        stuck_goals = await conn.fetch("""
            SELECT g.id, g.title, g.urgency, g.progress
            FROM goals g
            WHERE g.status = 'active'
              AND NOT EXISTS (SELECT 1 FROM goal_steps s WHERE s.goal_id = g.id)
              AND g.progress < 1.0
            ORDER BY CASE g.urgency WHEN 'imperative' THEN 0 WHEN 'soon' THEN 1 ELSE 2 END
            LIMIT 5
        """)

        # Recent activity (last 24h)
        recent_count = await conn.fetchval("""
            SELECT count(*) FROM goal_steps
            WHERE status = 'completed' AND completed_at > NOW() - INTERVAL '24 hours'
        """)

        # Pending steps count
        pending_steps = await conn.fetchval(
            "SELECT count(*) FROM goal_steps WHERE status = 'pending'"
        )

        # Total cost
        total_cost = await conn.fetchval("SELECT COALESCE(SUM(cost), 0) FROM goal_steps")

        # Active workers
        active_workers = await conn.fetchval("""
            SELECT count(*) FROM worker_registry
            WHERE last_heartbeat > NOW() - INTERVAL '5 minutes'
        """)

        # Gateway health
        gateway_ok = await check_health()

    await close_pool()

    # ── Build the dashboard ──────────────────────────────────────────
    lines.append("")
    lines.append("  Job-Star")
    lines.append(f"  {datetime.now().strftime('%A, %B %d — %H:%M')}")
    lines.append("")

    # What needs your attention
    if pending_cis:
        lines.append(f"  [!] {len(pending_cis)} check-in(s) need your response:")
        for ci in pending_cis[:3]:
            type_emoji = {
                "progress": "📊", "clarification": "❓",
                "milestone": "🏁", "completion": "✅",
            }.get(ci["type"], "📋")
            goal = ci["goal_title"] if ci["goal_title"] else str(ci["goal_id"])[:8]
            lines.append(f"      {type_emoji} {str(ci['id'])[:8]}  {goal}")
        if len(pending_cis) > 3:
            lines.append(f"      ...and {len(pending_cis) - 3} more")
        lines.append("")
        lines.append("      Run: job_star review    (guided walkthrough)")
        lines.append("           job_star checkin pending")
        lines.append("")
    else:
        lines.append("  [✓] Nothing needs your attention right now.")
        lines.append("")

    # What's happening
    if in_progress:
        lines.append(f"  [~] {len(in_progress)} step(s) in progress:")
        for s in in_progress[:3]:
            expert = f" [{s['expert']}]" if s["expert"] else ""
            lines.append(f"      {s['title'][:45]}{expert}")
        lines.append("")
    elif active_workers > 0:
        lines.append("  [~] Workers idle — no steps in progress")
        lines.append("")
    else:
        lines.append("  [~] System stopped — no workers running")
        lines.append("       Start: sudo systemctl start job-star-worker")
        lines.append("")

    # Goals summary
    active = goal_stats["active"]
    if active:
        lines.append(f"  [=] {active} active goals, {pending_steps} pending steps")
        if stuck_goals:
            lines.append(f"      Goals that need starting:")
            for g in stuck_goals[:3]:
                lines.append(f"        • {g['title'][:50]}")
            lines.append(f"      Run: job_star work <id>    (start a goal)")
        lines.append("")

    # System health
    cost_str = f"${total_cost:.2f}" if total_cost > 0 else "$0"
    health_emoji = "✓" if gateway_ok else "✗"
    lines.append(f"  [{health_emoji}] {recent_count} steps today, {cost_str} cost, "
                 f"{active_workers} workers, gateway {'up' if gateway_ok else 'down'}")

    # Quick commands
    lines.append("")
    lines.append("  Quick commands:")
    lines.append("    job_star review       Respond to check-ins")
    lines.append("    job_star commentary   AI summary of what's happening")
    lines.append("    job_star list         See all goals")
    lines.append("    job_star add \"title\"  Add a new goal")
    lines.append("")

    return "\n".join(lines)


async def render_review() -> str:
    """Guided review of pending check-ins, one at a time."""
    pending = await get_pending_check_ins()
    await close_pool()

    if not pending:
        return "\n  [✓] No pending check-ins. You're all caught up.\n"

    lines = ["", f"  {len(pending)} check-in(s) to review:", ""]

    for i, ci in enumerate(pending, 1):
        from .db import get_goal
        goal = await get_goal(ci.goal_id)
        goal_title = goal.title if goal else ci.goal_id[:8]

        type_emoji = {
            "progress": "📊", "clarification": "❓",
            "milestone": "🏁", "completion": "✅",
        }.get(ci.type.value, "📋")

        lines.append(f"  ┌─ {i}/{len(pending)}  {type_emoji} {ci.type.value.upper()}")
        lines.append(f"  │ Goal: {goal_title}")
        lines.append(f"  │ ID:   {ci.id[:8]}")
        lines.append(f"  └─")

        if ci.progress_summary:
            lines.append(f"     {ci.progress_summary[:200]}")
        if ci.questions:
            lines.append(f"     Questions:")
            for j, q in enumerate(ci.questions, 1):
                lines.append(f"       {j}. {q.question}")
                if q.options:
                    for k, opt in enumerate(q.options, 1):
                        lines.append(f"          {k}) {opt}")

        lines.append("")
        lines.append(f"     Web: http://job-star.craigdfrench.com/checkin/{ci.id}")
        lines.append(f"     CLI: job_star checkin respond {ci.id[:8]} --feedback '...'")
        lines.append("")

    return "\n".join(lines)