"""jobstar/idle/conflict_checker.py

Conflict detection for idle-opportunistic steps.

Given a candidate step and the set of currently running jobs, determine
whether executing the step would conflict. Conflicts arise from:

  1. File path overlap — the step and a running job touch the same file
     with incompatible access modes (write/write or write/read).
  2. Named lock overlap — the step requires a lock already held by a
     running job.
  3. Component overlap — the step targets a logical component that a
     running job is already mutating.

The function returns (ok: bool, reasons: list[str]). If ok is True,
reasons is empty. If ok is False, reasons contains human-readable strings
explaining each conflict, suitable for logging or surfacing to the user.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from jobstar.idle.locks import LockManager


@dataclass
class StepFootprint:
    """Normalized description of what a step touches.

    Extracted from a step dict via extract_footprint(). Kept as a dataclass
    so the conflict logic is explicit and testable.
    """
    step_id: str
    write_paths: set[str] = field(default_factory=set)
    read_paths: set[str] = field(default_factory=set)
    locks: set[str] = field(default_factory=set)
    components: set[str] = field(default_factory=set)


def _as_list(val: Any) -> list[str]:
    if val is None:
        return []
    if isinstance(val, str):
        return [val]
    if isinstance(val, (list, tuple, set)):
        return [str(v) for v in val]
    return [str(val)]


def extract_footprint(step: dict) -> StepFootprint:
    """Pull a StepFootprint out of a step dict.

    Recognized keys (all optional):
      id / step_id          — identifier
      writes / write_paths  — list of file paths the step will write
      reads / read_paths    — list of file paths the step will read
      locks / requires_locks — named locks
      component / components — logical component(s) targeted
    """
    sid = step.get("id") or step.get("step_id") or "<unknown>"
    return StepFootprint(
        step_id=str(sid),
        write_paths={str(p) for p in _as_list(step.get("writes") or step.get("write_paths"))},
        read_paths={str(p) for p in _as_list(step.get("reads") or step.get("read_paths"))},
        locks={str(l) for l in _as_list(step.get("locks") or step.get("requires_locks"))},
        components={str(c) for c in _as_list(step.get("component") or step.get("components"))},
    )


def _norm(p: str) -> str:
    """Normalize a path for comparison."""
    try:
        return str(Path(p).resolve())
    except (OSError, ValueError):
        return str(Path(p).absolute())


def _path_conflicts(a_writes: set[str], a_reads: set[str],
                    b_writes: set[str], b_reads: set[str]) -> list[tuple[str, str, str]]:
    """Return list of (path, mode_a, mode_b) for conflicting path pairs.

    Rules:
      write vs write  -> conflict (same file, both mutating)
      write vs read   -> conflict (reader may see partial write)
      read  vs read   -> OK
    """
    conflicts: list[tuple[str, str, str]] = []
    aw = {_norm(p): p for p in a_writes}
    ar = {_norm(p): p for p in a_reads}
    bw = {_norm(p): p for p in b_writes}
    br = {_norm(p): p for p in b_reads}

    # write vs write
    for np, ap in aw.items():
        if np in bw:
            conflicts.append((ap, "write", "write"))
    # write vs read (both directions)
    for np, ap in aw.items():
        if np in br:
            conflicts.append((ap, "write", "read"))
    for np, bp in bw.items():
        if np in ar:
            conflicts.append((bp, "read", "write"))
    return conflicts


def check_conflicts(
    step: dict,
    running_jobs: list[dict],
    lock_manager: Optional[LockManager] = None,
) -> tuple[bool, list[str]]:
    """Check whether a step conflicts with currently running jobs.

    Args:
        step: candidate step dict (see extract_footprint for expected keys).
        running_jobs: list of job dicts currently executing. Each should
            have an "id"/"job_id" and the same footprint keys as a step,
            plus optionally "held_locks" (list of lock keys already acquired).
        lock_manager: optional LockManager. If provided, named locks are
            also checked against the live lock table. If None, only the
            running_jobs' declared locks/components are considered.

    Returns:
        (ok, reasons) where ok is True if the step is safe to run, and
        reasons is a list of human-readable conflict descriptions (empty
        when ok is True).
    """
    fp = extract_footprint(step)
    reasons: list[str] = []

    # Build footprints for running jobs
    running_fps: list[tuple[str, StepFootprint, set[str]]] = []
    for job in running_jobs:
        jid = job.get("id") or job.get("job_id") or "<unknown>"
        jfp = extract_footprint(job)
        held = {str(l) for l in _as_list(job.get("held_locks"))}
        running_fps.append((str(jid), jfp, held))

    # 1. File path conflicts
    for jid, jfp, _ in running_fps:
        path_conflicts = _path_conflicts(fp.write_paths, fp.read_paths,
                                         jfp.write_paths, jfp.read_paths)
        for path, mode_a, mode_b in path_conflicts:
            reasons.append(
                f"file conflict: step {fp.step_id} ({mode_a}) vs job {jid} "
                f"({mode_b}) on {path}"
            )

    # 2. Named lock conflicts
    # A lock conflicts if it's declared by a running job OR held in the live
    # lock manager (and not by this step's owner).
    for lock in fp.locks:
        # Check running jobs' declared locks
        for jid, jfp, held in running_fps:
            if lock in jfp.locks or lock in held:
                reasons.append(
                    f"lock conflict: step {fp.step_id} requires lock '{lock}' "
                    f"already claimed by job {jid}"
                )
                break
        else:
            # Check live lock manager
            if lock_manager is not None and lock_manager.is_locked(lock):
                reasons.append(
                    f"lock conflict: step {fp.step_id} requires lock '{lock}' "
                    f"which is currently held"
                )

    # 3. Component conflicts
    # Two jobs targeting the same component is a conflict only if at least
    # one is writing to it. We treat component presence as "mutating" by
    # default, since idle-opportunistic steps are typically maintenance
    # actions on a component. If a step explicitly marks a component as
    # read-only via "read_only_components", we relax.
    ro_components = {str(c) for c in _as_list(step.get("read_only_components"))}
    for jid, jfp, _ in running_fps:
        j_ro = {str(c) for c in _as_list({})}  # jobs don't currently declare RO
        overlap = fp.components & jfp.components
        for comp in overlap:
            if comp in ro_components and comp in j_ro:
                continue  # both read-only, fine
            reasons.append(
                f"component conflict: step {fp.step_id} and job {jid} both "
                f"target component '{comp}'"
            )

    ok = len(reasons) == 0
    return ok, reasons


def acquire_step_locks(
    step: dict,
    lock_manager: LockManager,
    owner: Optional[str] = None,
) -> tuple[bool, list, list[str]]:
    """Convenience: try to acquire all locks a step needs.

    Returns (success, handles, reasons). On failure, any locks acquired
    are released before returning, so the caller doesn't need cleanup.

    This is *not* part of check_conflicts — it's a helper for the idle
    loop to call after a conflict check passes, to actually claim the
    locks atomically.
    """
    fp = extract_footprint(step)
    owner = owner or fp.step_id
    handles: list = []
    reasons: list[str] = []

    # Sort locks for deterministic acquisition order (reduces deadlock risk)
    for lock in sorted(fp.locks):
        h = lock_manager.acquire(lock, owner=owner)
        if h is None:
            reasons.append(f"could not acquire lock '{lock}' for step {fp.step_id}")
            # Roll back
            for acquired in handles:
                lock_manager.release(acquired)
            return False, [], reasons
        handles.append(h)

    return True, handles, []
