"""Research/tickle-file executor: recurring topic monitoring during idle time.

This executor handles goals that represent ongoing research interests — "tickle
file" items that should be checked periodically for new developments. It is
designed to run during idle harvesting time (urgency=idle-opportunistic).

How it works:
  1. A goal is created with expert='research' and a description of the topic.
  2. Each step represents one monthly check-in.
  3. The executor:
     a. Reads previous findings from completed steps (to avoid repeating)
     b. Reads the existing tickle file on disk (if any)
     c. Prompts the AI to search for / summarize recent developments
     d. Compiles findings into a structured markdown report
     e. Appends findings to the tickle file on disk
     f. Auto-creates the next month's step (recurring schedule)
  4. The idle loop picks up the next step when idle time comes around again.

The tickle file lives at:
  ~/tickle-file/<slug>.md

Each entry is timestamped and tagged with the model used.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..models import ExecutionResult, Goal, Step
from ..router import route
from ..gatehouse import execute as execute_ai
from ..gatehouse import GatewayMonitor
from .default import DefaultExecutor
from ..db import create_step, get_steps, audit


# Directory for tickle files
TICKLE_DIR = Path.home() / "tickle-file"

# How the research system prompt is framed
RESEARCH_SYSTEM_PROMPT = """You are Job-Star's research agent. You monitor topics
of interest and compile concise, insightful summaries of new developments.

Your output should be a structured markdown report with:
1. A summary of what's new since the last check-in
2. Key insights or shifts in the landscape
3. Interesting articles, papers, or releases (with links if you know them)
4. A brief assessment of whether this topic is heating up, cooling down, or stable

Format your output as clean markdown. Be concise but insightful. If nothing
notable has changed, say so — false updates are worse than no updates.

Focus on SIGNAL over NOISE. Skip marketing fluff. Prioritize:
- New model releases or architecture changes
- Benchmark results or performance claims
- Research papers with novel approaches
- Industry adoption signals
- Surprising or counterintuitive findings
"""


def _slugify(text: str, max_len: int = 60) -> str:
    """Create a filesystem-safe slug from text."""
    slug = re.sub(r'[^a-z0-9-]', '-', text.lower())[:max_len].strip('-')
    return slug or "research"


def _tickle_file_path(goal: Goal) -> Path:
    """Get the tickle file path for a goal."""
    slug = _slugify(goal.title)
    return TICKLE_DIR / f"{slug}.md"


def _load_tickle_file(path: Path) -> str:
    """Load the existing tickle file content (last 8000 chars for context)."""
    try:
        if path.exists():
            content = path.read_text()
            if len(content) > 8000:
                return content[-8000:]
            return content
    except Exception:
        pass
    return ""


def _append_tickle_file(path: Path, entry: str) -> None:
    """Append a new entry to the tickle file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if path.exists() else "w"
    with open(path, mode) as f:
        if mode == "w":
            f.write(f"# Tickle File: {path.stem}\n\n")
            f.write(f"_Recurring research topic monitored by job-star_\n\n")
            f.write("---\n\n")
        f.write(entry)
        f.write("\n\n---\n\n")


class ResearchExecutor(DefaultExecutor):
    """Recurring research/tickle-file executor.

    Handles goals that represent ongoing research interests. Each step is a
    monthly check-in. After completing a step, automatically creates the next
    month's step to maintain the recurring schedule.
    """

    name = "research"
    description = "Recurring research/tickle-file agent (monitors topics during idle time)"

    def __init__(self, gateway_monitor: GatewayMonitor | None = None):
        super().__init__(gateway_monitor)

    def curated_context(self) -> str:
        return ""

    def _build_research_prompt(
        self,
        goal: Goal,
        step: Step,
        previous_findings: str,
        tickle_content: str,
    ) -> str:
        """Build the user prompt for the research AI call."""
        parts = [
            f"# Research Topic: {goal.title}",
            "",
            f"## Topic Description",
            goal.description or "(no description provided)",
            "",
            "## Your Task",
            "Research the current state of this topic and compile a summary of",
            "recent developments. Focus on what's NEW since the last check-in.",
            "",
        ]

        if previous_findings:
            parts.append("## Previous Findings (from last check-in)")
            parts.append("```markdown")
            parts.append(previous_findings[:4000])
            parts.append("```")
            parts.append("")
        else:
            parts.append("## Previous Findings")
            parts.append("This is the first check-in. Provide a comprehensive overview")
            parts.append("of the current state of this topic, then focus on recent developments.")
            parts.append("")

        if tickle_content:
            parts.append("## Tickle File History (recent entries)")
            parts.append("```markdown")
            parts.append(tickle_content[-4000:])
            parts.append("```")
            parts.append("")

        parts.append("## Step Details")
        parts.append(f"Check-in: {step.title}")
        parts.append(f"Date: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
        parts.append("")
        parts.append("## Output Format")
        parts.append("Produce a markdown report with these sections:")
        parts.append("- **What's New**: Key developments since last check-in")
        parts.append("- **Insights**: What do these developments mean? Trends? Shifts?")
        parts.append("- **Notable Links**: Papers, repos, articles, announcements")
        parts.append("- **Assessment**: Heating up / cooling down / stable? Why?")
        parts.append("")
        parts.append("Be concise. Signal over noise. If nothing notable changed, say so.")

        return "\n".join(parts)

    async def _get_previous_findings(self, goal: Goal) -> str:
        """Get findings text from the most recent completed step."""
        try:
            steps = await get_steps(goal.id)
            completed = [s for s in steps if s.status.value == "completed"]
            if not completed:
                return ""
            # Get the most recent completed step's result
            last = max(completed, key=lambda s: s.order_index)
            if last.result and isinstance(last.result, dict):
                return last.result.get("content", "") or last.result.get("findings", "")
        except Exception:
            pass
        return ""

    async def _create_next_step(self, goal: Goal, current_step: Step) -> None:
        """Create the next month's check-in step (recurring schedule)."""
        now = datetime.now(timezone.utc)
        next_month_label = now.strftime("%B %Y")
        try:
            await create_step(
                goal_id=goal.id,
                title=f"Monthly check-in: {next_month_label}",
                description=(
                    f"Recurring monthly research check-in for: {goal.title}\n\n"
                    f"Look for new developments, papers, model releases, "
                    f"benchmark results, or industry shifts related to this topic. "
                    f"Compare against previous findings to identify what's genuinely new."
                ),
            )
            await audit("research_next_step_created", {
                "goal_id": goal.id,
                "next_check_in": next_month_label,
            }, goal_id=goal.id)
        except Exception as e:
            # Don't fail the whole execution if we can't create the next step
            print(f"  [research] Warning: could not create next step: {e}")

    async def execute(
        self,
        goal: Goal,
        step: Step,
        context: dict | None = None,
        model_override: str | None = None,
    ) -> ExecutionResult:
        """Execute a research check-in step."""
        context = context or {}

        # Route to a model (prefer cheap models for idle research)
        routing = await route(
            urgency=goal.urgency,
            request_type="research",
            description=step.description or step.title,
            model_override=model_override,
            allow_expensive=bool(model_override),
            gateway_monitor=self.gateway_monitor,
        )

        if not routing.model:
            return ExecutionResult(
                success=False,
                error=f"No model available: {routing.reason}",
                model="none",
            )

        # Gather context: previous findings + tickle file
        previous_findings = await self._get_previous_findings(goal)
        tickle_path = _tickle_file_path(goal)
        tickle_content = _load_tickle_file(tickle_path)

        # Build the prompt
        user_prompt = self._build_research_prompt(goal, step, previous_findings, tickle_content)

        # Execute the AI call
        result = await execute_ai(
            user_prompt,
            model=routing.model,
            system_prompt=RESEARCH_SYSTEM_PROMPT,
        )

        if not result.success:
            self.gateway_monitor.record_failure(routing.model, result.error or "error")
            return result

        self.gateway_monitor.record_success(
            routing.model,
            result.input_tokens + result.output_tokens,
            x_gatehouse=result.x_gatehouse,
        )

        # Compile the tickle file entry
        now = datetime.now(timezone.utc)
        entry_parts = [
            f"## Check-in: {now.strftime('%Y-%m-%d %H:%M UTC')}",
            f"**Model:** {result.model} | **Step:** {step.title}",
            "",
            result.content,
        ]
        entry = "\n".join(entry_parts)

        # Append to tickle file
        try:
            _append_tickle_file(tickle_path, entry)
        except Exception as e:
            print(f"  [research] Warning: could not write tickle file: {e}")

        # Create next month's step (recurring schedule)
        await self._create_next_step(goal, step)

        # Return the result with findings stored in the step result
        return ExecutionResult(
            content=result.content,
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            cost=result.cost,
            success=True,
            x_gatehouse=result.x_gatehouse,
        )