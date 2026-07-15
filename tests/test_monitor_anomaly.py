"""Tests for the monitor's rate-based anomaly detection (Phase 5).

Covers:
  - check_claim_storms detects a step claimed at high rate (the 2026-07-14
    incident signature: 1.35M claims on one step, step count never grew).
  - The unconditional auto_reset_failed is gone: failed steps are reported
    as warnings, NOT reset to pending. This defeated the retry limit and fed
    the hot loop.
"""

import pytest
import asyncpg

from job_star.monitor import (
    check_claim_storms,
    Thresholds,
    run_monitor,
)


@pytest.mark.asyncio
async def test_check_claim_storms_detects_high_claim_rate():
    """A step with >MAX_CLAIMS_PER_STEP_PER_HOUR claims in the last hour is a storm."""
    pool = await asyncpg.create_pool("postgresql://jobstar:jobstar@localhost:5432/job_star")
    async with pool.acquire() as conn:
        # Insert a test goal + step
        goal_id = await conn.fetchval(
            "INSERT INTO goals (title, domain, urgency, status) "
            "VALUES ('storm test', 'coding', 'soon', 'active') RETURNING id"
        )
        step_id = await conn.fetchval(
            "INSERT INTO goal_steps (goal_id, title, order_index, status) "
            "VALUES ($1, 'storm step', 1, 'failed') RETURNING id",
            goal_id,
        )
        # Insert 150 step_claimed + 150 step_blocked audit rows (>100 threshold)
        rows = []
        for _ in range(150):
            rows.append(("step_claimed", goal_id, step_id))
            rows.append(("step_blocked", goal_id, step_id))
        # Bulk insert via executemany
        await conn.executemany(
            "INSERT INTO audit_trail (event, goal_id, step_id, timestamp) "
            "VALUES ($1, $2, $3, NOW())",
            rows,
        )
        try:
            findings = await check_claim_storms(conn)
            storm = [f for f in findings if f.category == "claim_storm"]
            assert len(storm) >= 1
            assert storm[0].severity == "critical"
            assert str(step_id)[:8] in storm[0].message
        finally:
            # cleanup
            await conn.execute("DELETE FROM audit_trail WHERE step_id = $1", step_id)
            await conn.execute("DELETE FROM goal_steps WHERE id = $1", step_id)
            await conn.execute("DELETE FROM goals WHERE id = $1", goal_id)
    await pool.close()


@pytest.mark.asyncio
async def test_check_claim_storms_ignores_normal_activity():
    """A step with a few claims (below thresholds) is not a storm."""
    pool = await asyncpg.create_pool("postgresql://jobstar:jobstar@localhost:5432/job_star")
    async with pool.acquire() as conn:
        goal_id = await conn.fetchval(
            "INSERT INTO goals (title, domain, urgency, status) "
            "VALUES ('normal test', 'coding', 'soon', 'active') RETURNING id"
        )
        step_id = await conn.fetchval(
            "INSERT INTO goal_steps (goal_id, title, order_index, status) "
            "VALUES ($1, 'normal step', 1, 'completed') RETURNING id",
            goal_id,
        )
        # 5 claims — well below STORM_MIN_CLAIMS (20) and MAX_CLAIMS (100)
        for _ in range(5):
            await conn.execute(
                "INSERT INTO audit_trail (event, goal_id, step_id, timestamp) "
                "VALUES ('step_claimed', $1, $2, NOW())",
                goal_id, step_id,
            )
        try:
            findings = await check_claim_storms(conn)
            storm = [f for f in findings if f.category == "claim_storm"]
            assert len(storm) == 0
        finally:
            await conn.execute("DELETE FROM audit_trail WHERE step_id = $1", step_id)
            await conn.execute("DELETE FROM goal_steps WHERE id = $1", step_id)
            await conn.execute("DELETE FROM goals WHERE id = $1", goal_id)
    await pool.close()


@pytest.mark.asyncio
async def test_failed_steps_not_auto_reset():
    """Failed steps must NOT be auto-reset to pending by run_monitor.

    Regression test: the old auto_reset_failed logic reset any failed step to
    pending after 1h, defeating the retry limit and feeding the hot loop. Now
    failed steps are only reported as warnings (failed_step_escalation), and
    the step stays 'failed'.
    """
    pool = await asyncpg.create_pool("postgresql://jobstar:jobstar@localhost:5432/job_star")
    async with pool.acquire() as conn:
        goal_id = await conn.fetchval(
            "INSERT INTO goals (title, domain, urgency, status) "
            "VALUES ('failed-step test', 'coding', 'soon', 'active') RETURNING id"
        )
        step_id = await conn.fetchval(
            "INSERT INTO goal_steps (goal_id, title, order_index, status, attempted_at, consecutive_failures) "
            "VALUES ($1, 'failed step', 1, 'failed', NOW() - INTERVAL '2 hours', 3) RETURNING id",
            goal_id,
        )
        try:
            await run_monitor(auto_fix=True)
            # The step must still be 'failed', NOT reset to 'pending'
            status = await conn.fetchval("SELECT status FROM goal_steps WHERE id = $1", step_id)
            assert status == "failed", f"step was auto-reset to {status} (should stay failed)"
        finally:
            await conn.execute("DELETE FROM goal_steps WHERE id = $1", step_id)
            await conn.execute("DELETE FROM goals WHERE id = $1", goal_id)
    await pool.close()
