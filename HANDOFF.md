# Job-Star Handoff Document

**Date:** July 13, 2026
**Target model:** GLM-5.2 (200k context)
**Latest commit:** `ef0c608` — Add system integrity monitor + worker heartbeat fix

---

## 1. What Is Job-Star?

Job-Star is a constrained, supervised, goal-oriented AI orchestration system. It accepts user input (text, screenshots, voice), triages it into goals, plans steps, executes them with background workers using free/cheap AI models, and reports progress via structured check-ins.

Core philosophy: **constrained, supervised, goal-oriented AI with situational awareness beats unconstrained, reactive AI.**

---

## 2. Current State (Live)

### 2.1 Dashboard snapshot

```
Job-Star
Monday, July 13 — 16:12

[!] 1 check-in(s) need your response:
    ✅ 9f13507d  Fix the cog-proxy /v4/ routing bug in gatehouse-ai

[~] Workers idle — no steps in progress

[=] 10 active goals, 29 pending steps
    Goals that need starting:
      • Review and commit gatehouse-ai routing/quota/catal
      • Monitor Home Assistant event bus for anomalies in
      • Fix machine name mapping for old Pi session index
    Run: job_star work <id>    (start a goal)

[✓] 320 steps today, $0 cost, 4 workers, gateway up
```

### 2.2 Database counts

| Table | Counts |
|-------|--------|
| goals | active: 10, completed: 39, abandoned: 19 |
| goal_steps | pending: 29, completed: 530, blocked: 1 |
| check_ins | sent: 1, actioned: 1, expired: 4 |

### 2.3 Services

All active:

- `job-star-api` (127.0.0.1:8003)
- `job-star-worker`
- `job-star-worker-gatehouse`
- `job-star-worker-research`
- `job-star-worker-jobstar`
- `job-star-monitor.timer` (runs every 5 min)

### 2.4 Tests

`34 passed` in `tests/test_integration.py`.

---

## 3. Architecture & Key Components

```
user input
    ↓
CLI / API / web / email
    ↓
Triage → Goal + Steps (Postgres)
    ↓
Orchestrator picks next step
    ↓
Router → Expert executor (coding / research / gatehouse-ai)
    ↓
AI model (free/cheap by default)
    ↓
Step completed → trigger check-in if needed
```

### 3.1 Key files

| File | Purpose |
|------|---------|
| `job_star/cli.py` | CLI commands, dashboard, review, monitor |
| `job_star/api/app.py` | FastAPI app, `/health` endpoint |
| `job_star/api/routes.py` | REST API: goals, check-ins, respond, discuss |
| `job_star/api/auth.py` | Auth: Tailscale CGNAT + localhost trusted |
| `job_star/orchestrator.py` | Picks next step, triggers check-ins |
| `job_star/worker_core.py` | Worker loop, heartbeat, drain, registry |
| `job_star/executors/research.py` | Research executor (fixed loop bug) |
| `job_star/checkin/engine.py` | Check-in generation and lifecycle |
| `job_star/notify.py` | SMTP notifications to local gateway |
| `job_star/monitor.py` | System integrity monitor (self-healing) |
| `job_star/upgrade.py` | Safe deploy with blue-green restart |
| `job_star/dashboard.py` | Dashboard render |
| `job_star/commentary.py` | AI-generated system summary |

### 3.2 Supervision layers

1. **Per-execution Supervisor** (`job_star/supervisor/core.py`) — checks budget, retries, file paths per step.
2. **System Integrity Monitor** (`job_star/monitor.py`) — runs every 5 min, detects and fixes runaway loops, orphaned steps, stale check-ins, budget burnout, worker/gateway health.
3. **Upgrade Tool** (`job_star/upgrade.py`) — safe deploys with blue-green restart and auto-rollback.

---

## 4. What Changed This Session

### 4.1 Simplified user experience

- `python3 -m job_star` now shows a dashboard.
- `python3 -m job_star review` guides through pending check-ins.
- `python3 -m job_star commentary` gives an AI summary.
- Help text grouped by usage frequency.

### 4.2 Fixed research executor infinite loop

- `job_star/executors/research.py` was creating "Monthly check-in" steps with no time throttle.
- One goal produced **126 steps**, another **120 steps**, burning through the 500K token budget.
- Fix: `_create_next_step` now only creates a new recurring step if **25+ days** have passed since the last completed check-in.
- Cleaned up the looping goals and stale check-ins.

### 4.3 System integrity monitor

New `job_star/monitor.py` + `job-star-monitor.timer` every 5 minutes.

Checks and auto-fixes:

| Check | Threshold | Auto-fix |
|-------|-----------|----------|
| Runaway loops | >60 steps total or >8 steps/hour | Pause goal + reap in_progress steps |
| Orphaned steps | in_progress >10 min | Reset to pending |
| Stale check-ins | pending >7 days | Expire |
| Check-in backlog | >5 pending | Flag |
| Budget | >80% warning, >100% critical | Flag |
| Worker health | no heartbeat 5 min | Flag |
| Gateway health | down | Flag |

CLI: `python3 -m job_star monitor` (fix) or `python3 -m job_star monitor --check` (report only).

### 4.4 Worker heartbeat fix

Workers only heartbeated when claiming/completing steps. Idle workers looked "dead" to the monitor. Now `worker_core.py` heartbeats on every loop iteration.

### 4.5 Check-in / notification system

- `job_star/checkin/engine.py` triggers progress/clarification/milestone/completion check-ins.
- Weekly cooldown on progress check-ins (168 hours).
- `job_star/notify.py` sends SMTP to `localhost:2525` → fan-out to Gmail + Google Chat.
- `job_star/api/checkin_page.html` is a self-contained web page for responding.
- `POST /api/v1/check-ins/{id}/discuss` uses `gemini-3-5-flash-minimal` to help the user think through the check-in.

### 4.6 Safe upgrade process

- `python3 -m job_star upgrade` — pre-flight, reap orphans, migrate, blue-green restart, verify.
- Automatic rollback to previous git commit if verification fails.
- Schema version tracking in `schema_migrations` table.

### 4.7 Auth

- `auth.py` trusts `100.64.0.0/10` (Tailscale CGNAT) and `127.0.0.0/8`.
- Caddy reverse proxy (`/etc/caddy/conf.d/job-star.caddy`) is network-boundary only — no `tailscale_auth` because the machine is tagged `tag:githubactionstarget`.
- Direct external access requires token.

### 4.8 User guide

New `USER_GUIDE.md` with mental model, daily workflow, and command reference.

---

## 5. Daily Commands

```bash
# See what's happening
python3 -m job_star

# Respond to check-ins
python3 -m job_star review
python3 -m job_star checkin pending

# Get AI summary
python3 -m job_star commentary

# Add / manage goals
python3 -m job_star add "title" --desc "..." --urgency soon
python3 -m job_star list
python3 -m job_star show <id>
python3 -m job_star work <id>

# System health
python3 -m job_star monitor
python3 -m job_star status

# Safe deploy
python3 -m job_star upgrade

# Tests
python3 -m pytest tests/test_integration.py -q
```

---

## 6. Critical Configuration

- Database: `postgresql://jobstar:jobstar@localhost:5432/job_star`
- API: `127.0.0.1:8003`
- Gateway: `http://gatehouse-ai.craigdfrench.com/v1`
- SMTP gateway: `localhost:2525`
- Secrets: `/etc/job-star-api-secrets.env`
- Caddy: `/etc/caddy/conf.d/job-star.caddy`
- Systemd: `/etc/systemd/system/job-star-*.service`, `job-star-monitor.timer`

---

## 7. What to Do Next

1. **Respond to the pending check-in:** `9f13507d` — completion for the cog-proxy /v4/ routing bug. Use `python3 -m job_star review` or the email/Chat link.
2. **Start the 3 goals with 0 steps** if they're still relevant, or abandon them.
3. **Monitor the monitor** — check `python3 -m job_star monitor` over the next day to ensure no new loops.
4. **Fix flaky test** `test_step_dag_parallel_steps_no_deps` if it resurfaces.

---

## 8. Key Design Decisions

1. **Tailscale auth via IP range**, not Caddy `tailscale_auth`, because the machine is tagged and has no user identity.
2. **Discussion endpoint `POST /discuss` is unauthenticated** because it is advisory only; the actual `respond` endpoint requires auth.
3. **Completion check-ins require user acceptance** — goals don't auto-complete.
4. **Progress check-ins have a 7-day cooldown** to prevent spam.
5. **Free/cheap models by default** — `gemini-3-5-flash-minimal` for discussion, `QUOTA_FREE` tier for executors.
6. **Additive migrations only** — enables blue-green rolling restarts.
7. **System integrity monitor is the self-healing layer** — catches aggregate patterns the per-execution supervisor cannot.

---

## 9. Documentation Index

| File | Purpose |
|------|---------|
| `USER_GUIDE.md` | User-facing mental model + workflow |
| `UPGRADE.md` | Safe upgrade process |
| `docs/check-ins.md` | Check-in system details |
| `docs/feynman-thinking-plan.md` | 70-day learning plan |
| `CODE_REVIEW.md` | Prior code review notes |
| `job-star-design.md` | Architecture design doc |
| `job-star-bootstrap.md` | Self-hosting bootstrap plan |

---

*Handoff generated July 13, 2026. System is stable and self-healing.*
