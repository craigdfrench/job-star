"""
Tests for the fallback integration helper.
"""

import pytest
from job_star.model_registry.integration import (
    execute_with_fallback,
    build_fallback_metadata,
)
from job_star.model_registry.fallback import ModelFallbackExhaustedError


class TestExecuteWithFallback:
    """Tests for execute_with_fallback."""

    def test_succeeds_on_first_model(self):
        """If the first model succeeds, return its result."""
        step = {
            "id": "s1",
            "metadata": {
                "fallback_models": [
                    {"model": "m1", "platform": "p1"},
                    {"model": "m2", "platform": "p2"},
                ],
            },
        }

        def call_fn(model_info):
            return f"ok-{model_info['model']}"

        result, model = execute_with_fallback(step, call_fn)
        assert result == "ok-m1"
        assert model["model"] == "m1"

    def test_rotates_on_failure(self):
        """If first model fails, rotate to second."""
        step = {
            "id": "s2",
            "metadata": {
                "fallback_models": [
                    {"model": "m1", "platform": "p1"},
                    {"model": "m2", "platform": "p2"},
                ],
            },
        }
        call_count = [0]

        def call_fn(model_info):
            call_count[0] += 1
            if model_info["model"] == "m1":
                raise RuntimeError("429")
            return f"ok-{model_info['model']}"

        result, model = execute_with_fallback(step, call_fn)
        assert result == "ok-m2"
        assert model["model"] == "m2"
        assert call_count[0] == 2

    def test_all_fail_raises_exhausted(self):
        """If all models fail, raise ModelFallbackExhaustedError."""
        step = {
            "id": "s3",
            "metadata": {
                "fallback_models": [
                    {"model": "m1", "platform": "p1"},
                    {"model": "m2", "platform": "p2"},
                ],
            },
        }

        def call_fn(model_info):
            raise RuntimeError(f"error-{model_info['model']}")

        with pytest.raises(ModelFallbackExhaustedError) as exc_info:
            execute_with_fallback(step, call_fn)

        assert "All 2 model(s) failed" in str(exc_info.value)

    def test_on_rotation_callback(self):
        """on_rotation callback is called when rotating."""
        step = {
            "id": "s4",
            "metadata": {
                "fallback_models": [
                    {"model": "m1", "platform": "p1"},
                    {"model": "m2", "platform": "p2"},
                ],
            },
        }
        rotations = []

        def call_fn(model_info):
            if model_info["model"] == "m1":
                raise RuntimeError("timeout")
            return "ok"

        def on_rotation(failed, next_model, error):
            rotations.append((failed["model"], next_model["model"], str(error)))

        result, _ = execute_with_fallback(step, call_fn, on_rotation=on_rotation)
        assert result == "ok"
        assert len(rotations) == 1
        assert rotations[0] == ("m1", "m2", "timeout")

    def test_no_models_raises_exhausted(self):
        """If chain is empty, raise immediately."""
        step = {"id": "s5", "metadata": {}}

        def call_fn(model_info):
            return "ok"

        with pytest.raises(ModelFallbackExhaustedError):
            execute_with_fallback(step, call_fn)

    def test_on_rotation_callback_error_does_not_break(self):
        """If on_rotation callback raises, execution continues."""
        step = {
            "id": "s6",
            "metadata": {
                "fallback_models": [
                    {"model": "m1", "platform": "p1"},
                    {"model": "m2", "platform": "p2"},
                ],
            },
        }

        def call_fn(model_info):
            if model_info["model"] == "m1":
                raise RuntimeError("fail")
            return "ok"

        def bad_on_rotation(failed, next_model, error):
            raise ValueError("callback broken")

        result, model = execute_with_fallback(step, call_fn, on_rotation=bad_on_rotation)
        assert result == "ok"
        assert model["model"] == "m2"


class TestBuildFallbackMetadata:
    """Tests for build_fallback_metadata."""

    def test_builds_metadata_with_fallbacks(self):
        """Metadata includes model, platform, and fallback_models."""
        step = {"id": "s1", "metadata": {"task_type": "coding"}}
        selected = [
            {"model": "best", "platform": "nvidia", "task_score": 5, "cost_tier": "free"},
            {"model": "good", "platform": "groq", "task_score": 4, "cost_tier": "free"},
            {"model": "ok", "platform": "google", "task_score": 3, "cost_tier": "free"},
        ]
        meta = build_fallback_metadata(step, selected)
        assert meta["model"] == "best"
        assert meta["platform"] == "nvidia"
        assert meta["task_type"] == "coding"
        assert len(meta["fallback_models"]) == 3
        assert meta["fallback_models"][0]["model"] == "best"

    def test_caps_at_three(self):
        """Only top 3 models are stored."""
        step = {"id": "s2"}
        selected = [{"model": f"m{i}", "platform": "p"} for i in range(10)]
        meta = build_fallback_metadata(step, selected)
        assert len(meta["fallback_models"]) == 3

    def test_empty_selected(self):
        """If no models selected, metadata has no fallback_models."""
        step = {"id": "s3", "metadata": {"existing": "value"}}
        meta = build_fallback_metadata(step, [])
        assert "fallback_models" not in meta
        assert meta["existing"] == "value"

    def test_preserves_existing_metadata(self):
        """Existing metadata keys are preserved."""
        step = {"id": "s4", "metadata": {"priority": "high", "task_type": "research"}}
        selected = [{"model": "m1", "platform": "groq"}]
        meta = build_fallback_metadata(step, selected)
        assert meta["priority"] == "high"
        assert meta["task_type"] == "research"
        assert meta["model"] == "m1"