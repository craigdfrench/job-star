"""
Model registry and fallback chain configuration for Job-Star router.

Each task profile maps to an ordered list of (model, priority) candidates.
The router tries them in order, applying fallback logic on failures.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Tuple


class Complexity(str, Enum):
    SIMPLE = "simple"       # e.g., formatting, short summaries
    MODERATE = "moderate"   # e.g., code review, analysis
    COMPLEX = "complex"     # e.g., architecture design, deep reasoning


class Urgency(str, Enum):
    NOW = "now"         # interactive, <5s budget
    SOON = "soon"       # near-term, <30s budget
    LATER = "later"     # batch, minutes OK


class CostTier(str, Enum):
    LOW = "low"         # prefer cheapest
    STANDARD = "standard"  # balanced
    PREMIUM = "premium"    # quality first


@dataclass
class ModelSpec:
    """LiteLLM model identifier and metadata."""
    litellm_name: str          # e.g., "gpt-4o", "claude-3-5-sonnet-20241022"
    provider: str              # e.g., "openai", "anthropic"
    cost_per_1k_input: float   # USD
    cost_per_1k_output: float  # USD
    avg_latency_ms: int        # rough estimate for routing decisions
    context_window: int
    tags: List[str] = field(default_factory=list)


# Registry of available models with metadata
MODEL_REGISTRY: Dict[str, ModelSpec] = {
    "gpt-4o": ModelSpec(
        litellm_name="gpt-4o",
        provider="openai",
        cost_per_1k_input=0.0025,
        cost_per_1k_output=0.010,
        avg_latency_ms=2500,
        context_window=128000,
        tags=["complex", "premium"],
    ),
    "gpt-4o-mini": ModelSpec(
        litellm_name="gpt-4o-mini",
        provider="openai",
        cost_per_1k_input=0.00015,
        cost_per_1k_output=0.0006,
        avg_latency_ms=1200,
        context_window=128000,
        tags=["simple", "low_cost"],
    ),
    "claude-3-5-sonnet": ModelSpec(
        litellm_name="claude-3-5-sonnet-20241022",
        provider="anthropic",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
        avg_latency_ms=3000,
        context_window=200000,
        tags=["complex", "long_context"],
    ),
    "claude-3-haiku": ModelSpec(
        litellm_name="claude-3-haiku-20240307",
        provider="anthropic",
        cost_per_1k_input=0.00025,
        cost_per_1k_output=0.00125,
        avg_latency_ms=1000,
        context_window=200000,
        tags=["simple", "low_cost", "fast"],
    ),
    "gemini-1.5-flash": ModelSpec(
        litellm_name="gemini/gemini-1.5-flash",
        provider="google",
        cost_per_1k_input=0.000075,
        cost_per_1k_output=0.0003,
        avg_latency_ms=1500,
        context_window=1000000,
        tags=["simple", "low_cost", "long_context"],
    ),
    "gemini-1.5-pro": ModelSpec(
        litellm_name="gemini/gemini-1.5-pro",
        provider="google",
        cost_per_1k_input=0.00125,
        cost_per_1k_output=0.005,
        avg_latency_ms=3500,
        context_window=1000000,
        tags=["complex", "long_context"],
    ),
}


# Fallback chains keyed by (complexity, urgency, cost_tier).
# First entry is primary; subsequent entries are fallbacks in priority order.
# Cross-provider fallback ensures we don't fail just because one provider is down.
FALLBACK_CHAINS: Dict[Tuple[Complexity, Urgency, CostTier], List[str]] = {
    # Complex tasks — quality first, then speed, then cost
    (Complexity.COMPLEX, Urgency.NOW, CostTier.PREMIUM): [
        "gpt-4o", "claude-3-5-sonnet", "gemini-1.5-pro",
    ],
    (Complexity.COMPLEX, Urgency.SOON, CostTier.PREMIUM): [
        "claude-3-5-sonnet", "gpt-4o", "gemini-1.5-pro",
    ],
    (Complexity.COMPLEX, Urgency.LATER, CostTier.STANDARD): [
        "gemini-1.5-pro", "gpt-4o", "claude-3-5-sonnet",
    ],
    (Complexity.COMPLEX, Urgency.LATER, CostTier.LOW): [
        "gemini-1.5-pro", "gpt-4o-mini", "claude-3-haiku",
    ],

    # Moderate tasks — balanced
    (Complexity.MODERATE, Urgency.NOW, CostTier.STANDARD): [
        "gpt-4o-mini", "claude-3-haiku", "gemini-1.5-flash",
    ],
    (Complexity.MODERATE, Urgency.SOON, CostTier.STANDARD): [
        "gpt-4o-mini", "gemini-1.5-flash", "claude-3-haiku",
    ],
    (Complexity.MODERATE, Urgency.LATER, CostTier.LOW): [
        "gemini-1.5-flash", "gpt-4o-mini", "claude-3-haiku",
    ],
    (Complexity.MODERATE, Urgency.SOON, CostTier.PREMIUM): [
        "gpt-4o", "claude-3-5-sonnet", "gemini-1.5-pro",
    ],

    # Simple tasks — cost/speed first
    (Complexity.SIMPLE, Urgency.NOW, CostTier.LOW): [
        "gpt-4o-mini", "gemini-1.5-flash", "claude-3-haiku",
    ],
    (Complexity.SIMPLE, Urgency.SOON, CostTier.LOW): [
        "gemini-1.5-flash", "gpt-4o-mini", "claude-3-haiku",
    ],
    (Complexity.SIMPLE, Urgency.LATER, CostTier.LOW): [
        "gemini-1.5-flash", "claude-3-haiku", "gpt-4o-mini",
    ],
    (Complexity.SIMPLE, Urgency.NOW, CostTier.STANDARD): [
        "gpt-4o-mini", "claude-3-haiku", "gemini-1.5-flash",
    ],
}


def get_fallback_chain(
    complexity: Complexity,
    urgency: Urgency,
    cost_tier: CostTier,
) -> List[str]:
    """
    Return ordered list of model names for the given profile.
    Falls back to a sensible default if exact match not found.
    """
    key = (complexity, urgency, cost_tier)
    if key in FALLBACK_CHAINS:
        return FALLBACK_CHAINS[key]

    # Heuristic default: try to find closest match by relaxing cost tier
    for tier in [cost_tier, CostTier.STANDARD, CostTier.LOW, CostTier.PREMIUM]:
        relaxed_key = (complexity, urgency, tier)
        if relaxed_key in FALLBACK_CHAINS:
            return FALLBACK_CHAINS[relaxed_key]

    # Absolute fallback
    return ["gpt-4o-mini", "gemini-1.5-flash", "claude-3-haiku"]
