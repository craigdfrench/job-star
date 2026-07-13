"""
Shared base class for payload builders.

Provides common helpers so individual builder modules stay concise.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Mapping

from job_star.core.types import ExecutionParameters, JobSpec


class BasePayloadBuilder(ABC):
    """Base class for payload builders.

    Subclasses implement ``_build_body`` and may override
    ``default_parameters`` and ``required_permissions``.
    """

    def build(self, spec: JobSpec) -> Mapping[str, Any]:
        """Construct the task-specific payload body.

        Wraps the subclass ``_build_body`` result with common
        metadata (job_type, objective, files) so downstream
        consumers always have a consistent envelope.
        """
        body = self._build_body(spec)
        return {
            "job_type": spec.job_type.value,
            "objective": spec.objective,
            "target_files": list(spec.target_files),
            "context_files": list(spec.context_files),
            "requested_by": spec.requested_by,
            **body,
        }

    @abstractmethod
    def _build_body(self, spec: JobSpec) -> Mapping[str, Any]:
        """Return the task-specific fields for this job type."""
        ...

    def default_parameters(self) -> ExecutionParameters:
        """Default execution parameters for this job type.

        Subclasses override to customize timeout, retries, etc.
        """
        return ExecutionParameters()

    def required_permissions(self) -> tuple[str, ...]:
        """Permissions this job type needs.

        Defaults to read-only. Builders that modify files
        should override to include "write".
        """
        return ("read",)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_param(
        spec: JobSpec, key: str, default: Any = None
    ) -> Any:
        """Pull a value from spec.parameters with a fallback."""
        return spec.parameters.get(key, default)

    @staticmethod
    def _file_list(spec: JobSpec, key: str = "files") -> list[str]:
        """Get a list of files from parameters or target_files."""
        files = spec.parameters.get(key)
        if files:
            return list(files)
        return list(spec.target_files)
