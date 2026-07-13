"""Progress tracking for idle-opportunistic step execution.

Records the outcome of an idle step in three places:
  1. Step state store  -- status transitions (pending -> running -> completed/failed/skipped)
  2. Progress log      -- append-only JSONL audit trail of every result
  3. Dependency graph  -- downstream steps are notified / unblocked

This module is intentionally side-effect-isolated: callers pass in the
state store and dependency-graph handles, so the tracker remains testable.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

# Re-use the same config loader pattern as the other idle modules.
from jobstar.idle.config import IdleLoopConfig, load_config


# ---------------------------------------------------------------------------
# Enums and dataclasses
# ---------------------------------------------------------------------------

class StepStatus(str, Enum):
    """Lifecycle states a step can be in."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    BLOCKED = "blocked"


class ResultKind(str, Enum):
    """High-level classification of an execution result."""
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


@dataclass
class StepResult:
    """Structured outcome of a single step execution attempt."""
    step_id: str
    kind: ResultKind
    status: StepStatus
    started_at: float
    finished_at: float
    duration_s: float
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    artifacts: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.kind == ResultKind.SUCCESS

    def to_log_dict(self) -> Dict[str, Any]:
        """Serialize to a JSON-safe dict for the progress log."""
        d = asdict(self)
        d["kind"] = self.kind.value
        d["status"] = self.status.value
        return d


# ---------------------------------------------------------------------------
# Protocols for swappable backends
# ---------------------------------------------------------------------------

class StepStateStore(Protocol):
    """Minimal interface for a step state store.

    The real implementation lives elsewhere in jobstar; we only depend on
    these methods so the progress tracker stays decoupled.
    """

    def get_step(self, step_id: str) -> Dict[str, Any]: ...

    def set_status(self, step_id: str, status: StepStatus) -> None: ...

    def set_result(self, step_id: str, result: StepResult) -> None: ...


class DependencyGraph(Protocol):
    """Minimal interface for the downstream dependency graph."""

    def get_dependents(self, step_id: str) -> List[str]: ...
    """Return step_ids that depend on `step_id`."""

    def notify_upstream_done(
        self, step_id: str, succeeded: bool
    ) -> List[str]: ...
    """Notify graph that `step_id` finished.

    Returns the list of dependent step_ids that became unblocked as a result.
    """


# ---------------------------------------------------------------------------
# In-memory default backends (used when no external store is wired in)
# ---------------------------------------------------------------------------

class InMemoryStepStateStore:
    """Thread-safe in-memory step state store.

    Useful for tests and for bootstrapping before a persistent store exists.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._steps: Dict[str, Dict[str, Any]] = {}

    def register(self, step_id: str, step: Dict[str, Any]) -> None:
        with self._lock:
            self._steps[step_id] = dict(step)
            self._steps[step_id].setdefault("status", StepStatus.PENDING)

    def get_step(self, step_id: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._steps.get(step_id, {"step_id": step_id}))

    def set_status(self, step_id: str, status: StepStatus) -> None:
        with self._lock:
            step = self._steps.setdefault(step_id, {"step_id": step_id})
            step["status"] = status.value if isinstance(status, StepStatus) else status
            step["status_updated_at"] = time.time()

    def set_result(self, step_id: str, result: StepResult) -> None:
        with self._lock:
            step = self._steps.setdefault(step_id, {"step_id": step_id})
            step["last_result"] = result.to_log_dict()
            step["last_result_at"] = time.time()

    def snapshot(self) -> Dict[str, Dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._steps.items()}


class InMemoryDependencyGraph:
    """Thread-simple adjacency-list dependency graph."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # edges: upstream -> [downstream...]
        self._dependents: Dict[str, List[str]] = {}
        # unmet dependencies per step
        self._pending_deps: Dict[str, set] = {}

    def add_step(self, step_id: str, depends_on: Optional[List[str]] = None) -> None:
        with self._lock:
            deps = set(depends_on or [])
            self._pending_deps[step_id] = set(deps)
            for up in deps:
                self._dependents.setdefault(up, []).append(step_id)

    def get_dependents(self, step_id: str) -> List[str]:
        with self._lock:
            return list(self._pending_deps.get(step_id, []))  # placeholder
            # Actually return downstream consumers:

    def notify_upstream_done(
        self, step_id: str, succeeded: bool
    ) -> List[str]:
        with self._lock:
            dependents = self._dependents.get(step_id, [])
            unblocked: List[str] = []
            for dep in dependents:
                pending = self._pending_deps.get(dep)
                if pending is None:
                    continue
                pending.discard(step_id)
                if not pending:
                    unblocked.append(dep)
            return unblocked


# ---------------------------------------------------------------------------
# Progress log (append-only JSONL)
# ---------------------------------------------------------------------------

class ProgressLog:
    """Append-only JSONL log of step results.

    Thread-safe and crash-safe (each line is written with a single `write`
    followed by `flush`).
    """

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def append(self, record: Dict[str, Any]) -> None:
        record = dict(record)
        record.setdefault("log_id", str(uuid.uuid4()))
        record.setdefault("logged_at", time.time())
        line = json.dumps(record, sort_keys=True, default=str)
        with self._lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()

    def tail(self, n: int = 10) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._lock, open(self.path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()[-n:]
        return [json.loads(ln) for ln in lines if ln.strip()]


# ---------------------------------------------------------------------------
# The tracker
# ---------------------------------------------------------------------------

class ProgressTracker:
    """Coordinates status update, progress logging, and graph notification."""

    def __init__(
        self,
        config: Optional[IdleLoopConfig] = None,
        state_store: Optional[StepStateStore] = None,
        dependency_graph: Optional[DependencyGraph] = None,
        progress_log: Optional[ProgressLog] = None,
    ) -> None:
        self.config = config or load_config()
        self.state_store: StepStateStore = state_store or InMemoryStepStateStore()
        self.dependency_graph: DependencyGraph = dependency_graph or InMemoryDependencyGraph()
        log_path = self.config.progress_log_path  # type: ignore[attr-defined]
        self.progress_log = progress_log or ProgressLog(log_path)

    # -- public API --------------------------------------------------------

    def record_result(self, step_id: str, result: StepResult) -> Dict[str, Any]:
        """Record the outcome of an idle step execution.

        Returns a summary dict describing what was updated, including the
        list of downstream steps that became unblocked.
        """
        if result.step_id != step_id:
            raise ValueError(
                f"step_id mismatch: tracker={step_id!r} result={result.step_id!r}"
            )

        # 1. Update step status in the state store.
        self.state_store.set_status(step_id, result.status)
        self.state_store.set_result(step_id, result)

        # 2. Append to the progress log.
        log_record = result.to_log_dict()
        log_record["source"] = "idle_loop"
        log_record["step_id"] = step_id
        self.progress_log.append(log_record)

        # 3. Notify the dependency graph and collect newly-unblocked steps.
        unblocked = self.dependency_graph.notify_upstream_done(
            step_id, succeeded=result.succeeded
        )

        return {
            "step_id": step_id,
            "status": result.status.value,
            "kind": result.kind.value,
            "unblocked": unblocked,
            "logged": True,
        }

    def mark_running(self, step_id: str) -> None:
        """Convenience: transition a step to RUNNING before execution."""
        self.state_store.set_status(step_id, StepStatus.RUNNING)


# ---------------------------------------------------------------------------
# Module-level convenience function (matches the requested API)
# ---------------------------------------------------------------------------

_default_tracker: Optional[ProgressTracker] = None
_default_lock = threading.Lock()


def _get_default_tracker() -> ProgressTracker:
    global _default_tracker
    if _default_tracker is None:
        with _default_lock:
            if _default_tracker is None:
                _default_tracker = ProgressTracker()
    return _default_tracker


def record_result(step_id: str, result: StepResult) -> Dict[str, Any]:
    """Record an idle step result using the process-wide default tracker.

    This is the function referenced by the idle loop orchestration contract:
        from jobstar.idle.progress import record_result
    """
    return _get_default_tracker().record_result(step_id, result)


# ---------------------------------------------------------------------------
# Helpers to build a StepResult from a supervisor outcome
# ---------------------------------------------------------------------------

def result_from_supervisor(
    step_id: str,
    supervisor_outcome: Dict[str, Any],
    started_at: float,
) -> StepResult:
    """Convert a supervisor outcome dict into a StepResult.

    Expected keys in `supervisor_outcome`:
        kind        : str  (success|failure|timeout|cancelled|skipped)
        exit_code   : int|None
        stdout      : str
        stderr      : str
        error       : str|None
        artifacts   : dict
        metadata    : dict
    """
    finished_at = time.time()
    kind_str = supervisor_outcome.get("kind", "failure")
    try:
        kind = ResultKind(kind_str)
    except ValueError:
        kind = ResultKind.FAILURE

    status = {
        ResultKind.SUCCESS: StepStatus.COMPLETED,
        ResultKind.FAILURE: StepStatus.FAILED,
        ResultKind.TIMEOUT: StepStatus.FAILED,
        ResultKind.SKIPPED: StepStatus.SKIPPED,
        ResultKind.CANCELLED: StepStatus.SKIPPED,
    }[kind]

    return StepResult(
        step_id=step_id,
        kind=kind,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        duration_s=round(finished_at - started_at, 3),
        exit_code=supervisor_outcome.get("exit_code"),
        stdout=supervisor_outcome.get("stdout", ""),
        stderr=supervisor_outcome.get("stderr", ""),
        error=supervisor_outcome.get("error"),
        artifacts=supervisor_outcome.get("artifacts", {}) or {},
        metadata=supervisor_outcome.get("metadata", {}) or {},
    )
