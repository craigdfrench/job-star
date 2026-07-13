"""Research/tickle-file executor: recurring topic monitoring during idle time.

This executor handles goals that represent ongoing research interests — "tickle
file" items that should be checked periodically for new developments. It is
designed to run during idle harvesting time (urgency=idle-opportunistic).

Two modes:
  1. **Monitoring** — recurring check-ins on a topic. Uses Perplexity (sonar)
     models via gatehouse for web search capability, since the model needs
     current information that may postdate its training data.
  2. **Structured learning** — pre-created step sequences (e.g. a 16-week
     curriculum). Uses standard LLM models, since the content is established
     knowledge that doesn't require web search.

How it works:
  1. A goal is created with expert='research' and a description of the topic.
  2. Steps represent either monthly check-ins (monitoring) or weekly lessons
     (structured learning).
  3. The executor:
     a. Reads previous findings from completed steps (to avoid repeating)
     b. Reads the existing tickle file on disk (if any)
     c. Prompts the AI to research / teach the topic
     d. Compiles findings into a structured markdown report
     e. Appends findings to the tickle file on disk
     f. If no pending steps remain, auto-creates a recurring monthly check-in
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

# Perplexity models available via gatehouse (web search capable)
# Used for monitoring tasks that need current information
PERPLEXITY_MODELS = ["sonar", "sonar-pro"]

# System prompt for monitoring check-ins (uses web search models)
MONITORING_SYSTEM_PROMPT = """You are Job-Star's research agent. You monitor topics
of interest and compile concise, insightful summaries of new developments.

You have web search capability. Use it to find recent developments, new releases,
papers, and announcements related to the topic. Focus on what's genuinely NEW
since the last check-in.

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

# System prompt for structured learning lessons (uses standard LLM models)
LESSON_SYSTEM_PROMPT = """You are Job-Star's learning agent. You produce structured,
clear, and insightful lessons on technical topics for an intelligent professional
who needs to build technical fluency in a specific domain.

Your lessons should:
- Start with intuition and analogies before diving into technical detail
- Define every term and abbreviation when first used
- Build concepts sequentially — each section should depend on the previous one
- Include enough technical depth that the learner could follow a working group discussion
- End with a glossary and self-test questions

Be thorough but not exhausting. The goal is fluency, not expertise.
Write in clear, engaging prose. Use tables, diagrams (in ASCII), and examples where helpful.
"""


def _slugify(text: str, max_len: int = 60) -> str:
    """Create a filesystem-safe slug from text."""
    slug = re.sub(r'[^a-z0-9-]+', '-', text.lower())[:max_len].strip('-')
    return slug or "research"


def _tickle_file_path(goal: Goal) -> Path:
    """Get the tickle file path for a goal.

    If the goal has a 'tickle_file' in its metadata, use that path directly.
    Otherwise, auto-generate from the goal title.
    """
    if goal.metadata and "tickle_file" in goal.metadata:
        return Path(goal.metadata["tickle_file"])
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


def _append_tickle_file(path: Path, entry: str, header: str | None = None) -> None:
    """Append a new entry to the tickle file.

    Args:
        path: Path to the tickle file.
        entry: The markdown content to append.
        header: Optional header for the tickle file (only used on first creation).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if path.exists() else "w"
    with open(path, mode) as f:
        if mode == "w":
            if header:
                f.write(header)
            else:
                f.write(f"# Tickle File: {path.stem}\n\n")
                f.write(f"_Recurring research topic monitored by job-star_\n\n")
            f.write("---\n\n")
        f.write(entry)
        f.write("\n\n---\n\n")


def _is_lesson_step(step: Step) -> bool:
    """Detect if a step is a structured learning lesson vs. a monitoring check-in.

    Lessons have "Week N:" in the title and detailed descriptions with specific
    learning objectives. Monitoring check-ins have "check-in" in the title.
    """
    title_lower = step.title.lower()
    if "week " in title_lower and ":" in title_lower:
        return True
    if "check-in" in title_lower or "check in" in title_lower:
        return False
    # Fallback: long descriptions suggest structured learning
    return bool(step.description and len(step.description) > 200)


class ResearchExecutor(DefaultExecutor):
    """Recurring research/tickle-file executor.

    Handles goals that represent ongoing research interests or structured
    learning plans. Each step is either a monthly check-in (monitoring) or
    a weekly lesson (structured learning). After completing a step, if no
    more pending steps exist, automatically creates a recurring monthly
    check-in to maintain the schedule.
    """

    name = "research"
    description = "Recurring research/tickle-file agent (monitors topics during idle time)"

    def __init__(self, gateway_monitor: GatewayMonitor | None = None):
        super().__init__(gateway_monitor)

    def curated_context(self) -> str:
        return ""

    def _pick_model(self, is_lesson: bool, model_override: str | None,
                    gateway_monitor: GatewayMonitor) -> str | None:
        """Pick the best available model for this step.

        For monitoring check-ins: prefer Perplexity (sonar) models for web search.
        For lessons: use the router's default selection (standard LLM).

        Falls back to router-selected model if Perplexity is unavailable.
        """
        if model_override:
            return model_override

        if not is_lesson:
            # Monitoring task — try Perplexity models first for web search
            for model in PERPLEXITY_MODELS:
                state = gateway_monitor.state(model)
                # Use if the model is known and not in quota hold
                if state and not state.is_in_quota_hold:
                    return model
                # If we've never seen the model, try it anyway
                if state is None:
                    return model

        return None  # Fall back to router selection

    def _build_monitoring_prompt(
        self,
        goal: Goal,
        step: Step,
        previous_findings: str,
        tickle_content: str,
    ) -> str:
        """Build the user prompt for a monitoring check-in (web search task)."""
        parts = [
            f"# Research Topic: {goal.title}",
            "",
            f"## Topic Description",
            goal.description or "(no description provided)",
            "",
            "## Your Task",
            "Research the current state of this topic and compile a summary of",
            "recent developments. Use web search to find what's genuinely NEW",
            "since the last check-in. Focus on developments from the last 30-60 days.",
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
        parts.append("- **Notable Links**: Papers, repos, articles, announcements (with URLs)")
        parts.append("- **Assessment**: Heating up / cooling down / stable? Why?")
        parts.append("")
        parts.append("Be concise. Signal over noise. If nothing notable changed, say so.")

        return "\n".join(parts)

    def _build_lesson_prompt(
        self,
        goal: Goal,
        step: Step,
        previous_findings: str,
        tickle_content: str,
    ) -> str:
        """Build the user prompt for a structured learning lesson."""
        parts = [
            f"# Learning Module: {step.title}",
            "",
            f"## Course Context",
            goal.description or "(no description provided)",
            "",
            "## This Week's Topic",
            step.description or "",
            "",
            "## Your Task",
            "Produce a structured lesson for this week's topic. The learner is",
            "an intelligent professional who needs to build technical fluency",
            "but is not an RF engineer. Use clear analogies, define all terms,",
            "and explain concepts in a way that builds intuition.",
            "",
            "Structure your lesson as:",
            "1. **Overview** — what this topic is about and why it matters",
            "2. **Key Concepts** — each major concept explained clearly with analogies",
            "3. **Technical Details** — enough depth to follow a working group discussion",
            "4. **WiFi Alliance Context** — why this matters specifically for WiFi Alliance participation",
            "5. **Key Terms** — glossary of terms introduced this week",
            "6. **Check Your Understanding** — 3-5 questions to self-test",
            "",
        ]

        if previous_findings:
            parts.append("## Previous Lesson (for continuity)")
            parts.append("```markdown")
            parts.append(previous_findings[:4000])
            parts.append("```")
            parts.append("")

        if tickle_content:
            parts.append("## Tickle File History (recent lessons)")
            parts.append("```markdown")
            parts.append(tickle_content[-3000:])
            parts.append("```")
            parts.append("")

        parts.append("## Output")
        parts.append("Produce the complete lesson in markdown. Be thorough but engaging.")
        parts.append("Use tables and ASCII diagrams where helpful.")

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
        """Create a recurring monthly check-in step if no pending steps remain.

        Only creates a new step if there are no more pending steps for this goal.
        This allows pre-created step sequences (e.g. a 16-week curriculum) to run
        without the executor creating unwanted duplicates.

        After a structured curriculum completes, transitions to monthly monitoring
        check-ins so the learner stays current on the topic.
        """
        try:
            steps = await get_steps(goal.id)
            # Check for any non-completed steps (pending or in_progress),
            # excluding the current step. If there are any, don't create
            # another — it would be a duplicate.
            unfinished = [
                s for s in steps
                if s.status.value in ("pending", "in_progress") and s.id != current_step.id
            ]
            if unfinished:
                return

            # No unfinished steps besides the current one. Create the next
            # recurring check-in, with depends_on set to the current step
            # so it can't be claimed until the current step is marked completed.
            # This prevents the worker from immediately picking it up.

            # Time throttle: only create a recurring check-in if the last
            # completed check-in was more than 25 days ago. This prevents an
            # infinite loop where each completion immediately creates a new step.
            now = datetime.now(timezone.utc)
            recent_completed = [
                s for s in steps
                if s.status.value == "completed" and s.completed_at
            ]
            if recent_completed:
                last_completed = max(recent_completed, key=lambda s: s.completed_at)
                last_dt = last_completed.completed_at
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                days_since = (now - last_dt).days
                if days_since < 25:
                    return  # Too soon — don't create another check-in

            next_month_label = now.strftime("%B %Y")
            await create_step(
                goal_id=goal.id,
                title=f"Monthly check-in: {next_month_label}",
                depends_on=[current_step.id],
                description=(
                    f"Recurring monthly check-in for: {goal.title}\n\n"
                    f"Now that the structured curriculum is complete, this is an "
                    f"ongoing monitoring step. Look for new developments, standards "
                    f"updates, industry shifts, or regulatory changes related to "
                    f"this topic. Compare against previous findings to identify "
                    f"what's genuinely new.\n\n"
                    f"Use web search to find current information."
                ),
            )
            await audit("research_next_step_created", {
                "goal_id": goal.id,
                "next_check_in": next_month_label,
                "mode": "recurring_monitoring",
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
        """Execute a research check-in or learning lesson step."""
        context = context or {}

        # Detect mode: lesson (structured learning) vs. monitoring (recurring check-in)
        is_lesson = _is_lesson_step(step)

        # Pick the best model
        preferred_model = self._pick_model(is_lesson, model_override, self.gateway_monitor)

        if preferred_model:
            routing_model = preferred_model
            routing_reason = f"{'lesson' if is_lesson else 'monitoring'} mode → {preferred_model}"
        else:
            # Fall back to router selection
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
            routing_model = routing.model
            routing_reason = routing.reason

        # Gather context: previous findings + tickle file
        previous_findings = await self._get_previous_findings(goal)
        tickle_path = _tickle_file_path(goal)
        tickle_content = _load_tickle_file(tickle_path)

        # Build the prompt based on mode
        if is_lesson:
            user_prompt = self._build_lesson_prompt(goal, step, previous_findings, tickle_content)
            system_prompt = LESSON_SYSTEM_PROMPT
        else:
            user_prompt = self._build_monitoring_prompt(goal, step, previous_findings, tickle_content)
            system_prompt = MONITORING_SYSTEM_PROMPT

        print(f"  [research] Mode: {'lesson' if is_lesson else 'monitoring'} | Model: {routing_model}")

        # Execute the AI call
        result = await execute_ai(
            user_prompt,
            model=routing_model,
            system_prompt=system_prompt,
        )

        if not result.success:
            self.gateway_monitor.record_failure(routing_model, result.error or "error")
            return result

        self.gateway_monitor.record_success(
            routing_model,
            result.input_tokens + result.output_tokens,
            x_gatehouse=result.x_gatehouse,
        )

        # Compile the tickle file entry
        now = datetime.now(timezone.utc)
        if is_lesson:
            entry_header = (
                f"## Lesson: {step.title}\n"
                f"**Date:** {now.strftime('%Y-%m-%d')} | **Model:** {result.model}"
            )
        else:
            entry_header = (
                f"## Check-in: {now.strftime('%Y-%m-%d %H:%M UTC')}\n"
                f"**Model:** {result.model} | **Step:** {step.title}"
            )

        entry = entry_header + "\n\n" + result.content

        # Append to tickle file
        try:
            _append_tickle_file(tickle_path, entry)
        except Exception as e:
            print(f"  [research] Warning: could not write tickle file: {e}")

        # Create next step if no pending steps remain (recurring schedule)
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