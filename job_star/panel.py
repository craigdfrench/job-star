"""Lightweight terminal dashboard for Job-Star.

Shows live system status: goals, workers, events, job queue.
Updates every N seconds. Uses rich for rendering.

Usage:
    python -m job_star.panel
    python -m job_star.panel --interval 3
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime

import asyncpg
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

DEFAULT_DSN = os.environ.get("DATABASE_URL", "postgresql://jobstar:jobstar@localhost:5432/job_star")
REFRESH = float(os.environ.get("JOB_STAR_PANEL_INTERVAL", "5"))

# Colors matching the web panel
DIM = "dim"
GREEN = "green"
RED = "red"
YELLOW = "yellow"
ACCENT = "cyan"
ORANGE = "dark_orange"


async def fetch_stats(conn: asyncpg.Connection) -> dict:
    total = await conn.fetchval("SELECT count(*) FROM goals")
    active = await conn.fetchval("SELECT count(*) FROM goals WHERE status='active'")
    completed = await conn.fetchval("SELECT count(*) FROM goals WHERE status='completed'")
    blocked = await conn.fetchval("SELECT count(*) FROM goals WHERE status='blocked'")
    queue_pending = await conn.fetchval("SELECT count(*) FROM job_queue WHERE status='pending'")
    queue_claimed = await conn.fetchval("SELECT count(*) FROM job_queue WHERE status='claimed'")
    steps_pending = await conn.fetchval("SELECT count(*) FROM goal_steps WHERE status='pending'")
    steps_progress = await conn.fetchval("SELECT count(*) FROM goal_steps WHERE status='in_progress'")
    return {
        "total": total, "active": active, "completed": completed, "blocked": blocked,
        "queue_pending": queue_pending, "queue_claimed": queue_claimed,
        "steps_pending": steps_pending, "steps_progress": steps_progress,
    }


async def fetch_active_goals(conn: asyncpg.Connection) -> list[dict]:
    rows = await conn.fetch("""
        SELECT id, title, status, urgency, progress, expert,
               (SELECT count(*) FROM goal_steps s WHERE s.goal_id=g.id AND s.status='pending') as pending_steps,
               (SELECT count(*) FROM goal_steps s WHERE s.goal_id=g.id AND s.status='in_progress') as active_steps
        FROM goals g
        WHERE status='active'
        ORDER BY CASE urgency
            WHEN 'imperative' THEN 0 WHEN 'soon' THEN 1
            WHEN 'idle-opportunistic' THEN 2 ELSE 3 END, updated_at DESC
        LIMIT 20
    """)
    return [dict(r) for r in rows]


async def fetch_recent_events(conn: asyncpg.Connection, limit: int = 15) -> list[dict]:
    rows = await conn.fetch("""
        SELECT type, payload, created_at FROM events
        ORDER BY created_at DESC LIMIT $1
    """, limit)
    return [dict(r) for r in rows]


async def fetch_workers(conn: asyncpg.Connection) -> list[tuple[str, str, int]]:
    """Infer workers from recent audit_trail step_claimed events."""
    rows = await conn.fetch("""
        SELECT details->>'worker' as worker, max(timestamp) as last_seen,
               count(*) as claim_count
        FROM audit_trail
        WHERE event='step_claimed' AND details->>'worker' IS NOT NULL
          AND timestamp > NOW() - INTERVAL '1 hour'
        GROUP BY worker
        ORDER BY last_seen DESC
    """)
    return [(r["worker"] or "?", str(r["last_seen"]), r["claim_count"]) for r in rows]


async def fetch_job_queue(conn: asyncpg.Connection, limit: int = 10) -> list[dict]:
    rows = await conn.fetch("""
        SELECT q.id, q.kind, q.status, q.priority, q.goal_id,
               g.title as goal_title, q.created_at, q.claimed_at
        FROM job_queue q
        LEFT JOIN goals g ON q.goal_id = g.id
        WHERE q.status IN ('pending', 'claimed')
        ORDER BY q.priority DESC, q.created_at DESC
        LIMIT $1
    """, limit)
    return [dict(r) for r in rows]


def urgency_color(u: str) -> str:
    return {"imperative": RED, "soon": YELLOW, "idle-opportunistic": GREEN}.get(u, DIM)


def short_id(s: str) -> str:
    return str(s)[:8] if s else ""


def build_dashboard(
    stats: dict,
    goals: list[dict],
    events: list[dict],
    workers: list[tuple],
    queue: list[dict],
) -> Layout:
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
    )
    layout["body"].split_row(
        Layout(name="left"),
        Layout(name="right"),
    )
    layout["left"].split_column(
        Layout(name="stats", size=10),
        Layout(name="goals"),
    )
    layout["right"].split_column(
        Layout(name="workers", size=8),
        Layout(name="queue", size=14),
        Layout(name="events"),
    )

    now = datetime.now().strftime("%H:%M:%S")

    # Header
    header = Text.assemble(
        ("● ", GREEN), ("Job-Star Console  ", "bold"),
        (f"updated {now}  ", DIM),
        (f"refresh {REFRESH:.0f}s", DIM),
    )
    layout["header"].update(Panel(header, style=ACCENT, border_style="dim"))

    # Stats
    stats_tbl = Table(show_header=False, box=None, padding=(0, 1))
    stats_tbl.add_column(style=DIM, width=20)
    stats_tbl.add_column(style="bold")
    stats_tbl.add_row("Total goals", str(stats["total"]))
    stats_tbl.add_row("Active", str(stats["active"]))
    stats_tbl.add_row("Completed", str(stats["completed"]))
    if stats["blocked"]:
        stats_tbl.add_row("Blocked", Text(str(stats["blocked"]), style=RED))
    stats_tbl.add_row("Steps pending", str(stats["steps_pending"]))
    stats_tbl.add_row("Steps in progress", Text(str(stats["steps_progress"]), style=ACCENT))
    stats_tbl.add_row("Queue pending", str(stats["queue_pending"]))
    if stats["queue_claimed"]:
        stats_tbl.add_row("Queue claimed", Text(str(stats["queue_claimed"]), style=ACCENT))
    layout["stats"].update(Panel(stats_tbl, title="Status", border_style="dim"))

    # Active goals
    goals_tbl = Table(box=None, padding=(0, 1), show_lines=False)
    goals_tbl.add_column("ID", style=DIM, width=8)
    goals_tbl.add_column("Goal", ratio=3)
    goals_tbl.add_column("Urgency", width=12)
    goals_tbl.add_column("Steps", width=8, justify="right")
    goals_tbl.add_column("Progress", width=10, justify="right")

    for g in goals:
        u = g["urgency"]
        uc = urgency_color(u)
        progress = g["progress"] or 0
        bar_len = 10
        filled = int(progress * bar_len)
        bar = "█" * filled + "░" * (bar_len - filled)
        step_info = f"{g['active_steps']}a/{g['pending_steps']}p"
        goals_tbl.add_row(
            short_id(g["id"]),
            Text(g["title"][:50], overflow="ellipsis"),
            Text(u, style=uc),
            step_info,
            f"{bar} {int(progress*100):>3}%",
        )

    if not goals:
        goals_tbl.add_row("", Text("No active goals", style=DIM), "", "", "")

    layout["goals"].update(Panel(goals_tbl, title=f"Active Goals ({len(goals)})", border_style="dim"))

    # Workers
    w_tbl = Table(box=None, padding=(0, 1))
    w_tbl.add_column("", width=2)
    w_tbl.add_column("Worker", style="bold")
    w_tbl.add_column("Claims", justify="right", width=6)
    w_tbl.add_column("Last seen", style=DIM, width=10)
    if workers:
        for name, last, count in workers:
            w_tbl.add_row("●", name, str(count), last[11:19] if last else "")
    else:
        w_tbl.add_row("●", "nexus", "-", "-")
    layout["workers"].update(Panel(w_tbl, title="Workers", border_style="dim"))

    # Job queue
    q_tbl = Table(box=None, padding=(0, 1))
    q_tbl.add_column("Goal", style=DIM, width=8)
    q_tbl.add_column("Kind", width=6)
    q_tbl.add_column("Status", width=10)
    q_tbl.add_column("Title", ratio=2)
    if queue:
        for q in queue:
            color = ACCENT if q["status"] == "claimed" else YELLOW
            q_tbl.add_row(
                short_id(q["goal_id"]),
                q["kind"],
                Text(q["status"], style=color),
                Text((q["goal_title"] or "")[:30], overflow="ellipsis"),
            )
    else:
        q_tbl.add_row("", "", Text("Queue empty", style=DIM), "")
    layout["queue"].update(Panel(q_tbl, title="Job Queue", border_style="dim"))

    # Events
    e_tbl = Table(box=None, padding=(0, 1))
    e_tbl.add_column("Time", style=DIM, width=8)
    e_tbl.add_column("Event", style=ACCENT, width=18)
    e_tbl.add_column("Goal", style=DIM, width=8)
    for e in events:
        ts = e["created_at"]
        ts_str = ts.strftime("%H:%M:%S") if hasattr(ts, "strftime") else str(ts)[11:19]
        payload = e["payload"] or {}
        if isinstance(payload, str):
            import json
            payload = json.loads(payload) if payload else {}
        gid = payload.get("goal_id", "") if isinstance(payload, dict) else ""
        e_tbl.add_row(ts_str, e["type"], short_id(str(gid)) if gid else "")
    if not events:
        e_tbl.add_row("", Text("No events", style=DIM), "")
    layout["events"].update(Panel(e_tbl, title="Live Events", border_style="dim"))

    return layout


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Job-Star console panel")
    parser.add_argument("--interval", type=float, default=REFRESH, help="Refresh seconds (default 5)")
    parser.add_argument("--dsn", default=DEFAULT_DSN, help="Postgres DSN")
    args = parser.parse_args()

    console = Console()
    conn = await asyncpg.connect(dsn=args.dsn)

    try:
        with Live(console=console, refresh_per_second=1, screen=False) as live:
            while True:
                try:
                    stats = await fetch_stats(conn)
                    goals = await fetch_active_goals(conn)
                    events = await fetch_recent_events(conn)
                    workers = await fetch_workers(conn)
                    queue = await fetch_job_queue(conn)
                    layout = build_dashboard(stats, goals, events, workers, queue)
                    live.update(layout)
                except Exception as e:
                    # Don't crash on transient DB errors — show error and keep going
                    live.update(Panel(f"[red]Refresh error:[/red] {e}\n[dim]retrying in {args.interval}s...[/dim]",
                                      title="Job-Star Console", border_style="red"))
                await asyncio.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        await conn.close()
        console.print("\n[dim]Panel stopped.[/dim]")


if __name__ == "__main__":
    asyncio.run(main())