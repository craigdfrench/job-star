# Job-Star Upgrade Guide

## Safe, Staged, Verifiable Upgrades

---

## Overview

Job-star runs as a set of long-lived services (API + workers) backed by a shared Postgres database. Modifying the system while it's running requires a structured process to avoid orphaned work, broken services, and data loss.

The upgrade tool (`python3 -m job_star upgrade`) automates this process in 5 stages:

```
1. Pre-flight:  syntax + import + git + orphan check
2. Reap:         reset stale in_progress steps
3. Migrate:      apply versioned DB migrations
4. Restart:      blue-green rolling restart (zero downtime)
5. Verify:       health endpoint + DB check + auto-rollback on failure
```

---

## When to Upgrade

Run the upgrade process whenever you:
- Modify Python code that workers or the API use
- Add or modify database tables
- Change systemd service files
- Want to reap orphaned steps
- Want to verify system health

### When NOT to upgrade
- Modifying only documentation or generated code (no service impact)
- Modifying only the web panel HTML (served by the API but doesn't affect workers)
- Editing test files only

For these cases, just commit the changes. No restart needed.

---

## Quick Reference

```bash
# Check if it's safe to upgrade (dry run — no changes)
python3 -m job_star upgrade --check

# Reap orphaned steps only (no restart)
python3 -m job_star upgrade --reap

# Full upgrade (stops/restarts services)
python3 -m job_star upgrade

# Full upgrade with auto-commit first
python3 -m job_star upgrade --commit
```

---

## The 5 Stages in Detail

### Stage 1: Pre-flight

Checks that the code is safe to deploy before touching anything:

| Check | What it does | Failure action |
|-------|-------------|----------------|
| **Syntax** | `py_compile` on every `.py` file in `job_star/` | Refuses to upgrade |
| **Import** | Imports core modules (`orchestrator`, `cli`, `worker`, `checkin`) | Refuses to upgrade |
| **Git** | Checks for uncommitted changes | Warns (doesn't block) |
| **Orphans** | Counts `in_progress` steps older than 10 min | Warns |
| **Workers** | Counts active workers from recent audit_trail | Informational |
| **DB** | Verifies Postgres is reachable | Refuses to upgrade |

### Stage 2: Reap

Resets orphaned `in_progress` steps back to `pending`:

- A step is orphaned if it's been `in_progress` for more than 10 minutes
- This happens when a worker is killed mid-step (crash, OOM, manual kill)
- Orphaned steps are invisible to workers (`claim_next_step` only picks `pending`)
- Reaping makes them visible again
- Each reaping is logged to the audit trail with the reason

```sql
UPDATE goal_steps SET status = 'pending'
WHERE status = 'in_progress'
  AND attempted_at < NOW() - INTERVAL '10 minutes'
```

### Stage 3: Migrate

Applies versioned database migrations:

- The `schema_migrations` table tracks which migrations have been applied
- Migration files live in `sql/migrations/` with numbered names (`001_*.sql`, `002_*.sql`, ...)
- Only migrations with a version number higher than the current schema version are applied
- After applying, each migration is recorded in `schema_migrations`

**Adding a new migration:**
1. Create `sql/migrations/003_describe_your_change.sql`
2. Write additive SQL (CREATE TABLE IF NOT EXISTS, ALTER TABLE ADD COLUMN, etc.)
3. Commit it
4. Run `python3 -m job_star upgrade` — the migration applies automatically

**Rules for migrations:**
- ✅ Additive only: new tables, new columns with defaults, new indexes
- ❌ Never drop tables or columns
- ❌ Never rename columns (add a new one, migrate data, then drop the old in a later migration)
- ✅ Use `IF NOT EXISTS` on all CREATE statements
- ✅ Old code must still work with the new schema (backward compatible)

### Stage 4: Restart (Blue-Green Rolling)

Restarts services one at a time — zero downtime:

```
1. API restarts first (stateless — safe to restart immediately)
2. For each worker, one at a time:
   a. Set draining=TRUE in worker_registry (DB signal)
   b. Worker sees drain signal → stops claiming new work
   c. Worker finishes current step (if mid-execution)
   d. Worker exits gracefully
   e. systemctl restarts with new code
   f. New worker registers, starts claiming
3. Other workers keep running throughout — no gap in processing
```

**Why this is safe:**
- Workers communicate through Postgres, not through each other
- While one worker is restarting, others continue processing the queue
- The draining worker finishes its current step before exiting
- `TimeoutStopSec=120` gives workers 2 minutes to drain before SIGKILL
- The worker has a SIGTERM handler that sets a drain flag

### Stage 5: Verify (with Auto-Rollback)

Post-upgrade health check:

1. All systemd services must be `active`
2. The `/health` endpoint must return 200 (healthy)
3. The DB must be responsive

If any check fails:
1. The pre-upgrade git commit hash was saved before starting
2. `git reset --hard <previous commit>` restores the old code
3. All services are restarted with the old code
4. The rollback is logged to the audit trail
5. Exit code 3 = rollback succeeded, 4 = rollback failed (manual intervention needed)

---

## Worker Registry

Workers register themselves in the `worker_registry` table on startup:

| Column | Purpose |
|--------|---------|
| `worker_id` | From `JOB_STAR_WORKER` env var (e.g., `nexus`, `gatehouse-ai-expert`) |
| `generation` | From `JOB_STAR_GENERATION` env var (starts at 1) |
| `draining` | Set to TRUE by the upgrade tool to signal graceful drain |
| `last_heartbeat` | Updated each loop iteration and on step claim |
| `current_step_id` | The step currently being executed (NULL when idle) |
| `started_at` | When this worker instance started |
| `metadata` | JSON with machine, expert, urgency, interval |

Workers check `draining` on each loop iteration. If TRUE, they:
1. Finish the current step (if mid-execution)
2. Stop claiming new steps
3. Exit cleanly
4. Unregister from the table

---

## Health Endpoint

`GET /health` — no auth required. Returns 200 if healthy, 503 if unhealthy.

```json
{
  "status": "healthy",
  "timestamp": "2026-07-13T14:49:33Z",
  "checks": {
    "database": {
      "status": "healthy",
      "goals": 68,
      "steps_pending": 37,
      "steps_in_progress": 5,
      "orphaned_steps": 0
    },
    "gateway": { "status": "healthy" },
    "workers": {
      "status": "active",
      "active_count": 3,
      "registered": [
        { "worker_id": "nexus", "generation": 1, "draining": false }
      ]
    },
    "schema_version": 2
  }
}
```

This endpoint is used by:
- The upgrade tool's verification stage
- The web panel (could poll for health status)
- External monitoring (can poll without credentials)

---

## Graceful Worker Shutdown

Workers handle SIGTERM gracefully:

1. SIGTERM received → `_draining = True` set
2. Worker finishes the current step (AI call completes, result saved)
3. Worker stops claiming new steps
4. Worker exits cleanly
5. `close_pool()` closes the DB connection
6. `unregister()` removes from `worker_registry`

This prevents orphaned `in_progress` steps. Combined with `TimeoutStopSec=120`, workers have up to 2 minutes to finish before SIGKILL.

---

## Schema Versioning

### How it works

```
sql/
  schema.sql              ← canonical full schema (for fresh installs)
  migrations/
    001_initial_schema.sql  ← migration v1
    002_check_ins.sql        ← migration v2
    003_future.sql           ← add new migrations here
```

- `schema.sql` is the complete schema for a fresh database install
- `sql/migrations/` contains incremental migrations for existing databases
- The `schema_migrations` table tracks which versions have been applied
- The upgrade tool only applies migrations with version > current

### For a fresh install
```bash
createdb job_star
psql job_star < sql/schema.sql
# All tables created, seed data inserted
# schema_migrations not populated — run upgrade to populate
python3 -m job_star upgrade
```

### For an existing database
```bash
# Just run upgrade — it applies pending migrations
python3 -m job_star upgrade
```

---

## Manual Recovery

### If a worker is crash-looping
```bash
# Check the journal
sudo journalctl -u job-star-worker --since "5 min ago" --no-pager

# Stop the service
sudo systemctl stop job-star-worker

# Fix the code, then restart
sudo systemctl start job-star-worker
```

### If steps are stuck in_progress
```bash
# Reap manually
python3 -m job_star upgrade --reap

# Or directly in SQL
docker exec job-star-db psql -U jobstar -d job_star -c \
  "UPDATE goal_steps SET status='pending' WHERE status='in_progress' AND attempted_at < NOW() - INTERVAL '10 minutes'"
```

### If the upgrade broke something
```bash
# The upgrade auto-rolls back, but if you need to do it manually:
git log --oneline -5          # find the previous good commit
git reset --hard <commit>     # restore old code
sudo systemctl restart job-star-api job-star-worker job-star-worker-gatehouse job-star-worker-research
```

### If the DB schema is out of sync
```bash
# Check current version
docker exec job-star-db psql -U jobstar -d job_star -c "SELECT * FROM schema_migrations"

# Apply pending migrations
python3 -m job_star upgrade
```

---

## Design Principles

1. **Additive migrations only** — never drop or rename. Old code must work with new schema.
2. **Blue-green rolling restart** — one worker at a time, others keep running. Zero downtime.
3. **Drain via DB, not via signal** — the upgrade tool sets a flag in Postgres, workers poll it. This works across machines.
4. **Verify before declaring success** — health endpoint, DB check, service status. Auto-rollback on failure.
5. **Orphan reaping is idempotent** — running it multiple times is safe. Only reaps steps older than 10 minutes.
6. **The upgrade tool is version-agnostic** — it works regardless of code version. It doesn't import job-star modules (except for syntax/import checks).