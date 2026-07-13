# Job-Star Handoff

**Last updated:** 2026-07-13 (GLM-5.2 session)
**Repo:** `https://github.com/craigdfrench/job-star`
**Branch:** `main`

## What Job-Star Is

A self-hosted AI goal orchestration system. Users submit goals ("fix the login bug", "add a daily report"), the system triages them, breaks them into steps, and AI workers execute each step — writing code, running tests, creating PRs. A check-in system notifies the user when there's something to review or decide.

## Current State (2026-07-13)

- **12 active goals**, 5 awaiting user review (completion check-ins), 0 pending steps
- **System healthy**: 0 critical issues, monitor clean, $0 cost today, 4 workers running
- **34 tests passing**, pushed to GitHub
- **Commit:** `ef75780`

## Architecture

### Core Components

| Component | File | Purpose |
|-----------|------|---------|
| Orchestrator | `orchestrator.py` | Coordinates intake → plan → execute → check-in |
| Worker core | `worker_core.py` | Claims steps, executes them, heartbeats every loop |
| Triage | `triage/engine.py` | Classifies goals (domain/urgency/type), detects duplicates |
| Router | `router/engine.py` | Picks which model to use per step |
| Monitor | `monitor.py` | Self-healing: runs every 5 min via systemd timer |
| Dashboard | `dashboard.py` | CLI summary: what needs attention, what's happening |
| Notify | `notify.py` | SMTP → local vikunja mail gateway → Gmail + Google Chat |
| Upgrade | `upgrade.py` | Blue-green rolling restart, schema migrations, auto-rollback |

### Executors

| Executor | File | Handles |
|----------|------|---------|
| Default | `executors/default.py` | Text-only research/planning steps |
| PR executor | `executors/pr_executor.py` | Writes files, runs tests, creates GitHub PRs |
| Gatehouse-ai | `executors/gatehouse_ai.py` | Go codebase work (uses `go test`) |
| JobStar | `executors/job_star.py` | Job-star's own codebase (runs pytest) |
| Research | `executors/research.py` | Web/research steps (throttled: 25+ day recurring) |

### API & Web

| Endpoint | File | Purpose |
|----------|------|---------|
| `/api/v1/intake` | `routes.py` | Add goal (full triage pipeline, duplicate detection) |
| `/api/v1/whoami` | `routes.py` | Returns Tailscale user email |
| `/api/v1/check-ins/{id}/respond` | `routes.py` | Submit check-in response |
| `/api/v1/check-ins/{id}/discuss` | `routes.py` | LLM discussion (Gemini Flash, no auth) |
| `/add` | `intake_page.html` | Web intake form for family members |
| `/checkins` | `checkins_page.html` | List all check-ins with filters |
| `/checkin/{id}` | `checkin_page.html` | Single check-in discussion page |
| `/health` | `app.py` | Health endpoint for blue-green drain |

### Auth

- **Tailscale IP trust**: `100.64.0.0/10` + localhost are trusted (no token needed)
- **Tailscale user email**: `auth.py` runs `tailscale whois <ip>` to resolve user email (server is tagged, so Caddy `tailscale_auth` doesn't work)
- **`requested_by`**: goals store who requested them; notifications route to that email
- **Check-in API routes** require `Depends(get_current_user)` — direct internet hits need token
- **Discussion endpoint** is no-auth (advisory only, tailnet boundary)

### Check-in System

- **Types**: progress, clarification, milestone, completion
- **Cooldowns**: progress check-ins 7 days (168h), clarification 24h per goal
- **Actionable**: clarification check-ins show the actual error and offer choices (retry, skip, abandon, more info)
- **Auto-accept**: completion check-ins older than 7 days auto-accept (monitor)
- **Auto-expiry**: stale check-ins expired by monitor
- **Web flow**: `/checkin/{id}` → read → discuss with AI → "Draft my response with AI" → edit → submit

### Self-Healing (Monitor)

Runs every 5 min via `job-star-monitor.timer`:
- Reaps orphaned steps (on completed/paused goals)
- Pauses runaway loops (>20 steps/hour threshold)
- Resets failed steps to pending after 1-hour cooldown
- Auto-accepts completion check-ins after 7 days
- Expires stale check-ins
- Checks worker/gateway health, budget exhaustion

## Services (systemd)

- `job-star-api` — FastAPI on port 8003
- `job-star-worker` — generic worker (default executor)
- `job-star-worker-gatehouse` — gatehouse-ai expert worker
- `job-star-worker-research` — research expert worker
- `job-star-worker-jobstar` — job-star's own codebase worker
- `job-star-monitor.timer` — runs monitor every 5 min

Secrets: `/etc/job-star-api-secrets.env` (API token + SMTP credentials)

## Key Decisions

- **Tailscale auth via IP range** (not `tailscale_auth`): tagged machine has no user identity in Caddy
- **Gemini Flash for discussion**: `gemini-3-5-flash-minimal` is CHEAP tier, confirmed working
- **PR executor uses `git clone`** (not worktree): `.git` directory doesn't disappear
- **Force-push feature branches**: job-star owns the branch, so `--force` is safe
- **PR executor fails if tests don't pass**: no fake completion check-ins
- **PR executor fails if no file blocks**: text-only responses don't count as done
- **Auto-plan skips personal goals**: only coding/infra goals get auto-planned
- **`requested_by` routes notifications**: enables family multi-user support
- **Monitor threshold 20 steps/hour**: raised from 8 to avoid false positives

## Models That Work

- `glm-5-2` — works, follows file-block format (preamble text is stripped by parser)
- `ollama/*` — 502 "no provider owns model"
- `deepseek-v4`, `gemini-*` — return empty content (for discussion, `gemini-3-5-flash-minimal` works)

## Known Issues

1. **5 completion check-ins pending**: goals at 100% waiting for user review. Will auto-accept after 7 days if unanswered.
2. **3 personal goals at 0%**: human errands (tax, COFA renewal, colo termination) — will never auto-start.

## Resolved

- **Gatehouse-ai review goal completed**: All 24 steps done (0 failed). Go tests all pass (37 packages). The key fix: PR executor now detects review/analysis steps (title contains review/inspect/scan/verify/check/audit/analyze) and accepts a text review instead of requiring ## File: blocks.
- **Flaky test resolved**: `test_step_dag_parallel_steps_no_deps` now passes consistently.
- **Email notifications verified**: SMTP gateway at localhost:2525 accepts and relays to Gmail + Google Chat.
- **Goal->check-in linking**: /goals page shows a green 'Review ->' button on goals with pending check-ins.
- **Recent activity feed**: Dashboard shows last 6 completed steps with relative timestamps.

## Daily Workflow

```bash
python3 -m job_star              # dashboard — what needs attention
python3 -m job_star review       # guided check-in response walkthrough
python3 -m job_star commentary   # AI summary of what's happening
python3 -m job_star monitor --check  # system health
```

Or via web:
- `http://job-star.craigdfrench.com/add` — add a goal
- `http://job-star.craigdfrench.com/checkins` — review check-ins
- `http://job-star.craigdfrench.com/checkin/{id}` — respond to a specific check-in

## Next Steps (When Picking This Up)

1. **Respond to 5 completion check-ins** at `/checkins` (or let them auto-accept in 7 days)
2. **Fix gatehouse-ai test failures** in `costclass_test.go` (Go struct field changes)
3. **Clean up personal goals** — do them and mark complete, or abandon them
4. **Add a goals list web page** at `/goals` (dashboard is CLI-only right now)
5. **Fix flaky test** `test_step_dag_parallel_steps_no_deps`