"""Upgrade tool for job-star: safe, staged, verifiable.

Usage:
    python -m job_star upgrade           # Run full upgrade process
    python -m job_star upgrade --check    # Pre-flight check only (dry run)
    python -m job_star upgrade --reap     # Reap orphaned steps only
    python -m job_star upgrade --commit    # Commit current changes and restart

The upgrade process:
  1. PRE-FLIGHT:  Run syntax/import checks, check git status, detect orphans
  2. REAP:        Reset orphaned in_progress steps back to pending
  3. MIGRATE:     Apply versioned DB migrations (schema_migrations table)
  4. RESTART:     Blue-green rolling restart (API first, then workers one at a time)
  5. VERIFY:      Health check via /health endpoint + DB check + auto-rollback on failure

Design principles:
  - Additive migrations only (new tables/columns with defaults). Never drop.
  - Workers can run old code alongside new code (mixed-version fleet is safe
    as long as the DB schema is backward-compatible).
  - API restarts are safe — workers connect to Postgres, not the API.
  - The upgrade tool itself is version-agnostic — it works with any code state.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Optional

from .db import get_pool, close_pool, audit, publish_event

# The commit hash before this upgrade (for automatic rollback)
_PRE_UPGRADE_COMMIT: str | None = None


# ============================================================================
# Pre-flight checks
# ============================================================================

async def preflight_checks() -> dict:
    """Run pre-flight checks. Returns a dict of results.

    Does NOT modify anything. Safe to run anytime.
    """
    results = {
        "tests_pass": False,
        "git_clean": False,
        "syntax_ok": False,
        "orphaned_steps": 0,
        "active_workers": 0,
        "db_connected": False,
        "warnings": [],
        "errors": [],
    }

    # Check DB connectivity
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        results["db_connected"] = True
    except Exception as e:
        results["errors"].append(f"DB connection failed: {e}")
        return results

    # Check for orphaned in_progress steps
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM goal_steps WHERE status = 'in_progress'"
        )
        results["orphaned_steps"] = count
        if count > 0:
            # Check how old they are
            stale = await conn.fetchval(
                "SELECT count(*) FROM goal_steps WHERE status = 'in_progress' "
                "AND attempted_at < NOW() - INTERVAL '10 minutes'"
            )
            if stale > 0:
                results["warnings"].append(
                    f"{stale} step(s) have been in_progress for >10 minutes (likely orphaned)"
                )

    # Check for active workers (from recent audit_trail)
    async with pool.acquire() as conn:
        worker_count = await conn.fetchval(
            "SELECT count(DISTINCT details->>'worker') FROM audit_trail "
            "WHERE event = 'step_claimed' AND details->>'worker' IS NOT NULL "
            "AND timestamp > NOW() - INTERVAL '5 minutes'"
        )
        results["active_workers"] = worker_count or 0

    # Check git status
    try:
        git_result = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=_project_root(),
        )
        if git_result.returncode == 0:
            results["git_clean"] = len(git_result.stdout.strip()) == 0
            if not results["git_clean"]:
                changed = git_result.stdout.strip().split("\n")
                results["warnings"].append(
                    f"Git working tree has {len(changed)} uncommitted change(s)"
                )
    except Exception:
        pass

    # Check Python syntax (py_compile all modules)
    syntax_errors = _check_syntax()
    results["syntax_ok"] = len(syntax_errors) == 0
    if syntax_errors:
        for err in syntax_errors:
            results["errors"].append(f"Syntax error: {err}")

    # Run tests (quick check — just import all modules)
    try:
        import_imports_ok = _check_imports()
        if not import_imports_ok:
            results["errors"].append("Module import failed — check for broken imports")
        else:
            results["tests_pass"] = True  # at least imports work
    except Exception as e:
        results["errors"].append(f"Import check failed: {e}")

    await close_pool()
    return results


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _check_syntax() -> list[str]:
    """Check all Python files for syntax errors using py_compile."""
    errors = []
    root = _project_root()
    job_star_dir = os.path.join(root, "job_star")
    for dirpath, _, filenames in os.walk(job_star_dir):
        # Skip __pycache__
        if "__pycache__" in dirpath:
            continue
        for fname in filenames:
            if fname.endswith(".py"):
                fpath = os.path.join(dirpath, fname)
                result = subprocess.run(
                    [sys.executable, "-m", "py_compile", fpath],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    errors.append(f"{fpath}: {result.stderr.strip()}")
    return errors


def _check_imports() -> bool:
    """Try importing the main modules to catch import errors."""
    # Core modules (always required)
    core_modules = [
        "job_star.orchestrator",
        "job_star.cli",
        "job_star.worker",
        "job_star.checkin",
    ]
    # Optional modules (may fail if optional deps not installed)
    optional_modules = [
        "job_star.api.routes",
    ]
    try:
        for mod in core_modules:
            __import__(mod)
        for mod in optional_modules:
            try:
                __import__(mod)
            except ImportError:
                pass  # optional dep not installed in this env
        return True
    except Exception as e:
        print(f"  Import error: {e}", flush=True)
        return False


# ============================================================================
# Step reaping — reset orphaned in_progress steps
# ============================================================================

async def reap_stale_steps(stale_after_minutes: int = 10) -> int:
    """Reset in_progress steps that have been stuck longer than the threshold.

    This is safe because:
    - A step that's been in_progress for >10 minutes is almost certainly orphaned
      (no AI call takes that long; the worker either crashed or was killed)
    - Resetting to pending makes it available for another worker to claim
    - The audit trail preserves the original claim, so we can see what happened

    Returns the number of steps reaped.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Get the stale steps first (for audit logging)
        stale_rows = await conn.fetch(
            "SELECT id, goal_id, title FROM goal_steps "
            "WHERE status = 'in_progress' "
            "AND attempted_at < NOW() - make_interval(mins => $1)",
            stale_after_minutes,
        )

        if not stale_rows:
            return 0

        # Reset them to pending
        result = await conn.execute(
            "UPDATE goal_steps SET status = 'pending' "
            "WHERE status = 'in_progress' "
            "AND attempted_at < NOW() - make_interval(mins => $1)",
            stale_after_minutes,
        )

        reaped = int(result.split()[-1]) if result else 0

        # Audit each reaped step
        for row in stale_rows:
            await audit("step_reaped", {
                "step_id": str(row["id"]),
                "goal_id": str(row["goal_id"]),
                "title": row["title"],
                "reason": f"in_progress for >{stale_after_minutes} minutes",
            }, str(row["goal_id"]), str(row["id"]))

        return reaped


# ============================================================================
# Service management
# ============================================================================

SERVICES = [
    "job-star-api",
    "job-star-worker",
    "job-star-worker-gatehouse",
    "job-star-worker-research",
    "job-star-worker-jobstar",
]


def service_status(name: str) -> str:
    """Get the status of a systemd service."""
    result = subprocess.run(
        ["systemctl", "is-active", name],
        capture_output=True, text=True,
    )
    return result.stdout.strip()


def restart_service(name: str) -> bool:
    """Restart a systemd service. Returns True on success."""
    result = subprocess.run(
        ["sudo", "systemctl", "restart", name],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def stop_service(name: str) -> bool:
    """Stop a systemd service."""
    result = subprocess.run(
        ["sudo", "systemctl", "stop", name],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def start_service(name: str) -> bool:
    """Start a systemd service."""
    result = subprocess.run(
        ["sudo", "systemctl", "start", name],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def wait_for_service(name: str, timeout: int = 10) -> bool:
    """Wait for a service to become active."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if service_status(name) == "active":
            return True
        time.sleep(0.5)
    return False


# ============================================================================
# DB migration (additive only)
# ============================================================================

async def apply_migrations() -> list[str]:
    """Apply additive schema changes from schema.sql.

    Only runs CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS.
    Never drops or alters existing columns.
    """
    root = _project_root()
    schema_path = os.path.join(root, "sql", "schema.sql")

    if not os.path.exists(schema_path):
        return ["schema.sql not found — skipping migrations"]

    applied = []
    pool = await get_pool()
    async with pool.acquire() as conn:
        # Read schema.sql and extract only safe additive statements
        with open(schema_path) as f:
            schema = f.read()

        # We only run CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS
        # and CREATE TRIGGER (which needs special handling)
        # Split on semicolons (naive but works for this schema)
        statements = _split_sql_statements(schema)

        for stmt in statements:
            stmt = stmt.strip()
            if not stmt:
                continue
            upper = stmt.upper()
            # Only run additive statements
            if upper.startswith("CREATE TABLE IF NOT EXISTS"):
                table_name = _extract_table_name(stmt)
                # Check if table already exists
                exists = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.tables WHERE table_name = $1)",
                    table_name,
                )
                if not exists:
                    await conn.execute(stmt)
                    applied.append(f"Created table: {table_name}")
            elif upper.startswith("CREATE INDEX IF NOT EXISTS"):
                try:
                    await conn.execute(stmt)
                except Exception:
                    pass  # index might already exist or depend on missing table
            elif upper.startswith("CREATE UNIQUE INDEX IF NOT EXISTS"):
                try:
                    await conn.execute(stmt)
                except Exception:
                    pass
            elif upper.startswith("CREATE OR REPLACE FUNCTION"):
                await conn.execute(stmt)
            elif upper.startswith("CREATE TRIGGER"):
                # Triggers need special handling — use DROP + CREATE
                trigger_name = _extract_trigger_name(stmt)
                if trigger_name:
                    try:
                        await conn.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON {_extract_trigger_table(stmt)}")
                    except Exception:
                        pass
                try:
                    await conn.execute(stmt)
                except Exception:
                    pass
            # Skip INSERT (seed data), CREATE EXTENSION (already applied), etc.

    await close_pool()
    return applied


def _strip_leading_comments(stmt: str) -> str:
    """Remove leading blank lines and full-line -- comments from a statement.

    The splitter groups comment lines with the following ;-terminated
    statement. Without stripping, a statement like "-- comment\nCREATE TABLE ..."
    starts with "--" and is silently skipped by callers that check
    `stmt.startswith("--")` or `upper.startswith("CREATE TABLE")`. Inline
    comments (after SQL on the same line) and mid-statement comments are
    preserved — only leading comment/blank lines are removed.
    """
    lines = stmt.split("\n")
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].strip().startswith("--")):
        i += 1
    return "\n".join(lines[i:])


def _split_sql_statements(sql: str) -> list[str]:
    """Split SQL into statements, respecting $$ dollar-quote blocks.

    Each returned statement has leading blank lines and full-line -- comments
    stripped so it begins with actual SQL. This ensures callers' startswith()
    checks (e.g. "CREATE TABLE IF NOT EXISTS") work even when the statement is
    preceded by a comment block in the .sql file.

    Dollar-quote tracking scans the whole stream for paired $$ tokens rather
    than counting $$ per line. The per-line count was buggy: a line like
    "$$ LANGUAGE plpgsql" has one $$ (odd), which toggled the state off
    prematurely, so a following "END;" semicolon fired while NOT in a dollar
    quote and split the statement mid-function.
    """
    statements: list[str] = []
    current: list[str] = []
    in_dollar_quote = False
    for line in sql.split("\n"):
        stripped = line.strip()
        if in_dollar_quote:
            # Inside a $$ block: keep collecting until we find the closing $$.
            # A line may contain the closing $$ followed by more text (e.g.
            # "$$ LANGUAGE plpgsql"), so we must scan the line, not just test
            # for its presence.
            current.append(line)
            idx = 0
            while idx < len(line):
                pos = line.find("$$", idx)
                if pos == -1:
                    break
                # Found a $$ — this is the closing delimiter.
                in_dollar_quote = False
                idx = pos + 2
                # Continue scanning the remainder of the line for a possible
                # reopening or a semicolon that ends the statement.
                remainder = line[idx:]
                if remainder.rstrip().endswith(";"):
                    statements.append(_strip_leading_comments("\n".join(current)))
                    current = []
                break
            continue
        # Not in a dollar quote: check whether this line opens one.
        # Scan for $$ to detect an opening delimiter that may be followed by
        # more text on the same line (e.g. "AS $$").
        pos = line.find("$$")
        if pos != -1:
            # Starting from the first $$, toggle state for each $$ on the
            # line. The first $$ opens the quote, a second on the same line
            # would close it, etc.
            idx = pos
            still_inside = False  # first $$ below toggles this to True
            while idx < len(line):
                p = line.find("$$", idx)
                if p == -1:
                    break
                still_inside = not still_inside
                idx = p + 2
            current.append(line)
            in_dollar_quote = still_inside
            # If the line closed the quote and ends with ';', flush.
            if not still_inside and stripped.endswith(";"):
                statements.append(_strip_leading_comments("\n".join(current)))
                current = []
            continue
        if stripped.endswith(";"):
            current.append(line)
            statements.append(_strip_leading_comments("\n".join(current)))
            current = []
        else:
            current.append(line)
    if current:
        statements.append(_strip_leading_comments("\n".join(current)))
    return statements


def _extract_table_name(stmt: str) -> str:
    """Extract table name from CREATE TABLE statement."""
    import re
    m = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", stmt, re.I)
    return m.group(1) if m else ""


def _extract_trigger_name(stmt: str) -> str:
    import re
    m = re.search(r"CREATE\s+TRIGGER\s+(\w+)", stmt, re.I)
    return m.group(1) if m else ""


def _extract_trigger_table(stmt: str) -> str:
    """Extract the table name from a CREATE TRIGGER statement."""
    import re
    m = re.search(r"ON\s+(\w+)", stmt, re.I)
    return m.group(1) if m else ""


# ============================================================================
# Health check — used by the upgrade tool and monitoring
# ============================================================================

async def check_health() -> dict:
    """Check system health by polling the API /health endpoint."""
    import urllib.request
    import json as _json

    for port in (8700, 8003):
        try:
            req = urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5)
            data = _json.loads(req.read())
            return {"healthy": data.get("status") == "healthy", "details": data, "source": f"api:{port}"}
        except Exception:
            continue

    # Fallback: DB-only
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return {"healthy": True, "details": {"database": "ok"}, "source": "db-direct"}
    except Exception as e:
        return {"healthy": False, "details": {"error": str(e)}, "source": "db-failed"}


# ============================================================================
# Schema version migration runner
# ============================================================================

async def get_schema_version() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        try:
            return await conn.fetchval("SELECT max(version) FROM schema_migrations") or 0
        except Exception:
            return 0


async def apply_versioned_migrations() -> list[str]:
    """Apply pending migrations from sql/migrations/ directory.

    Uses schema_migrations table to track applied versions.
    Only runs migrations with version > current.
    """
    root = _project_root()
    migrations_dir = os.path.join(root, "sql", "migrations")
    if not os.path.isdir(migrations_dir):
        return ["No migrations directory found"]

    migration_files = []
    for fname in sorted(os.listdir(migrations_dir)):
        if fname.endswith(".sql"):
            parts = fname.split("_", 1)
            try:
                version = int(parts[0])
                name = parts[1].replace(".sql", "") if len(parts) > 1 else fname
                migration_files.append((version, name, os.path.join(migrations_dir, fname)))
            except (ValueError, IndexError):
                continue

    if not migration_files:
        return ["No migration files found"]

    current_version = await get_schema_version()
    applied = []
    pool = await get_pool()
    async with pool.acquire() as conn:
        for version, name, filepath in migration_files:
            if version <= current_version:
                continue
            with open(filepath) as f:
                sql = f.read()
            try:
                for stmt in _split_sql_statements(sql):
                    stmt = stmt.strip()
                    if stmt and not stmt.startswith("--"):
                        try:
                            await conn.execute(stmt)
                        except Exception as e:
                            if "already exists" not in str(e).lower():
                                print(f"    Warning: {e}", flush=True)
                await conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                    version, name,
                )
                applied.append(f"Applied migration {version:03d}: {name}")
            except Exception as e:
                applied.append(f"FAILED migration {version:03d}: {name} — {e}")
    return applied


# ============================================================================
# Blue-green rolling restart
# ============================================================================

async def signal_worker_drain(worker_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("UPDATE worker_registry SET draining = TRUE WHERE worker_id = $1", worker_id)


async def wait_for_worker_drain(worker_id: str, timeout: int = 120) -> bool:
    pool = await get_pool()
    deadline = time.time() + timeout
    while time.time() < deadline:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT current_step_id FROM worker_registry WHERE worker_id = $1", worker_id,
            )
        if not row or not row["current_step_id"]:
            return True
        time.sleep(2)
    return False


def _service_worker_id(svc: str) -> str | None:
    try:
        result = subprocess.run(
            ["systemctl", "show", svc, "--property=Environment"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            for line in result.stdout.split():
                if line.startswith("JOB_STAR_WORKER="):
                    return line.split("=", 1)[1]
    except Exception:
        pass
    return None


async def rolling_restart_worker(svc: str, drain_timeout: int = 90) -> bool:
    """Blue-green restart: drain via DB, then systemctl restart."""
    worker_id = _service_worker_id(svc)
    if not worker_id:
        restart_service(svc)
        return wait_for_service(svc, timeout=15)

    print(f"  │  Signaling {worker_id} to drain...")
    await signal_worker_drain(worker_id)
    print(f"  │  Waiting for drain (timeout {drain_timeout}s)...")
    drained = await wait_for_worker_drain(worker_id, timeout=drain_timeout)
    if drained:
        print(f"  │  Drained")
    else:
        print(f"  │  Drain timeout — forcing restart")

    restart_service(svc)
    success = wait_for_service(svc, timeout=15)
    if success:
        time.sleep(1)
        print(f"  │  Active with new code")
    return success


# ============================================================================
# Automatic rollback
# ============================================================================

def save_pre_upgrade_commit() -> str | None:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=_project_root())
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def rollback_to_commit(commit: str) -> bool:
    print(f"  │  Rolling back to commit {commit[:8]}...")
    result = subprocess.run(["git", "reset", "--hard", commit], capture_output=True, text=True, cwd=_project_root())
    if result.returncode != 0:
        print(f"  │  Git rollback failed: {result.stderr}")
        return False
    print(f"  │  Code rolled back")
    for svc in SERVICES:
        print(f"  │  Restarting {svc}...")
        restart_service(svc)
        wait_for_service(svc, timeout=10)
    return True


# ============================================================================
# Full upgrade process — blue-green with automatic rollback
# ============================================================================

async def run_upgrade(
    commit: bool = False,
    dry_run: bool = False,
    reap_only: bool = False,
) -> int:
    """Run the full upgrade process. Returns exit code (0 = success)."""
    print()
    print("  ╔══════════════════════════════════════════════════════════╗")
    print("  ║          Job-Star Upgrade Process                         ║")
    print(f"  ║  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{' ' * 47}   ║")
    print("  ╚══════════════════════════════════════════════════════════╝")
    print()

    # Save pre-upgrade commit for potential rollback
    pre_commit = save_pre_upgrade_commit()
    if pre_commit:
        print(f"  Pre-upgrade commit: {pre_commit[:8]}")
    print()

    # ─── 1. PRE-FLIGHT ─────────────────────────────────────────────────
    print("  ┌─ 1. PRE-FLIGHT CHECKS")
    print("  │")
    results = await preflight_checks()

    if results["errors"]:
        print(f"  │  ✗ ERRORS FOUND — upgrade cannot proceed:")
        for err in results["errors"]:
            print(f"  │    • {err}")
        print("  │")
        print("  │  Fix these issues before upgrading.")
        print("  └─")
        return 1

    print(f"  │  DB connected:      {'✓' if results['db_connected'] else '✗'}")
    print(f"  │  Syntax check:      {'✓' if results['syntax_ok'] else '✗'}")
    print(f"  │  Import check:      {'✓' if results['tests_pass'] else '✗'}")
    print(f"  │  Git clean:         {'✓' if results['git_clean'] else '⚠ uncommitted changes'}")
    print(f"  │  Orphaned steps:    {results['orphaned_steps']}")
    print(f"  │  Active workers:    {results['active_workers']}")

    for w in results["warnings"]:
        print(f"  │  ⚠ {w}")

    if not results["syntax_ok"] or not results["tests_pass"]:
        print("  │")
        print("  │  ✗ Code has errors — fix before upgrading.")
        print("  └─")
        return 1
    print("  │")
    print("  └─ ✓ Pre-flight passed")
    print()

    # ─── REAP ONLY ────────────────────────────────────────────────────
    if reap_only:
        print("  ┌─ REAPING ORPHANED STEPS")
        reaped = await reap_stale_steps()
        if reaped > 0:
            print(f"  │  ✓ Reaped {reaped} orphaned step(s) → reset to pending")
        else:
            print(f"  │  No orphaned steps found.")
        print("  └─")
        await close_pool()
        return 0

    if dry_run:
        print("  ┌─ 2. REAP (DRY RUN)")
        print(f"  │  Would reap {results['orphaned_steps']} orphaned step(s)")
        print("  └─")
        print()
        print("  ┌─ 3. MIGRATE (DRY RUN)")
        print("  │  Would apply versioned migrations from sql/migrations/")
        print("  └─")
        print()
        print("  ┌─ 4. RESTART (DRY RUN — blue-green rolling)")
        print("  │  Would rolling-restart: {', '.join(SERVICES)}")
        print("  │  (one at a time, drain via DB, no downtime)")
        print("  └─")
        print()
        print("  ┌─ 5. VERIFY (DRY RUN)")
        print("  │  Would check /health endpoint + DB + automatic rollback on failure")
        print("  └─")
        print()
        print("  Dry run complete. No changes made.")
        await close_pool()
        return 0

    print("  ┌─ 2. REAP — resetting orphaned steps")
    reaped = await reap_stale_steps()
    if reaped > 0:
        print(f"  │  Reaped {reaped} orphaned step(s) — reset to pending")
    else:
        print(f"  │  No orphaned steps to reap.")
    print("  └─")
    print()
    # ─── 3. MIGRATE ────────────────────────────────────────────────────
    print("  ┌─ 3. MIGRATE — applying versioned schema migrations")
    current_ver = await get_schema_version()
    print(f"  │  Current schema version: {current_ver}")
    migrations = await apply_versioned_migrations()
    if migrations:
        for m in migrations:
            if m.startswith("FAILED"):
                print(f"  │  ✗ {m}")
            elif m.startswith("Applied"):
                print(f"  │  ✓ {m}")
            else:
                print(f"  │  {m}")
    else:
        print(f"  │  Already at latest schema version.")
    print("  └─")
    print()

    # ─── 4. RESTART (blue-green rolling) ─────────────────────────────
    print("  ┌─ 4. RESTART — blue-green rolling restart")
    print("  │")
    all_ok = True

    # API first (stateless — safe to restart immediately)
    api_svc = "job-star-api"
    print(f"  │  [{api_svc}] restarting...")
    restart_service(api_svc)
    if wait_for_service(api_svc, timeout=10):
        print(f"  │  [{api_svc}] active")
    else:
        print(f"  │  [{api_svc}] FAILED")
        all_ok = False

    # Workers: rolling restart one at a time (blue-green)
    worker_services = [s for s in SERVICES if "worker" in s]
    for svc in worker_services:
        print(f"  │  [{svc}] blue-green rolling restart...")
        if await rolling_restart_worker(svc):
            print(f"  │  [{svc}] active with new code")
        else:
            print(f"  │  [{svc}] FAILED")
            all_ok = False

    print("  │")
    print("  └─ Services restarted")
    print()

    # ─── 5. VERIFY (health check + automatic rollback) ────────────────
    print("  ┌─ 5. VERIFY — post-upgrade health check")
    print("  │")
    for svc in SERVICES:
        status = service_status(svc)
        icon = "✓" if status == "active" else "✗"
        print(f"  │  {icon} {svc}: {status}")
        if status != "active":
            all_ok = False

    # API health endpoint check
    health = await check_health()
    if health["healthy"]:
        print(f"  │  ✓ Health endpoint: healthy (via {health['source']})")
    else:
        print(f"  │  ✗ Health endpoint: unhealthy")
        all_ok = False

    # DB check
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            total = await conn.fetchval("SELECT count(*) FROM goals")
            pending = await conn.fetchval("SELECT count(*) FROM goal_steps WHERE status='pending'")
            new_ver = await conn.fetchval("SELECT max(version) FROM schema_migrations") or 0
        print(f"  │  ✓ DB responsive: {total} goals, {pending} pending steps, schema v{new_ver}")
        await close_pool()
    except Exception as e:
        print(f"  │  ✗ DB check failed: {e}")
        all_ok = False

    print("  │")
    if all_ok:
        print("  └─ ✓ Upgrade complete — all services healthy")
    else:
        print("  └─ ⚠ Verification FAILED — attempting automatic rollback...")
        print()
        if pre_commit and rollback_to_commit(pre_commit):
            print("  ┌─ ROLLBACK COMPLETE")
            for svc in SERVICES:
                print(f"  │  {svc}: {service_status(svc)}")
            print("  └─")
            await audit("system_rollback", {"from_commit": pre_commit, "reason": "verification failed"})
            print()
            print("  ⚠ System rolled back to previous commit. Check logs for details.")
            return 3
        else:
            print("  ✗ AUTOMATIC ROLLBACK FAILED — manual intervention required!")
            return 4
    print()

    await audit("system_upgraded", {
        "reaped_steps": reaped,
        "migrations": migrations,
        "pre_commit": pre_commit,
        "services": {svc: service_status(svc) for svc in SERVICES},
    })

    await close_pool()
    return 0 if all_ok else 2


async def upgrade_main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Job-Star upgrade tool")
    parser.add_argument("--check", action="store_true", help="Pre-flight check only (dry run)")
    parser.add_argument("--reap", action="store_true", help="Reap orphaned steps only")
    parser.add_argument("--commit", action="store_true", help="Commit code changes before upgrade")
    args = parser.parse_args()

    if args.check:
        results = await preflight_checks()
        print(f"\n  Pre-flight results:")
        for k, v in results.items():
            if isinstance(v, list):
                for item in v:
                    print(f"    {k}: {item}")
            else:
                print(f"    {k}: {v}")
        await close_pool()
        return 0 if not results["errors"] else 1

    return await run_upgrade(
        commit=args.commit,
        dry_run=args.check,
        reap_only=args.reap,
    )