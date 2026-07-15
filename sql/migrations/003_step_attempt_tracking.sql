-- Migration 003: Step attempt tracking (circuit breaker)
-- Adds DB-backed attempt counters to goal_steps so retry state persists across
-- worker restarts and is visible to the monitor. Previously the retry count
-- lived only in an in-memory dict (BudgetTracker._step_failures), which was
-- both lossy across restarts AND never reset within a process — causing the
-- 2026-07-14 hot-loop incident (1.35M claim cycles, 19h, 5h CPU).
-- Columns: attempt_count (total claims), consecutive_failures (since last
-- success), last_attempt_at (stable claim timestamp). Additive only.

ALTER TABLE goal_steps
    ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS consecutive_failures INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMP
;

-- Backfill attempt_count + last_attempt_at from audit_trail step_claimed events.
UPDATE goal_steps s
SET attempt_count = sub.claim_count,
    last_attempt_at = sub.last_ts
FROM (
    SELECT step_id,
           count(*) AS claim_count,
           max(timestamp) AS last_ts
    FROM audit_trail
    WHERE event = 'step_claimed'
      AND step_id IS NOT NULL
    GROUP BY step_id
) sub
WHERE s.id = sub.step_id
  AND s.attempt_count = 0
;

-- Backfill consecutive_failures: step_failed events since the last
-- step_completed, only for steps currently in 'failed' status.
UPDATE goal_steps s
SET consecutive_failures = sub.fail_count
FROM (
    SELECT step_id, count(*) AS fail_count
    FROM audit_trail a
    WHERE a.event = 'step_failed'
      AND a.step_id IS NOT NULL
      AND NOT EXISTS (
          SELECT 1 FROM audit_trail b
          WHERE b.step_id = a.step_id
            AND b.event = 'step_completed'
            AND b.timestamp > a.timestamp
      )
    GROUP BY step_id
) sub
WHERE s.id = sub.step_id
  AND s.status = 'failed'
;

-- Indexes for monitor rate-based anomaly detection.
CREATE INDEX IF NOT EXISTS idx_steps_attempt_count ON goal_steps(attempt_count DESC);
CREATE INDEX IF NOT EXISTS idx_steps_last_attempt ON goal_steps(last_attempt_at DESC);
CREATE INDEX IF NOT EXISTS idx_steps_consecutive_failures ON goal_steps(consecutive_failures DESC);
