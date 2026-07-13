"""Job-Star's internal Job model.

A ``Job`` is Job-Star's complete record of a unit of work: what it is,
where it is in its lifecycle, and what happened when it ran. This is the
central data structure that Job-Star's decision-making layer reasons about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from job_star.models.job_status import JobStatus


def _utcnow() -> datetime:
    """Return current UTC time as a naive datetime (internal convention)."""
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


@dataclass(slots=True)
class Job:
    """A single unit of work tracked by Job-Star.

    Fields
    ------
    id:
        Unique identifier for this job. Generated as a UUID4 hex string if
        not provided. This is the same id used when submitting to gatehouse.
    type:
        The job type/category. Gatehouse uses this to route the job to the
        appropriate executor. Examples: "embedding", "inference", "training".
    payload:
        The opaque input data for the job. Structure depends on ``type``.
        Job-Star does not interpret this; it passes it through to gatehouse.
    status:
        Current lifecycle state. See :class:`JobStatus`.
    submitted_at:
        When Job-Star submitted the job to gatehouse (None if not yet submitted).
    completed_at:
        When the job reached a terminal state (None if still active).
    result:
        The output data returned by gatehouse on success. None until COMPLETED.
    error:
        Error details if the job failed, timed out, or was rejected.
        Structure: ``{"message": str, "type": str | None, "details": Any | None}``.
    attempt:
        Which attempt number this is (1-based). Increments on retry.
    metadata:
        Free-form dict for Job-Star's own bookkeeping (tags, priority,
        correlation ids, source, etc.). Not sent to gatehouse.
    gatehouse_id:
        The id gatehouse-ai assigned to the job, if different from ``id``.
        Usually None — gatehouse typically uses the id we provide.
    """

    id: str = field(default_factory=lambda: uuid4().hex)
    type: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = JobStatus.CREATED
    submitted_at: datetime | None = None
    completed_at: datetime | None = None
    result: Any = None
    error: dict[str, Any] | None = None
    attempt: int = 1
    metadata: dict[str, Any] = field(default_factory=dict)
    gatehouse_id: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    def mark_submitted(self, gatehouse_id: str | None = None) -> None:
        """Transition to SUBMITTED and record submission time.

        Call this right after sending the request to gatehouse.
        """
        if self.status != JobStatus.CREATED and self.status != JobStatus.RETRYING:
            raise ValueError(
                f"Cannot mark job {self.id} as SUBMITTED from status {self.status.value}"
            )
        self.status = JobStatus.SUBMITTED
        self.submitted_at = _utcnow()
        if gatehouse_id is not None:
            self.gatehouse_id = gatehouse_id

    def update_from_gatehouse(self, gatehouse_state: str) -> None:
        """Update status based on a state string received from gatehouse.

        Sets ``completed_at`` if the new status is terminal.
        """
        new_status = JobStatus.from_gatehouse(gatehouse_state)
        self.status = new_status
        if new_status.is_terminal and self.completed_at is None:
            self.completed_at = _utcnow()

    def mark_completed(self, result: Any) -> None:
        """Transition to COMPLETED with a result."""
        self.status = JobStatus.COMPLETED
        self.result = result
        self.completed_at = _utcnow()

    def mark_failed(self, error: dict[str, Any]) -> None:
        """Transition to FAILED with error details."""
        self.status = JobStatus.FAILED
        self.error = error
        self.completed_at = _utcnow()

    def mark_timeout(self, error: dict[str, Any] | None = None) -> None:
        """Transition to TIMEOUT."""
        self.status = JobStatus.TIMEOUT
        self.error = error or {"message": "Job timed out"}
        self.completed_at = _utcnow()

    def mark_cancelled(self, reason: str | None = None) -> None:
        """Transition to CANCELLED."""
        self.status = JobStatus.CANCELLED
        self.error = {"message": reason or "Job cancelled"} if reason else None
        self.completed_at = _utcnow()

    def mark_rejected(self, error: dict[str, Any]) -> None:
        """Transition to REJECTED (gatehouse refused the job)."""
        self.status = JobStatus.REJECTED
        self.error = error
        self.completed_at = _utcnow()

    def prepare_retry(self) -> None:
        """Prepare the job for another submission attempt.

        Increments attempt counter, clears terminal state, and resets
        submission/completion timestamps. Does NOT clear result/error
        from the previous attempt — those are preserved in case the
        caller wants to inspect them, but ``status`` moves to RETRYING.
        """
        if not self.status.is_terminal and self.status != JobStatus.RETRYING:
            raise ValueError(
                f"Cannot retry job {self.id} from non-terminal status {self.status.value}"
            )
        self.attempt += 1
        self.status = JobStatus.RETRYING
        self.submitted_at = None
        self.completed_at = None
        # Keep old result/error for inspection; they'll be overwritten on completion.

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict suitable for JSON encoding.

        Datetimes are rendered as ISO 8601 strings. Enums use their
        string value. None fields are included as null for completeness.
        """
        return {
            "id": self.id,
            "type": self.type,
            "payload": self.payload,
            "status": self.status.value,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "result": self.result,
            "error": self.error,
            "attempt": self.attempt,
            "metadata": self.metadata,
            "gatehouse_id": self.gatehouse_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Job":
        """Reconstruct a Job from a serialized dict (inverse of to_dict).

        Datetime strings are parsed back to naive datetime objects.
        """
        def _parse_dt(s: str | None) -> datetime | None:
            if s is None:
                return None
            # Parse ISO 8601; strip tzinfo to match internal convention
            dt = datetime.fromisoformat(s)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt

        return cls(
            id=data["id"],
            type=data["type"],
            payload=data.get("payload", {}),
            status=JobStatus(data["status"]),
            submitted_at=_parse_dt(data.get("submitted_at")),
            completed_at=_parse_dt(data.get("completed_at")),
            result=data.get("result"),
            error=data.get("error"),
            attempt=data.get("attempt", 1),
            metadata=data.get("metadata", {}),
            gatehouse_id=data.get("gatehouse_id"),
        )

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def is_terminal(self) -> bool:
        """True if the job is in a final state."""
        return self.status.is_terminal

    @property
    def is_active(self) -> bool:
        """True if the job is in-flight."""
        return self.status.is_active

    @property
    def duration_seconds(self) -> float | None:
        """Elapsed wall-clock time if completed, else None."""
        if self.submitted_at is None:
            return None
        end = self.completed_at or _utcnow()
        return (end - self.submitted_at).total_seconds()

    def __repr__(self) -> str:
        return (
            f"Job(id={self.id[:8]}..., type={self.type!r}, "
            f"status={self.status.value}, attempt={self.attempt})"
        )
