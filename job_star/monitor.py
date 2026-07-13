"""System integrity monitor: a reconciliation loop that keeps the system healthy.

Unlike the per-execution Supervisor (which checks budget/file-paths for each
individual step), this monitor checks SYSTEM-LEVEL health:

  - Runaway loops (a goal creating steps endlessly)
  - Orphaned steps (in_progress but no worker is touching them)
  - Stale goals (0% progress, never planned, sitting for days)
  - Check-in backlog (too many pending, user overwhelmed)
  - Budget exhaustion (goal approaching or over limit)
  - Worker health (no heartbeats = system stopped)
  - Gateway health (can't execute if gateway is down)

Runs periodically (every 5 minutes via systemd timer) and takes SAFE corrective
actions automatically. Unsafe situations create an alert check-in for the user.

Design principle: this is a read-mostly monitor. It fixes obvious drift
(reap orphans, pause runaway loops, expire stale check-ins) but escalates
anything ambiguous to the user via a check-in.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any

from .db import get_pool, close_pool, audit
from .gatehouse import check_health


# ============================================================================
# Thresholds (tuned from the issues we hit)
# ============================================================================

class Thresholds:
    # Runaway loop detection
    MAX_STEPS_PER_GOAL = 60         # a goal with >60 steps is likely looping
    MAX_STEPS_PER_HOUR = 20         # >20 steps completed in 1 hour = loop
    MAX_DUPLICATE_STEP_TITLES = 5   # same title >5 times = duplicate work

    # Orphan reaping
    ORPHAN_STEP_AGE_MINUTES = 10    # in_progress >10 min = orphaned

    # Stale goals
    STALE_GOAL_DAYS = 14            # 0% progress, no updates in 14 days
    STALE_CHECKIN_DAYS = 7          # pending check-in older than 7 days

    # Check-in backlog
    MAX_PENDING_CHECKINS = 5        # stop creating new progress check-ins above this

    # Budget
    BUDGET_WARNING_PCT = 0.8        # flag at 80% of budget

    # Workers
    WORKER_STALE_MINUTES = 5        # no heartbeat in 5 min = stopped


@dataclass
class Finding:
    """A single integrity finding."""
    severity: str  # "critical", "warning", "info"
    category: str  # "runaway_loop", "orphan", "stale_goal", etc.
    goal_id: str | None
    message: str
    fixed: bool = False
    action: str = ""


@dataclass
class MonitorReport:
    """The result of an integrity check run."""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    findings: list[Finding] = field(default_factory=list)
    fixed: int = 0
    escalated: int = 0

    @property
    def is_healthy(self) -> bool:
        return not any(f.severity == "critical" and not f.fixed for f in self.findings)

    def summary(self) -> str:
        crit = sum(1 for f in self.findings if f.severity == "critical")
        warn = sum(1 for f in self.findings if f.severity == "warning")
        info = sum(1 for f in self.findings if f.severity == "info")
        return (f"{crit} critical, {warn} warnings, {info} info — "
                f"{self.fixed} auto-fixed, {self.escalated} escalated")


# ============================================================================
# Integrity checks
# ============================================================================

async def check_runaway_loops(conn) -> list[Finding]:
    """Detect goals that are creating steps in an infinite loop."""
    findings = []

    # Goals with too many total steps
    rows = await conn.fetch("""
        SELECT g.id, g.title, count(s.id) as step_count, g.status
        FROM goals g
        JOIN goal_steps s ON s.goal_id = g.id
        WHERE g.status = 'active'
        GROUP BY g.id, g.title, g.status
        HAVING count(s.id) > $1
    """, Thresholds.MAX_STEPS_PER_GOAL)

    for row in rows:
        findings.append(Finding(
            severity="critical",
            category="runaway_loop",
            goal_id=str(row["id"]),
            message=f"Goal has {row['step_count']} steps (threshold {Thresholds.MAX_STEPS_PER_GOAL}) — likely looping: {row['title']}",
        ))

    # Goals with too many steps completed in the last hour
    rows = await conn.fetch("""
        SELECT g.id, g.title, count(s.id) as recent_count
        FROM goals g
        JOIN goal_steps s ON s.goal_id = g.id
        WHERE g.status = 'active' AND s.status = 'completed'
          AND s.completed_at > NOW() - INTERVAL '1 hour'
        GROUP BY g.id, g.title
        HAVING count(s.id) > $1
    """, Thresholds.MAX_STEPS_PER_HOUR)

    for row in rows:
        findings.append(Finding(
            severity="critical",
            category="runaway_loop",
            goal_id=str(row["id"]),
            message=f"Goal completed {row['recent_count']} steps in the last hour (threshold {Thresholds.MAX_STEPS_PER_HOUR}) — runaway loop: {row['title']}",
        ))

    return findings


async def check_orphaned_steps(conn) -> list[Finding]:
    """Detect in_progress steps that no worker is touching."""
    findings = []
    count = await conn.fetchval("""
        SELECT count(*) FROM goal_steps
        WHERE status = 'in_progress'
          AND attempted_at < NOW() - make_interval(mins => $1)
    """, Thresholds.ORPHAN_STEP_AGE_MINUTES)

    if count > 0:
        findings.append(Finding(
            severity="warning",
            category="orphan",
            goal_id=None,
            message=f"{count} step(s) stuck in_progress for >{Thresholds.ORPHAN_STEP_AGE_MINUTES} min (orphaned)",
        ))
    return findings


async def check_stale_goals(conn) -> list[Finding]:
    """Detect goals that are stale (no progress, no updates)."""
    findings = []

    # Active goals at 0% with no steps, not updated in STALE_GOAL_DAYS
    rows = await conn.fetch("""
        SELECT g.id, g.title, g.updated_at
        FROM goals g
        WHERE g.status = 'active'
          AND g.progress = 0
          AND NOT EXISTS (SELECT 1 FROM goal_steps s WHERE s.goal_id = g.id)
          AND g.updated_at < NOW() - make_interval(days => $1)
        ORDER BY g.updated_at
    """, Thresholds.STALE_GOAL_DAYS)

    for row in rows:
        findings.append(Finding(
            severity="info",
            category="stale_goal",
            goal_id=str(row["id"]),
            message=f"Goal at 0% with no steps, untouched for {Thresholds.STALE_GOAL_DAYS}+ days: {row['title']}",
        ))

    return findings


async def check_checkin_backlog(conn) -> list[Finding]:
    """Detect too many pending check-ins (user is overwhelmed)."""
    findings = []
    pending = await conn.fetchval(
        "SELECT count(*) FROM check_ins WHERE status = 'sent'"
    )

    if pending > Thresholds.MAX_PENDING_CHECKINS:
        findings.append(Finding(
            severity="warning",
            category="checkin_backlog",
            goal_id=None,
            message=f"{pending} pending check-in(s) (threshold {Thresholds.MAX_PENDING_CHECKINS}) — user can't keep up",
        ))

    # Auto-accept completion check-ins older than 7 days
    completion_expired = await conn.execute("""
        UPDATE check_ins SET status = 'actioned', responded_at = NOW(),
               response = 'Auto-accepted after 7-day timeout',
               decisions = '[{"question_id": "auto", "answer": "Accept"}]'::jsonb
        WHERE status = 'sent' AND type = 'completion'
          AND created_at < NOW() - INTERVAL '7 days'
    """)
    if int(completion_expired.split()[-1]) > 0:
        findings.append(Finding(
            severity="info",
            category="checkin_timeout",
            goal_id=None,
            message=f"Auto-accepted {completion_expired.split()[-1]} completion check-in(s) after 7-day timeout",
            fixed=True,
            action="auto-accepted",
        ))

    # Also flag very old pending check-ins
    old = await conn.fetch("""
        SELECT ci.id, ci.type, g.title
        FROM check_ins ci
        LEFT JOIN goals g ON ci.goal_id = g.id
        WHERE ci.status = 'sent'
          AND ci.created_at < NOW() - make_interval(days => $1)
    """, Thresholds.STALE_CHECKIN_DAYS)

    for row in old:
        title = row["title"] or str(row["id"])[:8]
        findings.append(Finding(
            severity="info",
            category="stale_checkin",
            goal_id=None,
            message=f"Pending check-in untouched for {Thresholds.STALE_CHECKIN_DAYS}+ days: {title}",
        ))

    return findings


async def check_budgets(conn) -> list[Finding]:
    """Detect goals approaching or over budget."""
    findings = []
    from .supervisor import BudgetTracker
    budget = BudgetTracker()
    max_tokens = budget.max_tokens_per_goal

    rows = await conn.fetch("""
        SELECT g.id, g.title,
               COALESCE(SUM(COALESCE(s.input_tokens,0) + COALESCE(s.output_tokens,0)), 0) as tokens
        FROM goals g
        JOIN goal_steps s ON s.goal_id = g.id
        WHERE g.status = 'active'
        GROUP BY g.id, g.title
        HAVING COALESCE(SUM(COALESCE(s.input_tokens,0) + COALESCE(s.output_tokens,0)), 0) > $1
    """, int(max_tokens * Thresholds.BUDGET_WARNING_PCT))

    for row in rows:
        pct = int((row["tokens"] / max_tokens) * 100) if max_tokens else 0
        severity = "critical" if row["tokens"] > max_tokens else "warning"
        findings.append(Finding(
            severity=severity,
            category="budget",
            goal_id=str(row["id"]),
            message=f"Goal at {pct}% of token budget ({row['tokens']:,}/{max_tokens:,}): {row['title']}",
        ))

    return findings


async def check_worker_health(conn) -> list[Finding]:
    """Detect if workers are running."""
    findings = []
    active = await conn.fetchval("""
        SELECT count(*) FROM worker_registry
        WHERE last_heartbeat > NOW() - make_interval(mins => $1)
    """, Thresholds.WORKER_STALE_MINUTES)

    if active == 0:
        findings.append(Finding(
            severity="warning",
            category="worker_health",
            goal_id=None,
            message=f"No workers have heartbeated in {Thresholds.WORKER_STALE_MINUTES} min — system may be stopped",
        ))
    return findings


# ============================================================================
# Corrective actions (safe, automatic)
# ============================================================================

async def fix_runaway_loop(conn, goal_id: str) -> bool:
    """Pause a runaway goal so it stops creating steps."""
    await conn.execute(
        "UPDATE goals SET status = 'paused' WHERE id = $1 AND status = 'active'",
        goal_id,
    )
    # Also reap any in_progress steps so they don't hang
    await conn.execute(
        "UPDATE goal_steps SET status = 'pending' WHERE goal_id = $1 AND status = 'in_progress'",
        goal_id,
    )
    return True


async def fix_orphaned_steps(conn) -> int:
    """Reset stale in_progress steps back to pending."""
    result = await conn.execute("""
        UPDATE goal_steps SET status = 'pending'
        WHERE status = 'in_progress'
          AND attempted_at < NOW() - make_interval(mins => $1)
    """, Thresholds.ORPHAN_STEP_AGE_MINUTES)
    return int(result.split()[-1]) if result else 0


async def expire_stale_checkins(conn) -> int:
    """Expire pending check-ins older than the stale threshold."""
    result = await conn.execute("""
        UPDATE check_ins SET status = 'expired'
        WHERE status = 'sent'
          AND created_at < NOW() - make_interval(days => $1)
    """, Thresholds.STALE_CHECKIN_DAYS)
    return int(result.split()[-1]) if result else 0


# ============================================================================
# Main monitor loop
# ============================================================================

async def run_monitor(auto_fix: bool = True) -> MonitorReport:
    """Run all integrity checks and apply safe corrective actions.

    Args:
        auto_fix: If True, apply safe fixes (reap orphans, pause loops,
                  expire stale check-ins). If False, just report.

    Returns a MonitorReport with all findings and what was fixed.
    """
    report = MonitorReport()
    pool = await get_pool()

    async with pool.acquire() as conn:
        # ── Run all checks ────────────────────────────────────────────
        report.findings.extend(await check_runaway_loops(conn))
        report.findings.extend(await check_orphaned_steps(conn))
        report.findings.extend(await check_stale_goals(conn))
        report.findings.extend(await check_checkin_backlog(conn))
        report.findings.extend(await check_budgets(conn))
        report.findings.extend(await check_worker_health(conn))

        # Gateway health
        gateway_ok = await check_health()
        if not gateway_ok:
            report.findings.append(Finding(
                severity="critical",
                category="gateway",
                goal_id=None,
                message="Gateway is down — execution will fail",
            ))

        # ── Auto-reset failed steps after cooldown ──────────────────
        if auto_fix:
            reset_result = await conn.execute("""
                UPDATE goal_steps SET status = 'pending', result = NULL,
                       model = NULL, attempted_at = NULL, completed_at = NULL
                WHERE status = 'failed'
                  AND attempted_at < NOW() - INTERVAL '1 hour'
                  AND goal_id IN (SELECT id FROM goals WHERE status = 'active')
            """)
            reset_count = int(reset_result.split()[-1]) if reset_result else 0
            if reset_count > 0:
                report.findings.append(Finding(
                    severity="info",
                    category="auto_reset_failed",
                    goal_id=None,
                    message=f"Reset {reset_count} failed step(s) to pending after 1-hour cooldown",
                    fixed=True,
                    action=f"reset {reset_count} step(s) to pending",
                ))
                report.fixed += 1

        # ── Apply safe fixes ──────────────────────────────────────────
        if auto_fix:
            # Pause runaway loops
            for f in report.findings:
                if f.category == "runaway_loop" and f.goal_id:
                    if await fix_runaway_loop(conn, f.goal_id):
                        f.fixed = True
                        f.action = "paused goal + reaped in_progress steps"
                        report.fixed += 1

            # Reap orphaned steps
            for f in report.findings:
                if f.category == "orphan":
                    reaped = await fix_orphaned_steps(conn)
                    if reaped > 0:
                        f.fixed = True
                        f.action = f"reset {reaped} step(s) to pending"
                        report.fixed += 1

            # Expire stale check-ins
            for f in report.findings:
                if f.category == "stale_checkin":
                    expired = await expire_stale_checkins(conn)
                    if expired > 0:
                        f.fixed = True
                        f.action = f"expired {expired} check-in(s)"
                        report.fixed += 1

        # Count escalations (critical findings that weren't auto-fixed)
        report.escalated = sum(
            1 for f in report.findings
            if f.severity == "critical" and not f.fixed
        )

    await close_pool()

    # Audit log
    await audit("monitor_run", {
        "findings": len(report.findings),
        "fixed": report.fixed,
        "escalated": report.escalated,
        "summary": report.summary(),
        "healthy": report.is_healthy,
    })

    return report


def format_report(report: MonitorReport) -> str:
    """Format a monitor report for terminal display."""
    status_icon = "✓" if report.is_healthy else "⚠"
    lines = [
        "",
        f"  {status_icon}  System Integrity Monitor",
        f"     {report.timestamp.strftime('%Y-%m-%d %H:%M UTC')}",
        f"     {report.summary()}",
        "",
    ]

    if not report.findings:
        lines.append("     All checks passed. System is healthy.")
        lines.append("")
        return "\n".join(lines)

    # Group by severity
    for severity in ("critical", "warning", "info"):
        items = [f for f in report.findings if f.severity == severity]
        if not items:
            continue
        icon = {"critical": "🔴", "warning": "🟡", "info": "🔵"}[severity]
        lines.append(f"  {icon}  {severity.upper()}")
        for f in items:
            fix_str = f"  → {f.action}" if f.fixed else ""
            lines.append(f"     {f.message}{fix_str}")
        lines.append("")

    return "\n".join(lines)