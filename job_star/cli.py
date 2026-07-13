#!/usr/bin/env python3
"""Job-Star unified CLI.

Usage:
    python -m job_star add "title" [--urgency soon] [--domain coding]
    python -m job_star list [--status active]
    python -m job_star show <goal-id>
    python -m job_star work <goal-id> [--model ollama/glm-5.2]
    python -m job_star complete <goal-id>
    python -m job_star digest [N]
    python -m job_star conflicts
    python -m job_star status
    python -m job_star idle
"""

from __future__ import annotations

import asyncio
import os
import sys
from typing import Optional

from .models import Domain, GoalStatus, Urgency
from .db import (
    audit, close_pool, create_goal, get_goal, get_steps, get_unresolved_conflicts,
    list_goals, update_goal_status, get_pool,
)
from .orchestrator import Orchestrator


def _parse_args(argv: list[str]) -> tuple[str, list[str], dict[str, str]]:
    """Parse command line arguments."""
    if not argv:
        return "help", [], {}

    command = argv[0]
    positional: list[str] = []
    flags: dict[str, str] = {}

    i = 1
    while i < len(argv):
        if argv[i].startswith("--"):
            key = argv[i][2:]
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                flags[key] = argv[i + 1]
                i += 2
            else:
                flags[key] = "true"
                i += 1
        else:
            positional.append(argv[i])
            i += 1

    return command, positional, flags


# ============================================================================
# COMMANDS
# ============================================================================

async def cmd_add(positional: list[str], flags: dict[str, str]) -> None:
    title = positional[0] if positional else ""
    if not title:
        print('Usage: job_star add "title" [--urgency soon] [--domain coding] [--desc "..."]')
        return

    desc = flags.get("desc", "")
    urgency = Urgency(flags.get("urgency", "soon")) if "urgency" in flags else None
    domain = Domain(flags.get("domain", "coding")) if "domain" in flags else None

    orch = Orchestrator()
    goal, triage = await orch.add_goal(title, desc, urgency_override=urgency, domain_override=domain)

    print(f"  Triage: {triage.rationale}")
    print()

    if goal:
        print(f"  ✦ Goal created")
        print(f"    ID:       {goal.id}")
        print(f"    Title:    {goal.title}")
        print(f"    Domain:   {goal.domain.value}")
        print(f"    Urgency:  {goal.urgency.value}")
        print(f"    Status:   {goal.status.value}")
        if goal.expert:
            print(f"    Expert:   {goal.expert}")
        print()
        print(f"    Work on it: job_star work {goal.id[:8]}")
    else:
        print(f"  ⚠ Duplicate detected (confidence: {triage.duplicate_confidence:.2f})")
        print(f"    Matches existing goal: {triage.duplicate_of}")
        print(f"    No new goal created.")

    await close_pool()


async def cmd_list(positional: list[str], flags: dict[str, str]) -> None:
    status = GoalStatus(flags["status"]) if "status" in flags else None
    domain = Domain(flags["domain"]) if "domain" in flags else None
    urgency = Urgency(flags["urgency"]) if "urgency" in flags else None

    goals = await list_goals(status=status, domain=domain, urgency=urgency)

    if not goals:
        print('  No goals found. Add one with: job_star add "title"')
        await close_pool()
        return

    # Group by urgency
    by_urgency: dict[str, list] = {}
    for g in goals:
        by_urgency.setdefault(g.urgency.value, []).append(g)

    icons = {
        "imperative": "🔴",
        "soon": "🟡",
        "idle-opportunistic": "🟢",
        "timed": "⏰",
    }

    for urgency_val in ["imperative", "soon", "idle-opportunistic", "timed"]:
        items = by_urgency.get(urgency_val)
        if not items:
            continue

        print(f"\n  {icons.get(urgency_val, '⬜')}  {urgency_val.upper()}")
        print(f"  {'─' * 60}")

        for g in items:
            id_short = g.id[:8]
            status_str = "  " if g.status == GoalStatus.ACTIVE else f"[{g.status.value}] "
            progress = f" ({int(g.progress * 100)}%)" if g.progress > 0 else ""
            print(f"  {status_str}{id_short}  {g.title}{progress}")

    print(f"\n  Total: {len(goals)} goals")
    print(f"\n  Show details:  job_star show <id>")
    print(f"  Work on one:   job_star work <id>")

    await close_pool()


async def cmd_show(positional: list[str], flags: dict[str, str]) -> None:
    goal_id = positional[0] if positional else ""
    if not goal_id:
        print("Usage: job_star show <goal-id>")
        await close_pool()
        return

    # Resolve partial UUID
    goal = await _resolve_goal(goal_id)
    if not goal:
        print(f"Goal not found: {goal_id}")
        await close_pool()
        return

    steps = await get_steps(goal.id)
    conflicts = await get_unresolved_conflicts(goal.id)

    print()
    print(f"  ┌─────────────────────────────────────────────────")
    print(f"  │ {goal.title}")
    print(f"  └─────────────────────────────────────────────────")
    print()
    if goal.description:
        print(f"  Description:  {goal.description}")
        print()
    print(f"  ID:           {goal.id}")
    print(f"  Domain:       {goal.domain.value}")
    print(f"  Urgency:      {goal.urgency.value}")
    print(f"  Status:       {goal.status.value}")
    print(f"  Progress:     {int(goal.progress * 100)}%")
    if goal.blockers:
        print(f"  Blockers:     {', '.join(goal.blockers)}")
    print()

    if not steps:
        print(f"  No steps yet. Work on it to auto-plan:")
        print(f"    job_star work {goal.id[:8]}")
    else:
        print(f"  STEPS:")
        print()
        from .models import StepStatus
        icons = {
            StepStatus.COMPLETED: "✓",
            StepStatus.IN_PROGRESS: "◉",
            StepStatus.FAILED: "✗",
            StepStatus.BLOCKED: "⊘",
            StepStatus.PENDING: "○",
        }
        for s in steps:
            icon = icons.get(s.status, "○")
            model = f" [{s.model}]" if s.model else ""
            print(f"    {icon} {s.id[:8]}  {s.title}{model}")
            if s.description:
                print(f"        {s.description}")

    if conflicts:
        print()
        print(f"  ⚠  CONFLICTS DETECTED:")
        for c in conflicts:
            other_id = c["goal_a_id"] if str(c["goal_b_id"]) == goal.id else c["goal_b_id"]
            print(f"    {c['conflict_type']}: {str(other_id)[:8]} — {c.get('description', '(no description)')}")

    print()
    print(f"  Work on it:   job_star work {goal.id[:8]}")
    print()

    await close_pool()


async def cmd_work(positional: list[str], flags: dict[str, str]) -> None:
    goal_id = positional[0] if positional else ""
    if not goal_id:
        print("Usage: job_star work <goal-id>")
        await close_pool()
        return

    goal = await _resolve_goal(goal_id)
    if not goal:
        print(f"Goal not found: {goal_id}")
        await close_pool()
        return

    model = flags.get("model")
    orch = Orchestrator()
    result = await orch.work_on_goal(goal.id, model_override=model)

    if result.success:
        print()
        print(result.content)
        print()
        print(f"  {'─' * 60}")
        print(f"  Model:  {result.model}")
        print(f"  Tokens: {result.input_tokens} in / {result.output_tokens} out")
    else:
        print(f"  ✗ Failed: {result.error}")

    await close_pool()


async def cmd_complete(positional: list[str], flags: dict[str, str]) -> None:
    goal_id = positional[0] if positional else ""
    if not goal_id:
        print("Usage: job_star complete <goal-id>")
        await close_pool()
        return

    goal = await _resolve_goal(goal_id)
    if not goal:
        print(f"Goal not found: {goal_id}")
        await close_pool()
        return

    await update_goal_status(goal.id, GoalStatus.COMPLETED)
    await audit("goal_completed", {"manually": True}, goal.id)
    print(f"  ✦ Goal completed: {goal.title}")

    await close_pool()


async def cmd_digest(positional: list[str], flags: dict[str, str]) -> None:
    limit = int(positional[0]) if positional else 20

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT a.*, g.title as goal_title
               FROM audit_trail a
               LEFT JOIN goals g ON a.goal_id = g.id
               ORDER BY a.timestamp DESC
               LIMIT $1""",
            limit,
        )

    if not rows:
        print("  No events yet. The system is quiet.")
        await close_pool()
        return

    print()
    print(f"  RECENT ACTIVITY (last {limit} events)")
    print(f"  {'─' * 70}")
    print()

    for e in rows:
        from datetime import datetime
        time = e["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
        goal_info = f"  {e['goal_title'][:40]}" if e.get("goal_title") else ""
        model_info = f"  [{e['model']}]" if e.get("model") else ""
        print(f"  {time}  {e['event']}{model_info}{goal_info}")

    print()
    await close_pool()


async def cmd_conflicts(positional: list[str], flags: dict[str, str]) -> None:
    orch = Orchestrator()
    conflicts = await orch.check_conflicts()

    if not conflicts:
        print("  No conflicts detected.")
    else:
        print(f"  Detected {len(conflicts)} conflicts:")
        for goal_a, goal_b, conflict_type, desc in conflicts:
            print(f"    [{conflict_type.value}] {goal_a[:8]} ↔ {goal_b[:8]}: {desc}")

    await close_pool()


async def cmd_status(positional: list[str], flags: dict[str, str]) -> None:
    orch = Orchestrator()
    status = await orch.status()

    print()
    print("  Job-Star System Status")
    print(f"  {'─' * 40}")
    print(f"  Total goals:      {status['total_goals']}")
    print(f"  Active:           {status['active']}")
    print(f"  Completed:        {status['completed']}")
    print(f"  Blocked:          {status['blocked']}")
    print(f"  Gateway healthy:  {'✓' if status['gateway_healthy'] else '✗'}")
    print(f"  Follow-up batch:  {status['followup_batch']}")

    unavailable = status.get("unavailable_models", {})
    if unavailable:
        print(f"  Unavailable models: {len(unavailable)}")
        for name, in_hold in unavailable.items():
            tag = "quota hold" if in_hold else "circuit open"
            print(f"    {name}: {tag}")

    observed = status.get("observed_models", {})
    if observed:
        print(f"  Observed models (x_gatehouse): {len(observed)}")
        for name, qs in observed.items():
            cc = qs.get("cost_class", "?")
            advice = qs.get("routing_advice", "?")
            windows = qs.get("quota_windows", [])
            win_str = ", ".join(f"{w['pool_id']}={w['remaining_pct']:.0f}%" for w in windows)
            print(f"    {name}: {cc} / {advice} / {win_str}")

    print()

    await close_pool()


async def cmd_idle(positional: list[str], flags: dict[str, str]) -> None:
    cycles = int(flags.get("cycles", "1"))
    interval = float(flags.get("interval", "60"))

    orch = Orchestrator()
    print(f"  Running {cycles} idle cycle(s)...")

    for i in range(cycles):
        print(f"\n  Cycle {i + 1}/{cycles}:")
        result = await orch.run_idle_cycle()
        print(f"    Status: {result.get('status', 'unknown')}")

        if result.get("success"):
            print(f"    Model: {result.get('model', '?')}")
            print(f"    Tokens: {result.get('tokens', 0)}")

        if i < cycles - 1:
            await asyncio.sleep(interval)

    await close_pool()


async def cmd_worker(positional: list[str], flags: dict[str, str]) -> None:
    """Distributed worker: continuously claims and executes steps from the shared queue.

    This is what other machines run to contribute spare cycles. It uses
    claim_next_step_any_goal (FOR UPDATE SKIP LOCKED) so multiple workers
    can pull from the same Postgres queue without colliding.

    Flags:
      --urgency <u>   Only work on goals of this urgency (default: any)
      --domain <d>    Only work on goals of this domain (default: any)
      --interval <s>  Seconds to sleep when no work is available (default: 30)
      --cycles <n>    Max cycles before exiting (default: run forever)
      --model <m>     Override model selection
    """
    from .db import claim_next_step_any_goal
    from .models import Domain, Urgency

    urgency = Urgency(flags["urgency"]) if "urgency" in flags else None
    domain = Domain(flags["domain"]) if "domain" in flags else None
    expert = flags.get("expert") or os.environ.get("JOB_STAR_EXPERT")
    interval = float(flags.get("interval", "30"))
    max_cycles = int(flags["cycles"]) if "cycles" in flags else None
    model = flags.get("model")
    worker_id = os.environ.get("JOB_STAR_WORKER", os.environ.get("HOSTNAME", "worker"))
    worker_machine = os.environ.get("JOB_STAR_MACHINE", os.environ.get("HOSTNAME", ""))

    orch = Orchestrator()
    print(f"  Worker '{worker_id}' started. interval={interval}s", flush=True)
    print(f"  Machine: {worker_machine or '(unknown)'}", flush=True)
    if urgency: print(f"  urgency filter: {urgency.value}", flush=True)
    if domain: print(f"  domain filter: {domain.value}", flush=True)
    if expert: print(f"  expert affinity: {expert}", flush=True)
    else: print(f"  expert affinity: generic (unowned goals only)", flush=True)
    if model: print(f"  model override: {model}", flush=True)
    print(flush=True)

    cycle = 0
    while True:
        if max_cycles and cycle >= max_cycles:
            print(f"  Worker '{worker_id}' finished after {cycle} cycles.", flush=True)
            break
        cycle += 1

        claimed = await claim_next_step_any_goal(
            urgency=urgency, domain=domain, expert=expert, worker_machine=worker_machine,
        )
        if not claimed:
            print(f"  [{worker_id}] no work available, sleeping {interval}s...", flush=True)
            await asyncio.sleep(interval)
            continue

        goal, step = claimed
        expert_tag = f" [{goal.expert}]" if goal.expert else ""
        print(f"  [{worker_id}] claimed:{expert_tag} {goal.title[:40]} → {step.title[:40]}", flush=True)

        # Execute via the orchestrator's work_on_goal (it will find the step
        # already in_progress and execute it)
        result = await orch.work_on_goal(goal.id, model_override=model)
        if result.success:
            print(f"  [{worker_id}] done: {step.title[:40]} [{result.model}]", flush=True)
        else:
            print(f"  [{worker_id}] failed: {result.error[:60] if result.error else 'unknown'}", flush=True)

    await close_pool()


async def _resolve_goal(goal_id: str):
    """Resolve a partial UUID to a full goal."""
    if len(goal_id) >= 36:
        return await get_goal(goal_id)

    goals = await list_goals()
    matches = [g for g in goals if g.id.startswith(goal_id)]
    if len(matches) == 0:
        return None
    if len(matches) > 1:
        print(f"Ambiguous goal ID: {goal_id} (matches {len(matches)} goals)")
        return None
    return matches[0]


async def cmd_panel(positional: list[str], flags: dict[str, str]) -> None:
    """Live console dashboard. Press Ctrl+C to exit."""
    from .panel import main as panel_main
    await panel_main()


async def cmd_experts(positional: list[str], flags: dict[str, str]) -> None:
    """List registered experts and their machine pinning."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM experts ORDER BY name")

    if not rows:
        print("  No experts registered.")
    else:
        print()
        print("  Registered Experts")
        print(f"  {'─' * 60}")
        for r in rows:
            machine = r["required_machine"] or "(any machine)"
            ctx = r["context_path"] or "(none)"
            print(f"  {r['name']}")
            print(f"    description: {r['description'] or '(none)'}")
            print(f"    machine:     {machine}")
            print(f"    context:     {ctx}")
            print()

    # Also show executors loaded in code
    from .executors import list_executors, register_defaults
    register_defaults()
    code_executors = list_executors()
    print(f"  Code-registered executors: {list(code_executors.keys())}")
    print()

    await close_pool()


# ============================================================================
# CHECK-IN commands
# ============================================================================

async def cmd_checkin(positional: list[str], flags: dict[str, str]) -> None:
    """Manage check-ins: structured progress dialogue.

    Usage:
      job_star checkin list [--goal <id>] [--status pending]
      job_star checkin show <checkin-id>
      job_star checkin respond <checkin-id> [--answer q1="A" --answer q2="B"] [--feedback "text"]
      job_star checkin create <goal-id> [--type progress|clarification|milestone|completion]
    """
    from .checkin import (
        CheckInType, CheckInStatus, list_check_ins, get_check_in,
        respond_to_check_in, create_check_in, get_pending_check_ins,
    )
    from .checkin.engine import CheckInEngine
    from .db import get_goal, get_steps

    subcommand = positional[0] if positional else "list"
    args = positional[1:]

    if subcommand == "list":
        goal_id = flags.get("goal")
        status_filter = CheckInStatus(flags["status"]) if "status" in flags else None

        # Resolve goal ID if partial
        if goal_id:
            goal = await _resolve_goal(goal_id)
            goal_id = goal.id if goal else goal_id

        check_ins = await list_check_ins(goal_id=goal_id, status=status_filter)

        if not check_ins:
            print("  No check-ins found.")
            await close_pool()
            return

        print(f"\n  {'─' * 70}")
        print(f"  CHECK-INS ({len(check_ins)})")
        print(f"  {'─' * 70}")

        for ci in check_ins:
            type_icon = {
                CheckInType.PROGRESS: "📊",
                CheckInType.CLARIFICATION: "❓",
                CheckInType.MILESTONE: "🏁",
                CheckInType.COMPLETION: "✅",
            }.get(ci.type, "📋")

            status_str = ci.status.value
            if ci.is_pending:
                status_str = "⏳ awaiting response"

            q_count = len(ci.questions)
            q_str = f"{q_count} question{'s' if q_count != 1 else ''}" if q_count else "no questions"

            print(f"  {type_icon} {ci.id[:8]}  [{status_str}]  {ci.type.value}  ({q_str})")
            if ci.progress_summary:
                print(f"           {ci.progress_summary[:80]}...")

        print(f"\n  Show details:  job_star checkin show <id>")
        print(f"  Respond:       job_star checkin respond <id> --feedback '...'\n")

    elif subcommand == "show":
        ci_id = args[0] if args else ""
        if not ci_id:
            print("Usage: job_star checkin show <checkin-id>")
            await close_pool()
            return

        # Resolve partial UUID
        all_cis = await list_check_ins(limit=200)
        matches = [c for c in all_cis if c.id.startswith(ci_id)]
        if len(matches) == 0:
            print(f"Check-in not found: {ci_id}")
            await close_pool()
            return
        ci = matches[0]

        goal = await get_goal(ci.goal_id)
        goal_title = goal.title if goal else ci.goal_id[:8]
        print(ci.format(goal_title))

        if ci.is_pending:
            print(f"  Respond:  job_star checkin respond {ci.id[:8]} --feedback 'your response'")
            print()

    elif subcommand == "respond":
        ci_id = args[0] if args else ""
        if not ci_id:
            print("Usage: job_star checkin respond <checkin-id> [--answer qid=val] [--feedback 'text']")
            await close_pool()
            return

        # Resolve partial UUID
        all_cis = await list_check_ins(limit=200)
        matches = [c for c in all_cis if c.id.startswith(ci_id)]
        if len(matches) == 0:
            print(f"Check-in not found: {ci_id}")
            await close_pool()
            return
        ci = matches[0]

        if not ci.is_pending:
            print(f"  This check-in is {ci.status.value} (not awaiting response).")
            await close_pool()
            return

        # Parse answers from --answer flags (format: --answer q1=choice A)
        # Multiple --answer flags are supported
        decisions = []
        feedback = flags.get("feedback", "")

        # The _parse_args stores repeated --answer as the last one wins.
        # For multiple answers, we need to parse sys.argv directly.
        import sys as _sys
        raw_answers = []
        i = 0
        argv = _sys.argv
        while i < len(argv):
            if argv[i] == "--answer" and i + 1 < len(argv):
                raw_answers.append(argv[i + 1])
                i += 2
            else:
                i += 1

        for raw in raw_answers:
            if "=" in raw:
                qid, answer = raw.split("=", 1)
                # Match question ID (partial match)
                for q in ci.questions:
                    if q.id.startswith(qid) or qid == q.id:
                        # If answer is a number and question has options, map it
                        if answer.strip().isdigit() and q.options:
                            idx_num = int(answer.strip()) - 1
                            if 0 <= idx_num < len(q.options):
                                answer = q.options[idx_num]
                        decisions.append({"question_id": q.id, "answer": answer.strip()})
                        break
            elif ci.questions:
                # No qid= prefix — if there's only one question, assign to it
                answer = raw.strip()
                q = ci.questions[0]
                # If answer is a number and question has options, map it
                if answer.isdigit() and q.options:
                    idx_num = int(answer) - 1
                    if 0 <= idx_num < len(q.options):
                        answer = q.options[idx_num]
                decisions.append({"question_id": q.id, "answer": answer})

        # Also parse --feedback
        if not feedback:
            # Check if feedback was passed as positional after the ID
            if len(args) > 1:
                feedback = " ".join(args[1:])

        if not decisions and not feedback:
            print("  Provide at least one --answer qid=value or --feedback 'text'")
            print(f"  Pending questions:")
            for i, q in enumerate(ci.questions, 1):
                print(f"    {i}. [{q.id}] {q.question}")
                if q.options:
                    for j, opt in enumerate(q.options, 1):
                        print(f"       {j}) {opt}")
            await close_pool()
            return

        updated = await respond_to_check_in(ci.id, feedback, decisions)
        print(f"  ✦ Response recorded for check-in {updated.id[:8]}")

        # Process the response (take action based on answers)
        engine = CheckInEngine()
        result = await engine.process_response(updated.id)

        if result["actions"]:
            print(f"  Actions taken: {', '.join(result['actions'])}")

        print()

    elif subcommand == "create":
        goal_id = args[0] if args else ""
        if not goal_id:
            print("Usage: job_star checkin create <goal-id> [--type progress|clarification|milestone|completion]")
            await close_pool()
            return

        goal = await _resolve_goal(goal_id)
        if not goal:
            print(f"Goal not found: {goal_id}")
            await close_pool()
            return

        steps = await get_steps(goal.id)
        ci_type = CheckInType(flags.get("type", "progress"))

        engine = CheckInEngine()
        if ci_type == CheckInType.PROGRESS:
            ci = await engine.create_progress_check_in(goal, steps)
        elif ci_type == CheckInType.CLARIFICATION:
            ci = await engine.create_clarification_check_in(goal, steps, issue=flags.get("issue", ""))
        elif ci_type == CheckInType.MILESTONE:
            ci = await engine.create_milestone_check_in(goal, steps, flags.get("description", ""))
        elif ci_type == CheckInType.COMPLETION:
            ci = await engine.create_completion_check_in(goal, steps)

        print(ci.format(goal.title))
        print(f"  Respond:  job_star checkin respond {ci.id[:8]} --feedback '...'\n")

    elif subcommand == "pending":
        """Show all check-ins awaiting a response across all goals."""
        pending = await get_pending_check_ins()
        if not pending:
            print("  No pending check-ins. The system is not waiting for your input.")
        else:
            print(f"\n  {'─' * 70}")
            print(f"  PENDING CHECK-INS ({len(pending)})")
            print(f"  {'─' * 70}")
            for ci in pending:
                goal = await get_goal(ci.goal_id)
                goal_title = goal.title if goal else ci.goal_id[:8]
                type_icon = {
                    CheckInType.PROGRESS: "📊",
                    CheckInType.CLARIFICATION: "❓",
                    CheckInType.MILESTONE: "🏁",
                    CheckInType.COMPLETION: "✅",
                }.get(ci.type, "📋")
                print(f"  {type_icon} {ci.id[:8]}  {ci.type.value}  →  {goal_title[:40]}")
                if ci.questions:
                    print(f"           {len(ci.questions)} question(s) pending")
            print(f"\n  Respond:  job_star checkin respond <id> --feedback '...'\n")

    await close_pool()


# ============================================================================
# UPGRADE command
# ============================================================================

async def cmd_upgrade(positional: list[str], flags: dict[str, str]) -> None:
    """Safe upgrade: pre-flight -> drain -> reap -> migrate -> restart -> verify."""
    from .upgrade import run_upgrade, preflight_checks, reap_stale_steps

    if flags.get("check"):
        results = await preflight_checks()
        print(f"\n  Pre-flight results:")
        for k, v in results.items():
            if isinstance(v, list):
                for item in v:
                    print(f"    {k}: {item}")
            else:
                print(f"    {k}: {v}")
        await close_pool()
        return

    if flags.get("reap"):
        reaped = await reap_stale_steps()
        if reaped > 0:
            print(f"  Reaped {reaped} orphaned step(s) -> reset to pending")
        else:
            print(f"  No orphaned steps found.")
        await close_pool()
        return

    await run_upgrade(
        commit=bool(flags.get("commit")),
        dry_run=False,
        reap_only=False,
    )


# ============================================================================
# COMMENTARY command
# ============================================================================

async def cmd_commentary(positional: list[str], flags: dict[str, str]) -> None:
    """AI-generated running commentary on what job-star is doing.

    Usage:
      job_star commentary          Full commentary
      job_star commentary --brief  One-paragraph summary
    """
    from .commentary import generate_commentary
    brief = bool(flags.get("brief"))
    text = await generate_commentary(brief=brief)
    print(text)
    print()
    await close_pool()



COMMANDS = {
    "add": cmd_add,
    "list": cmd_list,
    "show": cmd_show,
    "work": cmd_work,
    "complete": cmd_complete,
    "digest": cmd_digest,
    "conflicts": cmd_conflicts,
    "status": cmd_status,
    "idle": cmd_idle,
    "worker": cmd_worker,
    "panel": cmd_panel,
    "experts": cmd_experts,
    "checkin": cmd_checkin,
    "upgrade": cmd_upgrade,
    "commentary": cmd_commentary,
}


def main():
    command, positional, flags = _parse_args(sys.argv[1:])

    if command == "help" or command not in COMMANDS:
        print("""
  Job-Star v0.1.0 — Constrained, supervised, goal-oriented AI orchestration

  USAGE:
    python -m job_star <command> [args] [flags]

  COMMANDS:
    add "title"              Add a goal through the full intake pipeline
      --urgency <u>            imperative | soon | idle-opportunistic | timed
      --domain <d>             coding | personal | infra | meta
      --desc "description"

    list [--status <s>]      List all goals
         [--domain <d>]
         [--urgency <u>]

    show <id>                Show goal details and steps

    work <id>                Auto-plan + execute next step
      --model <model>         Override model selection

    complete <id>            Mark a goal as completed

    digest [N]               Show last N audit events (default: 20)

    conflicts                Run conflict detection across all goals

    status                   Show system status

    idle [--cycles N]        Run N idle loop cycles (default: 1)
         [--interval S]       Sleep S seconds between cycles (default: 60)

    worker [--urgency <u>]   Distributed worker: continuously claim & execute steps
           [--domain <d>]      from the shared queue. Other machines run this to
           [--interval S]      contribute spare cycles. (default: run forever)
           [--cycles N]
           [--model <m>]

    panel                   Live console dashboard (goals, workers, events, queue)
      [--interval S]       Refresh seconds (default 5)

    checkin list [--goal <id>]  List check-ins (structured progress dialogue)
              [--status pending]
    checkin show <id>           Show a check-in with questions and your response
    checkin pending           Show all check-ins awaiting your response
    checkin create <goal-id>   Create a check-in for a goal
              [--type progress|clarification|milestone|completion]
    checkin respond <id>       Respond to a check-in
              [--answer qid=value]  Answer a specific question
              [--feedback "text"]  Free-text feedback

    commentary [--brief]      AI-generated summary of what job-star is doing\n\n    upgrade [--check]        Safe upgrade: pre-flight → drain → reap → migrate → restart
            [--reap]           Reap orphaned steps only
            [--commit]         Commit code before upgrading

  ENVIRONMENT:
    GATEHOUSE_API_URL          Gatehouse-AI endpoint
    JOB_STAR_MODEL              Default model override
    DATABASE_URL                Postgres connection string

  The loop begins. 🦞
""")
        return

    handler = COMMANDS[command]
    asyncio.run(handler(positional, flags))


if __name__ == "__main__":
    main()