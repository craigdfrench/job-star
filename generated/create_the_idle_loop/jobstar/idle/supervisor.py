"""Supervised execution wrapper for idle-opportunistic steps.

Executes a step's action under strict supervision:
  - enforces a hard timeout
  - captures stdout/stderr
  - isolates crashes so the idle loop never dies
  - returns a structured StepResult

Public API:
    execute_step(step, timeout=None) -> StepResult
"""

from __future__ import annotations

import os
import shlex
import signal
import subprocess
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional, Union

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class StepResult:
    """Structured outcome of a supervised step execution."""

    step_id: str
    success: bool
    return_code: Optional[int] = None
    timed_out: bool = False
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    duration_s: float = 0.0
    mode: str = "unknown"  # "callable" | "command" | "noop"
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "step_id": self.step_id,
            "success": self.success,
            "return_code": self.return_code,
            "timed_out": self.timed_out,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "error": self.error,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_s": round(self.duration_s, 4),
            "mode": self.mode,
            "metadata": self.metadata,
        }

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        status = "OK" if self.success else ("TIMEOUT" if self.timed_out else "FAIL")
        return f"<StepResult {self.step_id} {status} rc={self.return_code} {self.duration_s:.2f}s>"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_S: float = 300.0
MAX_OUTPUT_BYTES: int = 1 * 1024 * 1024  # 1 MiB per stream
MAX_ERROR_LEN: int = 4096


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _truncate(text: str, max_bytes: int = MAX_OUTPUT_BYTES) -> str:
    """Truncate captured output to a sane bound, noting truncation."""
    if text is None:
        return ""
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    kept = encoded[:max_bytes].decode("utf-8", errors="replace")
    return kept + f"\n...[truncated {len(encoded) - max_bytes} bytes]"


def _get_step_id(step: Any) -> str:
    for attr in ("id", "step_id", "name"):
        if hasattr(step, attr):
            val = getattr(step, attr)
            if val:
                return str(val)
    return "<unknown>"


def _resolve_timeout(step: Any, timeout: Optional[float]) -> float:
    if timeout is not None:
        return float(timeout)
    if hasattr(step, "timeout_s") and step.timeout_s is not None:
        return float(step.timeout_s)
    if hasattr(step, "timeout") and step.timeout is not None:
        return float(step.timeout)
    return DEFAULT_TIMEOUT_S


# ---------------------------------------------------------------------------
# Callable execution (threaded, deadline-joined)
# ---------------------------------------------------------------------------

class _CallableOutcome:
    """Holds the result of a threaded callable execution."""

    __slots__ = ("return_code", "stdout", "stderr", "error", "done")

    def __init__(self) -> None:
        self.return_code: Optional[int] = None
        self.stdout: str = ""
        self.stderr: str = ""
        self.error: Optional[str] = None
        self.done: threading.Event = threading.Event()


def _run_callable(action: Callable, step: Any, outcome: _CallableOutcome) -> None:
    try:
        # A step callable may return:
        #   - None / True           -> success, rc 0
        #   - False                 -> failure, rc 1
        #   - int                   -> treated as return code
        #   - (rc, stdout, stderr)  -> explicit triple
        #   - dict with those keys
        result = action(step) if _action_takes_step(action) else action()
        if result is None or result is True:
            outcome.return_code = 0
        elif result is False:
            outcome.return_code = 1
        elif isinstance(result, int):
            outcome.return_code = result
        elif isinstance(result, tuple) and len(result) == 3:
            rc, out, err = result
            outcome.return_code = int(rc) if rc is not None else 0
            outcome.stdout = str(out or "")
            outcome.stderr = str(err or "")
        elif isinstance(result, dict):
            outcome.return_code = int(result.get("return_code", 0) or 0)
            outcome.stdout = str(result.get("stdout", "") or "")
            outcome.stderr = str(result.get("stderr", "") or "")
        else:
            outcome.return_code = 0
            outcome.stdout = str(result)
    except SystemExit as e:  # callable called sys.exit()
        outcome.return_code = int(e.code) if isinstance(e.code, int) else 1
    except BaseException as e:  # noqa: BLE001 - we must capture everything
        outcome.error = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        outcome.return_code = 1
    finally:
        outcome.done.set()


def _action_takes_step(action: Callable) -> bool:
    """Heuristic: does the callable accept a single positional arg?"""
    try:
        import inspect
        sig = inspect.signature(action)
        params = [
            p for p in sig.parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        ]
        # Subtract parameters with defaults from required count
        required = [p for p in params if p.default is p.empty]
        return len(required) >= 1 or len(params) >= 1
    except (ValueError, TypeError):
        return False


def _execute_callable(
    step: Any, action: Callable, timeout: float, step_id: str
) -> StepResult:
    started = _now()
    start_perf = time.monotonic()
    outcome = _CallableOutcome()

    worker = threading.Thread(
        target=_run_callable,
        args=(action, step, outcome),
        name=f"jobstar-idle-{step_id}",
        daemon=True,
    )
    worker.start()
    completed = outcome.done.wait(timeout=timeout)
    duration = time.monotonic() - start_perf

    result = StepResult(
        step_id=step_id,
        success=False,
        started_at=started,
        ended_at=_now(),
        duration_s=duration,
        mode="callable",
    )

    if not completed:
        result.timed_out = True
        result.error = (
            f"Callable exceeded timeout of {timeout}s and was abandoned "
            "(thread is daemonized; cannot be hard-killed)."
        )
        result.return_code = None
        result.metadata["abandoned_thread"] = worker.name
        return result

    result.return_code = outcome.return_code
    result.stdout = _truncate(outcome.stdout)
    result.stderr = _truncate(outcome.stderr)
    result.error = (
        _truncate(outcome.error, MAX_ERROR_LEN) if outcome.error else None
    )
    result.success = (
        outcome.return_code is not None
        and outcome.return_code == 0
        and outcome.error is None
    )
    return result


# ---------------------------------------------------------------------------
# Command execution (subprocess, process-group kill on timeout)
# ---------------------------------------------------------------------------

def _execute_command(
    step: Any, command: Union[str, list], timeout: float, step_id: str
) -> StepResult:
    started = _now()
    start_perf = time.monotonic()

    # Normalize command to a list, preserving shell semantics when given a str.
    if isinstance(command, str):
        shell_cmd = command
        use_shell = True
    else:
        shell_cmd = None
        use_shell = False

    result = StepResult(
        step_id=step_id,
        success=False,
        started_at=started,
        mode="command",
    )

    try:
        proc = subprocess.Popen(
            shell_cmd if use_shell else list(command),
            shell=use_shell,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            start_new_session=True,  # new process group -> can kill the tree
            text=True,
            errors="replace",
            env=os.environ.copy(),
        )
    except (OSError, ValueError) as e:
        result.ended_at = _now()
        result.duration_s = time.monotonic() - start_perf
        result.error = f"Failed to spawn command: {e!r}\n{traceback.format_exc()}"
        result.return_code = None
        return result

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        duration = time.monotonic() - start_perf
        result.ended_at = _now()
        result.duration_s = duration
        result.return_code = proc.returncode
        result.stdout = _truncate(stdout or "")
        result.stderr = _truncate(stderr or "")
        result.success = proc.returncode == 0
        return result

    except subprocess.TimeoutExpired:
        # Kill the entire process group.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass

        # Give it a short grace period, then SIGKILL.
        try:
            stdout, stderr = proc.communicate(timeout=5.0)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            try:
                stdout, stderr = proc.communicate(timeout=5.0)
            except Exception:  # noqa: BLE001
                stdout, stderr = "", ""

        duration = time.monotonic() - start_perf
        result.ended_at = _now()
        result.duration_s = duration
        result.timed_out = True
        result.return_code = proc.returncode
        result.stdout = _truncate(stdout or "")
        result.stderr = _truncate(stderr or "")
        result.error = f"Command exceeded timeout of {timeout}s; process group terminated."
        result.success = False
        return result

    except BaseException as e:  # noqa: BLE001 - never let the loop die
        # Best-effort cleanup on unexpected supervisor-side error.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:  # noqa: BLE001
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
        try:
            proc.communicate(timeout=2.0)
        except Exception:  # noqa: BLE001
            pass

        duration = time.monotonic() - start_perf
        result.ended_at = _now()
        result.duration_s = duration
        result.return_code = proc.returncode
        result.error = (
            f"Supervisor error while running command: {e!r}\n"
            + traceback.format_exc()
        )
        result.success = False
        return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def execute_step(step: Any, timeout: Optional[float] = None) -> StepResult:
    """Execute a step under supervision and return a structured result.

    The step object is duck-typed. Recognized attributes (any subset):
        id / step_id / name   : identifier (for result reporting)
        action                : a Python callable to invoke
        command               : a shell command (str) or argv (list)
        timeout_s / timeout   : per-step timeout override

    If neither `action` nor `command` is present, a noop success is returned
    so the idle loop can still record progress for marker-only steps.

    Exceptions never escape this function.
    """
    step_id = _get_step_id(step)
    timeout_s = _resolve_timeout(step, timeout)

    # ---- Resolve what to run ----------------------------------------------
    action = getattr(step, "action", None)
    command = getattr(step, "command", None)

    if action is None and command is None:
        # Marker / no-op step.
        started = _now()
        return StepResult(
            step_id=step_id,
            success=True,
            return_code=0,
            started_at=started,
            ended_at=_now(),
            duration_s=0.0,
            mode="noop",
            metadata={"reason": "no action or command defined"},
        )

    # ---- Dispatch ----------------------------------------------------------
    try:
        if action is not None and callable(action):
            return _execute_callable(step, action, timeout_s, step_id)
        if command is not None:
            return _execute_command(step, command, timeout_s, step_id)

        # action was set but not callable
        started = _now()
        return StepResult(
            step_id=step_id,
            success=False,
            return_code=1,
            started_at=started,
            ended_at=_now(),
            duration_s=0.0,
            mode="unknown",
            error=f"step.action is not callable (type={type(action).__name__})",
        )

    except BaseException as e:  # noqa: BLE001 - ultimate safety net
        started = _now()
        return StepResult(
            step_id=step_id,
            success=False,
            return_code=1,
            started_at=started,
            ended_at=_now(),
            duration_s=0.0,
            mode="unknown",
            error=(
                f"Supervisor top-level error: {e!r}\n"
                + traceback.format_exc()
            ),
        )


# ---------------------------------------------------------------------------
# Self-test / smoke check (run as: python -m jobstar.idle.supervisor)
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import json

    class _DummyStep:
        def __init__(self, sid, **kw):
            self.id = sid
            for k, v in kw.items():
                setattr(self, k, v)

    def _good_callable(step):
        print("hello from callable")
        return 0

    def _bad_callable(step):
        raise RuntimeError("boom")

    def _slow_callable(step):
        time.sleep(10)
        return 0

    tests = [
        ("cmd-ok", _DummyStep("cmd-ok", command="echo hello && echo oops 1>&2")),
        ("cmd-fail", _DummyStep("cmd-fail", command="exit 3")),
        ("cmd-timeout", _DummyStep("cmd-timeout", command="sleep 30", timeout_s=1)),
        ("call-ok", _DummyStep("call-ok", action=_good_callable)),
        ("call-bad", _DummyStep("call-bad", action=_bad_callable)),
        ("call-slow", _DummyStep("call-slow", action=_slow_callable, timeout_s=1)),
        ("noop", _DummyStep("noop")),
    ]

    for name, step in tests:
        res = execute_step(step)
        print(f"\n=== {name} ===")
        print(json.dumps(res.to_dict(), indent=2))
