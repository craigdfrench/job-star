"""Check-in engine: structured two-way progress dialogue between job-star and the user.

A check-in is a deliberate, structured interaction at a key point in a goal's lifecycle:

  - PROGRESS: "Here's what I've done, here's what's next, here's where I need your input."
  - CLARIFICATION: "I'm uncertain about X. Which direction should I take?"
  - MILESTONE: "Phase 1 is done. Here are the results. Does this match what you expected?"
  - COMPLETION: "The goal is complete. Here's the final output. Do you accept this?"

Each check-in is:
  - AI-generated: the system summarizes progress and formulates questions
  - Persistent: stored in Postgres, survives restarts
  - Asynchronous: the user responds whenever they want
  - Actionable: user responses feed back into goal direction

Lifecycle:
  draft → sent → awaiting_response → responded → actioned
                                                       ↓
                                                    expired
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


class CheckInType(str, Enum):
    PROGRESS = "progress"          # regular progress report
    CLARIFICATION = "clarification"  # system needs user input to proceed
    MILESTONE = "milestone"        # logical phase complete, results for review
    COMPLETION = "completion"      # goal complete, final output for acceptance


class CheckInStatus(str, Enum):
    DRAFT = "draft"
    SENT = "sent"
    AWAITING_RESPONSE = "awaiting_response"
    RESPONDED = "responded"
    ACTIONED = "actioned"
    EXPIRED = "expired"


@dataclass
class CheckInQuestion:
    """A single question within a check-in."""
    id: str = field(default_factory=lambda: str(uuid4())[:8])
    question: str = ""
    type: str = "text"  # text, choice, approval, rating
    options: list[str] = field(default_factory=list)  # for choice type
    required: bool = True
    answer: Optional[str] = None  # filled when user responds

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "question": self.question,
            "type": self.type,
            "options": self.options,
            "required": self.required,
            "answer": self.answer,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CheckInQuestion":
        return cls(
            id=d.get("id", str(uuid4())[:8]),
            question=d.get("question", ""),
            type=d.get("type", "text"),
            options=d.get("options", []),
            required=d.get("required", True),
            answer=d.get("answer"),
        )


@dataclass
class CheckIn:
    """A structured check-in between job-star and the user."""
    id: str = field(default_factory=lambda: str(uuid4()))
    goal_id: str = ""
    step_id: Optional[str] = None
    type: CheckInType = CheckInType.PROGRESS
    status: CheckInStatus = CheckInStatus.DRAFT
    progress_summary: str = ""
    next_steps: str = ""
    results: str = ""
    questions: list[CheckInQuestion] = field(default_factory=list)
    response: Optional[str] = None
    decisions: list[dict] = field(default_factory=list)
    responded_at: Optional[datetime] = None
    created_by: str = "system"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_row(cls, row: dict) -> "CheckIn":
        questions_raw = row.get("questions")
        if isinstance(questions_raw, str):
            questions_raw = json.loads(questions_raw) if questions_raw else []
        questions_raw = questions_raw or []
        questions = [CheckInQuestion.from_dict(q) for q in questions_raw]

        decisions_raw = row.get("decisions")
        if isinstance(decisions_raw, str):
            decisions_raw = json.loads(decisions_raw) if decisions_raw else []
        decisions_raw = decisions_raw or []

        return cls(
            id=str(row["id"]),
            goal_id=str(row["goal_id"]),
            step_id=str(row["step_id"]) if row.get("step_id") else None,
            type=CheckInType(row.get("type", "progress")),
            status=CheckInStatus(row.get("status", "draft")),
            progress_summary=row.get("progress_summary") or "",
            next_steps=row.get("next_steps") or "",
            results=row.get("results") or "",
            questions=questions,
            response=row.get("response"),
            decisions=decisions_raw if isinstance(decisions_raw, list) else [],
            responded_at=row.get("responded_at"),
            created_by=row.get("created_by", "system"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    @property
    def is_pending(self) -> bool:
        """True if the check-in is waiting for a user response."""
        return self.status in (CheckInStatus.SENT, CheckInStatus.AWAITING_RESPONSE)

    @property
    def has_questions(self) -> bool:
        return any(q for q in self.questions)

    def format(self, goal_title: str = "") -> str:
        """Format the check-in for terminal/CLI display."""
        type_icon = {
            CheckInType.PROGRESS: "📊",
            CheckInType.CLARIFICATION: "❓",
            CheckInType.MILESTONE: "🏁",
            CheckInType.COMPLETION: "✅",
        }.get(self.type, "📋")

        status_str = {
            CheckInStatus.DRAFT: "draft",
            CheckInStatus.SENT: "awaiting response",
            CheckInStatus.AWAITING_RESPONSE: "awaiting response",
            CheckInStatus.RESPONDED: "responded",
            CheckInStatus.ACTIONED: "actioned",
            CheckInStatus.EXPIRED: "expired",
        }.get(self.status, self.status.value)

        lines = [
            "",
            f"  ┌─────────────────────────────────────────────────────────",
            f"  │ {type_icon}  CHECK-IN: {self.type.value.upper()}  ({status_str})",
            f"  │ Goal: {goal_title or self.goal_id[:8]}",
            f"  │ ID:   {self.id[:8]}",
            f"  └─────────────────────────────────────────────────────────",
        ]

        if self.progress_summary:
            lines.append("")
            lines.append(f"  📝 PROGRESS SUMMARY")
            lines.append(f"  {self.progress_summary}")

        if self.results:
            lines.append("")
            lines.append(f"  📦 RESULTS")
            lines.append(f"  {self.results}")

        if self.next_steps:
            lines.append("")
            lines.append(f"  ➡️  NEXT STEPS")
            lines.append(f"  {self.next_steps}")

        if self.questions:
            lines.append("")
            lines.append(f"  ❓ QUESTIONS FOR YOU")
            for i, q in enumerate(self.questions, 1):
                req = " (required)" if q.required else " (optional)"
                lines.append(f"    {i}. {q.question}{req}")
                if q.options:
                    for j, opt in enumerate(q.options, 1):
                        lines.append(f"       {j}) {opt}")
                if q.answer:
                    lines.append(f"       → Your answer: {q.answer}")

        if self.response:
            lines.append("")
            lines.append(f"  💬 YOUR RESPONSE")
            lines.append(f"  {self.response}")

        if self.decisions:
            lines.append("")
            lines.append(f"  📋 DECISIONS")
            for d in self.decisions:
                lines.append(f"    {d.get('question_id', '?')}: {d.get('answer', '?')}")

        lines.append("")

        return "\n".join(lines)

# Re-export engine functions and classes for convenience
from .engine import (
    CheckInEngine,
    create_check_in,
    get_check_in,
    list_check_ins,
    get_pending_check_ins,
    respond_to_check_in,
    action_check_in,
    should_create_progress_check_in,
    should_create_completion_check_in,
    generate_check_in_content,
    DEFAULT_CHECK_IN_INTERVAL,
)

__all__ = [
    "CheckIn", "CheckInType", "CheckInStatus", "CheckInQuestion",
    "CheckInEngine",
    "create_check_in", "get_check_in", "list_check_ins",
    "get_pending_check_ins", "respond_to_check_in", "action_check_in",
    "should_create_progress_check_in", "should_create_completion_check_in",
    "generate_check_in_content", "DEFAULT_CHECK_IN_INTERVAL",
]
