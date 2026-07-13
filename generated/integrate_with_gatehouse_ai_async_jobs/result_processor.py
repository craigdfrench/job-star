"""
job_star.core.result_processor
==============================

Ingests completed async-job results from gatehouse-ai and closes the feedback
loop back into the planner.

Pipeline:

    gatehouse result
        -> normalize -> JobResult
        -> route by outcome
            -> success handler   (commit artifacts, trigger dependents)
            -> failure handler   (classify, retry w/ backoff or escalate)
            -> timeout handler   (mark timed out, retry once or escalate)
            -> cancelled handler (record, no follow-up unless planner wants)
        -> update state
        -> emit FeedbackSignal to planner
        -> submit follow-up JobSpecs via gatehouse client

This module is deliberately synchronous and side-effectful: it is meant to be
driven by an async dispatcher that awaits gatehouse's completion stream and
calls `ResultProcessor.process(raw)` per result. Keeping it synchronous makes
the decision logic trivially testable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Protocol

from job_star.core.models import JobRecord, JobResult, JobSpec, JobStatus
from job_star.core.state import JobStateStore
from job_star.integrations.gatehouse import GatehouseClient

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Feedback signal: the typed message we hand back to the planner.
# --------------------------------------------------------------------------- #

class FeedbackKind(str, Enum):
    SUCCESS = "success"
    RETRYABLE_FAILURE = "retryable_failure"
    PERMANENT_FAILURE = "permanent_failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    DEPENDENT_TRIGGERED = "dependent_triggered"
    BACKPRESSURE = "backpressure"


@dataclass(frozen=True)
class FeedbackSignal:
    """A single observation the planner can use to adjust future plans."""

    kind: FeedbackKind
    job_id: str
    spec_id: str
    attempt: int
    detail: Dict[str, Any] = field(default_factory=dict)
    # Hints the planner may use: e.g. {"avoid_executor": "gpu-pool-a"}
    hints: Dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Retry policy: configurable per-spec fallback.
# --------------------------------------------------------------------------- #

@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 2.0
    max_delay_seconds: float = 60.0
    multiplier: float = 2.0
    retry_on_timeout: bool = True

    def delay_for(self, attempt: int) -> float:
        """Exponential backoff with cap. `attempt` is 1-indexed (next attempt)."""
        delay = self.base_delay_seconds * (self.multiplier ** (attempt - 1))
        return min(delay, self.max_delay_seconds)


# --------------------------------------------------------------------------- #
# Protocols for collaborators (kept loose for testability).
# --------------------------------------------------------------------------- #

class _PlannerLike(Protocol):
    def handle_feedback(self, signal: FeedbackSignal) -> None: ...
    def plan_followups(self, result: JobResult) -> List[JobSpec]: ...


# --------------------------------------------------------------------------- #
# Result processor
# --------------------------------------------------------------------------- #

class ResultProcessor:
    """Ingests gatehouse job results and drives the feedback loop."""

    def __init__(
        self,
        state: JobStateStore,
        planner: _PlannerLike,
        gatehouse: GatehouseClient,
        retry_policy: Optional[RetryPolicy] = None,
        # Hook for tests/observability: called with every FeedbackSignal emitted.
        on_feedback: Optional[Callable[[FeedbackSignal], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._state = state
        self._planner = planner
        self._gatehouse = gatehouse
        self._retry_policy = retry_policy or RetryPolicy()
        self._on_feedback = on_feedback
        self._clock = clock

    # ------------------------------------------------------------------ #
    # Public entry point
    # ------------------------------------------------------------------ #

    def process(self, raw: Dict[str, Any]) -> JobResult:
        """Process one raw result dict from gatehouse's completion stream.

        Returns the normalized JobResult for callers that want it (e.g. for
        logging or metrics). All side effects (state, follow-ups, feedback)
        are performed internally.
        """
        result = self._normalize(raw)
        logger.info(
            "result_processor.process job_id=%s status=%s attempt=%s",
            result.job_id, result.status, result.attempt,
        )

        try:
            self._route(result)
        except Exception:
            # Never let a single bad result kill the processing loop.
            logger.exception("result_processor.process failed job_id=%s", result.job_id)
            self._emit(FeedbackSignal(
                kind=FeedbackKind.PERMANENT_FAILURE,
                job_id=result.job_id,
                spec_id=result.spec_id,
                attempt=result.attempt,
                detail={"error": "result_processor_internal_error"},
            ))
            raise

        return result

    # ------------------------------------------------------------------ #
    # Normalization
    # ------------------------------------------------------------------ #

    def _normalize(self, raw: Dict[str, Any]) -> JobResult:
        """Map gatehouse's completion payload to Job-Star's JobResult.

        Gatehouse is expected to send at least:
            job_id, spec_id, status, attempt, started_at, finished_at,
            output (optional), error (optional), metadata (optional)
        """
        status_raw = str(raw.get("status", "")).lower()
        try:
            status = JobStatus(status_raw)
        except ValueError:
            logger.warning("unknown gatehouse status %r; treating as failure", status_raw)
            status = JobStatus.FAILURE

        started_at = raw.get("started_at")
        finished_at = raw.get("finished_at")
        duration = None
        if started_at is not None and finished_at is not None:
            try:
                duration = float(finished_at) - float(started_at)
            except (TypeError, ValueError):
                duration = None

        return JobResult(
            job_id=str(raw["job_id"]),
            spec_id=str(raw.get("spec_id", "")),
            status=status,
            attempt=int(raw.get("attempt", 1)),
            output=raw.get("output"),
            error=raw.get("error"),
            metadata=raw.get("metadata", {}) or {},
            started_at=started_at,
            finished_at=finished_at,
            duration_seconds=duration,
        )

    # ------------------------------------------------------------------ #
    # Routing
    # ------------------------------------------------------------------ #

    def _route(self, result: JobResult) -> None:
        handler = {
            JobStatus.SUCCESS: self._handle_success,
            JobStatus.FAILURE: self._handle_failure,
            JobStatus.TIMEOUT: self._handle_timeout,
            JobStatus.CANCELLED: self._handle_cancelled,
        }.get(result.status, self._handle_unknown)

        handler(result)

        # Always commit the result to state, regardless of outcome.
        self._state.mark_completed(result.job_id, result)

    # ------------------------------------------------------------------ #
    # Outcome handlers
    # ------------------------------------------------------------------ #

    def _handle_success(self, result: JobResult) -> None:
        self._emit(FeedbackSignal(
            kind=FeedbackKind.SUCCESS,
            job_id=result.job_id,
            spec_id=result.spec_id,
            attempt=result.attempt,
            detail={
                "duration_seconds": result.duration_seconds,
                "output_keys": _safe_keys(result.output),
            },
        ))

        followups = self._safe_plan_followups(result)
        submitted = self._submit_followups(followups, result)

        if submitted:
            self._emit(FeedbackSignal(
                kind=FeedbackKind.DEPENDENT_TRIGGERED,
                job_id=result.job_id,
                spec_id=result.spec_id,
                attempt=result.attempt,
                detail={"followup_count": len(submitted), "followup_ids": submitted},
            ))

    def _handle_failure(self, result: JobResult) -> None:
        record = self._state.get(result.job_id)
        attempts_so_far = (record.attempts if record else result.attempt)

        if self._should_retry(result, attempts_so_far):
            delay = self._retry_policy.delay_for(attempts_so_far + 1)
            self._schedule_retry(result, attempts_so_far + 1, delay)
            self._emit(FeedbackSignal(
                kind=FeedbackKind.RETRYABLE_FAILURE,
                job_id=result.job_id,
                spec_id=result.spec_id,
                attempt=result.attempt,
                detail={
                    "error": _truncate(result.error),
                    "next_attempt": attempts_so_far + 1,
                    "delay_seconds": delay,
                },
            ))
        else:
            self._emit(FeedbackSignal(
                kind=FeedbackKind.PERMANENT_FAILURE,
                job_id=result.job_id,
                spec_id=result.spec_id,
                attempt=result.attempt,
                detail={"error": _truncate(result.error)},
            ))

    def _handle_timeout(self, result: JobResult) -> None:
        record = self._state.get(result.job_id)
        attempts_so_far = (record.attempts if record else result.attempt)

        if self._retry_policy.retry_on_timeout and self._should_retry(result, attempts_so_far):
            delay = self._retry_policy.delay_for(attempts_so_far + 1)
            # Timeouts often warrant a longer base delay; we let the policy decide.
            self._schedule_retry(result, attempts_so_far + 1, delay)
            self._emit(FeedbackSignal(
                kind=FeedbackKind.TIMEOUT,
                job_id=result.job_id,
                spec_id=result.spec_id,
                attempt=result.attempt,
                detail={
                    "duration_seconds": result.duration_seconds,
                    "next_attempt": attempts_so_far + 1,
                    "delay_seconds": delay,
                },
                hints={"avoid_executor": result.metadata.get("executor")},
            ))
        else:
            self._emit(FeedbackSignal(
                kind=FeedbackKind.PERMANENT_FAILURE,
                job_id=result.job_id,
                spec_id=result.spec_id,
                attempt=result.attempt,
                detail={
                    "reason": "timeout_exhausted",
                    "duration_seconds": result.duration_seconds,
                },
            ))

    def _handle_cancelled(self, result: JobResult) -> None:
        # Cancellations are usually user- or system-initiated; we record and
        # let the planner decide whether to resurface. No automatic retry.
        self._emit(FeedbackSignal(
            kind=FeedbackKind.CANCELLED,
            job_id=result.job_id,
            spec_id=result.spec_id,
            attempt=result.attempt,
            detail={"reason": result.metadata.get("cancel_reason", "unknown")},
        ))

    def _handle_unknown(self, result: JobResult) -> None:
        logger.error("unknown job status encountered job_id=%s status=%s",
                     result.job_id, result.status)
        self._emit(FeedbackSignal(
            kind=FeedbackKind.PERMANENT_FAILURE,
            job_id=result.job_id,
            spec_id=result.spec_id,
            attempt=result.attempt,
            detail={"reason": "unknown_status", "status": str(result.status)},
        ))

    # ------------------------------------------------------------------ #
    # Retry decision & scheduling
    # ------------------------------------------------------------------ #

    def _should_retry(self, result: JobResult, attempts_so_far: int) -> bool:
        if attempts_so_far >= self._retry_policy.max_attempts:
            return False
        # Allow spec-level opt-out via metadata.
        if result.metadata.get("no_retry"):
            return False
        # Allow transient error classification via metadata from the executor.
        transient = result.metadata.get("transient", True)
        return bool(transient)

    def _schedule_retry(self, result: JobResult, next_attempt: int, delay: float) -> None:
        """Build a retry JobSpec from the original and resubmit.

        We rely on the state store having the original JobRecord. If it's
        missing (e.g., state was evicted), we escalate to a permanent failure
        rather than guessing the spec.
        """
        record = self._state.get(result.job_id)
        if record is None or record.spec is None:
            logger.error(
                "cannot retry job_id=%s: original spec not found in state", result.job_id
            )
            self._emit(FeedbackSignal(
                kind=FeedbackKind.PERMANENT_FAILURE,
                job_id=result.job_id,
                spec_id=result.spec_id,
                attempt=result.attempt,
                detail={"reason": "retry_spec_missing"},
            ))
            return

        retry_spec = record.spec.with_attempt(
            attempt=next_attempt,
            # gatehouse supports a `schedule_at` hint; we set it in the future.
            schedule_at=self._clock() + delay,
            metadata={
                **(record.spec.metadata or {}),
                "retry_of": result.job_id,
                "retry_attempt": next_attempt,
            },
        )

        self._state.record_attempt(result.job_id)  # bump attempt counter
        new_job_id = self._gatehouse.submit(retry_spec)
        logger.info(
            "scheduled retry job_id=%s -> new_job_id=%s attempt=%s delay=%.2fs",
            result.job_id, new_job_id, next_attempt, delay,
        )

    # ------------------------------------------------------------------ #
    # Follow-up submission
    # ------------------------------------------------------------------ #

    def _safe_plan_followups(self, result: JobResult) -> List[JobSpec]:
        try:
            return list(self._planner.plan_followups(result))
        except Exception:
            logger.exception("planner.plan_followups raised for job_id=%s", result.job_id)
            return []

    def _submit_followups(self, specs: List[JobSpec], parent: JobResult) -> List[str]:
        submitted: List[str] = []
        for spec in specs:
            # Tag the follow-up so we can trace the chain.
            spec = spec.with_metadata(
                parent_job_id=parent.job_id,
                parent_spec_id=parent.spec_id,
            )
            try:
                job_id = self._gatehouse.submit(spec)
                submitted.append(job_id)
            except Exception:
                logger.exception(
                    "failed to submit follow-up spec_id=%s parent_job_id=%s",
                    spec.spec_id, parent.job_id,
                )
        return submitted

    # ------------------------------------------------------------------ #
    # Feedback emission
    # ------------------------------------------------------------------ #

    def _emit(self, signal: FeedbackSignal) -> None:
        try:
            self._planner.handle_feedback(signal)
        except Exception:
            logger.exception("planner.handle_feedback raised signal=%s", signal.kind)
        if self._on_feedback is not None:
            try:
                self._on_feedback(signal)
            except Exception:
                logger.exception("on_feedback callback raised signal=%s", signal.kind)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _safe_keys(obj: Any) -> List[str]:
    if isinstance(obj, dict):
        return list(obj.keys())
    if isinstance(obj, list):
        return [f"[{i}]" for i in range(len(obj))]
    return []


def _truncate(value: Any, limit: int = 500) -> str:
    if value is None:
        return ""
    text = value if isinstance(value, str) else repr(value)
    return text if len(text) <= limit else text[:limit] + "...[truncated]"
