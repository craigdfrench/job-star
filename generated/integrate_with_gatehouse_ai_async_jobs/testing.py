"""Payload builder for testing jobs."""

from __future__ import annotations

from datetime import timedelta
from typing import Mapping

from job_star.core.types import ExecutionParameters, JobSpec

from .base import BasePayloadBuilder


class TestingBuilder(BasePayloadBuilder):
    """Builds payloads for test generation and test execution.

    Two modes:
    - ``generate``: create new test files for target code.
    - ``run``: execute existing tests and collect results.

    Both modes may need write access (generate writes test files;
    run may need to write coverage reports).
    """

    def _build_body(self, spec: JobSpec) -> Mapping[str, Any]:
        mode: str = self._extract_param(spec, "mode", "generate")
        test_framework: str = self._extract_param(spec, "test_framework", "pytest")
        coverage: bool = self._extract_param(spec, "coverage", True)
        output_path: str | None = self._extract_param(spec, "output_path")
        min_coverage: float | None = self._extract_param(spec, "min_coverage")

        return {
            "mode": mode,
            "test_framework": test_framework,
            "files": self._file_list(spec),
            "coverage": coverage,
            "min_coverage": min_coverage,
            "output_path": output_path,
            "instructions": spec.objective,
        }

    def default_parameters(self) -> ExecutionParameters:
        return ExecutionParameters(
            timeout=timedelta(minutes=25),
            max_retries=2,
            retry_backoff_seconds=20,
            resource_profile="standard",
            tags=("testing", "write"),
        )

    def required_permissions(self) -> tuple[str, ...]:
        return ("read", "write", "execute")
