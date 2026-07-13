# Job-Star Handoff Document

## Date: July 11, 2026
## Session: Bootstrap + Integration + Gateway-Aware Routing + Cost-Tier Protection

---

## 1. What Is Job-Star?

Job-Star is a **constrained, supervised, goal-oriented AI orchestration system** that manages both coding and personal goals over extended timeframes. It accepts raw input (text, screenshots, voice) through zero-friction intake surfaces, triages it with AI, registers it in a shared goal registry, and routes execution to the appropriate AI model based on urgency, cost, and availability.

The core philosophy: **constrained, supervised, goal-oriented AI with situational awareness beats unconstrained, reactive, general-purpose AI every time.**

---

## 2. Current State Summary

### 2.1 Bootstrap Components (Complete)

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

### 2.2 Integration (Complete)

Unified Python orchestration package (`job_star/`) wires the core loop:

```
Intake → Triage → Conflict Check → Goal Registry (Postgres)
→ Router → Supervisor → Gatehouse AI → Result → Follow-up
```

### 2.3 Gateway-Aware Routing + Cost-Tier Protection + x_gatehouse (Added This Session)

Added cost-tier protection so job-star never silently falls back to expensive models, plus **real-time pricing/quota parsing from the dev server's `x_gatehouse` response metadata**.

**Key files:**

| File | Purpose |
|------|---------|
| `job_star/gatehouse/monitor.py` | Model availability, quota hold, circuit breaker, cost tier mapping, `x_gatehouse` parsing |
| `job_star/gatehouse/client.py` | Extracts `usage.x_gatehouse` from responses into `ExecutionResult.x_gatehouse` |
| `job_star/scheduler.py` | Deferred jobs, retry after quota hold, fallback resolution |
| `job_star/router/engine.py` | Live model routing with cost-tier protection |
| `job_star/orchestrator.py` | Multi-attempt execution with fallback; feeds `x_gatehouse` back to monitor |
| `job_star/idle/loop.py` | Uses gateway monitor for idle work; feeds `x_gatehouse` back |
| `job_star/models.py` | `ExecutionResult.x_gatehouse` field added |

**x_gatehouse parsing (the real pricing source):**
The dev server now includes `usage.x_gatehouse` in every chat completion response. `GatewayMonitor.record_success()` parses it and updates model state with:
- `cost_class` (e.g. `included_quota`, `retail`) — authoritative tier source
- `routing_advice` (e.g. `harvest`, `switch`) — gatehouse's recommendation
- `quota_windows[]` — per-pool `remaining_pct`, `resets_at`, `hours_until_reset`
- `retail_value_this_request` — market value of the request
- `reason` — human-readable explanation

`tier_for(model_id)` prefers observed `cost_class` over the static config/heuristics. `_cost_class_to_tier()` maps `included_quota`/`promotional_free`/`zero_rated` → `FREE`, `retail`/`paid` → `PREMIUM`.

If any `quota_window.remaining_pct <= 0`, the model is marked unavailable and enters a quota hold until the soonest `resets_at` (instead of a blind 3-hour wait).

`pick_fallback()` now boosts models with `routing_advice == "harvest"` and penalizes `"switch"`.

**Verified with `kimi-k2-7`:** `cost_class=included_quota`, `routing_advice=harvest`, `reason="$0-rated - doesn't consume dollar quota, harvest free retail value"`, `tier_for=FREE`, `is_expensive=False`. Quota windows: `windsurf_daily=94%`, `windsurf_weekly=8%` (resets 2026-07-12T08:00:00Z).

**Cost tiers (from x_gatehouse cost_class, then gatehouse config, then heuristics):**
- `FREE` — `included_quota`, `promotional_free`, `zero_rated`; `ollama/*`, `glm-5-2*`, `deepseek-ai/deepseek-v4-*`, `kimi-k2-7` (zero-rated)
- `CHEAP` — `gemini-3-5-flash-*`, `deepseek-v4` (non-nvidia)
- `STANDARD` — `claude-sonnet-5*` (reasoning variants), `claude-sonnet-4*` (only with explicit `allow_expensive=True`)
- `PREMIUM` — `claude-opus-*`, `claude-5-fable-*` (only with explicit `allow_expensive=True`)

**Routing rules:**
- By default, only `FREE` and `CHEAP` models are eligible for routing and fallback.
- A model override only works if `allow_expensive=True` OR the model is free/cheap.
- The idle loop never uses expensive models.
- `GatewayMonitor` tracks consecutive failures and puts a model in quota hold after quota/availability errors.
- Quota hold duration is now driven by `resets_at` from `x_gatehouse` when available.

### 2.4 Real Work (Ongoing)

1. **Google Takeout verification** — ✅
2. **YouTube watch history analysis** — ✅
3. **Image extraction** — ✅
4. **AI vision tagging** — **running**, tagger now has 3-hour quota holds
5. **Home Assistant monitoring** — blocked on token auth
6. **Wire job-star components together** — active, 30% complete, now using `deepseek-v4-flash` for routing

---

## 3. File Locations

```
/home/craig/job-star/job-star/
├── package.json
├── tsconfig.json
├── pyproject.toml
├── sql/
│   └── schema.sql
├── src/                      # TypeScript CLI
├── job_star/                 # Unified Python orchestration package
│   ├── __init__.py
│   ├── __main__.py
│   ├── models.py
│   ├── db.py
│   ├── orchestrator.py
│   ├── cli.py
│   ├── scheduler.py
│   ├── triage/
│   ├── router/
│   ├── gatehouse/
│   │   ├── __init__.py
│   │   ├── client.py
│   │   └── monitor.py
│   ├── supervisor/
│   ├── conflict/
│   ├── intake/
│   ├── followup/
│   └── idle/
├── tests/
│   └── test_integration.py
├── generated/                 # AI-generated component code
├── job-star-design.md
└── job-star-bootstrap.md
```

### 3.2 Database

- **Container:** `job-star-db` (running)
- **Connection:** `postgresql://jobstar:jobstar@localhost:5432/job_star`
- **Goals:** 20 (3 active, 17 completed)

### 3.3 AI Gateway

- **Dev (now the default for both pi and job-star):** `http://100.64.158.87:8090/v1` — the local gatehouse build at `/home/craig/gatehouse-ai/`, PID on port 8090. Model IDs are **unprefixed** (`glm-5-2`, `kimi-k2-7`, `deepseek-v4-flash`).
- **Production:** `http://gatehouse-ai.craigdfrench.com/v1` → Caddy → `localhost:18080` → systemd gatehouse (`/etc/gatehouse/config.json`). Model IDs are **`ollama/`-prefixed** (`ollama/glm-5.2`).
- **`ai.craigdfrench.com`** is a *different* gateway entirely (689 models, `cognition/`/`nvidia/`/`together/`/`openrouter/` prefixed IDs) — not used by job-star.

**Why dev:** the user is actively working on gatehouse enhancements (pricing/cost-class fields) on the dev build. Pointing job-star and pi at dev lets us test the new fields as they land.

**Caveat:** the dev instance has inconsistent routing — some models in `/v1/models` return `model_not_found` when called (e.g. `gemini-3-flash-preview`, `deepseek-v4-flash`, `kimi-k2.7-code`). Working models on dev: `glm-5-2`, `glm-5.2`, `glm-5-2-none`, `kimi-k2-7`, `kimi-k2.6`, `kimi-k2-6`.

### 3.4 Current Live Model Situation

- `ollama/*` models (including `ollama/glm-5.2` and `ollama/gemini-3-flash-preview`) are listed but return 404/401 when called.
- `claude-5-fable-*` is `quota_bearing` in gatehouse config and is **treated as PREMIUM, not used by default**.
- `claude-sonnet-5*` (high/low/medium/max/xhigh) are **treated as STANDARD, not used by default**.
- `deepseek-ai/deepseek-v4-flash` is `included_unlimited` from nvidia and is **treated as FREE**.
- `glm-5-2` is `promotional_free` and is **treated as FREE**.

---

## 4. How to Run

### Python CLI

```bash
export GATEHOUSE_API_URL="http://gatehouse-ai.craigdfrench.com/v1"

cd /home/craig/job-star/job-star

python3 -m job_star add "title" --desc "description" --urgency soon
python3 -m job_star list
python3 -m job_star show <id>
python3 -m job_star work <id>
python3 -m job_star work <id> --model glm-5-2  # override model if allowed
python3 -m job_star complete <id>
python3 -m job_star digest 50
python3 -m job_star conflicts
python3 -m job_star status
python3 -m job_star idle --cycles 1
```

### Tests

```bash
python3 -m pytest tests/test_integration.py -v
# 22 tests, all passing
```

### Image Tagger

```bash
# Check status
ps aux | grep image_tagger
tail -5 /tmp/image_tagger_prod.log

# Tagger pauses 3 hours on quota/availability errors
# Restart from checkpoint if needed
python3 /tmp/image_tagger_prod.py
```

---

## 5. What Works End-to-End

1. **Intake** — triage + duplicate detection + goal creation
2. **Live routing** — fetches available models from gatehouse, falls back to free/cheap
3. **Cost protection** — expensive models never used as silent fallback
4. **Execution** — tries up to 3 allowed models on failure
5. **Quota hold tracking** — 3-hour hold for unavailable models
6. **Supervision** — budget, retry, path consistency checks
7. **Conflict detection** — duplicates, contradictions, resource, tension
8. **Follow-up** — interrupt/batch/silent classification
9. **Idle loop** — opportunistic execution with gateway awareness
10. **Audit trail** — all actions logged in Postgres

---

## 6. Immediate Next Steps

### 6.1 Investigate Ollama Backend (Priority 1)

`ollama/*` models are listed but return 404/401. This is a gateway/ollama backend routing issue. The non-ollama `deepseek-v4-flash` is working as a fallback.

### 6.2 Investigate `deepseek-v4-flash` Quota (Priority 2)

`deepseek-v4-flash` is now working and cheap. Monitor it with the `GatewayMonitor` and `status` command. If it hits quota, the system will fall back to other free/cheap models.

### 6.3 Add `--allow-expensive` Flag to CLI (Priority 3)

The `work` command should accept `--allow-expensive` to let users explicitly request premium models like `claude-5-fable-*`.

### 6.4 Wire Rust Supervisor (Priority 4)

Build the generated Rust supervisor as a service and connect it.

### 6.3 Wire Web Intake & Telegram (Priority 5)

Connect the React app and Telegram bot to the Python orchestrator.

### 2.4 Expert Routing + Distributed Workers (Added This Session)

Added **topic ownership** so a custom expert agent owns goals for a specific codebase, and other workers can't touch them.

**Key files:**

| File | Purpose |
|------|---------|
| `job_star/executors/__init__.py` | Executor registry: maps expert names to specialized execution backends |
| `job_star/executors/default.py` | Default executor (generic AI model via gatehouse) |
| `job_star/executors/gatehouse_ai.py` | **Gatehouse-AI expert** — curated context from README/DESIGN/HANDOFF/docs + codebase structure |
| `job_star/db.py` | `claim_next_step_any_goal(expert=...)` with worker affinity |
| `job_star/triage/engine.py` | `EXPERT_KEYWORDS` + `_detect_expert()` — auto-assigns expert at triage |
| `job_star/models.py` | `Goal.expert` field + `TriageResult.expert` |
| `sql/schema.sql` | `goals.expert` column + index |

**How it works:**
1. **Triage detects the expert.** Keywords like "gatehouse", "cog-proxy", "model_costs", "x_gatehouse", "routing_advice" trigger `expert=gatehouse-ai`.
2. **Goal is owned.** `goals.expert = 'gatehouse-ai'` — only workers with matching affinity can claim its steps.
3. **Worker affinity.** `JOB_STAR_EXPERT=gatehouse-ai python3 -m job_star worker` only claims goals with that expert. Generic workers (no `JOB_STAR_EXPERT`) only claim `expert IS NULL` goals. `--expert any` claims any.
4. **Executor dispatch.** The orchestrator's `work_on_goal` looks up the executor for `goal.expert` and dispatches to it. The `GatehouseAIExecutor` injects curated context (README, DESIGN, HANDOFF, codebase structure, key concepts) into the system prompt.
5. **Delegation.** An expert can create child goals with `expert=NULL` to send sub-tasks to the generic pool.

**Verified:** Added "Fix the cog-proxy /v4/ routing bug in gatehouse-ai" → triage tagged `expert=gatehouse-ai` → generic worker skipped it → expert worker claimed it → `GatehouseAIExecutor` executed steps with curated context using `kimi-k2-7` (free/zero-rated).

**Distributed claiming (done earlier):** `claim_next_step` and `claim_next_step_any_goal` use `FOR UPDATE SKIP LOCKED` so multiple machines pull from the shared Postgres queue without colliding. `JOB_STAR_WORKER=<name>` identifies each worker in audit logs.

**First expert: gatehouse-ai developer.** Curated from local docs (`/home/craig/gatehouse-ai/README.md`, `DESIGN.md`, `HANDOFF.md`, `docs/`, `config.sample.json`) + codebase structure (`internal/` packages) + key concepts (providers, model_costs, x_gatehouse, endpoints, two instances). Devin wiki URL (`https://app.devin.ai/org/craigdfrench/wiki/craigdfrench/gatehouse-ai?branch=main`) is referenced for future integration once API access is available.

**PR-based execution with test feedback (PRExecutor):** The gatehouse-ai expert (and any expert with a `repo_path` + `test_command`) uses the `PRExecutor`, which closes the loop between code generation and verification:
1. AI generates code → parsed into file blocks (`## File: path` + fenced code)
2. Files written to the repo working tree (supervisor checks paths within repo)
3. Test command run (`go test ./...` for gatehouse-ai)
4. If tests fail → failure output fed back to the AI → retry (up to `max_test_retries`)
5. When tests pass → commit, push branch, create PR via `gh` CLI
6. If budget exhausted → commit + create PR with "tests-failing" label for human review
7. Step result stores `{pr_url, branch, files, test_output}` — the DB tracks linkage, not code

The `experts` table has `repo_path`, `test_command`, `base_branch` columns. gatehouse-ai: `/home/craig/gatehouse-ai`, `go test ./...`, `main`.

**Verified:** PRExecutor test on a temp repo — AI generated Go code, files written to working tree, test command run, failure captured and fed back, retried, committed with "tests failing" message. The loop works end-to-end (only blocked by `go` not being installed in the test env).

**Machine pinning (Option A):** The `experts` table has a `required_machine` column. `gatehouse-ai` is pinned to `DESKTOP-RNK6J72` (the machine with the codebase). The `claim_next_step_any_goal` query enforces this at the SQL level: a worker on the wrong machine cannot claim a machine-pinned expert's goals, even if it sets `JOB_STAR_EXPERT=gatehouse-ai`. The worker reports its machine via `JOB_STAR_MACHINE` or `HOSTNAME`.

**Verified:** A worker on `mac` with `JOB_STAR_EXPERT=gatehouse-ai` got "no work available" (machine mismatch). A worker on `DESKTOP-RNK6J72` claimed the gatehouse-ai goal successfully.

**CLI:** `python3 -m job_star experts` lists registered experts and their machine pinning.

### 6.6 Fix HA Token (Priority 6)

Home Assistant monitoring goal is blocked on token auth.

### 6.7 Continue Image Tagging (Background)

Image tagger is running with 3-hour quota holds.

### 6.8 Integrate Context Gatherer (Priority 7)

Collect files/git/logs before triage and work execution.

---

## 7. Key Design Decisions

1. **Live gateway model list is authoritative.** Static `MODEL_REGISTRY` is a fallback.
2. **Cost-tier protection is mandatory.** Only `FREE` and `CHEAP` models route by default.
3. **Failures are model-specific.** One model failing doesn't block the goal; fallback is automatic.
4. **Quota hold is time-based.** 3-hour default, configurable per `GatewayMonitor`.
5. **Circuit breaker prevents hammering.** 3 failures → model marked unavailable.
6. **Python orchestrates, Rust supervises.** Python handles dynamic routing and scheduling; Rust provides hardened constraint enforcement.
7. **One database, many clients.** Both TypeScript and Python CLI share Postgres.

---

## 8. What to Tell the Next Session

**Cost-tier protection is now in place.** Job-star will never silently fall back to `claude-5-fable-*` or other premium models. It only uses `FREE`/`CHEAP` models unless the user explicitly requests an expensive model with `allow_expensive=True`.

**The `work` command completed a step using `deepseek-v4-flash`** after the Ollama models failed. This confirms the fallback routing works.

**Tests pass:** 22/22 integration tests.

**Current active goal:** `bd6d62f2` "Wire job-star components together into a running system" is at 30%.

**Next priorities:**
1. Add `--allow-expensive` CLI flag to `work` command
2. Investigate Ollama backend 404/401
3. Investigate `deepseek-v4-flash` quota limits
4. Wire Rust supervisor
5. Wire web intake and Telegram
6. Fix HA token
7. Continue image tagging
8. Integrate context gatherer

---

*Generated by job-star session, July 11 2026. The system now protects against expensive silent fallbacks and routes through live gateway model availability.*

---

## 9. Code Review Fixes (Added This Session)

A structured code review was performed using a 3-model debate format (prosecutor → defender → arbitrator) via glm-5-2, then re-run with claude-opus-4-8-max as arbitrator. 24 findings debated, 8 upheld, 15 partially upheld, 1 dismissed. Full report: `CODE_REVIEW.md`.

### Fixes Applied

1. **PRExecutor uses git worktree instead of checkout (critical)** — `_ensure_branch` now creates an isolated `git worktree add` in `/tmp/job-star-worktrees/` instead of `git checkout` on the user's primary working tree. This prevents clobbering uncommitted changes. Worktree is cleaned up in `try/finally`.

2. **Router fails fast when gateway is down (critical)** — When `gateway_monitor` is provided and returns no candidates (gateway unreachable), the router returns `RoutingDecision(model="", reason="gateway unreachable")` instead of falling back to static `MODEL_REGISTRY`. The static models are also served through the gateway, so the fallback was unexecutable.

3. **Supervisor budget is DB-backed (major)** — `BudgetTracker.check_budget_db()` queries `SUM(input_tokens + output_tokens)` from `goal_steps` so budget persists across process restarts. `check_before_execute` is now async and uses the DB-backed check. In-memory dict is a cache only.

4. **Conflict detection is incremental + dedup (major)** — `detect_conflicts(incremental_goal_id=...)` checks only the new goal vs existing (O(n) not O(n²)). Intake uses this. Duplicate conflict rows prevented by checking existing rows before insert + a unique index `idx_conflicts_unique` on `(LEAST(a,b), GREATEST(a,b), conflict_type)`.

5. **work_on_goal retry loop fixed (minor but real bug)** — The ternary `model_override if attempts == 0 else None` meant the fallback model was never actually passed to `executor.execute()`. Fixed: `model_override` is now passed on every attempt, and is set to the fallback model after a failure.

6. **FollowUpEngine.batch has max size + auto-flush (major)** — `max_batch_size=100` (default). When exceeded, `_flush()` is called, invoking an optional `_on_flush` callback. Prevents unbounded memory growth in long-running workers.

7. **plan_goal __import__ fixed (minor)** — Replaced `__import__("job_star.router", ...)` with a top-level `from .router import route, MODEL_REGISTRY`.

8. **Idle loop redundant update_step_status removed (trivial)** — `claim_next_step_any_goal` already sets in_progress; the second call was a wasted DB roundtrip.

9. **get_pool() handles closed pool (minor)** — Checks `getattr(_pool, '_closed', False)` and recreates the pool if it was closed (fixes test isolation issues).

### Step DAG (depends_on) — Added This Session

- `goal_steps.depends_on UUID[]` column added (migration + schema.sql)
- `claim_next_step` and `claim_next_step_any_goal` only claim steps whose `depends_on` are all completed
- `create_step` accepts `depends_on` parameter
- Tests: `test_step_dag_blocks_unmet_dependency`, `test_step_dag_parallel_steps_no_deps`

### Test Cleanup Fixed (Vikunja #696)

- `clean_db` fixture deletes test goals before AND after each DB-touching test
- Tests now pass on rerun without manual DB cleanup

### Code Review Skill

The debate format is codified as a skill: `~/.agents/skills/code-review-debate/`
- `review.py` — configurable 3-model debate harness
- `SKILL.md` — documentation
- Arbitrator defaults to `claude-opus-4-8-max` (strongest available)
- `--infra-context` flag injects deployment facts to prevent false infrastructure claims
- `format_report.py` — converts JSON results to markdown

**Tests:** 27/27 passing.