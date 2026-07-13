"""
AI prompt templates for semantic conflict analysis.

These prompts are used when rule-based heuristics need augmentation
from an LLM to assess semantic relationships between goals.
"""

from __future__ import annotations

from .types import GoalSnapshot

DUPLICATE_PROMPT = """\
You are analyzing two goals to determine if they are duplicates.

Goal A: {goal_a_title}
Description: {goal_a_description}
Success criteria: {goal_a_success}

Goal B: {goal_b_title}
Description: {goal_b_description}
Success criteria: {goal_b_success}

Respond in JSON:
{{
  "is_duplicate": true/false,
  "confidence": 0.0-1.0,
  "reasoning": "explanation",
  "merge_suggestion": "if duplicate, a merged title/description, else empty"
}}
"""

CONTRADICTION_PROMPT = """\
You are analyzing two goals to determine if they directly contradict each other.
A contradiction means achieving one goal makes achieving the other impossible,
not merely difficult.

Goal A: {goal_a_title}
Description: {goal_a_description}
Success criteria: {goal_a_success}
Domain: {goal_a_domain}

Goal B: {goal_b_title}
Description: {goal_b_description}
Success criteria: {goal_b_success}
Domain: {goal_b_domain}

Consider:
- Do the success criteria directly oppose each other?
- Would achieving one prevent the other's success criteria from being met?
- Are they in opposite directions on the same axis?

Respond in JSON:
{{
  "is_contradiction": true/false,
  "confidence": 0.0-1.0,
  "reasoning": "explanation",
  "opposing_axis": "what dimension they oppose on, or empty"
}}
"""

TENSION_PROMPT = """\
You are analyzing two goals to determine if they create tension or trade-offs.
Tension means both can be achieved, but pursuing both creates friction,
stress, or requires sacrifice that wouldn't exist if pursuing either alone.

Goal A: {goal_a_title}
Description: {goal_a_description}
Domain: {goal_a_domain}
Priority: {goal_a_priority}

Goal B: {goal_b_title}
Description: {goal_b_description}
Domain: {goal_b_domain}
Priority: {goal_b_priority}

Consider:
- Do they pull attention in different directions?
- Does progress on one create resistance for the other?
- Are there psychological, temporal, or social trade-offs?
- Cross-domain tensions (e.g., work goal vs. health goal) count.

Respond in JSON:
{{
  "has_tension": true/false,
  "confidence": 0.0-1.0,
  "reasoning": "explanation",
  "tension_type": "psychological|temporal|social|emotional|logistical|none",
  "mitigation": "suggested way to reduce the tension, or empty"
}}
"""


def format_duplicate_prompt(a: GoalSnapshot, b: GoalSnapshot) -> str:
    return DUPLICATE_PROMPT.format(
        goal_a_title=a.title,
        goal_a_description=a.description or "(none)",
        goal_a_success=a.success_criteria or "(none)",
        goal_b_title=b.title,
        goal_b_description=b.description or "(none)",
        goal_b_success=b.success_criteria or "(none)",
    )


def format_contradiction_prompt(a: GoalSnapshot, b: GoalSnapshot) -> str:
    return CONTRADICTION_PROMPT.format(
        goal_a_title=a.title,
        goal_a_description=a.description or "(none)",
        goal_a_success=a.success_criteria or "(none)",
        goal_a_domain=a.domain or "(unspecified)",
        goal_b_title=b.title,
        goal_b_description=b.description or "(none)",
        goal_b_success=b.success_criteria or "(none)",
        goal_b_domain=b.domain or "(unspecified)",
    )


def format_tension_prompt(a: GoalSnapshot, b: GoalSnapshot) -> str:
    return TENSION_PROMPT.format(
        goal_a_title=a.title,
        goal_a_description=a.description or "(none)",
        goal_a_domain=a.domain or "(unspecified)",
        goal_a_priority=a.priority,
        goal_b_title=b.title,
        goal_b_description=b.description or "(none)",
        goal_b_domain=b.domain or "(unspecified)",
        goal_b_priority=b.priority,
    )
