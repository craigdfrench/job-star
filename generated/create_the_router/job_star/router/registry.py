"""Model registry — the catalog of models Job-Star can route to."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import litellm


@dataclass
class ModelEntry:
    """A single model the router can select."""

    # Identity
    name: str                           # Job-Star alias, e.g. "gpt-4o"
    litellm_model: str                  # LiteLLM string, e.g. "openai/gpt-4o"
    provider: str                       # "openai", "anthropic", "groq", etc.

    # Capability profile (0.0 – 1.0)
    capability_trivial: float = 1.0
    capability_simple: float = 1.0
    capability_moderate: float = 0.8
    capability_complex: float = 0.6
    capability_frontier: float = 0.3

    # Modalities
    supports_tools: bool = False
    supports_vision: bool = False
    supports_json_mode: bool = False
    max_context_tokens: int = 8192
    max_output_tokens: int = 4096

    # Performance estimates
    typical_latency_s: float = 2.0      # median first-token + completion for a typical task
    throughput_tokens_s: float = 50.0   # output tokens per second

    # Cost (USD per 1M tokens)
    cost_input_per_1m: float = 0.0
    cost_output_per_1m: float = 0.0

    # Reliability
    availability_weight: float = 1.0    # multiplier, can be degraded at runtime

    def capability_for(self, complexity) -> float:
        return {
            "trivial": self.capability_trivial,
            "simple": self.capability_simple,
            "moderate": self.capability_moderate,
            "complex": self.capability_complex,
            "frontier": self.capability_frontier,
        }[complexity.value]

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        return (
            self.cost_input_per_1m * (input_tokens / 1_000_000)
            + self.cost_output_per_1m * (output_tokens / 1_000_000)
        )

    def estimate_latency(self, input_tokens: int, output_tokens: int) -> float:
        # Rough: fixed overhead + output / throughput
        return self.typical_latency_s + (output_tokens / max(self.throughput_tokens_s, 1))


class ModelRegistry:
    """Holds the catalog of routable models."""

    def __init__(self) -> None:
        self._models: dict[str, ModelEntry] = {}

    def register(self, entry: ModelEntry) -> None:
        self._models[entry.name] = entry

    def get(self, name: str) -> Optional[ModelEntry]:
        return self._models.get(name)

    def all_models(self) -> list[ModelEntry]:
        return list(self._models.values())

    def remove(self, name: str) -> None:
        self._models.pop(name, None)


def _cost_from_litellm(model_id: str) -> tuple[float, float]:
    """Try to pull pricing from LiteLLM's model_cost dict. Fallback to 0."""
    try:
        info = litellm.get_model_info(model_id)
        inp = info.get("input_cost_per_token", 0) * 1_000_000
        out = info.get("output_cost_per_token", 0) * 1_000_000
        return float(inp), float(out)
    except Exception:
        return 0.0, 0.0


def default_registry() -> ModelRegistry:
    """A sensible starting catalog. Pricing is enriched from LiteLLM when available."""
    reg = ModelRegistry()

    # --- OpenAI ---
    reg.register(ModelEntry(
        name="gpt-4o",
        litellm_model="openai/gpt-4o",
        provider="openai",
        capability_trivial=1.0, capability_simple=1.0,
        capability_moderate=0.95, capability_complex=0.9, capability_frontier=0.8,
        supports_tools=True, supports_vision=True, supports_json_mode=True,
        max_context_tokens=128_000, max_output_tokens=16_384,
        typical_latency_s=1.5, throughput_tokens_s=80.0,
        *_cost_from_litellm("gpt-4o"),
    ))
    reg.register(ModelEntry(
        name="gpt-4o-mini",
        litellm_model="openai/gpt-4o-mini",
        provider="openai",
        capability_trivial=1.0, capability_simple=0.95,
        capability_moderate=0.7, capability_complex=0.45, capability_frontier=0.2,
        supports_tools=True, supports_vision=True, supports_json_mode=True,
        max_context_tokens=128_000, max_output_tokens=16_384,
        typical_latency_s=0.8, throughput_tokens_s=120.0,
        *_cost_from_litellm("gpt-4o-mini"),
    ))

    # --- Anthropic ---
    reg.register(ModelEntry(
        name="claude-3-5-sonnet",
        litellm_model="anthropic/claude-3-5-sonnet-20240620",
        provider="anthropic",
        capability_trivial=1.0, capability_simple=1.0,
        capability_moderate=0.97, capability_complex=0.93, capability_frontier=0.85,
        supports_tools=True, supports_vision=True, supports_json_mode=False,
        max_context_tokens=200_000, max_output_tokens=8192,
        typical_latency_s=1.8, throughput_tokens_s=70.0,
        *_cost_from_litellm("claude-3-5-sonnet-20240620"),
    ))
    reg.register(ModelEntry(
        name="claude-3-haiku",
        litellm_model="anthropic/claude-3-haiku-20240307",
        provider="anthropic",
        capability_trivial=1.0, capability_simple=0.9,
        capability_moderate=0.65, capability_complex=0.4, capability_frontier=0.15,
        supports_tools=True, supports_vision=True, supports_json_mode=False,
        max_context_tokens=200_000, max_output_tokens=4096,
        typical_latency_s=0.6, throughput_tokens_s=150.0,
        *_cost_from_litellm("claude-3-haiku-20240307"),
    ))

    # --- Groq (fast inference) ---
    reg.register(ModelEntry(
        name="llama-3.3-70b-groq",
        litellm_model="groq/llama-3.3-70b-versatile",
        provider="groq",
        capability_trivial=1.0, capability_simple=0.9,
        capability_moderate=0.75, capability_complex=0.55, capability_frontier=0.25,
        supports_tools=True, supports_vision=False, supports_json_mode=True,
        max_context_tokens=128_000, max_output_tokens=8192,
        typical_latency_s=0.3, throughput_tokens_s=500.0,
        *_cost_from_litellm("groq/llama-3.3-70b-versatile"),
    ))

    # --- Gemini ---
    reg.register(ModelEntry(
        name="gemini-2.0-flash",
        litellm_model="gemini/gemini-2.0-flash",
        provider="gemini",
        capability_trivial=1.0, capability_simple=0.95,
        capability_moderate=0.8, capability_complex=0.6, capability_frontier=0.35,
        supports_tools=True, supports_vision=True, supports_json_mode=True,
        max_context_tokens=1_000_000, max_output_tokens=8192,
        typical_latency_s=1.0, throughput_tokens_s=200.0,
        *_cost_from_litellm("gemini-2.0-flash"),
    ))

    return reg
