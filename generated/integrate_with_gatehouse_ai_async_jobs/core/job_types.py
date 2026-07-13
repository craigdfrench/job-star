"""Job type registry for Job-Star.

A :class:`JobType` describes a *category* of work (e.g. "generate_code",
"run_tests"). It does not describe how the work is performed — that is the
execution layer's job, talking to gatehouse-ai. The registry is what the
planner consults to decide which categories of jobs are even possible and
what they require.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class JobType:
    """Metadata describing a supported category of job."""

    name: str
    description: str
    # Names of job types that must have at least one successful completion before
    # this type can be planned. Used to encode coarse ordering (e.g. you must
    # generate code before you can test it).
    requires_completed: tuple[str, ...] = ()
    # Soft estimate of relative cost. Higher = more expensive (tokens/time).
    # Used by the selector for budget-aware scheduling. Units are abstract.
    estimated_cost: float = 1.0
    # Default urgency weight in [0.0, 1.0]. The selector combines this with the
    # goal's urgency and dependency readiness to produce a final score.
    base_priority: float = 0.5
    # Whether multiple instances of this type may run concurrently.
    allow_concurrent: bool = True
    # Minimal input schema: keys this job type expects in JobSpec.inputs.
    # This is a documentation/validation aid, not a full JSON Schema engine.
    required_inputs: tuple[str, ...] = ()
    optional_inputs: tuple[str, ...] = ()
    # Free-form tags for grouping/filtering.
    tags: frozenset[str] = field(default_factory=frozenset)

    def validate_inputs(self, inputs: Mapping[str, Any]) -> list[str]:
        """Return a list of missing required input keys (empty if valid)."""
        return [k for k in self.required_inputs if k not in inputs]


class JobTypeRegistry:
    """A lookup table of supported job types.

    The registry is intentionally mutable so bootstrap code and plugins can
    register new types at runtime. Callers should treat the registry as the
    source of truth for "what can Job-Star currently do".
    """

    def __init__(self) -> None:
        self._types: dict[str, JobType] = {}

    def register(self, job_type: JobType) -> JobType:
        if job_type.name in self._types:
            raise ValueError(f"JobType already registered: {job_type.name!r}")
        self._types[job_type.name] = job_type
        return job_type

    def get(self, name: str) -> Optional[JobType]:
        return self._types.get(name)

    def require(self, name: str) -> JobType:
        jt = self._types.get(name)
        if jt is None:
            raise KeyError(f"Unknown job type: {name!r}")
        return jt

    def all(self) -> list[JobType]:
        return list(self._types.values())

    def names(self) -> list[str]:
        return list(self._types.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._types

    def __len__(self) -> int:
        return len(self._types)

    def dependencies_satisfied(
        self, name: str, completed_types: set[str]
    ) -> bool:
        """True if all `requires_completed` types have at least one completion."""
        jt = self.require(name)
        return all(dep in completed_types for dep in jt.requires_completed)


def default_registry() -> JobTypeRegistry:
    """Build and return a registry pre-populated with Job-Star's built-in types.

    These cover the meta-domain work Job-Star itself does: generating code,
    running tests, analyzing results, and fixing problems. The set is small
    and composable; new types can be added via :meth:`register`.
    """
    reg = JobTypeRegistry()

    reg.register(JobType(
        name="generate_code",
        description="Generate or modify source code to satisfy a goal.",
        requires_completed=(),
        estimated_cost=2.0,
        base_priority=0.8,
        allow_concurrent=True,
        required_inputs=("goal_description",),
        optional_inputs=("target_files", "constraints", "context"),
        tags=frozenset({"code", "generation", "write"}),
    ))

    reg.register(JobType(
        name="run_tests",
        description="Execute a test suite or subset of tests and capture results.",
        requires_completed=("generate_code",),
        estimated_cost=1.5,
        base_priority=0.7,
        allow_concurrent=False,  # avoid clobbering shared build artifacts
        required_inputs=("test_command",),
        optional_inputs=("test_filter", "working_dir"),
        tags=frozenset({"test", "execution", "verify"}),
    ))

    reg.register(JobType(
        name="analyze_results",
        description="Analyze job/test results and produce a structured summary.",
        requires_completed=("run_tests",),
        estimated_cost=1.0,
        base_priority=0.6,
        allow_concurrent=True,
        required_inputs=("results_ref",),
        optional_inputs=("focus_areas",),
        tags=frozenset({"analysis", "reasoning"}),
    ))

    reg.register(JobType(
        name="fix_failures",
        description="Generate patches to address reported failures.",
        requires_completed=("analyze_results",),
        estimated_cost=2.5,
        base_priority=0.75,
        allow_concurrent=True,
        required_inputs=("failure_report",),
        optional_inputs=("target_files",),
        tags=frozenset({"code", "fix", "repair"}),
    ))

    reg.register(JobType(
        name="lint_check",
        description="Run linters/static analysis and report violations.",
        requires_completed=("generate_code",),
        estimated_cost=0.5,
        base_priority=0.5,
        allow_concurrent=True,
        required_inputs=("lint_command",),
        optional_inputs=("target_files",),
        tags=frozenset({"lint", "quality", "static-analysis"}),
    ))

    reg.register(JobType(
        name="summarize_session",
        description="Produce a human-readable summary of the session's work.",
        requires_completed=("analyze_results",),
        estimated_cost=0.75,
        base_priority=0.3,
        allow_concurrent=True,
        required_inputs=(),
        optional_inputs=("session_id",),
        tags=frozenset({"report", "summary"}),
    ))

    return reg
