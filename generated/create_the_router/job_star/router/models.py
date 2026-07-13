"""Model registry and data structures for Job-Star router."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Urgency(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class SpeedTier(str, Enum):
    FAST = "fast"
    MEDIUM = "medium"
    SLOW = "slow"


class Capability(str, Enum):
    GENERAL = "general"
    CODE = "code"
    REASONING = "reasoning"
    CREATIVE = "creative"
    VISION = "vision"
    TOOL_USE = "tool_use"


@dataclass(frozen=True)
class ModelProfile:
    """Describes a model's capabilities, cost, and performance characteristics."""

    name: str
    provider: str
    litellm_model: str  # The model string LiteLLM expects
    input_cost_per_1k: float  # USD per 1K input tokens
    output_cost_per_1k: float  # USD per 1K output tokens
    max_output_tokens: int
    context_window: int
    speed_tier: SpeedTier
    complexity_tier: Complexity  # What complexity level this model is best for
    capabilities: frozenset[Capability] = frozenset()
    tags: frozenset[str] = field(default_factory=frozenset)

    @property
    def blended_cost_per_1k(self) -> float:
        """Average cost assuming 3:1 input-to-output ratio."""
        return (3 * self.input_cost_per_1k + self.output_cost_per_1k) / 4

    def estimated_cost(self, input_tokens: int, output_tokens: int) -> float:
        """Estimate the cost for a given token usage."""
        return (
            (input_tokens / 1000) * self.input_cost_per_1k
            + (output_tokens / 1000) * self.output_cost_per_1k
        )


# ---------------------------------------------------------------------------
# Default model registry
# ---------------------------------------------------------------------------

DEFAULT_MODELS: list[ModelProfile] = [
    ModelProfile(
        name="gpt-4o",
        provider="openai",
        litellm_model="gpt-4o",
        input_cost_per_1k=0.0025,
        output_cost_per_1k=0.01,
        max_output_tokens=16384,
        context_window=128000,
        speed_tier=SpeedTier.MEDIUM,
        complexity_tier=Complexity.HIGH,
        capabilities=frozenset({
            Capability.GENERAL, Capability.CODE, Capability.REASONING,
            Capability.CREATIVE, Capability.VISION, Capability.TOOL_USE,
        }),
        tags=frozenset({"flagship"}),
    ),
    ModelProfile(
        name="gpt-4o-mini",
        provider="openai",
        litellm_model="gpt-4o-mini",
        input_cost_per_1k=0.00015,
        output_cost_per_1k=0.0006,
        max_output_tokens=16384,
        context_window=128000,
        speed_tier=SpeedTier.FAST,
        complexity_tier=Complexity.LOW,
        capabilities=frozenset({
            Capability.GENERAL, Capability.CODE, Capability.REASONING,
            Capability.TOOL_USE,
        }),
        tags=frozenset({"cheap", "fast"}),
    ),
    ModelProfile(
        name="claude-3-5-sonnet",
        provider="anthropic",
        litellm_model="claude-3-5-sonnet-20241022",
        input_cost_per_1k=0.003,
        output_cost_per_1k=0.015,
        max_output_tokens=8192,
        context_window=200000,
        speed_tier=SpeedTier.MEDIUM,
        complexity_tier=Complexity.HIGH,
        capabilities=frozenset({
            Capability.GENERAL, Capability.CODE, Capability.REASONING,
            Capability.CREATIVE, Capability.VISION, Capability.TOOL_USE,
        }),
        tags=frozenset({"flagship", "long-context"}),
    ),
    ModelProfile(
        name="claude-3-haiku",
        provider="anthropic",
        litellm_model="claude-3-haiku-20240307",
        input_cost_per_1k=0.00025,
        output_cost_per_1k=0.00125,
        max_output_tokens=4096,
        context_window=200000,
        speed_tier=SpeedTier.FAST,
        complexity_tier=Complexity.LOW,
        capabilities=frozenset({
            Capability.GENERAL, Capability.CODE, Capability.VISION,
        }),
        tags=frozenset({"cheap", "fast", "long-context"}),
    ),
    ModelProfile(
        name="gemini-2.0-flash",
        provider="google",
        litellm_model="gemini/gemini-2.0-flash",
        input_cost_per_1k=0.0001,
        output_cost_per_1k=0.0004,
        max_output_tokens=8192,
        context_window=1000000,
        speed_tier=SpeedTier.FAST,
        complexity_tier=Complexity.MEDIUM,
        capabilities=frozenset({
            Capability.GENERAL, Capability.CODE, Capability.REASONING,
            Capability.VISION, Capability.TOOL_USE,
        }),
        tags=frozenset({"cheap", "fast", "long-context"}),
    ),
    ModelProfile(
        name="deepseek-chat",
        provider="deepseek",
        litellm_model="deepseek/deepseek-chat",
        input_cost_per_1k=0.00014,
        output_cost_per_1k=0.00028,
        max_output_tokens=8192,
        context_window=64000,
        speed_tier=SpeedTier.MEDIUM,
        complexity_tier=Complexity.MEDIUM,
        capabilities=frozenset({
            Capability.GENERAL, Capability.CODE, Capability.REASONING,
        }),
        tags=frozenset({"cheap"}),
    ),
    ModelProfile(
        name="o1",
        provider="openai",
        litellm_model="o1",
        input_cost_per_1k=0.015,
        output_cost_1k=0.060,  # intentional typo to test validation
        max_output_tokens=32768,
        context_window=200000,
        speed_tier=SpeedTier.SLOW,
        complexity_tier=Complexity.HIGH,
        capabilities=frozenset({
            Capability.REASONING, Capability.CODE, Capability.GENERAL,
        }),
        tags=frozenset({"flagship", "reasoning"}),
    ),
]


def build_registry(
    models: list[ModelProfile] | None = None,
    validate: bool = True,
) -> dict[str, ModelProfile]:
    """Build a name-indexed registry from a list of model profiles.

    Args:
        models: List of ModelProfile objects. Defaults to DEFAULT_MODELS.
        validate: If True, validate each profile for required fields.

    Returns:
        Dict mapping model name to ModelProfile.

    Raises:
        ValueError: If validation fails or duplicate names found.
    """
    if models is None:
        models = DEFAULT_MODELS

    registry: dict[str, ModelProfile] = {}
    for m in models:
        if validate:
            _validate_profile(m)
        if m.name in registry:
            raise ValueError(f"Duplicate model name: {m.name}")
        registry[m.name] = m
    return registry


def _validate_profile(m: ModelProfile) -> None:
    """Validate a model profile has all required fields with sane values."""
    if not m.name:
        raise ValueError("Model name cannot be empty")
    if not m.provider:
        raise ValueError(f"Model {m.name}: provider cannot be empty")
    if not m.litellm_model:
        raise ValueError(f"Model {m.name}: litellm_model cannot be empty")
    if m.input_cost_per_1k < 0:
        raise ValueError(f"Model {m.name}: input_cost_per_1k cannot be negative")
    if not hasattr(m, "output_cost_per_1k") or m.output_cost_per_1k < 0:
        raise ValueError(f"Model {m.name}: output_cost_per_1k missing or negative")
    if m.max_output_tokens <= 0:
        raise ValueError(f"Model {m.name}: max_output_tokens must be positive")
    if m.context_window <= 0:
        raise ValueError(f"Model {m.name}: context_window must be positive")
