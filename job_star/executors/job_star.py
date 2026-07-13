"""Job-Star expert executor.

A specialized agent that handles goals related to job-star's OWN codebase.
It has curated context from the project docs (UPGRADE.md, HANDOFF.md,
design doc, check-ins docs, CODE_REVIEW.md) and enforces the rules:
additive migrations, blue-green upgrade, tests before PR, etc.

Goals tagged with expert='job-star' are routed to this executor and can
only be claimed by workers with matching affinity (JOB_STAR_EXPERT=job-star).
Machine-pinned to DESKTOP-RNK6J72 (where the codebase lives).

Safety model (from job-star-bootstrap.md §5):
  - Works in a git worktree (isolated copy, not the live system)
  - Tests must pass before PR creation
  - Self-referential changes go through a review gate (human approves the PR)
  - The upgrade tool deploys changes, not the executor directly
"""

from __future__ import annotations

import os
from pathlib import Path

from ..models import ExecutionResult, Goal, Step
from .pr_executor import PRExecutor
from ..gatehouse import GatewayMonitor


JOB_STAR_PATH = "/home/craig/job-star/job-star"


def _load_doc(path: Path, max_chars: int = 6000) -> str:
    """Load a doc file, truncated to max_chars."""
    try:
        if path.exists() and path.is_file():
            text = path.read_text(errors="replace")
            if len(text) > max_chars:
                return text[:max_chars] + "\n...[truncated]\n"
            return text
    except Exception:
        pass
    return ""


def _codebase_overview() -> str:
    """Build a structural overview of the job-star codebase."""
    base = Path(JOB_STAR_PATH)
    lines = ["\n## Codebase structure"]

    # Top-level files
    for e in sorted(base.iterdir()):
        if e.name.startswith(".git") or e.name in (".venv", "node_modules", "__pycache__", ".pytest_cache"):
            continue
        if e.is_dir():
            lines.append(f"  {e.name}/")
        else:
            lines.append(f"  {e.name}")

    # job_star/ modules
    pkg = base / "job_star"
    if pkg.exists():
        lines.append("\n## job_star/ package modules:")
        for e in sorted(pkg.iterdir()):
            if e.name.startswith("__pycache__") or e.name.startswith("."):
                continue
            if e.is_dir():
                sub = [f"    {s.name}" for s in sorted(e.iterdir())
                       if not s.name.startswith("__pycache__") and not s.name.startswith(".")]
                lines.append(f"  {e.name}/")
                lines.extend(sub[:10])  # limit depth
            else:
                lines.append(f"  {e.name}")

    # sql/ structure
    sql = base / "sql"
    if sql.exists():
        lines.append("\n## sql/")
        for e in sorted(sql.rglob("*")):
            if not e.name.startswith("."):
                lines.append(f"  {e.relative_to(sql)}")

    return "\n".join(lines)


class JobStarExecutor(PRExecutor):
    """Expert executor for job-star's own codebase.

    Extends PRExecutor with curated job-star context. Writes code to the
    job-star repo, runs `python3 -m pytest tests/test_integration.py -v`,
    feeds failures back, creates PRs. Enforces the project rules.
    """

    name = "job-star"
    description = "Job-Star self-improvement expert (curated docs + rules + test/PR loop)"

    def __init__(self, gateway_monitor: GatewayMonitor | None = None):
        super().__init__(
            gateway_monitor=gateway_monitor,
            repo_path=JOB_STAR_PATH,
            test_command="python3 -m pytest tests/test_integration.py -v --tb=short --cache-clear",
            base_branch="main",
        )
        self._curated_context: str | None = None

    def curated_context(self) -> str:
        """Build curated context from job-star docs and rules."""
        if self._curated_context is not None:
            return self._curated_context

        base = Path(JOB_STAR_PATH)
        parts = [
            "# Job-Star Expert Context",
            "",
            "You are the job-star self-improvement expert. You work on job-star's own"
            " codebase — the system that manages goals, routes AI work, and orchestrates"
            " supervised execution. You know the architecture, the rules, and the safety model.",
            "",
        ]

        # ── Rules (most important — load first) ──────────────────────────
        rules = _load_doc(base / "UPGRADE.md", max_chars=8000)
        if rules:
            parts.append(f"\n## UPGRADE.md (RULES — follow these)\n```\n{rules}\n```")

        # ── Design document ──────────────────────────────────────────────
        # This is large (50KB), so only load the executive summary + principles
        design = _load_doc(base / "job-star-design.md", max_chars=4000)
        if design:
            parts.append(f"\n## job-star-design.md (architecture)\n```\n{design}\n```")

        # ── Handoff ───────────────────────────────────────────────────────
        handoff = _load_doc(base / "HANDOFF.md", max_chars=6000)
        if handoff:
            parts.append(f"\n## HANDOFF.md (current state)\n```\n{handoff}\n```")

        # ── Check-in docs ────────────────────────────────────────────────
        checkins = _load_doc(base / "docs" / "check-ins.md", max_chars=4000)
        if checkins:
            parts.append(f"\n## docs/check-ins.md\n```\n{checkins}\n```")

        # ── Code review findings ─────────────────────────────────────────
        review = _load_doc(base / "CODE_REVIEW.md", max_chars=4000)
        if review:
            parts.append(f"\n## CODE_REVIEW.md (known issues + fixes)\n```\n{review}\n```")

        # ── Codebase structure ───────────────────────────────────────────
        parts.append(_codebase_overview())

        # ── Schema ───────────────────────────────────────────────────────
        schema = _load_doc(base / "sql" / "schema.sql", max_chars=4000)
        if schema:
            parts.append(f"\n## sql/schema.sql (canonical schema)\n```\n{schema}\n```")

        self._curated_context = "\n".join(parts)
        return self._curated_context

    def _system_prompt(self) -> str:
        """Override to inject curated job-star context + rules into the PR executor."""
        curated = self.curated_context()
        base_prompt = super()._system_prompt()
        return f"""You are Job-Star's self-improvement expert developer.

You have deep knowledge of job-star's own codebase. You understand the architecture
(intake → triage → conflict → goal registry → router → supervisor → execution → follow-up),
the check-in system, the upgrade process, and the safety model.

## MANDATORY RULES (follow these or your PR will be rejected)

1. **Additive migrations only.** Never DROP tables or columns. Never RENAME columns.
   Use CREATE TABLE IF NOT EXISTS, ALTER TABLE ADD COLUMN (with a default).
   Old code must still work with the new schema.

2. **New migrations go in sql/migrations/003_*.sql, 004_*.sql, etc.**
   Also update sql/schema.sql for fresh installs.
   Record the migration version in the schema_migrations table.

3. **Tests must pass.** Run `python3 -m pytest tests/test_integration.py -v`.
   If you add new features, add tests for them. The test suite is the ground truth.

4. **Never modify the running system directly.** You work in a git worktree (isolated
   copy). Your changes become a PR. A human reviews and merges. The upgrade tool
   (`python3 -m job_star upgrade`) deploys changes safely with blue-green restart.

5. **Self-referential safety.** You are modifying the system that is running you.
   - Never modify the supervisor's constraint model or the audit trail
   - Never modify the upgrade tool's rollback logic
   - Never disable safety checks
   - If you're modifying the check-in engine, the router, or the orchestrator,
     be extra careful — these are core loop components

6. **Follow existing patterns.** Look at how existing code is structured.
   - New DB functions go in db.py
   - New models go in models.py
   - New CLI commands go in cli.py with a cmd_* function and COMMANDS entry
   - New API routes go in api/routes.py
   - New executors go in executors/ and register in __init__.py

## WHAT YOU KNOW

{curated}

{base_prompt}"""