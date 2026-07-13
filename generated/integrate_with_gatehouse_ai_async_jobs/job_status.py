"""Job-Star's internal job status enum.

This enum maps gatehouse-ai's async job states to Job-Star's internal
representation. Gatehouse states are the "execution" perspective; Job-Star
adds states that represent *its* perspective as the orchestrating client.

State flow (typical):

    CREATED → SUBMITTED → ACCEPTED → RUNNING → COMPLETED
                                            ↘ FAILED → RETRYING → SUBMITTED → ...
                                            ↘ TIMEOUT
                ↘ REJECTED
            ↘ CANCELLED (can happen at several points)

Gatehouse states we expect to receive:
    - pending    → maps to ACCEPTED (gatehouse has the job, hasn't started)
    - running    → maps to RUNNING
    - completed  → maps to COMPLETED
    - failed     → maps to FAILED
    - cancelled   → maps to CANCELLED
    - timed_out   → maps to TIMEOUT

Job-Star-only states (not received from gatehouse):
    - CREATED    — Job-Star has constructed the job but not submitted it
    - SUBMITTED  — Job-Star sent the request to gatehouse, awaiting acknowledgment
    - REJECTED   — Gatehouse refused the job (invalid, quota exceeded, etc.)
    - RETRYING   — Job-Star decided to retry after a failure/timeout
"""

from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    """Internal job status used throughout Job-Star.

    Inherits from ``str`` so values serialize cleanly to JSON and are
    usable as dictionary keys without explicit conversion.
    """

    # --- Job-Star lifecycle states (not received from gatehouse) ---

    CREATED = "created"
    """Job-Star has created the job object but not yet submitted it."""

    SUBMITTED = "submitted"
    """Request sent to gatehouse-ai; awaiting acknowledgment."""

    RETRYING = "retrying"
    """Job-Star is retrying after a failure or timeout."""

    # --- States mapped from gatehouse-ai ---

    ACCEPTED = "accepted"
    """Gatehouse acknowledged the job and queued it (gatehouse: pending)."""

    RUNNING = "running"
    """Gatehouse is actively executing the job (gatehouse: running)."""

    COMPLETED = "completed"
    """Job finished successfully (gatehouse: completed)."""

    FAILED = "failed"
    """Job execution failed (gatehouse: failed)."""

    TIMEOUT = "timeout"
    """Job exceeded its time limit (gatehouse: timed_out)."""

    CANCELLED = "cancelled"
    """Job was cancelled before or during execution (gatehouse: cancelled)."""

    REJECTED = "rejected"
    """Gatehouse refused the job outright (not a gatehouse state — set by Job-Star)."""

    @classmethod
    def from_gatehouse(cls, gatehouse_state: str) -> "JobStatus":
        """Map a gatehouse-ai job state string to a Job-Star JobStatus.

        Parameters
        ----------
        gatehouse_state:
            The state string returned by gatehouse-ai's async job API.

        Returns
        -------
        JobStatus
            The corresponding internal status.

        Raises
        ------
        ValueError
            If the gatehouse state is not recognized.
        """
        mapping = {
            "pending": cls.ACCEPTED,
            "running": cls.RUNNING,
            "completed": cls.COMPLETED,
            "failed": cls.FAILED,
            "timed_out": cls.TIMEOUT,
            "cancelled": cls.CANCELLED,
        }
        normalized = gatehouse_state.strip().lower()
        if normalized not in mapping:
            raise ValueError(
                f"Unrecognized gatehouse job state: {gatehouse_state!r}. "
                f"Known states: {sorted(mapping.keys())}"
            )
        return mapping[normalized]

    @property
    def is_terminal(self) -> bool:
        """True if this status represents a final state (no further transitions)."""
        return self in {
            self.COMPLETED,
            self.FAILED,
            self.TIMEOUT,
            self.CANCELLED,
            self.REJECTED,
        }

    @property
    def is_active(self) -> bool:
        """True if the job is in-flight (submitted but not yet terminal)."""
        return self in {
            self.SUBMITTED,
            self.ACCEPTED,
            self.RUNNING,
            self.RETRYING,
        }
