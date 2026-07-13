"""Idle-opportunistic queue access.

Provides functions to peek at and pop the next eligible step from the
idle-opportunistic queue, with priority ordering and dependency filtering,
plus status transitions (started / completed / failed).

Backend: SQLite (WAL mode) for atomic transitions and crash recovery.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS idle_steps (
    id              TEXT PRIMARY KEY,
    queue           TEXT NOT NULL DEFAULT 'idle-opportunistic',
    step_name       TEXT NOT NULL,
    goal_id         TEXT,
    priority        INTEGER NOT NULL DEFAULT 0,
    payload         TEXT NOT NULL DEFAULT '{}',   -- JSON blob
    depends_on      TEXT NOT NULL DEFAULT '[]',   -- JSON list of step ids
    status          TEXT NOT NULL DEFAULT 'pending',
                    -- pending | started | completed | failed
    attempts        INTEGER NOT NULL DEFAULT 0,
    max_attempts    INTEGER NOT NULL DEFAULT 3,
    worker_id       TEXT,
    lease_until     REAL,                         -- epoch seconds
    result          TEXT,                         -- JSON blob
    error           TEXT,
    created_at      REAL NOT NULL,
    started_at      REAL,
    completed_at    REAL,
    updated_at      REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_idle_status
    ON idle_steps(status);
CREATE INDEX IF NOT EXISTS idx_idle_queue_status
    ON idle_steps(queue, status);
CREATE INDEX IF NOT EXISTS idx_idle_lease
    ON idle_steps(lease_until);
"""

# Status constants
PENDING = "pending"
STARTED = "started"
COMPLETED = "completed"
FAILED = "failed"


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

@contextmanager
def _connect(db_path: str | Path):
    """Open a SQLite connection with WAL mode and a short timeout."""
    conn = sqlite3.connect(str(db_path), timeout=10.0, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.row_factory = sqlite3.Row
        yield conn
    finally:
        conn.close()


def init_store(db_path: str | Path) -> None:
    """Create the queue schema if it doesn't already exist."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


# ---------------------------------------------------------------------------
# Enqueue (helper for producers; not strictly required by this step but
# needed to make the queue usable and testable)
# ---------------------------------------------------------------------------

def enqueue_step(
    db_path: str | Path,
    step_name: str,
    *,
    goal_id: Optional[str] = None,
    priority: int = 0,
    payload: Optional[Dict[str, Any]] = None,
    depends_on: Optional[List[str]] = None,
    max_attempts: int = 3,
    queue: str = "idle-opportunistic",
    step_id: Optional[str] = None,
) -> str:
    """Insert a new pending step and return its id."""
    init_store(db_path)
    step_id = step_id or str(uuid.uuid4())
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO idle_steps
                (id, queue, step_name, goal_id, priority, payload,
                 depends_on, status, attempts, max_attempts,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (
                step_id,
                queue,
                step_name,
                goal_id,
                int(priority),
                json.dumps(payload or {}),
                json.dumps(depends_on or []),
                PENDING,
                int(max_attempts),
                now,
                now,
            ),
        )
    return step_id


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------

def _dependencies_satisfied(conn: sqlite3.Connection, depends_on: List[str]) -> bool:
    """Return True iff every id in depends_on exists and is completed."""
    if not depends_on:
        return True
    placeholders = ",".join("?" for _ in depends_on)
    rows = conn.execute(
        f"""
        SELECT status FROM idle_steps
        WHERE id IN ({placeholders})
        """,
        depends_on,
    ).fetchall()
    # Every dependency must exist and be completed.
    if len(rows) != len(depends_on):
        return False
    return all(r["status"] == COMPLETED for r in rows)


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["payload"] = json.loads(d["payload"] or "{}")
    d["depends_on"] = json.loads(d["depends_on"] or "[]")
    if d.get("result") is not None:
        d["result"] = json.loads(d["result"])
    return d


# ---------------------------------------------------------------------------
# Core: get_next_step (peek + pop atomically)
# ---------------------------------------------------------------------------

def get_next_step(
    db_path: str | Path,
    *,
    worker_id: str,
    lease_seconds: float = 600.0,
    queue: str = "idle-opportunistic",
    reclaim_stale: bool = True,
) -> Optional[Dict[str, Any]]:
    """Atomically pick and lease the next eligible step.

    Selection rules:
      1. status == 'pending' (or 'started' with expired lease, if reclaim_stale)
      2. queue matches
      3. all dependencies are completed
      4. ordered by priority DESC, then created_at ASC

    The chosen step is transitioned to 'started', assigned to worker_id,
    given a lease, and returned as a dict. Returns None if nothing eligible.
    """
    init_store(db_path)

    with _connect(db_path) as conn:
        # Reclaim stale leases first so they become candidates again.
        if reclaim_stale:
            reclaim_stale_leases(db_path, conn=conn)

        # IMMEDIATE transaction so the pick-and-lease is atomic.
        conn.execute("BEGIN IMMEDIATE;")
        try:
            candidates = conn.execute(
                """
                SELECT * FROM idle_steps
                WHERE queue = ? AND status = ?
                ORDER BY priority DESC, created_at ASC
                """,
                (queue, PENDING),
            ).fetchall()

            chosen = None
            for row in candidates:
                depends_on = json.loads(row["depends_on"] or "[]")
                if _dependencies_satisfied(conn, depends_on):
                    chosen = row
                    break

            if chosen is None:
                conn.execute("COMMIT;")
                return None

            now = time.time()
            lease_until = now + lease_seconds
            conn.execute(
                """
                UPDATE idle_steps
                SET status = ?, worker_id = ?, attempts = attempts + 1,
                    started_at = ?, lease_until = ?, updated_at = ?
                WHERE id = ?
                """,
                (STARTED, worker_id, now, lease_until, now, chosen["id"]),
            )
            conn.execute("COMMIT;")
            return _row_to_dict(chosen)
        except Exception:
            conn.execute("ROLLBACK;")
            raise


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------

def mark_step_started(
    db_path: str | Path,
    step_id: str,
    *,
    worker_id: str,
    lease_seconds: float = 600.0,
) -> Optional[Dict[str, Any]]:
    """Explicitly mark a previously-peeked step as started (re-lease).

    Useful when a caller peeks without leasing and later commits to run.
    """
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            conn.execute(
                """
                UPDATE idle_steps
                SET status = ?, worker_id = ?, attempts = attempts + 1,
                    started_at = COALESCE(started_at, ?),
                    lease_until = ?, updated_at = ?
                WHERE id = ? AND status IN (?, ?)
                """,
                (STARTED, worker_id, now, now + lease_seconds, now,
                 step_id, PENDING, STARTED),
            )
            row = conn.execute(
                "SELECT * FROM idle_steps WHERE id = ?", (step_id,)
            ).fetchone()
            conn.execute("COMMIT;")
            return _row_to_dict(row) if row else None
        except Exception:
            conn.execute("ROLLBACK;")
            raise


def mark_step_completed(
    db_path: str | Path,
    step_id: str,
    *,
    result: Optional[Dict[str, Any]] = None,
    worker_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Mark a step completed and store its result payload."""
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            if worker_id is not None:
                conn.execute(
                    """
                    UPDATE idle_steps
                    SET status = ?, result = ?, completed_at = ?,
                        updated_at = ?, lease_until = NULL
                    WHERE id = ? AND worker_id = ? AND status = ?
                    """,
                    (COMPLETED, json.dumps(result or {}), now, now,
                     step_id, worker_id, STARTED),
                )
            else:
                conn.execute(
                    """
                    UPDATE idle_steps
                    SET status = ?, result = ?, completed_at = ?,
                        updated_at = ?, lease_until = NULL
                    WHERE id = ? AND status = ?
                    """,
                    (COMPLETED, json.dumps(result or {}), now, now,
                     step_id, STARTED),
                )
            row = conn.execute(
                "SELECT * FROM idle_steps WHERE id = ?", (step_id,)
            ).fetchone()
            conn.execute("COMMIT;")
            return _row_to_dict(row) if row else None
        except Exception:
            conn.execute("ROLLBACK;")
            raise


def mark_step_failed(
    db_path: str | Path,
    step_id: str,
    *,
    error: str,
    worker_id: Optional[str] = None,
    retry: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """Mark a step failed.

    If retry is None, the step is retried automatically iff attempts <
    max_attempts. When retried, status returns to 'pending'; otherwise it
    becomes 'failed' permanently.
    """
    now = time.time()
    with _connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            row = conn.execute(
                "SELECT * FROM idle_steps WHERE id = ?", (step_id,)
            ).fetchone()
            if row is None:
                conn.execute("COMMIT;")
                return None

            should_retry = (
                retry if retry is not None
                else row["attempts"] < row["max_attempts"]
            )

            if should_retry:
                new_status = PENDING
            else:
                new_status = FAILED

            if worker_id is not None:
                conn.execute(
                    """
                    UPDATE idle_steps
                    SET status = ?, error = ?, updated_at = ?,
                        lease_until = NULL, worker_id = CASE WHEN ? THEN NULL ELSE worker_id END
                    WHERE id = ? AND worker_id = ? AND status = ?
                    """,
                    (new_status, error, now, 1 if should_retry else 0,
                     step_id, worker_id, STARTED),
                )
            else:
                conn.execute(
                    """
                    UPDATE idle_steps
                    SET status = ?, error = ?, updated_at = ?,
                        lease_until = NULL, worker_id = CASE WHEN ? THEN NULL ELSE worker_id END
                    WHERE id = ? AND status = ?
                    """,
                    (new_status, error, now, 1 if should_retry else 0,
                     step_id, STARTED),
                )

            row = conn.execute(
                "SELECT * FROM idle_steps WHERE id = ?", (step_id,)
            ).fetchone()
            conn.execute("COMMIT;")
            return _row_to_dict(row) if row else None
        except Exception:
            conn.execute("ROLLBACK;")
            raise


# ---------------------------------------------------------------------------
# Stale lease recovery
# ---------------------------------------------------------------------------

def reclaim_stale_leases(
    db_path: str | Path,
    *,
    now: Optional[float] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    """Re-queue steps whose lease has expired back to 'pending'.

    Returns the number of reclaimed steps. If a connection is supplied,
    the update runs within the caller's transaction; otherwise a new
    connection is opened.
    """
    now = now or time.time()
    own_conn = conn is None
    if own_conn:
        ctx = _connect(db_path)
        c = ctx.__enter__()
    else:
        c = conn

    try:
        cur = c.execute(
            """
            UPDATE idle_steps
            SET status = ?, lease_until = NULL, updated_at = ?
            WHERE status = ? AND lease_until IS NOT NULL AND lease_until < ?
            """,
            (PENDING, now, STARTED, now),
        )
        count = cur.rowcount
        return count
    finally:
        if own_conn:
            ctx.__exit__(None, None, None)


# ---------------------------------------------------------------------------
# Introspection (handy for the idle loop and for tests)
# ---------------------------------------------------------------------------

def peek_pending(
    db_path: str | Path,
    *,
    queue: str = "idle-opportunistic",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """Return pending steps (priority DESC, created ASC) without leasing."""
    init_store(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM idle_steps
            WHERE queue = ? AND status = ?
            ORDER BY priority DESC, created_at ASC
            LIMIT ?
            """,
            (queue, PENDING, limit),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


def get_step(db_path: str | Path, step_id: str) -> Optional[Dict[str, Any]]:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM idle_steps WHERE id = ?", (step_id,)
        ).fetchone()
        return _row_to_dict(row) if row else None


def queue_depth(
    db_path: str | Path,
    *,
    queue: str = "idle-opportunistic",
    status: Optional[str] = None,
) -> int:
    with _connect(db_path) as conn:
        if status is None:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM idle_steps WHERE queue = ?",
                (queue,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM idle_steps WHERE queue = ? AND status = ?",
                (queue, status),
            ).fetchone()
        return int(row["n"])
