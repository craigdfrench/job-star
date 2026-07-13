"""
Tests for runtime model fallback logic.
"""

import pytest
from job_star.model_registry.fallback import (
    ModelFallbackChain,
    ModelFallbackExhaustedError,
    FallbackAttempt,
)


class TestModelFallbackChain:
    """Tests for ModelFallbackChain construction and iteration."""

    def test_from_step_with_metadata_fallbacks(self):
        """Chain is built from step metadata fallback_models."""
        step = {
            "id": "step-1",
            "goal_id": "goal-1",
            "metadata": {
                "model": "primary-model",
                "platform": "nvidia",
                "fallback_models": [
                    {"model": "fb-1", "platform": "groq"},
                    {"model": "fb-2", "platform": "google"},
                ],
            },
        }
        chain = ModelFallbackChain.from_step(step)
        assert len(chain) == 3  # primary + 2 fallbacks
        # Primary should be first
        assert chain.models[0]["model"] == "primary-model"
        assert chain.models[1]["model"] == "fb-1"
        assert chain.models[2]["model"] == "fb-2"

    def test_from_step_caps_at_three(self):
        """Chain should cap at 3 models."""
        step = {
            "id": "step-2",
            "metadata": {
                "fallback_models": [
                    {"model": f"model-{i}", "platform": "p"}
                    for i in range(10)
                ],
            },
        }
        chain = ModelFallbackChain.from_step(step)
        assert len(chain) == 3

    def test_from_step_no_fallbacks(self):
        """If no fallbacks in metadata, chain still has primary if present."""
        step = {
            "id": "step-3",
            "metadata": {
                "model": "only-model",
            },
        }
        chain = ModelFallbackChain.from_step(step)
        # Should have at least the primary model
        assert len(chain) >= 1
        assert chain.models[0]["model"] == "only-model"

    def test_from_step_empty(self):
        """Empty step produces empty or minimal chain."""
        step = {"id": "step-4"}
        chain = ModelFallbackChain.from_step(step)
        # May be empty or have computed models
        assert isinstance(chain, ModelFallbackChain)

    def test_iteration_yields_models(self):
        """Iterating the chain yields each model in order."""
        step = {
            "id": "step-5",
            "metadata": {
                "fallback_models": [
                    {"model": "m1", "platform": "p1"},
                    {"model": "m2", "platform": "p2"},
                    {"model": "m3", "platform": "p3"},
                ],
            },
        }
        chain = ModelFallbackChain.from_step(step)
        models_yielded = list(chain)
        assert len(models_yielded) == 3

    def test_mark_success(self):
        """mark_success updates the last attempt status."""
        step = {
            "id": "step-6",
            "metadata": {
                "fallback_models": [
                    {"model": "m1", "platform": "p1"},
                ],
            },
        }
        chain = ModelFallbackChain.from_step(step)
        for model_info in chain:
            chain.mark_success(model_info)
            break
        assert chain.attempts[-1].status == "success"

    def test_mark_failure_logs_and_continues(self):
        """mark_failure records the error and allows continuation."""
        step = {
            "id": "step-7",
            "metadata": {
                "fallback_models": [
                    {"model": "m1", "platform": "p1"},
                    {"model": "m2", "platform": "p2"},
                ],
            },
        }
        chain = ModelFallbackChain.from_step(step)
        iterator = iter(chain)
        first = next(iterator)
        chain.mark_failure(first, RuntimeError("rate limit hit"))
        assert chain.attempts[0].status == "failed"
        assert chain.attempts[0].error == "rate limit hit"
        assert chain.attempts[0].error_type == "RuntimeError"

    def test_final_error_when_all_fail(self):
        """final_error raises ModelFallbackExhaustedError with details."""
        step = {
            "id": "step-8",
            "metadata": {
                "fallback_models": [
                    {"model": "m1", "platform": "p1"},
                    {"model": "m2", "platform": "p2"},
                ],
            },
        }
        chain = ModelFallbackChain.from_step(step)
        for model_info in chain:
            chain.mark_failure(model_info, RuntimeError("timeout"))

        with pytest.raises(ModelFallbackExhaustedError) as exc_info:
            raise chain.final_error()

        assert "All 2 model(s) failed" in str(exc_info.value)
        assert "m1" in str(exc_info.value)
        assert "m2" in str(exc_info.value)
        assert "Suggestions" in str(exc_info.value)

    def test_final_error_has_chain_reference(self):
        """The error contains a reference to the chain for inspection."""
        step = {
            "id": "step-9",
            "metadata": {
                "fallback_models": [
                    {"model": "m1", "platform": "p1"},
                ],
            },
        }
        chain = ModelFallbackChain.from_step(step)
        for model_info in chain:
            chain.mark_failure(model_info, ValueError("bad response"))

        with pytest.raises(ModelFallbackExhaustedError) as exc_info:
            raise chain.final_error()

        assert exc_info.value.chain is chain
        assert len(exc_info.value.attempts) == 1
        assert exc_info.value.attempts[0].error_type == "ValueError"

    def test_full_rotation_scenario(self):
        """Simulate a full rotation: first fails, second succeeds."""
        step = {
            "id": "step-10",
            "metadata": {
                "fallback_models": [
                    {"model": "primary", "platform": "nvidia"},
                    {"model": "fallback-1", "platform": "groq"},
                    {"model": "fallback-2", "platform": "google"},
                ],
            },
        }
        chain = ModelFallbackChain.from_step(step)
        result = None
        for model_info in chain:
            model_name = model_info["model"]
            if model_name == "primary":
                chain.mark_failure(model_info, RuntimeError("429 rate limit"))
                continue
            # Fallback succeeds
            result = f"result from {model_name}"
            chain.mark_success(model_info)
            break

        assert result == "result from fallback-1"
        assert len(chain.attempts) == 2
        assert chain.attempts[0].status == "failed"
        assert chain.attempts[1].status == "success"

    def test_primary_prepended_if_not_in_list(self):
        """Primary model from metadata is prepended if not in fallback list."""
        step = {
            "id": "step-11",
            "metadata": {
                "model": "my-primary",
                "fallback_models": [
                    {"model": "fb-1", "platform": "groq"},
                    {"model": "fb-2", "platform": "google"},
                ],
            },
        }
        chain = ModelFallbackChain.from_step(step)
        assert chain.models[0]["model"] == "my-primary"
        assert chain.models[1]["model"] == "fb-1"
        assert chain.models[2]["model"] == "fb-2"

    def test_primary_not_duplicated(self):
        """If primary is already in the fallback list, it's not duplicated."""
        step = {
            "id": "step-12",
            "metadata": {
                "model": "shared-model",
                "fallback_models": [
                    {"model": "shared-model", "platform": "nvidia"},
                    {"model": "fb-1", "platform": "groq"},
                ],
            },
        }
        chain = ModelFallbackChain.from_step(step)
        names = [m["model"] for m in chain.models]
        assert names.count("shared-model") == 1
        assert names[0] == "shared-model"

    def test_attempt_records_platform(self):
        """Each attempt records the platform."""
        step = {
            "id": "step-13",
            "metadata": {
                "fallback_models": [
                    {"model": "m1", "platform": "nvidia"},
                    {"model": "m2", "platform": "groq"},
                ],
            },
        }
        chain = ModelFallbackChain.from_step(step)
        for model_info in chain:
            chain.mark_success(model_info)
            break
        assert chain.attempts[0].platform == "nvidia"

    def test_from_step_with_result_fallbacks(self):
        """Chain can be built from step.result.fallback_models."""
        step = {
            "id": "step-14",
            "result": {
                "fallback_models": [
                    {"model": "r-fb-1", "platform": "cohere"},
                ],
            },
        }
        chain = ModelFallbackChain.from_step(step)
        assert len(chain) >= 1
        assert chain.models[0]["model"] == "r-fb-1"