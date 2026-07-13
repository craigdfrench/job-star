"""Test package for Job-Star router."""


// --- DUPLICATE BLOCK ---

"""Shared fixtures for router tests."""
import pytest

from router.registry import ModelRegistry
from router.strategy import RoutingStrategy


# ---------------------------------------------------------------------------
# Model catalog used across all router tests
# ---------------------------------------------------------------------------
# Each entry: (name, provider, cost_per_1k_tokens, capability, speed, available)
# capability: 1-10 (higher = can handle harder tasks)
# speed:      1-10 (higher = faster latency)
DEFAULT_MODELS = [
    # Cheap / fast tier
    ("gpt-4o-mini", "openai",   0.00015, 5, 9, True),
    ("claude-3-haiku", "anthropic", 0.00025, 6, 8, True),
    ("gemini-1.5-flash", "gemini",  0.00010, 5, 9, True),
    # Mid tier
    ("gpt-4o", "openai",     0.005,  8, 6, True),
    ("claude-3.5-sonnet", "anthropic", 0.003, 9, 6, True),
    # Premium tier
    ("claude-3-opus", "anthropic",  0.015, 10, 3, True),
    ("gpt-4-turbo", "openai",    0.010,  9, 5, True),
]


@pytest.fixture
def registry():
    """A fresh registry populated with the default catalog."""
    reg = ModelRegistry()
    for name, provider, cost, cap, speed, avail in DEFAULT_MODELS:
        reg.register(
            name=name,
            provider=provider,
            cost_per_1k_tokens=cost,
            capability_score=cap,
            speed_score=speed,
            available=avail,
        )
    return reg


@pytest.fixture
def strategy(registry):
    return RoutingStrategy(registry=registry)


// --- DUPLICATE BLOCK ---

"""Test package for Job-Star router."""


// --- DUPLICATE BLOCK ---

"""Shared fixtures for router tests."""
import pytest

from router.registry import ModelRegistry
from router.strategy import RoutingStrategy


# ---------------------------------------------------------------------------
# Model catalog used across all router tests
# ---------------------------------------------------------------------------
# Each entry: (name, provider, cost_per_1k_tokens, capability, speed, available)
# capability: 1-10 (higher = can handle harder tasks)
# speed:      1-10 (higher = faster latency)
DEFAULT_MODELS = [
    # Cheap / fast tier
    ("gpt-4o-mini", "openai",   0.00015, 5, 9, True),
    ("claude-3-haiku", "anthropic", 0.00025, 6, 8, True),
    ("gemini-1.5-flash", "gemini",  0.00010, 5, 9, True),
    # Mid tier
    ("gpt-4o", "openai",     0.005,  8, 6, True),
    ("claude-3.5-sonnet", "anthropic", 0.003, 9, 6, True),
    # Premium tier
    ("claude-3-opus", "anthropic",  0.015, 10, 3, True),
    ("gpt-4-turbo", "openai",    0.010,  9, 5, True),
]


@pytest.fixture
def registry():
    """A fresh registry populated with the default catalog."""
    reg = ModelRegistry()
    for name, provider, cost, cap, speed, avail in DEFAULT_MODELS:
        reg.register(
            name=name,
            provider=provider,
            cost_per_1k_tokens=cost,
            capability_score=cap,
            speed_score=speed,
            available=avail,
        )
    return reg


@pytest.fixture
def strategy(registry):
    return RoutingStrategy(registry=registry)
