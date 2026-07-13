"""Core data types for the routing engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Complexity(str, Enum):
    """How cognitively demanding the task is."""
    TRIVIAL = "trivial"       # e.g., format conversion, simple extraction
    SIMPLE = "simple"         # e.g., summarization, basic Q&A
    MODERATE = "moderate"     # e.g., multi-step reasoning, code review
    COMPLEX = "complex"       # e.g., architecture design, nuanced analysis
    FRONTIER = "frontier"     # e.g., novel research, hard math proofs


class Urgency(str, Enum):
    """How quickly the result is needed."""
    INSTANT = "instant"       # < 2s acceptable (chat UI, interactive)
    SOON = "soon"             # < 30s acceptable (background jobs)
    BATCH = "batch"           # minutes-to-hours fine (overnight runs)
    FLEXIBLE = "flexible"     # no deadline (best quality for cost)


@dataclass
class TaskProfile:
    """Everything the router needs to know about a task."""

    prompt: str = ""
    complexity: Complexity = Complexity.SIMPLE
    urgency: Urgency = Urgency.SOON
    max_cost_usd: Optional[float] = None        # hard budget cap per call
    context_tokens: int = 0                     # estimated input length
    max_output_tokens: int = 4096
    requires_tools: bool = False                # function calling / agents
    requires_vision: bool = False
    requires_json_mode: bool = False
    preferred_providers: list[str] = field(default_factory=list)
    excluded_models: list[str] = field(default_factory=list)
    quality_weight: float = 1.0                 # user can override defaults
    cost_weight: float = 1.0
    speed_weight: float = 1.0

    def __post_init__(self) -> None:
        if isinstance(self.complexity, str):
            self.complexity = Complexity(self.complexity)
        if isinstance(self.urgency, str):
            self.urgency = Urgency(self.urgency)


@dataclass
class ScoreBreakdown:
    """Transparent scoring details for a single candidate model."""
    model: str
    capability_score: float
    speed_score: float
    cost_score: float
    availability_score: float
    composite: float
    notes: list[str] = field(default_factory=list)


@dataclass
class RoutingDecision:
    """The router's verdict."""
    selected_model: str
    litellm_model: str          # the string LiteLLM expects, e.g. "openai/gpt-4o"
    scores: list[ScoreBreakdown]
    fallback_chain: list[str]
    estimated_cost_usd: float
    estimated_latency_s: float
    rationale: str
    task_profile: TaskProfile
