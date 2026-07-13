# Job-Star Code Review — GLM-5.2 Debate Format

**Reviewer:** glm-5.2 (via ollama)
**Date:** 2026-07-11
**Method:** Codebase broken into 5 logical parts. 24 findings identified. Each finding debated by 3 independent LLM calls (prosecutor → defender → arbitrator). Each role got fresh context.

## Summary

| Verdict | Count |
|---------|-------|
| UPHELD | 8 |
| PARTIALLY_UPHELD | 15 |
| OVERRULED | 0 |
| DISMISSED | 1 |

**Severity changes after debate:** 5 findings were downgraded (prosecutor overstated), 1 was upgraded (FollowUpEngine.batch: minor → major due to unbounded memory growth). 1 critical was upheld (PRExecutor git checkout clobbers uncommitted work). 1 critical was corrected post-debate (router fallback — the defender's "local Ollama" claim was factually wrong; no local Ollama exists, so the fallback IS unexecutable).

---

## Part 1: Data Layer (db.py, models.py, schema.sql)

### [PARTIALLY_UPHELD, major→minor] Connection pool global singleton
**Summary:** asyncpg's Pool self-heals (discards dead connections, creates fresh ones), so the "zombie worker" claim is overstated. But there's no SIGTERM/SIGINT handler calling `close_pool()` for graceful shutdown.
**Action:** Add a signal handler in the worker that calls `await close_pool()`. No pool recreation or health-check polling needed.

### [PARTIALLY_UPHELD, minor] claim_next_step_any_goal locking gap
**Summary:** The race is real — locking only `s` leaves `g.status` unprotected. But `FOR UPDATE OF s, g` doesn't actually prevent the bad outcome (claiming a step from a goal that's about to be completed). The true fix is a runtime re-check.
**Action:** Add a goal-status re-verification at step execution time. Optionally add `g` to `FOR UPDATE` as defense-in-depth.

### [DISMISSED, minor→trivial] depends_on correlated subquery
**Summary:** PK index exists, NOT EXISTS short-circuits, no measurable impact at current scale. Speculative future concern.
**Action:** No action. Add a performance test if scale becomes a concern.

### [PARTIALLY_UPHELD, major→minor] No migration system
**Summary:** Factually valid — no migration tool, schema.sql lacks idempotency. But schema.sql was updated with all columns, so drift is hypothetical. For a pre-production project, a full migration framework is reasonable but not urgent.
**Action:** Add `IF NOT EXISTS` to CREATE TABLE statements immediately. Adopt a migration tool (golang-migrate) when the project reaches production/multiple developers.

---

## Part 2: Gateway & Routing (gatehouse/, router/)

### [PARTIALLY_UPHELD, major→minor] TIER_OVERRIDES 70+ hardcoded entries
**Summary:** Config is first priority, so "silent reversion" is impossible — TIER_OVERRIDES only applies when config has no entry. But the maintenance concern is legitimate (dead entries, code changes for updates).
**Action:** Migrate TIER_OVERRIDES into a `model_tiers` section in config.json. Cleanup task, not urgent.

### [PARTIALLY_UPHELD, major→minor] GatewayMonitor not thread-safe
**Summary:** In single-threaded asyncio, synchronous dict mutations are atomic — no interleaving risk demonstrated. But no documented contract preventing future await points.
**Action:** Add a docstring stating record_success/record_failure/_apply_x_gatehouse must not contain await points during mutation. Add a lock only if they do.

### [UPHELD, critical] Router falls back to static MODEL_REGISTRY when gateway is unreachable
**Summary:** When the gateway is down and all live models are unavailable, the router falls back to the static `MODEL_REGISTRY`, which contains `ollama/glm-5.2`. This model is served **through the gatehouse gateway** (provider config: `base_url: https://ollama.com`), not by a local Ollama daemon — there is no local Ollama running. So the fallback returns a model that cannot be reached when the gateway is down. The router returns a "decision" that fails at execution time instead of failing fast. The cost-tier constraint bypass is also real — the static fallback doesn't re-apply the caller's constraints.

> **Note on the debate:** The defender incorrectly claimed `ollama/glm-5.2` is "a local model served by a separate Ollama daemon," which misled the arbitrator into downgrading this from critical to major. Verified: no local Ollama exists (port 11434 is closed); `ollama/*` models are routed through the gateway to `https://ollama.com`. The original critical severity is correct.

**Action:** When the gateway is unreachable and no live candidates are available, return `RoutingDecision(model="", reason="gateway unreachable")` — fail fast. Do NOT fall back to static MODEL_REGISTRY, since those models also require the gateway. Re-apply cost-tier/capability constraints to any fallback that IS reachable. Optionally validate reachability before returning a decision.

### [PARTIALLY_UPHELD, minor] is_expensive() / tier_for() misleading comment
**Summary:** Factually correct — comment is misleading, unknown models default to PREMIUM (safe but undocumented). Tri-state refactor is disproportionate.
**Action:** Update the comment. Optionally add a debug log when classification falls through to the unknown-default path.

### [UPHELD, minor] execute() no retry on transient failure
**Summary:** Both sides agree — 503s and connection errors aren't retried, and the orchestrator's model-fallback can't fix gateway-wide issues. Raw httpx exceptions propagate unhandled.
**Action:** Add bounded retry with exponential backoff for 502/503/504 and connection-level exceptions. Convert raw httpx exceptions to `ExecutionResult(success=False)`.

---

## Part 3: Pipeline (intake, triage, conflict, orchestrator)

### [PARTIALLY_UPHELD, major→minor] Triage is pure keyword matching
**Summary:** Real weaknesses (Jaccard normalization bias, no confidence threshold). But most triage inputs are unambiguous, and embeddings/LLMs relocate ambiguity into an opaque space. The incremental fix is better.
**Action:** Implement weighted keyword scoring, add a confidence threshold for manual review. Document the cross-domain limitation. Do NOT introduce embedding/LLM dependencies.

### [UPHELD, major] Conflict detection O(n²) on every intake
**Summary:** Both sides agree — full re-scan on every intake is architecturally unsound, and `save=True` without dedup creates unbounded duplicate rows. Data integrity bug regardless of scale.
**Action:** Implement incremental detection (new goal vs existing only). Add unique constraint on (goal_a_id, goal_b_id, conflict_type). Trigger re-scan on goal mutations.

### [UPHELD, major] Duplicate conflict rows on every intake
**Summary:** Both sides agree — `create_conflict()` called unconditionally with no existence check or resolution-status awareness. Unbounded table growth, silent resurrection of resolved conflicts.
**Action:** Query for existing row before insert. If resolved row exists, respect the resolution and skip. Add unique constraint as backstop.

### [UPHELD, minor] work_on_goal retry loop — fallback is dead code
**Summary:** Both sides agree this is a real logic bug — `model_override if attempts == 0 else None` means the fallback assignment is dead code. The fallback model is never actually passed to `executor.execute()`. Minor severity (confined to retry behavior, no data corruption).
**Action:** Fix the ternary so the fallback model reaches `executor.execute()` on retry. Remove the misleading comment.

### [UPHELD, minor] plan_goal uses __import__
**Summary:** Both sides agree — fragile, unpythonic, unnecessary given `from .router import route` already works.
**Action:** Replace with a top-level import of `MODEL_REGISTRY` (or `from . import router` if circular import is a concern).

---

## Part 4: Execution (executors/, supervisor/)

### [PARTIALLY_UPHELD, major→minor] PRExecutor._write_files silently skips paths
**Summary:** Technical defects conceded — `startswith` is a broken path traversal guard, silent skipping is poor practice. But blast radius is limited (PR review gates the output, threat model is AI hallucination not adversarial input).
**Action:** Fix `startswith` to use `os.path.commonpath`. Add a warning log when files are skipped. Remove the misleading supervisor comment. Propagate skipped file info to the PR description.

### [PARTIALLY_UPHELD, critical→minor] PRExecutor shell=True for tests
**Summary:** Valid — `shell=True` is an unnecessary injection surface. But the executor already runs untrusted PR code (test files can execute arbitrary commands), so `shell=True` adds only marginal risk. `shlex.split` would break legitimate shell features.
**Action:** Remove `shell=True` for simple commands. Don't blindly use `shlex.split` — constrain `test_command` to a safe subset or sandbox the executor (container/nsjail). Document that `test_command` is trusted admin input.

### [UPHELD, critical] PRExecutor clobbers uncommitted git work
**Summary:** Both parties agree — bare `git checkout` with no safeguards can silently carry uncommitted changes to a job-star branch, commit them, and push to remote. Half-written edits, debug code, or secrets could be exfiltrated. Genuine data integrity + security issue.
**Action:** Refactor PRExecutor to use `git worktree add` (or a separate clone) instead of the user's working tree. Handle cleanup in `try/finally`. Never `git checkout` against the user's checkout.

### [PARTIALLY_UPHELD, minor] PRExecutor branch-per-goal, commit-per-step
**Summary:** Real UX wart — PR body goes stale across steps. But git history preserves the audit trail; the PR body is convenience metadata, not source of truth.
**Action:** File a follow-up to update the PR body after each step commit (`gh pr edit --body`).

### [UPHELD, major] Supervisor budget tracker in-memory, resets on restart
**Summary:** Both parties agree — in-memory dicts with no DB hydration means restarts reset budget to zero. The goal_steps table already has the data; the supervisor never reads it.
**Action:** On each budget check, query `SELECT COALESCE(SUM(input_tokens + output_tokens), 0) FROM goal_steps WHERE goal_id = ?`. Use in-memory dict as cache only, DB as source of truth.

### [PARTIALLY_UPHELD, minor] Supervisor path consistency too coarse
**Summary:** False positives on `tests/`, `docs/`, `scripts/` are real and should be allowlisted. But the first-segment comparison is a reasonable coarse heuristic — deeper comparison would introduce new false positives.
**Action:** Add an allowlist for common auxiliary directories. Do NOT change the core comparison to deeper path segments.

---

## Part 5: Feedback & Scheduling (followup/, idle/, scheduler.py)

### [PARTIALLY_UPHELD, minor→major] FollowUpEngine.batch in-memory, never flushed
**Summary:** Both sides agree — in-memory batch with no consumer, no max size, no flush trigger grows unboundedly in long-running workers (OOM risk). Audit trail mitigates data-loss. Upgraded to major due to unbounded memory growth.
**Action:** Add a max batch size guard that forces a flush when exceeded. Wire `get_batch()` into a periodic callback. Document that the batch is not durable (audit trail is recovery source).

### [PARTIALLY_UPHELD, major→minor] Scheduler is dead code
**Summary:** Correctly identified as dead code that duplicates `goal_steps`. But "data loss on restart" is hypothetical — if nothing calls `schedule()`, no jobs are lost. Actual harm is developer confusion.
**Action:** Remove the `Scheduler` class entirely, or wire it into the orchestrator backed by Postgres `FOR UPDATE SKIP LOCKED`. Don't leave it as an unused in-memory shadow.

### [UPHELD, trivial] Idle loop redundant update_step_status(IN_PROGRESS)
**Summary:** Both sides agree — `claim_next_step_any_goal` already sets in_progress. The second call is a wasted DB roundtrip.
**Action:** Remove the redundant `update_step_status(step.id, StepStatus.IN_PROGRESS)` call.

### [PARTIALLY_UPHELD, major→minor] Idle loop no persistent global budget
**Summary:** Core observation valid — no aggregate budget across cycles. But per-goal caps bound individual blast radius, caller controls max_cycles. Persistent budget store is deployment-layer complexity inappropriate for a library default.
**Action:** Add an optional in-memory cumulative cost/token counter with configurable `max_total_cost`/`max_total_tokens` (default None). Stop gracefully when exceeded. Leave persistent storage to the deployment layer.

---

## Priority Action List

### Fix now (critical/major, upheld)
1. **PRExecutor git checkout clobbers uncommitted work** — use `git worktree add` instead (critical, upheld)
2. **Router falls back to static MODEL_REGISTRY when gateway down** — fail fast, don't return an unexecutable model (critical, upheld — corrected post-debate)
3. **Supervisor budget tracker resets on restart** — query DB for cumulative spend (major, upheld)
4. **Conflict detection O(n²) + duplicate rows** — incremental detection + unique constraint (major, upheld ×2)
5. **work_on_goal retry loop dead code** — fix the ternary so fallback is actually used (minor but real bug, upheld)
6. **FollowUpEngine.batch unbounded growth** — add max size + periodic flush (major, upgraded)

### Fix soon (minor, upheld/partial)
7. **execute() no retry on transient failure** — add bounded retry for 503s (minor, upheld)
8. **plan_goal __import__** — replace with top-level import (minor, upheld)
9. **Idle loop redundant update_step_status** — remove the redundant call (trivial, upheld)
10. **schema.sql idempotency** — add IF NOT EXISTS (minor, partial)

### Clean up (minor, partial)
11. **TIER_OVERRIDES** — migrate to config (minor, partial)
12. **PRExecutor _write_files** — fix path check, add logging (minor, partial)
13. **PRExecutor shell=True** — remove for simple commands, document trust assumption (minor, partial)
14. **Supervisor path consistency** — add allowlist for tests/docs/scripts (minor, partial)
15. **GatewayMonitor thread-safety** — add docstring contract (minor, partial)
16. **Connection pool** — add SIGTERM handler (minor, partial)
17. **claim_next_step locking** — add runtime goal-status re-check (minor, partial)
18. **Triage keyword matching** — weighted scoring + confidence threshold (minor, partial)
19. **Idle loop global budget** — optional cumulative counter (minor, partial)
20. **Scheduler dead code** — remove or wire it in (minor, partial)

### No action (dismissed)
21. **depends_on correlated subquery** — no measurable impact (dismissed)
