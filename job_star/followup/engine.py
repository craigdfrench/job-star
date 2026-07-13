"""Follow-up engine: handles escalations and notifications.

Classifies events by urgency and routes them to appropriate channels:
- Interrupt: immediate notification (imperative issues)
- Batch: collected and summarized (soon)
- Silent: logged only (idle-opportunistic)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from ..models import Goal, GoalStatus, Step, StepStatus, Urgency
from ..db import audit


class FollowUpLevel(str, Enum):
    INTERRUPT = "interrupt"  # immediate notification
    BATCH = "batch"          # collect and summarize
    SILENT = "silent"        # log only


@dataclass
class FollowUpEvent:
    """An event that needs follow-up."""
    goal_id: str
    step_id: Optional[str]
    level: FollowUpLevel
    message: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class FollowUpEngine:
    """Collects and routes follow-up events."""

    batch: list[FollowUpEvent] = field(default_factory=list)
    max_batch_size: int = 100  # flush when batch exceeds this (prevents unbounded growth)
    _on_flush: Optional[object] = None  # optional callback: callable(batch: list[FollowUpEvent])

    def classify(self, goal: Goal, event_type: str, message: str) -> FollowUpLevel:
        """Classify an event's follow-up level based on goal urgency and event type."""
        # Critical events always interrupt
        if event_type in ("step_failed", "constraint_violated", "budget_exceeded", "goal_blocked"):
            return FollowUpLevel.INTERRUPT

        # Goal urgency determines baseline
        if goal.urgency == Urgency.IMPERATIVE:
            if event_type in ("step_completed", "goal_completed"):
                return FollowUpLevel.INTERRUPT
            return FollowUpLevel.BATCH

        elif goal.urgency == Urgency.SOON:
            if event_type == "goal_completed":
                return FollowUpLevel.BATCH
            return FollowUpLevel.SILENT

        else:  # idle-opportunistic or timed
            return FollowUpLevel.SILENT

    async def emit(
        self,
        goal: Goal,
        event_type: str,
        message: str,
        step_id: str | None = None,
    ) -> FollowUpEvent:
        """Process a follow-up event."""
        level = self.classify(goal, event_type, message)
        event = FollowUpEvent(
            goal_id=goal.id,
            step_id=step_id,
            level=level,
            message=message,
        )

        if level == FollowUpLevel.INTERRUPT:
            # In a full system, this would send a push notification, Telegram message, etc.
            # For now, log it prominently
            await audit("followup_interrupt", {
                "goal_id": goal.id,
                "event_type": event_type,
                "message": message,
            })
            print(f"\n⚠️  INTERRUPT: {message}\n    Goal: {goal.title}\n")

        elif level == FollowUpLevel.BATCH:
            self.batch.append(event)
            await audit("followup_batched", {
                "goal_id": goal.id,
                "event_type": event_type,
                "message": message,
            })
            # Auto-flush if batch is full (prevents unbounded memory growth)
            if len(self.batch) >= self.max_batch_size:
                self._flush()

        else:
            await audit("followup_silent", {
                "goal_id": goal.id,
                "event_type": event_type,
                "message": message,
            })

        return event

    def _flush(self) -> None:
        """Internal flush: invoke the on_flush callback if set, then clear."""
        if not self.batch:
            return
        if self._on_flush:
            try:
                self._on_flush(list(self.batch))
            except Exception:
                pass  # callback failure should not break the pipeline
        self.batch.clear()

    def get_batch(self) -> list[FollowUpEvent]:
        """Get and clear the batch of collected events."""
        events = list(self.batch)
        self.batch.clear()
        return events

    def format_batch(self) -> str:
        """Format the batch as a human-readable summary."""
        if not self.batch:
            return "No batched events."

        lines = [f"Follow-up batch ({len(self.batch)} events):"]
        for e in self.batch:
            lines.append(f"  [{e.timestamp.strftime('%H:%M')}] {e.message}")
        return "\n".join(lines)