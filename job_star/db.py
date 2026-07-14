"""Database client for Job-Star.

Talks to the same Postgres database that the TypeScript CLI uses.
All components share this single connection pool.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional
from uuid import UUID

import asyncpg

from .models import (
    Conflict,
    ConflictResolution,
    ConflictType,
    Domain,
    Goal,
    GoalStatus,
    Step,
    StepStatus,
    Urgency,
)

# Connection string — same as the TypeScript CLI
DEFAULT_DSN = "postgresql://jobstar:jobstar@localhost:5432/job_star"

_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None or getattr(_pool, '_closed', False):
        dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
        _pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=2,
            max_size=10,
            command_timeout=30,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


# ============================================================================
# AUDIT — every significant action gets logged
# ============================================================================

async def audit(
    event: str,
    details: dict[str, Any] | None = None,
    goal_id: str | UUID | None = None,
    step_id: str | UUID | None = None,
    model: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost: float = 0.0,
) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO audit_trail (goal_id, step_id, event, details, model,
               input_tokens, output_tokens, cost)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            str(goal_id) if goal_id else None,
            str(step_id) if step_id else None,
            event,
            json.dumps(details or {}),
            model,
            input_tokens,
            output_tokens,
            cost,
        )


# ============================================================================
# GOAL operations
# ============================================================================

async def create_goal(
    title: str,
    description: str | None = None,
    domain: Domain = Domain.CODING,
    urgency: Urgency = Urgency.SOON,
    source: str = "intake",
    parent_id: str | None = None,
    metadata: dict | None = None,
    expert: str | None = None,
    requested_by: str | None = None,
    vikunja_task_id: int | None = None,
) -> Goal:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO goals (title, description, domain, urgency, source, metadata, parent_id, expert, requested_by, vikunja_task_id)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
               RETURNING *""",
            title,
            description,
            domain.value,
            urgency.value,
            source,
            json.dumps(metadata or {}),
            parent_id,
            expert,
            requested_by,
            vikunja_task_id,
        )
    await audit("goal_created", {"title": title, "domain": domain.value, "urgency": urgency.value, "expert": expert, "requested_by": requested_by, "vikunja_task_id": vikunja_task_id}, row["id"])
    return Goal.from_row(dict(row))


async def get_goal(goal_id: str) -> Goal | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM goals WHERE id = $1", UUID(goal_id))
    return Goal.from_row(dict(row)) if row else None


async def get_goal_by_vikunja_task(task_id: int) -> Goal | None:
    """Find a goal by its linked Vikunja task ID."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM goals WHERE vikunja_task_id = $1", task_id)
    return Goal.from_row(dict(row)) if row else None


async def list_goals(
    status: GoalStatus | None = None,
    domain: Domain | None = None,
    urgency: Urgency | None = None,
) -> list[Goal]:
    pool = await get_pool()
    conditions = []
    params: list[Any] = []
    idx = 1

    if status:
        conditions.append(f"status = ${idx}")
        params.append(status.value)
        idx += 1
    if domain:
        conditions.append(f"domain = ${idx}")
        params.append(domain.value)
        idx += 1
    if urgency:
        conditions.append(f"urgency = ${idx}")
        params.append(urgency.value)
        idx += 1

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"""SELECT * FROM goals{where}
              ORDER BY CASE urgency
                WHEN 'imperative' THEN 0
                WHEN 'soon' THEN 1
                WHEN 'idle-opportunistic' THEN 2
                ELSE 3 END, updated_at DESC"""

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *params)
    return [Goal.from_row(dict(r)) for r in rows]


async def get_active_goals_with_no_steps() -> list[Goal]:
    """Return active goals that have no steps yet.

    These need to be planned before workers can execute them.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT g.* FROM goals g
            WHERE g.status = 'active'
              AND NOT EXISTS (SELECT 1 FROM goal_steps s WHERE s.goal_id = g.id)
            ORDER BY CASE g.urgency
              WHEN 'imperative' THEN 0
              WHEN 'soon' THEN 1
              WHEN 'idle-opportunistic' THEN 2
              ELSE 3 END,
              g.updated_at DESC
        """)
    return [Goal.from_row(dict(r)) for r in rows]


async def update_goal_status(goal_id: str, status: GoalStatus) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE goals SET status = $2 WHERE id = $1", UUID(goal_id), status.value)
        # Synchronize step statuses with the new goal status so the queue
        # and dashboard don't show orphaned pending/in_progress steps.
        if status == GoalStatus.COMPLETED:
            await conn.execute(
                "UPDATE goal_steps SET status = 'completed', completed_at = NOW() WHERE goal_id = $1 AND status IN ('pending', 'in_progress')",
                UUID(goal_id),
            )
        elif status == GoalStatus.ABANDONED:
            await conn.execute(
                "UPDATE goal_steps SET status = 'abandoned' WHERE goal_id = $1 AND status IN ('pending', 'in_progress', 'failed')",
                UUID(goal_id),
            )
        elif status == GoalStatus.PAUSED:
            await conn.execute(
                "UPDATE goal_steps SET status = 'pending' WHERE goal_id = $1 AND status = 'in_progress'",
                UUID(goal_id),
            )
    await audit("goal_updated", {"status": status.value}, goal_id)


async def update_goal_progress(goal_id: str, progress: float) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE goals SET progress = $2 WHERE id = $1", UUID(goal_id), progress)


# ============================================================================
# STEP operations
# ============================================================================

async def create_step(goal_id: str, title: str, description: str | None = None, order_index: int | None = None, depends_on: list[str] | None = None) -> Step:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if order_index is None:
            max_row = await conn.fetchrow(
                "SELECT COALESCE(MAX(order_index), 0) as max FROM goal_steps WHERE goal_id = $1",
                UUID(goal_id),
            )
            order_index = (max_row["max"] or 0) + 1

        row = await conn.fetchrow(
            """INSERT INTO goal_steps (goal_id, title, description, order_index, depends_on)
               VALUES ($1, $2, $3, $4, $5) RETURNING *""",
            UUID(goal_id), title, description, order_index,
            [UUID(d) for d in depends_on] if depends_on else [],
        )
    await audit("step_created", {"title": title, "order_index": order_index, "depends_on": depends_on}, goal_id, row["id"])
    return Step.from_row(dict(row))


async def get_steps(goal_id: str) -> list[Step]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM goal_steps WHERE goal_id = $1 ORDER BY order_index",
            UUID(goal_id),
        )
    return [Step.from_row(dict(r)) for r in rows]


async def get_next_pending_step(goal_id: str) -> Step | None:
    """Get the next pending step for a goal (read-only, no claim).

    Use claim_next_step for distributed execution where the step should be
    atomically marked in_progress to prevent duplicate work.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT * FROM goal_steps WHERE goal_id = $1 AND status = 'pending'
               ORDER BY order_index LIMIT 1""",
            UUID(goal_id),
        )
    return Step.from_row(dict(row)) if row else None


async def claim_next_step(goal_id: str) -> Step | None:
    """Atomically claim the next pending step for a goal.

    Uses SELECT FOR UPDATE SKIP LOCKED so multiple workers can pull from the
    same goal without colliding. The step is marked in_progress and returned.
    Returns None if no pending step is available.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """UPDATE goal_steps
                   SET status = 'in_progress', attempted_at = NOW()
                   WHERE id = (
                     SELECT id FROM goal_steps
                     WHERE goal_id = $1 AND status = 'pending'
                       AND (
                         depends_on = '{}' OR
                         NOT EXISTS (
                           SELECT 1 FROM unnest(depends_on) AS dep(id)
                           JOIN goal_steps dep_step ON dep_step.id = dep.id
                           WHERE dep_step.status != 'completed'
                         )
                       )
                     ORDER BY order_index
                     FOR UPDATE SKIP LOCKED
                     LIMIT 1
                   )
                   RETURNING *""",
                UUID(goal_id),
            )
    if row:
        await audit("step_claimed", {"worker": os.environ.get("JOB_STAR_WORKER", "default")}, goal_id, row["id"])
    return Step.from_row(dict(row)) if row else None


async def claim_next_step_any_goal(
    urgency: Urgency | None = None,
    domain: Domain | None = None,
    expert: str | None = None,
    expert_any: bool = False,
    worker_machine: str | None = None,
) -> tuple[Goal, Step] | None:
    """Atomically claim the next pending step across ALL goals.

    For distributed workers / idle loop. Optionally filter by urgency/domain/expert.
    Returns (goal, step) or None. Uses FOR UPDATE SKIP LOCKED so multiple
    machines can pull from the shared queue safely.

    Expert affinity:
      - expert='gatehouse-ai'  → only claim goals where goals.expert = 'gatehouse-ai'
      - expert=None, expert_any=False → only claim goals where goals.expert IS NULL (generic pool)
      - expert=None, expert_any=True  → claim any goal regardless of expert

    Machine pinning (Option A):
      If an expert has required_machine set in the experts table, only a worker
      on that machine (worker_machine matching) can claim its goals. Goals whose
      expert has no required_machine can be claimed by any worker with the affinity.
    """
    pool = await get_pool()
    conditions = ["s.status = 'pending'", "g.status = 'active'"]
    params: list = []
    idx = 1

    if urgency:
        conditions.append(f"g.urgency = ${idx}")
        params.append(urgency.value)
        idx += 1
    if domain:
        conditions.append(f"g.domain = ${idx}")
        params.append(domain.value)
        idx += 1
    if expert_any:
        pass  # no expert filter — claim any goal (but still respect machine pinning)
    elif expert:
        conditions.append(f"g.expert = ${idx}")
        params.append(expert)
        idx += 1
    else:
        # generic worker: only unowned goals
        conditions.append("g.expert IS NULL")

    # Machine pinning: LEFT JOIN experts. A goal is claimable if:
    #   - it has no expert (generic), OR
    #   - its expert has no required_machine (any machine), OR
    #   - its expert's required_machine matches the worker's machine
    if worker_machine:
        conditions.append(f"""
          (g.expert IS NULL
           OR NOT EXISTS (SELECT 1 FROM experts e WHERE e.name = g.expert AND e.required_machine IS NOT NULL)
           OR EXISTS (SELECT 1 FROM experts e WHERE e.name = g.expert AND e.required_machine = ${idx}))
        """)
        params.append(worker_machine)
        idx += 1

    where = " AND ".join(conditions)
    # Order by urgency priority, then goal, then step order
    # A step is claimable only if all its depends_on steps are completed
    # (or it has no dependencies).
    sql = f"""UPDATE goal_steps
              SET status = 'in_progress', attempted_at = NOW()
              WHERE id = (
                SELECT s.id FROM goal_steps s
                JOIN goals g ON s.goal_id = g.id
                WHERE {where}
                  AND (
                    s.depends_on = '{{}}' OR
                    NOT EXISTS (
                      SELECT 1 FROM unnest(s.depends_on) AS dep(id)
                      JOIN goal_steps dep_step ON dep_step.id = dep.id
                      WHERE dep_step.status != 'completed'
                    )
                  )
                ORDER BY CASE g.urgency
                  WHEN 'imperative' THEN 0
                  WHEN 'soon' THEN 1
                  WHEN 'idle-opportunistic' THEN 2
                  ELSE 3 END,
                  g.updated_at DESC, s.order_index
                FOR UPDATE OF s SKIP LOCKED
                LIMIT 1
              )
              RETURNING *"""

    async with pool.acquire() as conn:
        async with conn.transaction():
            step_row = await conn.fetchrow(sql, *params)
            if not step_row:
                return None
            goal_row = await conn.fetchrow(
                "SELECT * FROM goals WHERE id = $1",
                step_row["goal_id"],
            )

    if step_row:
        await audit("step_claimed", {"worker": os.environ.get("JOB_STAR_WORKER", "default")},
                    step_row["goal_id"], step_row["id"])
    goal = Goal.from_row(dict(goal_row)) if goal_row else None
    step = Step.from_row(dict(step_row)) if step_row else None
    if goal and step:
        return (goal, step)
    return None


async def update_step_status(step_id: str, status: StepStatus, result: dict | None = None,
                              model: str | None = None, input_tokens: int | None = None,
                              output_tokens: int | None = None, cost: float = 0.0) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if status == StepStatus.COMPLETED:
            await conn.execute(
                """UPDATE goal_steps
                   SET status = $2, result = $3, model = $4, input_tokens = $5,
                       output_tokens = $6, cost = $7, completed_at = NOW()
                   WHERE id = $1""",
                UUID(step_id), status.value,
                json.dumps(result) if result else None,
                model, input_tokens, output_tokens, cost,
            )
        elif status == StepStatus.IN_PROGRESS:
            await conn.execute(
                "UPDATE goal_steps SET status = $2, attempted_at = NOW() WHERE id = $1",
                UUID(step_id), status.value,
            )
        else:
            await conn.execute(
                "UPDATE goal_steps SET status = $2, result = $3 WHERE id = $1",
                UUID(step_id), status.value,
                json.dumps(result) if result else None,
            )
    await audit(f"step_{status.value}", {}, step_id=step_id, model=model,
                input_tokens=input_tokens, output_tokens=output_tokens, cost=cost)


# ============================================================================
# CONFLICT operations
# ============================================================================

async def create_conflict(goal_a_id: str, goal_b_id: str, conflict_type: ConflictType,
                           description: str | None = None) -> Conflict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO goal_conflicts (goal_a_id, goal_b_id, conflict_type, description)
               VALUES ($1, $2, $3, $4) RETURNING *""",
            UUID(goal_a_id), UUID(goal_b_id), conflict_type.value, description,
        )
    await audit("conflict_detected", {"type": conflict_type.value, "goal_a": goal_a_id, "goal_b": goal_b_id})
    return Conflict(
        id=str(row["id"]),
        goal_a_id=str(row["goal_a_id"]),
        goal_b_id=str(row["goal_b_id"]),
        conflict_type=ConflictType(row["conflict_type"]),
        description=row.get("description"),
        resolution=ConflictResolution(row["resolution"]),
    )


async def get_unresolved_conflicts(goal_id: str | None = None) -> list[dict]:
    pool = await get_pool()
    if goal_id:
        sql = """SELECT * FROM goal_conflicts
                 WHERE resolution = 'unresolved'
                   AND (goal_a_id = $1 OR goal_b_id = $1)"""
        rows = await pool.fetch(sql, UUID(goal_id))
    else:
        rows = await pool.fetch("SELECT * FROM goal_conflicts WHERE resolution = 'unresolved'")
    return [dict(r) for r in rows]


# ============================================================================
# DECISION operations
# ============================================================================

async def record_decision(goal_id: str, decision: str, reasoning: str | None = None,
                           alternatives: list[dict] | None = None, decided_by: str = "ai") -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO decisions (goal_id, decision, reasoning, alternatives_considered, decided_by)
               VALUES ($1, $2, $3, $4, $5)""",
            UUID(goal_id), decision, reasoning,
            json.dumps(alternatives or []),
            decided_by,
        )


# ============================================================================
# JOB QUEUE operations
# ============================================================================

async def enqueue_job(goal_id: str, kind: str = "plan", priority: int = 0, payload: dict | None = None) -> str:
    """Enqueue a job queue item. Returns the job id."""
    pool = await get_pool()
    payload = payload or {}
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO job_queue (goal_id, kind, status, priority, payload)
               VALUES ($1, $2, 'pending', $3, $4) RETURNING id""",
            UUID(goal_id), kind, priority, json.dumps(payload),
        )
    return str(row["id"])


async def claim_job_queue_item(
    worker_id: str,
    stale_after_minutes: int = 5,
    expert: str | None = None,
    expert_any: bool = False,
) -> dict | None:
    """Atomically claim the next pending (or stale claimed) job queue item.

    Expert affinity mirrors claim_next_step_any_goal:
      - expert='gatehouse-ai'  → only claim jobs whose goal has expert='gatehouse-ai'
      - expert=None, expert_any=False → only claim jobs whose goal has expert IS NULL
      - expert=None, expert_any=True  → claim any job
    This stops a generic worker from stealing expert jobs (and vice versa).
    """
    pool = await get_pool()
    params: list = [worker_id, stale_after_minutes]

    if expert_any:
        expert_clause = ""
    elif expert:
        expert_clause = " AND g.expert = $3"
        params.append(expert)
    else:
        expert_clause = " AND g.expert IS NULL"

    sql = f"""UPDATE job_queue
              SET status = 'claimed', worker_id = $1, claimed_at = NOW()
              WHERE id = (
                SELECT q.id FROM job_queue q
                JOIN goals g ON q.goal_id = g.id
                WHERE (q.status = 'pending'
                       OR (q.status = 'claimed' AND q.claimed_at < NOW() - make_interval(mins => $2)))
                {expert_clause}
                ORDER BY q.priority DESC, q.created_at
                FOR UPDATE OF q SKIP LOCKED
                LIMIT 1
              )
              RETURNING *"""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(sql, *params)
    if row:
        return dict(row)
    return None


async def complete_job(job_id: str, status: str = "completed") -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE job_queue SET status = $2, completed_at = NOW() WHERE id = $1",
            UUID(job_id), status,
        )


# ============================================================================
# EVENTS operations
# ============================================================================

async def publish_event(event_type: str, payload: dict) -> None:
    """Publish an event to the distributed event store."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO events (type, payload) VALUES ($1, $2)",
            event_type, json.dumps(payload),
        )


async def get_events_since(since_id: str | None = None, limit: int = 100) -> list[dict]:
    """Get events newer than the given id."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if since_id:
            rows = await conn.fetch(
                """SELECT * FROM events
                   WHERE id > $1
                   ORDER BY id
                   LIMIT $2""",
                UUID(since_id), limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM events ORDER BY id DESC LIMIT $1",
                limit,
            )
    return [dict(r) for r in rows]


async def prune_events(older_than_hours: int = 24) -> int:
    """Delete old events."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM events WHERE created_at < NOW() - make_interval(hours => $1)",
            older_than_hours,
        )
    return int(result.split()[-1]) if result else 0