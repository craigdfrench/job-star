"""
Job-Star Model Registry
=======================
A registry of available AI models with their capabilities, costs, speed,
and task suitability. Used by the router to select the best model for a given task.

This is a static configuration. Runtime availability/health is tracked separately
by the HealthMonitor (see health.py).
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Set, Dict, Optional


class ModelTier(Enum):
    """Broad capability tier for quick filtering."""
    FAST = "fast"          # Cheap, fast, lower quality (e.g., Haiku, Mini)
    BALANCED = "balanced"  # Good quality, moderate cost (e.g., Sonnet, Flash)
    POWERFUL = "powerful"  # Best quality, higher cost (e.g., Opus, Pro, o1)
    REASONING = "reasoning"  # Extended reasoning models (e.g., o1, o3-mini)


class TaskType(Enum):
    """Types of tasks the router can handle."""
    CODE_GENERATION = "code_generation"
    CODE_REVIEW = "code_review"
    TEXT_SUMMARIZATION = "text_summarization"
    TRANSLATION = "translation"
    CREATIVE_WRITING = "creative_writing"
    ANALYSIS = "analysis"
    REASONING = "reasoning"
    SIMPLE_QA = "simple_qa"
    EXTRACTION = "extraction"
    EMBEDDING = "embedding"
    VISION = "vision"
    TOOL_USE = "tool_use"
    LONG_CONTEXT = "long_context"


@dataclass
class ModelInfo:
    """
    Complete metadata for a single model.
    
    Costs are in USD per 1M tokens (as of early 2025 — update periodically).
    Latency is approximate median time-to-first-token in seconds.
    """
    # Identity
    id: str                          # LiteLLM model identifier (e.g., "anthropic/claude-3-5-sonnet-20241022")
    name: str                        # Human-readable name
    provider: str                    # Provider: anthropic, openai, google, etc.
    tier: ModelTier                  # Broad capability tier
    
    # Capabilities
    context_window: int              # Max input + output tokens
    max_output_tokens: int           # Max output tokens per request
    supports_vision: bool = False
    supports_tool_use: bool = False
    supports_json_mode: bool = False
    supports_parallel_tool_use: bool = False
    
    # Cost (USD per 1M tokens)
    input_cost_per_1m: float = 0.0
    output_cost_per_1m: float = 0.0
    
    # Performance (approximate)
    latency_ttft_seconds: float = 1.0   # Time to first token
    throughput_tokens_per_sec: float = 50.0  # Output tokens per second
    
    # Task suitability scores (0.0 to 1.0 — higher is better)
    task_scores: Dict[TaskType, float] = field(default_factory=dict)
    
    # Operational
    rate_limit_rpm: Optional[int] = None   # Requests per minute limit
    enabled: bool = True                     # Can be disabled without removing from registry
    
    @property
    def blended_cost_per_1m(self) -> float:
        """Approximate cost for a typical 80% input / 20% output mix."""
        return (self.input_cost_per_1m * 0.8) + (self.output_cost_per_1m * 0.2)
    
    @property
    def cost_per_1k_tokens(self) -> float:
        """Blended cost per 1K tokens for quick comparisons."""
        return self.blended_cost_per_1m / 1000.0
    
    def score_for_task(self, task: TaskType) -> float:
        """Get suitability score for a task type. Defaults to 0.5 (neutral)."""
        return self.task_scores.get(task, 0.5)
    
    def can_handle(self, task: TaskType) -> bool:
        """Check if model can handle a given task type (score > 0)."""
        return self.score_for_task(task) > 0.0
    
    def __repr__(self) -> str:
        return f"ModelInfo(id={self.id!r}, tier={self.tier.value}, cost=${self.blended_cost_per_1m:.2f}/1M)"


# ---------------------------------------------------------------------------
# Model Definitions
# ---------------------------------------------------------------------------

_MODELS: Dict[str, ModelInfo] = {}


def _register(model: ModelInfo) -> ModelInfo:
    """Register a model in the registry."""
    _MODELS[model.id] = model
    return model


# --- Anthropic ---

_register(ModelInfo(
    id="anthropic/claude-3-5-sonnet-20241022",
    name="Claude 3.5 Sonnet",
    provider="anthropic",
    tier=ModelTier.BALANCED,
    context_window=200_000,
    max_output_tokens=8_192,
    supports_vision=True,
    supports_tool_use=True,
    supports_json_mode=False,
    supports_parallel_tool_use=True,
    input_cost_per_1m=3.00,
    output_cost_per_1m=15.00,
    latency_ttft_seconds=1.2,
    throughput_tokens_per_sec=60.0,
    rate_limit_rpm=1000,
    task_scores={
        TaskType.CODE_GENERATION: 0.92,
        TaskType.CODE_REVIEW: 0.90,
        TaskType.TEXT_SUMMARIZATION: 0.88,
        TaskType.TRANSLATION: 0.82,
        TaskType.CREATIVE_WRITING: 0.85,
        TaskType.ANALYSIS: 0.88,
        TaskType.REASONING: 0.80,
        TaskType.SIMPLE_QA: 0.85,
        TaskType.EXTRACTION: 0.87,
        TaskType.VISION: 0.85,
        TaskType.TOOL_USE: 0.90,
        TaskType.LONG_CONTEXT: 0.88,
    },
))

_register(ModelInfo(
    id="anthropic/claude-3-5-haiku-20241022",
    name="Claude 3.5 Haiku",
    provider="anthropic",
    tier=ModelTier.FAST,
    context_window=200_000,
    max_output_tokens=8_192,
    supports_vision=True,
    supports_tool_use=True,
    supports_json_mode=False,
    supports_parallel_tool_use=True,
    input_cost_per_1m=0.80,
    output_cost_per_1m=4.00,
    latency_ttft_seconds=0.5,
    throughput_tokens_per_sec=120.0,
    rate_limit_rpm=2000,
    task_scores={
        TaskType.CODE_GENERATION: 0.75,
        TaskType.CODE_REVIEW: 0.72,
        TaskType.TEXT_SUMMARIZATION: 0.80,
        TaskType.TRANSLATION: 0.75,
        TaskType.CREATIVE_WRITING: 0.70,
        TaskType.ANALYSIS: 0.72,
        TaskType.REASONING: 0.60,
        TaskType.SIMPLE_QA: 0.85,
        TaskType.EXTRACTION: 0.80,
        TaskType.VISION: 0.75,
        TaskType.TOOL_USE: 0.78,
        TaskType.LONG_CONTEXT: 0.82,
    },
))

_register(ModelInfo(
    id="anthropic/claude-3-opus-20240229",
    name="Claude 3 Opus",
    provider="anthropic",
    tier=ModelTier.POWERFUL,
    context_window=200_000,
    max_output_tokens=4_096,
    supports_vision=True,
    supports_tool_use=True,
    supports_json_mode=False,
    supports_parallel_tool_use=False,
    input_cost_per_1m=15.00,
    output_cost_per_1m=75.00,
    latency_ttft_seconds=2.5,
    throughput_tokens_per_sec=30.0,
    rate_limit_rpm=500,
    task_scores={
        TaskType.CODE_GENERATION: 0.88,
        TaskType.CODE_REVIEW: 0.92,
        TaskType.TEXT_SUMMARIZATION: 0.90,
        TaskType.TRANSLATION: 0.88,
        TaskType.CREATIVE_WRITING: 0.95,
        TaskType.ANALYSIS: 0.93,
        TaskType.REASONING: 0.90,
        TaskType.SIMPLE_QA: 0.90,
        TaskType.EXTRACTION: 0.88,
        TaskType.VISION: 0.88,
        TaskType.TOOL_USE: 0.85,
        TaskType.LONG_CONTEXT: 0.90,
    },
))

# --- OpenAI ---

_register(ModelInfo(
    id="openai/gpt-4o",
    name="GPT-4o",
    provider="openai",
    tier=ModelTier.BALANCED,
    context_window=128_000,
    max_output_tokens=16_384,
    supports_vision=True,
    supports_tool_use=True,
    supports_json_mode=True,
    supports_parallel_tool_use=True,
    input_cost_per_1m=2.50,
    output_cost_per_1m=10.00,
    latency_ttft_seconds=0.8,
    throughput_tokens_per_sec=80.0,
    rate_limit_rpm=5000,
    task_scores={
        TaskType.CODE_GENERATION: 0.88,
        TaskType.CODE_REVIEW: 0.85,
        TaskType.TEXT_SUMMARIZATION: 0.85,
        TaskType.TRANSLATION: 0.85,
        TaskType.CREATIVE_WRITING: 0.85,
        TaskType.ANALYSIS: 0.87,
        TaskType.REASONING: 0.82,
        TaskType.SIMPLE_QA: 0.88,
        TaskType.EXTRACTION: 0.86,
        TaskType.VISION: 0.88,
        TaskType.TOOL_USE: 0.88,
        TaskType.LONG_CONTEXT: 0.80,
    },
))

_register(ModelInfo(
    id="openai/gpt-4o-mini",
    name="GPT-4o Mini",
    provider="openai",
    tier=ModelTier.FAST,
    context_window=128_000,
    max_output_tokens=16_384,
    supports_vision=True,
    supports_tool_use=True,
    supports_json_mode=True,
    supports_parallel_tool_use=True,
    input_cost_per_1m=0.15,
    output_cost_per_1m=0.60,
    latency_ttft_seconds=0.4,
    throughput_tokens_per_sec=150.0,
    rate_limit_rpm=10000,
    task_scores={
        TaskType.CODE_GENERATION: 0.70,
        TaskType.CODE_REVIEW: 0.65,
        TaskType.TEXT_SUMMARIZATION: 0.78,
        TaskType.TRANSLATION: 0.72,
        TaskType.CREATIVE_WRITING: 0.68,
        TaskType.ANALYSIS: 0.70,
        TaskType.REASONING: 0.55,
        TaskType.SIMPLE_QA: 0.82,
        TaskType.EXTRACTION: 0.78,
        TaskType.VISION: 0.72,
        TaskType.TOOL_USE: 0.75,
        TaskType.LONG_CONTEXT: 0.75,
    },
))

_register(ModelInfo(
    id="openai/o3-mini",
    name="o3-mini",
    provider="openai",
    tier=ModelTier.REASONING,
    context_window=200_000,
    max_output_tokens=100_000,
    supports_vision=False,
    supports_tool_use=True,
    supports_json_mode=False,
    supports_parallel_tool_use=False,
    input_cost_per_1m=1.10,
    output_cost_per_1m=4.40,
    latency_ttft_seconds=5.0,
    throughput_tokens_per_sec=40.0,
    rate_limit_rpm=500,
    task_scores={
        TaskType.CODE_GENERATION: 0.90,
        TaskType.CODE_REVIEW: 0.88,
        TaskType.TEXT_SUMMARIZATION: 0.70,
        TaskType.TRANSLATION: 0.65,
        TaskType.CREATIVE_WRITING: 0.60,
        TaskType.ANALYSIS: 0.92,
        TaskType.REASONING: 0.95,
        TaskType.SIMPLE_QA: 0.75,
        TaskType.EXTRACTION: 0.72,
        TaskType.VISION: 0.0,
        TaskType.TOOL_USE: 0.80,
        TaskType.LONG_CONTEXT: 0.85,
    },
))

# --- Google ---

_register(ModelInfo(
    id="gemini/gemini-2.0-flash",
    name="Gemini 2.0 Flash",
    provider="google",
    tier=ModelTier.FAST,
    context_window=1_000_000,
    max_output_tokens=8_192,
    supports_vision=True,
    supports_tool_use=True,
    supports_json_mode=True,
    supports_parallel_tool_use=True,
    input_cost_per_1m=0.10,
    output_cost_per_1m=0.40,
    latency_ttft_seconds=0.6,
    throughput_tokens_per_sec=200.0,
    rate_limit_rpm=10000,
    task_scores={
        TaskType.CODE_GENERATION: 0.72,
        TaskType.CODE_REVIEW: 0.68,
        TaskType.TEXT_SUMMARIZATION: 0.80,
        TaskType.TRANSLATION: 0.78,
        TaskType.CREATIVE_WRITING: 0.70,
        TaskType.ANALYSIS: 0.72,
        TaskType.REASONING: 0.60,
        TaskType.SIMPLE_QA: 0.82,
        TaskType.EXTRACTION: 0.80,
        TaskType.VISION: 0.80,
        TaskType.TOOL_USE: 0.78,
        TaskType.LONG_CONTEXT: 0.95,  # 1M context is its superpower
    },
))

_register(ModelInfo(
    id="gemini/gemini-2.0-flash-thinking",
    name="Gemini 2.0 Flash Thinking",
    provider="google",
    tier=ModelTier.REASONING,
    context_window=1_000_000,
    max_output_tokens=8_192,
    supports_vision=True,
    supports_tool_use=False,
    supports_json_mode=False,
    supports_parallel_tool_use=False,
    input_cost_per_1m=0.10,
    output_cost_per_1m=0.40,
    latency_ttft_seconds=2.0,
    throughput_tokens_per_sec=100.0,
    rate_limit_rpm=1000,
    task_scores={
        TaskType.CODE_GENERATION: 0.82,
        TaskType.CODE_REVIEW: 0.80,
        TaskType.TEXT_SUMMARIZATION: 0.75,
        TaskType.TRANSLATION: 0.70,
        TaskType.CREATIVE_WRITING: 0.65,
        TaskType.ANALYSIS: 0.88,
        TaskType.REASONING: 0.90,
        TaskType.SIMPLE_QA: 0.78,
        TaskType.EXTRACTION: 0.75,
        TaskType.VISION: 0.78,
        TaskType.TOOL_USE: 0.0,
        TaskType.LONG_CONTEXT: 0.92,
    },
))

# --- Meta (via Together/Groq) ---

_register(ModelInfo(
    id="groq/llama-3.3-70b-versatile",
    name="Llama 3.3 70B (Groq)",
    provider="groq",
    tier=ModelTier.FAST,
    context_window=128_000,
    max_output_tokens=32_768,
    supports_vision=False,
    supports_tool_use=True,
    supports_json_mode=True,
    supports_parallel_tool_use=False,
    input_cost_per_1m=0.59,
    output_cost_per_1m=0.79,
    latency_ttft_seconds=0.2,
    throughput_tokens_per_sec=500.0,  # Groq is extremely fast
    rate_limit_rpm=1200,
    task_scores={
        TaskType.CODE_GENERATION: 0.78,
        TaskType.CODE_REVIEW: 0.75,
        TaskType.TEXT_SUMMARIZATION: 0.80,
        TaskType.TRANSLATION: 0.78,
        TaskType.CREATIVE_WRITING: 0.78,
        TaskType.ANALYSIS: 0.76,
        TaskType.REASONING: 0.70,
        TaskType.SIMPLE_QA: 0.82,
        TaskType.EXTRACTION: 0.78,
        TaskType.VISION: 0.0,
        TaskType.TOOL_USE: 0.75,
        TaskType.LONG_CONTEXT: 0.75,
    },
))

# --- DeepSeek ---

_register(ModelInfo(
    id="deepseek/deepseek-chat",
    name="DeepSeek V3",
    provider="deepseek",
    tier=ModelTier.BALANCED,
    context_window=64_000,
    max_output_tokens=8_192,
    supports_vision=False,
    supports_tool_use=True,
    supports_json_mode=True,
    supports_parallel_tool_use=False,
    input_cost_per_1m=0.27,
    output_cost_per_1m=1.10,
    latency_ttft_seconds=1.0,
    throughput_tokens_per_sec=60.0,
    rate_limit_rpm=1000,
    task_scores={
        TaskType.CODE_GENERATION: 0.88,
        TaskType.CODE_REVIEW: 0.85,
        TaskType.TEXT_SUMMARIZATION: 0.82,
        TaskType.TRANSLATION: 0.80,
        TaskType.CREATIVE_WRITING: 0.78,
        TaskType.ANALYSIS: 0.82,
        TaskType.REASONING: 0.80,
        TaskType.SIMPLE_QA: 0.82,
        TaskType.EXTRACTION: 0.82,
        TaskType.VISION: 0.0,
        TaskType.TOOL_USE: 0.82,
        TaskType.LONG_CONTEXT: 0.65,
    },
))

_register(ModelInfo(
    id="deepseek/deepseek-reasoner",
    name="DeepSeek R1",
    provider="deepseek",
    tier=ModelTier.REASONING,
    context_window=64_000,
    max_output_tokens=8_192,
    supports_vision=False,
    supports_tool_use=False,
    supports_json_mode=False,
    supports_parallel_tool_use=False,
    input_cost_per_1m=0.55,
    output_cost_per_1m=2.19,
    latency_ttft_seconds=8.0,
    throughput_tokens_per_sec=30.0,
    rate_limit_rpm=200,
    task_scores={
        TaskType.CODE_GENERATION: 0.90,
        TaskType.CODE_REVIEW: 0.88,
        TaskType.TEXT_SUMMARIZATION: 0.65,
        TaskType.TRANSLATION: 0.60,
        TaskType.CREATIVE_WRITING: 0.55,
        TaskType.ANALYSIS: 0.92,
        TaskType.REASONING: 0.95,
        TaskType.SIMPLE_QA: 0.70,
        TaskType.EXTRACTION: 0.68,
        TaskType.VISION: 0.0,
        TaskType.TOOL_USE: 0.0,
        TaskType.LONG_CONTEXT: 0.60,
    },
))


# ---------------------------------------------------------------------------
# Registry Access Functions
# ---------------------------------------------------------------------------

def get_model(model_id: str) -> Optional[ModelInfo]:
    """Get a single model by its LiteLLM ID. Returns None if not found."""
    return _MODELS.get(model_id)


def get_all_models() -> Dict[str, ModelInfo]:
    """Get all registered models."""
    return dict(_MODELS)


def get_enabled_models() -> Dict[str, ModelInfo]:
    """Get all enabled models."""
    return {k: v for k, v in _MODELS.items() if v.enabled}


def get_models_by_tier(tier: ModelTier) -> Dict[str, ModelInfo]:
    """Get all enabled models in a given tier."""
    return {k: v for k, v in _MODELS.items() if v.tier == tier and v.enabled}


def get_models_for_task(task: TaskType, enabled_only: bool = True) -> Dict[str, ModelInfo]:
    """Get all models that can handle a given task type."""
    pool = get_enabled_models() if enabled_only else get_all_models()
    return {k: v for k, v in pool.items() if v.can_handle(task)}


def get_cheapest_model_for_task(task: TaskType) -> Optional[ModelInfo]:
    """Get the cheapest enabled model that can handle the task."""
    candidates = get_models_for_task(task)
    if not candidates:
        return None
    return min(candidates.values(), key=lambda m: m.blended_cost_per_1m)


def get_fastest_model_for_task(task: TaskType) -> Optional[ModelInfo]:
    """Get the fastest (lowest TTFT) enabled model that can handle the task."""
    candidates = get_models_for_task(task)
    if not candidates:
        return None
    return min(candidates.values(), key=lambda m: m.latency_ttft_seconds)


def get_best_model_for_task(task: TaskType) -> Optional[ModelInfo]:
    """Get the highest-scoring enabled model for a task (ignoring cost)."""
    candidates = get_models_for_task(task)
    if not candidates:
        return None
    return max(candidates.values(), key=lambda m: m.score_for_task(task))


def set_model_enabled(model_id: str, enabled: bool) -> bool:
    """Enable or disable a model at runtime. Returns True if model was found."""
    model = _MODELS.get(model_id)
    if model is None:
        return False
    model.enabled = enabled
    return True


def list_models_summary() -> str:
    """Print a human-readable summary of all registered models."""
    lines = []
    lines.append(f"{'Model ID':<45} {'Tier':<10} {'In$/1M':<8} {'Out$/1M':<8} {'TTFT':<6} {'Ctx':<10}")
    lines.append("-" * 95)
    for model in sorted(_MODELS.values(), key=lambda m: (m.tier.value, m.blended_cost_per_1m)):
        lines.append(
            f"{model.id:<45} {model.tier.value:<10} "
            f"{model.input_cost_per_1m:<8.2f} {model.output_cost_per_1m:<8.2f} "
            f"{model.latency_ttft_seconds:<6.1f} {model.context_window:<10,}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "ModelTier",
    "TaskType",
    "ModelInfo",
    "get_model",
    "get_all_models",
    "get_enabled_models",
    "get_models_by_tier",
    "get_models_for_task",
    "get_cheapest_model_for_task",
    "get_fastest_model_for_task",
    "get_best_model_for_task",
    "set_model_enabled",
    "list_models_summary",
]
