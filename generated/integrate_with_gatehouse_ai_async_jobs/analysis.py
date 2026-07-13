"""Payload builder for codebase analysis jobs."""

from __future__ import annotations

from datetime import timedelta
from typing import Mapping

from job_star.core.types import ExecutionParameters, JobSpec

from .base import BasePayloadBuilder


class AnalysisBuilder(BasePayloadBuilder):
    """Builds payloads for analyzing code structure and quality.

    Analysis is read-only. It can produce dependency graphs,
    complexity metrics, dead-code reports, and architectural
    assessments. Output is a report, not modified files.
    """

    def _build_body(self, spec: JobSpec) -> Mapping[str, Any]:
        analysis_type: str = self._extract_param(
            spec, "analysis_type", "overview"
        )  # overview | dependencies | complexity | dead_code | security
        depth: str = self._extract_param(spec, "depth", "module")
        include_metrics: bool = self._extract_param(spec, "include_metrics", True)

        return {
            "analysis_type": analysis_type,
            "files": self._file_list(spec),
            "depth": depth,
            "include_metrics": include_metrics,
            "output_format": self._extract_param(spec, "output_format", "report"),
            "instructions": spec.objective,
        }

    def default_parameters(self) -> ExecutionParameters:
        return ExecutionParameters(
            timeout=timedelta(minutes=20),
            max_retries=2,
            retry_backoff_seconds=30,
            resource_profile="standard",
            tags=("analysis", "read-only"),
        )

    def required_permissions(self) -> tuple[str, ...]:
        return ("read",)
