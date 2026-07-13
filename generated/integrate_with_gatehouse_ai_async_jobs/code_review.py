"""Payload builder for code review jobs."""

from __future__ import annotations

from datetime import timedelta
from typing import Mapping

from job_star.core.types import ExecutionParameters, JobSpec

from .base import BasePayloadBuilder


class CodeReviewBuilder(BasePayloadBuilder):
    """Builds payloads for reviewing existing code.

    Code review is read-only. It focuses on quality, security,
    and adherence to conventions. Output is a structured review,
    not modified files.
    """

    def _build_body(self, spec: JobSpec) -> Mapping[str, Any]:
        review_focus: list[str] = self._extract_param(
            spec, "focus", ["correctness", "style", "security"]
        )
        severity_threshold: str = self._extract_param(
            spec, "severity_threshold", "medium"
        )
        diff_only: bool = self._extract_param(spec, "diff_only", False)

        return {
            "files": self._file_list(spec),
            "review_focus": review_focus,
            "severity_threshold": severity_threshold,
            "diff_only": diff_only,
            "output_format": self._extract_param(spec, "output_format", "structured"),
        }

    def default_parameters(self) -> ExecutionParameters:
        return ExecutionParameters(
            timeout=timedelta(minutes=20),
            max_retries=2,
            retry_backoff_seconds=30,
            resource_profile="light",
            tags=("review", "read-only"),
        )

    def required_permissions(self) -> tuple[str, ...]:
        return ("read",)
