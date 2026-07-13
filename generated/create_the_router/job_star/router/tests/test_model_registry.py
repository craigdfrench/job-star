"""Tests for the model registry."""

import pytest
from job_star.router.model_registry import (
    ModelTier,
    TaskType,
    get_model,
    get_all_models,
    get_enabled_models,
    get_models_by_tier,
    get_models_for_task,
    get_cheapest_model_for_task,
    get_fastest_model_for_task,
    get_best_model_for_task,
    set_model_enabled,
    list_models_summary,
)


class TestModelInfo:
    def test_blended_cost(self):
        model = get_model("anthropic/claude-3-5-sonnet-20241022")
        assert model is not None
        # 80% of 3.00 + 20% of 15.00 = 2.40 + 3.00 = 5.40
        assert abs(model.blended_cost_per_1m - 5.40) < 0.01

    def test_cost_per_1k(self):
        model = get_model("anthropic/claude-3-5-sonnet-20241022")
        assert abs(model.cost_per_1k_tokens - 0.0054) < 0.0001

    def test_score_for_task(self):
        model = get_model("openai/gpt-4o")
        assert model.score_for_task(TaskType.CODE_GENERATION) == 0.88

    def test_score_for_unknown_task_defaults_neutral(self):
        model = get_model("openai/gpt-4o")
        # Task not in scores dict returns 0.5
        assert model.score_for_task(TaskType.EMBEDDING) == 0.5

    def test_can_handle(self):
        model = get_model("openai/o3-mini")
        assert model.can_handle(TaskType.REASONING) is True
        assert model.can_handle(TaskType.VISION) is False  # score is 0.0


class TestRegistryAccess:
    def test_get_model_returns_none_for_unknown(self):
        assert get_model("nonexistent/model") is None

    def test_get_all_models_returns_dict(self):
        models = get_all_models()
        assert isinstance(models, dict)
        assert len(models) >= 8  # We registered at least 8 models

    def test_get_enabled_models_excludes_disabled(self):
        set_model_enabled("groq/llama-3.3-70b-versatile", False)
        enabled = get_enabled_models()
        assert "groq/llama-3.3-70b-versatile" not in enabled
        # Cleanup
        set_model_enabled("groq/llama-3.3-70b-versatile", True)

    def test_get_models_by_tier(self):
        fast_models = get_models_by_tier(ModelTier.FAST)
        assert all(m.tier == ModelTier.FAST for m in fast_models.values())
        assert len(fast_models) >= 3  # Haiku, Mini, Flash, Llama

    def test_get_models_for_task(self):
        code_models = get_models_for_task(TaskType.CODE_GENERATION)
        assert len(code_models) >= 5
        # Vision models should NOT appear for vision score 0
        vision_models = get_models_for_task(TaskType.VISION)
        assert "openai/o3-mini" not in vision_models
        assert "deepseek/deepseek-chat" not in vision_models


class TestHelperFunctions:
    def test_get_cheapest_model_for_task(self):
        cheapest = get_cheapest_model_for_task(TaskType.SIMPLE_QA)
        assert cheapest is not None
        # Gemini Flash at $0.10/1M input should be among cheapest
        assert cheapest.input_cost_per_1m <= 0.60

    def test_get_fastest_model_for_task(self):
        fastest = get_fastest_model_for_task(TaskType.SIMPLE_QA)
        assert fastest is not None
        # Groq Llama has 0.2s TTFT
        assert fastest.latency_ttft_seconds <= 0.6

    def test_get_best_model_for_task(self):
        best = get_best_model_for_task(TaskType.REASONING)
        assert best is not None
        assert best.score_for_task(TaskType.REASONING) >= 0.90

    def test_get_cheapest_returns_none_for_impossible_task(self):
        # All models have default 0.5 for EMBEDDING, so this should still return something
        # Let's test with a model that has 0.0 score
        # Actually, let's disable all models and check
        for model_id in get_all_models():
            set_model_enabled(model_id, False)
        assert get_cheapest_model_for_task(TaskType.SIMPLE_QA) is None
        # Re-enable
        for model_id in get_all_models():
            set_model_enabled(model_id, True)


class TestSummary:
    def test_list_models_summary(self):
        summary = list_models_summary()
        assert "Model ID" in summary
        assert "claude-3-5-sonnet" in summary
        assert len(summary.split("\n")) >= 10
