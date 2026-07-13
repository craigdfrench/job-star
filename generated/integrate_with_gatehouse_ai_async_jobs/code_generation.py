"""Payload builder for code generation jobs."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping

from job_star.core.types import ExecutionParameters, JobSpec

from .base import BasePayloadBuilder


class CodeGenerationBuilder(BasePayloadBuilder):
    """Builds payloads for generating new code or code scaffolds.

    Code generation is write-heavy and may need longer timeouts
    for large scaffolds. It requires write permissions.
    """

    def _build_body(self, spec: JobSpec) -> Mapping[str, Any]:
        language: str = self._extract_param(spec, "language", "python")
        framework: str | None = self._extract_param(spec, "framework")
        style_guide: str | None = self._extract_param(spec, "style_guide")
        output_path: str | None = self._extract_param(spec, "output_path")
        template: str | None = self._extract_param(spec, "template")

        return {
            "language": language,
            "framework": framework,
            "style_guide": style_guide,
            "output_path": output_path,
            "template": template,
            "instructions": spec.objective,
            "context_files": list(spec.context_files),
        }

    def default_parameters(self) -> ExecutionParameters:
        return ExecutionParameters(
            timeout=timedelta(minutes=45),
            max_retries=1,
            retry_backoff_seconds=60,
            resource_profile="standard",
            tags=("code-gen", "write"),
        )

    def required_permissions(self) -> tuple[str, ...]:
        return ("read", "write")
