"""Intake handler: accepts raw input and processes it through the pipeline.

This is the entry point for all new work entering the system.
"""

from __future__ import annotations

from typing import Optional

from ..models import Domain, Goal, IntakeRequest, TriageResult, Urgency
from ..db import create_goal, audit, get_unresolved_conflicts
from ..triage import triage as run_triage
from ..conflict import detect_conflicts


async def intake(
    title: str,
    description: str = "",
    source: str = "manual",
    urgency_override: Urgency | None = None,
    domain_override: Domain | None = None,
    metadata: dict | None = None,
    check_conflicts: bool = True,
    requested_by: str = "",
) -> tuple[Goal | None, TriageResult]:
    """Process a raw intake request through the full pipeline.

    Steps:
    1. Create an IntakeRequest
    2. Triage it (classify + duplicate check)
    3. If not a duplicate, create a goal in the registry
    4. Check for conflicts with existing goals
    5. Return the created goal and triage result

    Args:
        title: Short title for the request.
        description: Longer description.
        source: Where this came from (manual, web, telegram, voice).
        urgency_override: Force urgency level.
        domain_override: Force domain.
        metadata: Extra metadata.
        check_conflicts: Whether to run conflict detection.

    Returns:
        (Goal or None if duplicate, TriageResult)
    """
    request = IntakeRequest(
        title=title,
        description=description,
        source=source,
        urgency_override=urgency_override,
        domain_override=domain_override,
        metadata=metadata or {},
    )

    # Triage
    result = await run_triage(request)

    await audit("intake_triaged", {
        "title": title,
        "triage": result.rationale,
        "source": source,
    })

    # If duplicate, don't create a new goal
    if result.is_duplicate and result.duplicate_of:
        await audit("intake_duplicate_detected", {
            "title": title,
            "duplicate_of": result.duplicate_of,
            "confidence": result.duplicate_confidence,
        })
        return None, result

    # Create the goal
    goal = await create_goal(
        title=title,
        description=description,
        domain=result.domain,
        urgency=result.urgency,
        source=source,
        expert=result.expert,
        requested_by=requested_by,
        metadata={
            **(metadata or {}),
            "triage": {
                "request_type": result.request_type,
                "confidence": result.confidence,
                "keywords": result.keywords,
                "rationale": result.rationale,
                "expert": result.expert,
            },
        },
    )

    # Check for conflicts (incremental: only the new goal vs existing)
    if check_conflicts:
        conflicts = await detect_conflicts(save=True, incremental_goal_id=goal.id)
        if conflicts:
            await audit("intake_conflicts_detected", {
                "goal_id": goal.id,
                "conflict_count": len(conflicts),
            })

    return goal, result