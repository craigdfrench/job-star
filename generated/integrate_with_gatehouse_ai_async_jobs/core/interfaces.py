"""
Protocol interfaces for Job-Star pipeline components.

Each component is defined as a Protocol so the orchestrator can work
with any implementation that satisfies the interface. This enables
testing with mocks and swapping strategies without touching the loop.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from job_star.core.types import (
    JobProposal,
    JobRecord,
    JobSelection,
    OrchestratorState,
    ScheduledJob,
)


@runtime_checkable
class Planner(Protocol):
    """
    Examines current state and proposes candidate jobs.

    Called once per orchestrator cycle. Returns a list of JobProposals
    that the Selector will filter down. The Planner should be proactive:
    it looks at what's running, what's completed, and what external
    signals suggest should happen next.
    """

    async def propose(self, state: OrchestratorState) -> list[JobProposal]:
        """Generate job proposals based on current orchestrator state."""
        ...


@runtime_checkable
class Selector(Protocol):
    """
    Picks the best proposal(s) from a batch.

    Applies constraints: capacity, dependencies, deduplication, cost.
    Returns zero or more selections (may filter out all proposals if
    nothing is worth running right now).
    """

    async def select(
        self,
        proposals: list[JobProposal],
        state: OrchestratorState,
    ) -> list[JobSelection]:
        """Select which proposals to pursue and in what order."""
        ...


@runtime_checkable
class Scheduler(Protocol):
    """
    Determines timing for selected jobs.

    May decide a job is ready now, should wait for a dependency, should
    be batched, or should be deferred to a specific time.
    """

    async def schedule(
        self,
        selections: list[JobSelection],
        state: OrchestratorState,
    ) -> list[ScheduledJob]:
        """Assign timing and readiness to selected jobs."""
        ...


@runtime_checkable
class Executor(Protocol):
    """
    Submits scheduled jobs to gatehouse-ai via the JobManager.

    Handles the actual mechanics of calling gatehouse-ai's async job
    interface, translating Job-Star's ScheduledJob into a gatehouse
    submission.
    """

    async def execute(
        self,
        scheduled: ScheduledJob,
        job_manager: "JobManager",
    ) -> JobRecord:
        """Submit a scheduled job and return the resulting JobRecord."""
        ...


@runtime_checkable
class JobManager(Protocol):
    """
    Tracks job lifecycle and interfaces with gatehouse-ai.

    Maintains the authoritative list of known jobs, queries gatehouse-ai
    for status updates, and provides the bridge between Job-Star's
    JobRecord and gatehouse-ai's job representation.
    """

    async def submit(self, scheduled: ScheduledJob) -> JobRecord:
        """Submit a job to gatehouse-ai and return the tracked record."""
        ...

    async def poll_status(self) -> list[JobRecord]:
        """Check gatehouse-ai for status updates on active jobs."""
        ...

    async def get_completed(self) -> list[JobRecord]:
        """Return jobs that have reached a terminal state since last call."""
        ...

    async def cancel(self, job_id: Any) -> bool:
        """Cancel a running or pending job."""
        ...

    @property
    def active_jobs(self) -> list[JobRecord]:
        """Currently active (submitted/running) jobs."""
        ...


@runtime_checkable
class ResultProcessor(Protocol):
    """
    Processes results from completed jobs.

    Handles successful results, failures, and side effects. May update
    external state, trigger follow-up actions, or feed information back
    into the orchestrator's context for the next planning cycle.
    """

    async def process(self, job: JobRecord) -> dict[str, Any]:
        """Process a completed job's result. Returns updated context."""
        ...
