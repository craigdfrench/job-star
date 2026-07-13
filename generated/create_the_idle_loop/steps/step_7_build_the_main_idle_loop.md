# Step: Build the Main Idle Loop

I'll tie together all six previously-built components into a single orchestrating loop. The loop will:
1. Sleep for N minutes between cycles (interruptible for graceful shutdown)
2. Check resource availability via `resource_checker`
3. Peek the next eligible step from the idle-opportunistic `queue`
4. Acquire locks and run `conflict_checker` against active jobs
5. Execute the step under `supervisor` supervision
6. Record outcome via `progress`
7. Release locks and repeat

I'll also add POSIX signal handling for graceful shutdown, structured logging, and a `shutdown()` function that can be called programmatically.

## File: `jobstar/idle/loop.py`

```python
"""
jobstar/idle/loop.py

Main idle loop orchestrator.

Ties together:
  - resource_checker  : decide whether the system is idle enough to work
  - queue             : peek/pop the next eligible idle-opportunistic step
  - locks             : exclusive access to resources the step touches
  - conflict_checker  : ensure the step does not collide with active jobs
  - supervisor        : run the step's action under timeout/resource guards
  - progress          : record the outcome and advance the step's state

Public API:
  run_idle_loop(config, ...)   blocking; returns when shutdown is requested
  shutdown(reason="")          request graceful shutdown from any thread/signal
  is_shutdown_requested()      poll the flag
"""

from __future__ import annotations

import logging
import signal
import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any, Callable, Optional

from jobstar.idle import resource_checker as rc_mod
from jobstar.idle import queue as queue_mod
from jobstar.idle import locks as locks_mod
from jobstar.idle import conflict_checker as conflict_mod
from jobstar.idle import supervisor as supervisor_mod
from jobstar.idle import progress as progress_mod

log = logging.getLogger("jobstar.idle.loop")


# ---------------------------------------------------------------------------
# Shutdown coordination
# ---------------------------------------------------------------------------

# A single module-level event so that signal handlers and programmatic
# shutdown() calls can all flip the same flag without holding the GIL
# in a busy loop.
_SHUTDOWN_EVENT: threading.Event = threading.Event()
_SHUTDOWN_REASON: str = ""

# Track whether we have installed signal handlers so we don't double-install
_SIGNALS_INSTALLED: bool = False
_SIGNALS_INSTALLED_LOCK = threading.Lock()


def shutdown(reason: str = "") -> None:
    """Request a graceful shutdown of any running idle loop.

    Safe to call from signal handlers, other threads, or the main thread.
    The loop will finish the current supervised step (if any) and then exit
    at the top of the next cycle, or interrupt its sleep immediately.
    """
    global _SHUTDOWN_REASON
    _SHUTDOWN_REASON = reason or "shutdown requested"
    log.info("idle-loop shutdown requested: %s", _SHUTDOWN_REASON)
    _SHUTDOWN_EVENT.set()


def is_shutdown_requested() -> bool:
    return _SHUTDOWN_EVENT.is_set()


def _clear_shutdown() -> None:
    global _SHUTDOWN_REASON
    _SHUTDOWN_REASON = ""
    _SHUTDOWN_EVENT.clear()


# ---------------------------------------------------------------------------
# Config normalization
# ---------------------------------------------------------------------------

@dataclass
class LoopConfig:
    """Flattened, validated view of the idle-loop configuration.

    Built from the raw config dict produced by the config schema step.
    All time values are in seconds.
    """
    cycle_interval_s: float = 300.0           # N minutes between cycles
    max_steps_per_cycle: int = 1              # how many steps to attempt per cycle
    resource_profile: str = "default"         # key into resource thresholds
    supervisor_timeout_s: float = 1800.0
    supervisor_memory_limit_mb: Optional[float] = None
    supervisor_cpu_limit_percent: Optional[float] = None
    progress_backend: str = "sqlite"
    progress_db_path: str = "jobstar_idle_progress.db"
    queue_backend: str = "sqlite"
    queue_db_path: str = "jobstar_idle_queue.db"
    locks_backend: str = "sqlite"
    locks_db_path: str = "jobstar_idle_locks.db"
    conflict_backend: str = "sqlite"
    conflict_db_path: str = "jobstar_idle_conflicts.db"
    resource_sample_interval_s: float = 1.0
    on_step_error: str = "record_and_continue"  # record_and_continue | stop

    @classmethod
    def from_dict(cls, cfg: dict) -> "LoopConfig":
        cfg = dict(cfg or {})
        # Support nested sections if present, but also flat keys.
        loop_cfg = cfg.get("loop", cfg)
        res_cfg = cfg.get("resources", {})
        sup_cfg = cfg.get("supervisor", {})
        prog_cfg = cfg.get("progress", {})
        q_cfg = cfg.get("queue", {})
        lock_cfg = cfg.get("locks", {})
        conflict_cfg = cfg.get("conflict", {})

        return cls(
            cycle_interval_s=float(loop_cfg.get("cycle_interval_s",
                                                loop_cfg.get("cycle_interval_minutes", 5) * 60)),
            max_steps_per_cycle=int(loop_cfg.get("max_steps_per_cycle", 1)),
            resource_profile=str(loop_cfg.get("resource_profile", "default")),
            supervisor_timeout_s=float(sup_cfg.get("timeout_s", 1800.0)),
            supervisor_memory_limit_mb=(
                float(sup_cfg["memory_limit_mb"]) if sup_cfg.get("memory_limit_mb") else None
            ),
            supervisor_cpu_limit_percent=(
                float(sup_cfg["cpu_limit_percent"]) if sup_cfg.get("cpu_limit_percent") else None
            ),
            progress_backend=str(prog_cfg.get("backend", "sqlite")),
            progress_db_path=str(prog_cfg.get("db_path", "jobstar_idle_progress.db")),
            queue_backend=str(q_cfg.get("backend", "sqlite")),
            queue_db_path=str(q_cfg.get("db_path", "jobstar_idle_queue.db")),
            locks_backend=str(lock_cfg.get("backend", "sqlite")),
            locks_db_path=str(lock_cfg.get("db_path", "jobstar_idle_locks.db")),
            conflict_backend=str(conflict_cfg.get("backend", "sqlite")),
            conflict_db_path=str(conflict_cfg.get("db_path", "jobstar_idle_conflicts.db")),
            resource_sample_interval_s=float(res_cfg.get("sample_interval_s", 1.0)),
            on_step_error=str(loop_cfg.get("on_step_error", "record_and_continue")),
        )


# ---------------------------------------------------------------------------
# Component factory helpers
# ---------------------------------------------------------------------------

class _Components:
    """Holds constructed instances of each subsystem for the loop's lifetime.

    Keeping construction in one place makes it easy to swap backends later
    and keeps the loop body readable.
    """

    def __init__(self, cfg: LoopConfig):
        self.cfg = cfg

        # Resource checker
        self.resource_checker = rc_mod.ResourceChecker(
            profile=cfg.resource_profile,
            sample_interval_s=cfg.resource_sample_interval_s,
        )

        # Queue
        self.queue = queue_mod.IdleQueue(
            backend=cfg.queue_backend,
            db_path=cfg.queue_db_path,
        )

        # Lock manager
        self.locks = locks_mod.LockManager(
            backend=cfg.locks_backend,
            db_path=cfg.locks_db_path,
        )

        # Conflict checker
        self.conflicts = conflict_mod.ConflictChecker(
            backend=cfg.conflict_backend,
            db_path=cfg.conflict_db_path,
        )

        # Supervisor
        self.supervisor = supervisor_mod.Supervisor(
            timeout_s=cfg.supervisor_timeout_s,
            memory_limit_mb=cfg.supervisor_memory_limit_mb,
            cpu_limit_percent=cfg.supervisor_cpu_limit_percent,
        )

        # Progress recorder
        self.progress = progress_mod.ProgressTracker(
            backend=cfg.progress_backend,
            db_path=cfg.progress_db_path,
        )

    def close(self) -> None:
        for comp in (self.queue, self.locks, self.conflicts, self.progress):
            closer = getattr(comp, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # noqa: BLE001
                    log.exception("error closing idle-loop component %r", comp)


# ---------------------------------------------------------------------------
# Interruptible sleep
# ---------------------------------------------------------------------------

def _interruptible_sleep(seconds: float) -> bool:
    """Sleep for up to `seconds`. Return True if interrupted by shutdown."""
    if seconds <= 0:
        return is_shutdown_requested()
    # Wait on the event with a timeout; event.set() wakes immediately.
    interrupted = _SHUTDOWN_EVENT.wait(timeout=seconds)
    return bool(interrupted)


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

def _install_signal_handlers() -> None:
    global _SIGNALS_INSTALLED
    with _SIGNALS_INSTALLED_LOCK:
        if _SIGNALS_INSTALLED:
            return
        # Only install on the main thread; signal.* can only be called there.
        if threading.current_thread() is not threading.main_thread():
            log.debug("not installing signal handlers: not main thread")
            return

        def _handler(signum, _frame):
            name = signal.Signals(signum).name
            shutdown(reason=f"received {name}")

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, _handler)
            except (ValueError, OSError):  # pragma: no cover - platform dependent
                log.warning("could not install handler for %s", sig)

        _SIGNALS_INSTALLED = True
        log.debug("idle-loop signal handlers installed")


# ---------------------------------------------------------------------------
# Single step execution
# ---------------------------------------------------------------------------

def _execute_one_step(
    components: _Components,
    step: dict,
) -> dict:
    """Run a single idle step end-to-end.

    Returns a result dict describing what happened. The caller decides
    whether to continue or stop based on `cfg.on_step_error`.
    """
    step_id = step.get("id") or step.get("step_id") or "<unknown>"
    action = step.get("action")
    resources = step.get("resources", []) or []
    job_id = step.get("job_id")

    result: dict[str, Any] = {
        "step_id": step_id,
        "status": "unknown",
        "started_at": time.time(),
        "ended_at": None,
        "error": None,
    }

    acquired_locks: list[str] = []

    try:
        # --- Acquire locks for declared resources -------------------------
        for res in resources:
            res_name = str(res)
            ok = components.locks.acquire(res_name, owner="idle-loop")
            if not ok:
                result["status"] = "skipped_lock_busy"
                result["error"] = f"resource {res_name!r} locked by another owner"
                log.info("step %s skipped: %s locked", step_id, res_name)
                return result
            acquired_locks.append(res_name)

        # --- Conflict check against active jobs ---------------------------
        conflict = components.conflicts.check(step)
        if conflict:
            result["status"] = "skipped_conflict"
            result["error"] = conflict
            log.info("step %s skipped due to conflict: %s", step_id, conflict)
            return result

        # --- Supervised execution ----------------------------------------
        if not callable(action):
            # Some steps may carry a fully-qualified action reference string
            # that the supervisor knows how to resolve; pass it through.
            action_ref = step.get("action_ref")
            if action_ref:
                action = action_ref
            else:
                raise ValueError(
                    f"step {step_id} has no callable 'action' or 'action_ref'"
                )

        log.info("executing idle step %s under supervision", step_id)
        outcome = components.supervisor.run(action, context=step)

        result["ended_at"] = time.time()
        result["outcome"] = outcome
        result["status"] = "completed"
        return result

    except supervisor_mod.SupervisorTimeout as exc:
        result["status"] = "timeout"
        result["error"] = str(exc)
        log.warning("step %s timed out: %s", step_id, exc)
        return result

    except supervisor_mod.SupervisorResourceLimitExceeded as exc:
        result["status"] = "resource_limit"
        result["error"] = str(exc)
        log.warning("step %s exceeded resource limits: %s", step_id, exc)
        return result

    except Exception as exc:  # noqa: BLE001
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
        log.exception("step %s raised an error", step_id)
        return result

    finally:
        # --- Release any locks we acquired --------------------------------
        for res_name in acquired_locks:
            try:
                components.locks.release(res_name, owner="idle-loop")
            except Exception:  # noqa: BLE001
                log.exception("failed to release lock %s for step %s", res_name, step_id)

        # --- Record progress ----------------------------------------------
        if result["ended_at"] is None:
            result["ended_at"] = time.time()
        try:
            components.progress.record(
                step_id=step_id,
                job_id=job_id,
                status=result["status"],
                started_at=result["started_at"],
                ended_at=result["ended_at"],
                error=result.get("error"),
                outcome=result.get("outcome"),
            )
        except Exception:  # noqa: BLE001
            log.exception("failed to record progress for step %s", step_id)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_idle_loop(
    config: dict | LoopConfig,
    *,
    max_cycles: Optional[int] = None,
    cycle_callback: Optional[Callable[[int, dict], None]] = None,
) -> dict:
    """Run the idle loop until shutdown is requested or max_cycles reached.

    Parameters
    ----------
    config
        Either a raw config dict (per the config schema) or a LoopConfig.
    max_cycles
        If set, stop after this many cycles (useful for tests). None = run
        forever until shutdown().
    cycle_callback
        Optional hook invoked after each cycle with (cycle_index, summary).

    Returns
    -------
    dict
        Summary: {"cycles": int, "steps_attempted": int,
                  "steps_completed": int, "shutdown_reason": str}
    """
    cfg = config if isinstance(config, LoopConfig) else LoopConfig.from_dict(config)
    _clear_shutdown()
    _install_signal_handlers()

    components = _Components(cfg)
    summary = {
        "cycles": 0,
        "steps_attempted": 0,
        "steps_completed": 0,
        "shutdown_reason": "",
    }

    log.info(
        "idle loop starting (cycle_interval=%.0fs, max_steps_per_cycle=%d, profile=%s)",
        cfg.cycle_interval_s, cfg.max_steps_per_cycle, cfg.resource_profile,
    )

    try:
        cycle_index = 0
        while not is_shutdown_requested():
            if max_cycles is not None and cycle_index >= max_cycles:
                log.info("idle loop reached max_cycles=%d, stopping", max_cycles)
                break

            cycle_index += 1
            summary["cycles"] = cycle_index
            cycle_summary = _run_cycle(components, cfg, cycle_index)
            summary["steps_attempted"] += cycle_summary["attempted"]
            summary["steps_completed"] += cycle_summary["completed"]

            if cycle_callback:
                try:
                    cycle_callback(cycle_index, cycle_summary)
                except Exception:  # noqa: BLE001
                    log.exception("cycle_callback raised; continuing")

            # Interruptible sleep between cycles.
            if is_shutdown_requested():
                break
            interrupted = _interruptible_sleep(cfg.cycle_interval_s)
            if interrupted:
                log.info("idle loop sleep interrupted by shutdown")
                break

    finally:
        components.close()
        summary["shutdown_reason"] = _SHUTDOWN_REASON
        log.info("idle loop exiting: %s", summary)

    return summary


def _run_cycle(components: _Components, cfg: LoopConfig, cycle_index: int) -> dict:
    """Execute one cycle: resource check -> up to N steps -> done."""
    cycle_summary = {"attempted": 0, "completed": 0, "skipped": 0, "errors": 0}

    # 1. Resource availability
    try:
        available, report = components.resource_checker.is_available()
    except Exception:  # noqa: BLE001
        log.exception("resource checker failed; skipping cycle")
        return cycle_summary

    if not available:
        log.debug("cycle %d: resources not available: %s", cycle_index, report)
        return cycle_summary

    log.debug("cycle %d: resources available, proceeding", cycle_index)

    # 2. Attempt up to max_steps_per_cycle steps
    for _ in range(cfg.max_steps_per_cycle):
        if is_shutdown_requested():
            break

        # 3. Peek/pop next eligible step
        try:
            step = components.queue.peek_next()
        except Exception:  # noqa: BLE001
            log.exception("queue.peek_next failed; ending cycle")
            break

        if step is None:
            log.debug("cycle %d: queue empty", cycle_index)
            break

        # Mark as in-flight so other workers don't grab it.
        try:
            popped = components.queue.pop(step["id"])
        except Exception:  # noqa: BLE001
            log.exception("queue.pop failed for step %s; ending cycle", step.get("id"))
            break

        if popped is None:
            # Someone else grabbed it between peek and pop; try next.
            cycle_summary["skipped"] += 1
            continue

        cycle_summary["attempted"] += 1

        # 4-6. Conflicts -> supervised execution -> progress (inside helper)
        result = _execute_one_step(components, popped)

        status = result.get("status", "unknown")
        if status == "completed":
            cycle_summary["completed"] += 1
        elif status in ("skipped_lock_busy", "skipped_conflict"):
            cycle_summary["skipped"] += 1
            # Re-enqueue skipped steps so they can be retried later.
            try:
                components.queue.requeue(popped)
            except Exception:  # noqa: BLE001
                log.exception("failed to requeue skipped step %