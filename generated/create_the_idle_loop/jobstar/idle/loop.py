"""Main idle loop orchestrator for Job-Star.

Ties together: resource checker, idle-opportunistic queue, conflict
detection, supervised execution, and progress tracking. Each cycle is
fully logged for observability.
"""

from __future__ import annotations

import time
import traceback
from typing import Any, Dict, Optional

from .resource_checker import ResourceChecker, ResourceSnapshot
from .queue import IdleQueue
from .conflict_checker import ConflictChecker
from .supervisor import Supervisor, StepResult
from .progress import ProgressTracker
from .logging_setup import (
    IdleLogAdapter,
    MetricsEmitter,
    setup_idle_logger,
)


class IdleLoop:
    """Background loop that opportunistically executes idle steps.

    Lifecycle per cycle:
      1. Sleep for configured interval.
      2. Sample resources.
      3. If resources insufficient -> log + skip.
      4. Peek next eligible step from idle queue.
      5. If none -> log + continue.
      6. Acquire locks / check conflicts.
      7. If conflicts -> log + skip (leave in queue).
      8. Supervised execution with timeout.
      9. Record progress (success / failure / skipped).
     10. Release locks.
     11. Emit metrics + structured log for the cycle.
    """

    def __init__(
        self,
        resource_checker: ResourceChecker,
        queue: IdleQueue,
        conflict_checker: ConflictChecker,
        supervisor: Supervisor,
        progress_tracker: ProgressTracker,
        interval_s: float = 60.0,
        log_file: Optional[str] = None,
        log_level: int = 20,  # INFO
        console: bool = False,
        metrics_emitter: Optional[MetricsEmitter] = None,
    ) -> None:
        self.resource_checker = resource_checker
        self.queue = queue
        self.conflict_checker = conflict_checker
        self.supervisor = supervisor
        self.progress_tracker = progress_tracker
        self.interval_s = interval_s
        self._stop = False
        self._cycle_count = 0

        self._logger = setup_idle_logger(
            log_file=log_file, level=log_level, console=console,
        )
        self.log = IdleLogAdapter(self._logger)
        self.metrics = metrics_emitter or MetricsEmitter()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Request the loop to stop after the current cycle."""
        self.log.info("stop_requested")
        self._stop = True

    def run_once(self) -> Dict[str, Any]:
        """Execute a single cycle and return a summary dict."""
        self._cycle_count += 1
        cycle = self._cycle_count
        t0 = time.monotonic()
        self.log.info("cycle_start", cycle=cycle)
        self.metrics.cycle_started()

        summary: Dict[str, Any] = {
            "cycle": cycle,
            "outcome": "skipped",
            "step_id": None,
            "duration_s": 0.0,
        }

        try:
            summary = self._run_cycle(cycle)
        except Exception as exc:
            self.log.exception("cycle_error", cycle=cycle, error=str(exc))
            summary["outcome"] = "error"
            summary["error"] = str(exc)
        finally:
            duration = time.monotonic() - t0
            summary["duration_s"] = round(duration, 3)
            self.log.info(
                "cycle_end",
                cycle=cycle,
                outcome=summary["outcome"],
                step_id=summary.get("step_id"),
                duration_s=summary["duration_s"],
            )
            self.metrics.cycle_completed(duration, summary["outcome"])

        return summary

    def run_forever(self) -> None:
        """Run cycles until stop() is called or a fatal error occurs."""
        self.log.info("loop_started", interval_s=self.interval_s)
        try:
            while not self._stop:
                self.run_once()
                if self._stop:
                    break
                self.log.debug("sleep", seconds=self.interval_s)
                time.sleep(self.interval_s)
        except KeyboardInterrupt:
            self.log.warning("loop_interrupted", reason="keyboard")
        except Exception as exc:
            self.log.exception("loop_fatal", error=str(exc))
            raise
        finally:
            self.log.info("loop_stopped", cycles_run=self._cycle_count)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _run_cycle(self, cycle: int) -> Dict[str, Any]:
        # 1. Resource check -------------------------------------------------
        snap: ResourceSnapshot = self.resource_checker.check()
        self.log.info(
            "resources",
            cycle=cycle,
            cpu=snap.cpu_percent,
            mem=snap.mem_percent,
            disk=snap.disk_percent,
            available=snap.available,
        )
        self.metrics.resource_snapshot(
            snap.cpu_percent, snap.mem_percent, snap.disk_percent,
        )

        if not snap.available:
            self.log.info(
                "resources_insufficient",
                cycle=cycle,
                cpu=snap.cpu_percent,
                mem=snap.mem_percent,
                disk=snap.disk_percent,
            )
            return {"cycle": cycle, "outcome": "resources_insufficient",
                    "step_id": None}

        # 2. Peek next step -------------------------------------------------
        step = self.queue.peek()
        if step is None:
            self.log.info("queue_empty", cycle=cycle)
            self.metrics.queue_empty()
            return {"cycle": cycle, "outcome": "queue_empty",
                    "step_id": None}

        self.log.info(
            "step_selected",
            cycle=cycle,
            step_id=step.step_id,
            action=step.action,
            priority=step.priority,
        )

        # 3. Conflict detection --------------------------------------------
        conflicts = self.conflict_checker.check(step)
        if conflicts:
            conflict_desc = "; ".join(
                f"{c.resource}:{c.reason}" for c in conflicts
            )
            self.log.warning(
                "conflicts_found",
                cycle=cycle,
                step_id=step.step_id,
                count=len(conflicts),
                conflicts=conflict_desc,
            )
            for c in conflicts:
                self.metrics.conflict_detected(step.step_id, c.resource)
            # Leave step in queue; will retry next cycle.
            return {"cycle": cycle, "outcome": "conflict",
                    "step_id": step.step_id}

        # 4. Acquire locks --------------------------------------------------
        acquired = self.conflict_checker.acquire(step)
        if not acquired:
            self.log.warning(
                "lock_acquire_failed",
                cycle=cycle,
                step_id=step.step_id,
            )
            return {"cycle": cycle, "outcome": "lock_failed",
                    "step_id": step.step_id}

        # 5. Pop from queue now that we hold locks -------------------------
        popped = self.queue.pop()
        if popped is None or popped.step_id != step.step_id:
            # Someone else grabbed it; release and bail.
            self.log.warning(
                "step_lost",
                cycle=cycle,
                step_id=step.step_id,
                popped_id=(popped.step_id if popped else None),
            )
            self.conflict_checker.release(step)
            return {"cycle": cycle, "outcome": "step_lost",
                    "step_id": step.step_id}

        # 6. Supervised execution ------------------------------------------
        exec_t0 = time.monotonic()
        self.log.info(
            "exec_start",
            cycle=cycle,
            step_id=step.step_id,
            timeout_s=step.timeout_s,
        )
        result: StepResult = self.supervisor.execute(step)
        exec_duration = time.monotonic() - exec_t0

        self.log.info(
            "exec_result",
            cycle=cycle,
            step_id=step.step_id,
            status=result.status,
            duration_s=round(exec_duration, 3),
            error=(result.error if result.error else "-"),
        )
        self.metrics.step_executed(
            step.step_id, result.status, exec_duration,
        )

        # 7. Progress tracking ---------------------------------------------
        self.progress_tracker.record(step, result)
        self.log.info(
            "progress_recorded",
            cycle=cycle,
            step_id=step.step_id,
            status=result.status,
            attempts=result.attempts,
        )

        # 8. Release locks --------------------------------------------------
        self.conflict_checker.release(step)

        outcome = "success" if result.status == "success" else "failed"
        return {
            "cycle": cycle,
            "outcome": outcome,
            "step_id": step.step_id,
            "exec_duration_s": round(exec_duration, 3),
        }
