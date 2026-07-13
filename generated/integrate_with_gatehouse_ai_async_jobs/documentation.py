"""Payload builder for documentation generation jobs."""

from __future__ import annotations

from datetime import timedelta
from typing import Mapping

from job_star.core.types import ExecutionParameters, JobSpec

from .base import BasePayloadBuilder


class DocumentationBuilder(BasePayloadBuilder):
    """Builds payloads for generating or updating documentation.

    Supports generating docstrings, README files, API docs, and
    architectural overviews. Write permission is needed to save
    documentation files.
    """

    def _build_body(self, spec: JobSpec) -> Mapping[str, Any]:
        doc_type: str = self._extract_param(
            spec, "doc_type", "docstring"
        )  # docstring | readme | api | architecture
        output_path: str | None = self._extract_param(spec, "output_path")
        include_private: bool = self._extract_param(spec, "include_private", False)
        format: str = self._extract_param(spec, "format", "markdown")

        return {
            "doc_type": doc_type,
            "files": self._file_list(spec),
            "output_path": output_path,
            "include_private": include_private,
            "format": format,
            "instructions": spec.objective,
        }

    def default_parameters(self) -> ExecutionParameters:
        return ExecutionParameters(
            timeout=timedelta(minutes=30),
            max_retries=2,
            retry_backoff_seconds=30,
            resource_profile="light",
            tags=("docs", "write"),
        )

    def required_permissions(self) -> tuple[str, ...]:
        return ("read", "write")
