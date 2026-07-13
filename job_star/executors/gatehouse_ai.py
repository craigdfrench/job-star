"""Gatehouse-AI expert executor.

A specialized agent that handles goals related to the gatehouse-ai codebase.
It has curated context from the gatehouse-ai docs (README, DESIGN, HANDOFF,
docs/) and knows the codebase structure, provider model, pricing/quota system,
and the x_gatehouse metadata.

Goals tagged with expert='gatehouse-ai' are routed to this executor and can
only be claimed by workers with matching affinity (JOB_STAR_EXPERT=gatehouse-ai).
"""

from __future__ import annotations

import os
from pathlib import Path

from ..models import ExecutionResult, Goal, Step
from ..router import route
from ..gatehouse import execute as execute_ai
from ..gatehouse import GatewayMonitor
from .pr_executor import PRExecutor


GATEHOUSE_AI_PATHS = [
    "/home/craig/gatehouse-ai",
    "/etc/gatehouse",
]

# Devin wiki URL (curated docs) — requires auth to fetch, referenced here
# for future integration with a Devin API client.
DEVIN_WIKI_URL = "https://app.devin.ai/org/craigdfrench/wiki/craigdfrench/gatehouse-ai?branch=main"


def _load_doc(path: Path, max_chars: int = 4000) -> str:
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
    """Build a structural overview of the gatehouse-ai codebase."""
    lines = []
    for base in GATEHOUSE_AI_PATHS:
        p = Path(base)
        if not p.exists():
            continue
        lines.append(f"\n## {p}")
        if p.is_dir():
            try:
                entries = sorted(p.iterdir())
                for e in entries:
                    if e.name.startswith(".git"):
                        continue
                    lines.append(f"  {e.name}/" if e.is_dir() else f"  {e.name}")
            except Exception:
                pass
    return "\n".join(lines)


class GatehouseAIExecutor(PRExecutor):
    """Expert executor for the gatehouse-ai codebase.

    Extends PRExecutor with curated gatehouse-ai context. Writes code to the
    gatehouse-ai repo, runs `go test ./...`, feeds failures back, creates PRs.
    """

    name = "gatehouse-ai"
    description = "Gatehouse-AI developer expert (curated docs + codebase + test/PR loop)"

    def __init__(self, gateway_monitor: GatewayMonitor | None = None):
        super().__init__(
            gateway_monitor=gateway_monitor,
            repo_path="/home/craig/gatehouse-ai",
            test_command="go test ./...",
            base_branch="main",
        )
        self._curated_context: str | None = None

    def curated_context(self) -> str:
        """Build curated context from gatehouse-ai docs."""
        if self._curated_context is not None:
            return self._curated_context

        parts = [
            "# Gatehouse-AI Expert Context",
            "",
            "You are an expert gatehouse-ai developer. Gatehouse is an identity-aware,"
            " accounting-owning AI gateway written in Go. It routes to direct providers"
            " (NVIDIA NIM, cog-proxy/Windsurf, z.ai, Ollama) and owns accounting"
            " (budgets, rate limits, cost ledger).",
            "",
            f"Devin wiki (curated): {DEVIN_WIKI_URL}",
            "",
        ]

        # Load key docs. AGENTS.md is the canonical onboarding doc — load it
        # first (build/test commands, the WSL/systemd-run gotcha, governing
        # principles, reading list, current state). It is the single most
        # important context for working on gatehouse-ai.
        base = Path("/home/craig/gatehouse-ai")
        agents_md = _load_doc(base / "AGENTS.md", max_chars=12000)
        if agents_md:
            parts.append(f"\n## AGENTS.md (onboarding — read first)\n```\n{agents_md}\n```")

        for doc in ["README.md", "DESIGN.md", "HANDOFF.md"]:
            content = _load_doc(base / doc, max_chars=6000)
            if content:
                parts.append(f"\n## {doc}\n```\n{content}\n```")

        # Codebase structure
        parts.append(_codebase_overview())

        # internal/ packages
        internal = base / "internal"
        if internal.exists():
            parts.append("\n## internal/ packages")
            try:
                for d in sorted(internal.iterdir()):
                    if d.is_dir():
                        parts.append(f"  {d.name}/")
            except Exception:
                pass

        # Config reference
        config_sample = _load_doc(base / "config.sample.json", max_chars=3000)
        if config_sample:
            parts.append(f"\n## config.sample.json\n```json\n{config_sample}\n```")

        # Docs directory
        docs = base / "docs"
        if docs.exists():
            parts.append("\n## docs/")
            try:
                for d in sorted(docs.iterdir()):
                    parts.append(f"  {d.name}")
            except Exception:
                pass

        parts.append(
            "\n## Key concepts you know"
            "\n- Providers: nvidia, cog-proxy (Windsurf), z-ai-coding-plan, perplexity, ollama"
            "\n- model_costs: free_kind (included_unlimited, promotional_free, quota_bearing),"
            " quota_pools (windsurf_daily, windsurf_weekly, zai_5h, zai_weekly)"
            "\n- x_gatehouse response metadata: cost_class, routing_advice (harvest/switch/conserve),"
            " quota_windows (remaining_pct, resets_at), retail_value_this_request"
            "\n- Endpoints: /v1/models (public, 141 models), /api/v1/admin/models (admin-only,"
            " retail pricing + cost_class + routable), /v1/usage (aggregate)"
            "\n- Two instances: production (systemd, port 18080, Caddy → gatehouse-ai.craigdfrench.com)"
            " and dev (local build, port 8090, 100.64.158.87:8090)"
            "\n- Model ID schemes: production uses ollama/-prefixed IDs, dev uses unprefixed IDs"
        )

        self._curated_context = "\n".join(parts)
        return self._curated_context

    def _system_prompt(self) -> str:
        """Override to inject curated gatehouse-ai context into the PR executor."""
        curated = self.curated_context()
        base = super()._system_prompt()
        return f"""You are Job-Star's gatehouse-ai expert developer.

You have deep knowledge of the gatehouse-ai codebase. Use the curated context below
to ground your work. You understand the provider model, pricing/quota system,
x_gatehouse metadata, the Go codebase structure, and the admin API.

When working on a gatehouse-ai goal:
- Reference real files and packages from the codebase
- Use the correct terminology (providers, model_costs, cost_class, routing_advice, quota_pools)
- Be consistent with the existing Go code style and structure
- If the task involves config, reference config.sample.json
- If the task involves pricing/quota, reference the x_gatehouse metadata model

{curated}

{base}"""
