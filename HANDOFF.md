# Job-Star Handoff Document

## Date: July 13, 2026
## Session: Check-In System + Safe Upgrade Process + Blue-Green Deployment

---

## 1. What Is Job-Star?

Job-Star is a **constrained, supervised, goal-oriented AI orchestration system** that manages both coding and personal goals over extended timeframes. It accepts raw input (text, screenshots, voice) through zero-friction intake surfaces, triages it with AI, registers it in a shared goal registry, and routes execution to the appropriate AI model based on urgency, cost, and availability.

The core philosophy: **constrained, supervised, goal-oriented AI with situational awareness beats unconstrained, reactive, general-purpose AI every time.**

---

## 2. What Changed This Session

### 2.1 Check-In System (New)

Structured two-way progress dialogue between job-star and the user. See `docs/check-ins.md` for full documentation.

**Four types:**
- 📊 **Progress** — after every N completed steps (default 3, configurable per goal)
- ❓ **Clarification** — when a step fails 2+ times
- 🏁 **Milestone** — on demand, for phase reviews
- ✅ **Completion** — when all steps done, replaces auto-complete (user must accept)

**Key files:**

| File | Purpose |
|------|---------|
| `job_star/checkin/__init__.py` | Models: `CheckIn`, `CheckInType`, `CheckInStatus`, `CheckInQuestion` |
| `job_star/checkin/engine.py` | `CheckInEngine`: AI generation, trigger logic, response processing, DB CRUD |
| `job_star/orchestrator.py` | Triggers check-ins after step completion, failure, and goal completion |
| `job_star/cli.py` | `checkin` command: list, show, pending, create, respond |
| `job_star/api/routes.py` | API endpoints: `GET/POST /check-ins`, `POST /check-ins/{id}/respond` |

**How it works:**
1. Trigger detected (step count, failure, all-done) → AI generates structured check-in
2. Check-in saved to Postgres, event published to SSE bus
3. User responds via CLI or API with answers + free-text feedback
4. System processes response: accept → goal completed, reject → goal stays open
5. Decision logged in decisions table with shadow paths

**Verified end-to-end:** AI generated a progress check-in for the Feynman learning goal that summarized the goal, suggested next steps, and asked "Which Feynman-inspired practice would you like to start with?" with 4 options. User responded via API → system processed the decision.

### 2.2 Safe Upgrade Process (New)

Replaces the previous "edit files and hope" approach with a structured 5-stage process. See `UPGRADE.md` for full documentation.

**The upgrade tool:**
```bash
python3 -m job_star upgrade           # Full upgrade
python3 -m job_star upgrade --check    # Pre-flight only (dry run)
python3 -m job_star upgrade --reap     # Reap orphaned steps only
```

**5 stages:**
1. **Pre-flight** — syntax check, import check, git status, orphan detection, DB connectivity
2. **Reap** — reset stale `in_progress` steps to `pending` (orphaned by crashed workers)
3. **Migrate** — apply versioned DB migrations from `sql/migrations/`
4. **Restart** — blue-green rolling restart (one worker at a time, zero downtime)
5. **Verify** — health endpoint + DB check + automatic rollback on failure

**Key files:**

| File | Purpose |
|------|---------|
| `job_star/upgrade.py` | The upgrade tool: pre-flight, reap, migrate, restart, verify, rollback |
| `UPGRADE.md` | Full upgrade documentation |
| `sql/migrations/` | Versioned migration files (`001_*.sql`, `002_*.sql`, ...) |

### 2.3 Blue-Green Deployment (New)

Workers are restarted one at a time — never all at once. Zero downtime.

**How it works:**
1. `worker_registry` table tracks all active workers (worker_id, generation, draining, heartbeat)
2. Workers register on startup, send heartbeats each loop, check `draining` flag
3. Upgrade tool sets `draining=TRUE` for one worker via DB
4. Worker sees drain signal → finishes current step → exits gracefully
5. systemctl restarts with new code → new worker registers
6. Other workers keep running throughout

**Key files:**

| File | Purpose |
|------|---------|
| `job_star/worker_core.py` | `_register()`, `_heartbeat()`, `_check_drain_signal()`, `_unregister()`, SIGTERM handler |
| `job_star/upgrade.py` | `rolling_restart_worker()`, `signal_worker_drain()`, `wait_for_worker_drain()` |
| DB table | `worker_registry` (worker_id, generation, draining, last_heartbeat, current_step_id) |

**systemd changes:**
- `TimeoutStopSec=120` added to all worker services (2 min to drain before SIGKILL)
- `JOB_STAR_GENERATION=1` added to all worker services

### 2.4 Health Check Endpoint (New)

`GET /health` — no auth required. Returns 200 (healthy) or 503 (unhealthy).

Checks: database (goals, steps, orphans), gateway, workers (from registry), schema version.

Used by the upgrade tool's verification stage and available for external monitoring.

**Key file:** `job_star/api/app.py` — comprehensive `/health` endpoint replacing the old stub.

### 2.5 Automatic Rollback (New)

Before each upgrade, the current git commit hash is saved. If verification fails:
1. `git reset --hard` to the previous commit
2. All services restarted with old code
3. Logged to audit trail as `system_rollback`
4. Exit code 3 (rollback succeeded) or 4 (rollback failed — manual intervention)

### 2.6 Schema Version Tracking (New)

- `schema_migrations` table tracks applied migration versions
- `sql/migrations/` directory with numbered files
- Migrations 001 (initial schema) and 002 (check-ins + worker registry + schema versioning) marked as applied
- Future migrations: drop a new `003_*.sql` file, run upgrade

### 2.7 Graceful Worker Shutdown (New)

Workers now handle SIGTERM:
1. SIGTERM → `_draining = True`
2. Finish current step (AI call completes, result saved to DB)
3. Stop claiming new steps
4. Exit cleanly (unregister from `worker_registry`, close DB pool)
5. systemd restarts with new code

### 2.8 Documentation (New)

| File | Purpose |
|------|---------|
| `UPGRADE.md` | Full upgrade guide: stages, blue-green, rollback, schema versioning, manual recovery |
| `docs/check-ins.md` | Check-in system: types, lifecycle, CLI/API usage, triggers, schema |
| `docs/feynman-thinking-plan.md` | 70-day daily practice plan for learning Feynman's thinking methods |

---

## 3. Current State Summary

### 3.1 Bootstrap Components (Complete)

| # | Component | Status |
|---|-----------|--------|
| 1 | Postgres schema | ✅ |
| 2 | Seed CLI (TypeScript) | ✅ |
| 3 | Context Gatherer | ✅ |
| 4 | Triage Engine | ✅ |
| 5 | Router | ✅ |
| 6 | Supervisor (Rust) | ✅ |
| 7 | Gatehouse Integration | ✅ |
| 8 | Consolidation | ✅ |
| 9 | Follow-up Engine | ✅ |
| 10 | Idle Loop | ✅ |
| 11 | Conflict Detection | ✅ |
| 12 | Web Intake | ✅ |
| 13 | Telegram Integration | ✅ |

### 3.2 New This Session

| # | Component | Status |
|---|-----------|--------|
| 14 | Check-In System | ✅ |
| 15 | Upgrade Tool | ✅ |
| 16 | Blue-Green Deployment | ✅ |
| 17 | Health Endpoint | ✅ |
| 18 | Automatic Rollback | ✅ |
| 19 | Schema Version Tracking | ✅ |
| 20 | Graceful Worker Shutdown | ✅ |
| 21 | Orphan Step Reaper | ✅ |

### 3.3 Tests

**33/33 passing** (27 original + 6 new check-in tests)

### 3.4 Database

- **Container:** `job-star-db` (running)
- **Connection:** `postgresql://jobstar:jobstar@localhost:5432/job_star`
- **Tables:** goals, goal_steps, audit_trail, goal_conflicts, decisions, job_queue, events, experts, check_ins, schema_migrations, worker_registry
- **Schema version:** 2
- **Goals:** 68 (32 active, 35 completed, 1 blocked)

### 3.5 Services

| Service | Status | Port |
|---------|--------|------|
| job-star-api | active | 8003 |
| job-star-worker | active | — |
| job-star-worker-gatehouse | active | — |
| job-star-worker-research | active | — |
| job-star-panel | active (tmux) | — |

### 3.6 Worker Registry

3 workers registered: nexus, gatehouse-ai-expert, research-harvester — all generation 1, none draining.

### 3.7 Health Endpoint

```
GET /health → 200
  database: healthy (68 goals, 37 pending, 0 orphans)
  gateway: healthy
  workers: 3 active
  schema_version: 2
```

---

## 4. How to Run

### CLI

```bash
# Goal management
python3 -m job_star add "title" --desc "description" --urgency soon
python3 -m job_star list
python3 -m job_star show <id>
python3 -m job_star work <id>
python3 -m job_star complete <id>
python3 -m job_star status
python3 -m job_star digest [N]

# Check-ins
python3 -m job_star checkin list [--goal <id>] [--status pending]
python3 -m job_star checkin pending
python3 -m job_star checkin show <id>
python3 -m job_star checkin create <goal-id> --type progress|clarification|milestone|completion
python3 -m job_star checkin respond <id> --answer "1" --feedback "text"

# Upgrade
python3 -m job_star upgrade           # Full upgrade (blue-green)
python3 -m job_star upgrade --check  # Pre-flight check
python3 -m job_star upgrade --reap   # Reap orphaned steps

# Workers
python3 -m job_star worker --interval 15
JOB_STAR_EXPERT=gatehouse-ai python3 -m job_star worker  # expert worker
```

### API

```bash
# Health (no auth)
curl http://localhost:8003/health

# Goals (auth required)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8003/api/v1/goals

# Check-ins
curl -H "Authorization: Bearer $TOKEN" http://localhost:8003/api/v1/check-ins
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"type": "progress"}' \
  http://localhost:8003/api/v1/goals/<id>/check-in
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"response": "Looks good", "decisions": [{"question_id": "abc", "answer": "Accept"}]}' \
  http://localhost:8003/api/v1/check-ins/<id>/respond
```

### Tests
```bash
python3 -m pytest tests/test_integration.py -v
# 33 tests, all passing
```

---

## 5. Documentation Index

| Document | What it covers |
|----------|---------------|
| `job-star-design.md` | Full architecture design document (v0.1) |
| `job-star-bootstrap.md` | Self-hosting bootstrap plan |
| `HANDOFF.md` | This file — current state and how to run |
| `UPGRADE.md` | Safe upgrade process: blue-green, rollback, schema versioning |
| `docs/check-ins.md` | Check-in system: types, lifecycle, CLI/API, triggers |
| `docs/feynman-thinking-plan.md` | 70-day Feynman thinking practice plan |
| `CODE_REVIEW.md` | Code review findings and fixes (from prior session) |

---

## 6. Key Design Decisions (This Session)

1. **Completion check-ins replace auto-complete.** Goals are no longer auto-completed when all steps finish. A completion check-in is created, and the user must accept the result. This ensures quality control on AI-generated work.

2. **Check-ins are AI-generated.** The system uses an AI model to summarize progress and formulate specific questions. If AI is unavailable, a fallback check-in is created from raw step data.

3. **Blue-green via DB drain signals.** Workers poll a `draining` flag in Postgres. The upgrade tool sets it. This works across machines — no signals or API calls needed.

4. **Additive migrations only.** Never drop or rename. Old code must work with new schema. This enables mixed-version fleets during rolling restarts.

5. **The upgrade tool is version-agnostic.** It doesn't import job-star modules for its core logic (only for syntax/import checks). It works regardless of code version.

6. **Health endpoint is unauthenticated.** So the upgrade tool and external monitoring can poll it without credentials. It's on the tailnet boundary.

7. **Automatic rollback is last resort.** The upgrade verifies health before declaring success. If it fails, it rolls back the code and restarts. This prevents leaving the system in a broken state.

---

## 7. What to Tell the Next Session

**The system is stable and fully operational.** All 4 services are running, 33 tests pass, the health endpoint reports healthy, 3 workers are registered and heartbeating.

**Key things to know:**
- To modify job-star safely: commit changes, then run `python3 -m job_star upgrade`
- The upgrade tool handles draining, reaping, migrating, restarting, and verifying
- If something breaks, the upgrade auto-rolls back to the previous git commit
- Check-ins are now automatically triggered after step completion and at goal completion
- The `/health` endpoint shows full system status
- Documentation is in `UPGRADE.md` and `docs/check-ins.md`

**Git commits this session:**
- `bf017c6` — Check-in system + upgrade tool + graceful worker shutdown
- `c84f5d3` — Blue-green deployment + health endpoint + schema versioning + auto-rollback
- `bdfba2f` — Fix missing import + remove old DRAIN section + fix pool management

---

*Generated by job-star session, July 13 2026. The system now upgrades itself safely.*