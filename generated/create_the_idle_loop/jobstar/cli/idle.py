"""
Process lifecycle management for the Job-Star idle loop.

Supports two execution modes:
  - "daemon":    Double-fork detach (Unix). Process survives parent exit.
  - "subprocess": Spawn via subprocess.Popen with redirected stdio.

PID file management is used in both modes to track the live process.
"""

from __future__ import annotations

import errno
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_RUNTIME_DIR = Path(os.environ.get("JOBSTAR_RUNTIME_DIR", "/tmp/jobstar"))
DEFAULT_PID_FILE = DEFAULT_RUNTIME_DIR / "idle.pid"
DEFAULT_LOG_FILE = DEFAULT_RUNTIME_DIR / "idle.log"
DEFAULT_CONFIG_PATH = "config/idle_defaults.yaml"

# Seconds to wait for a process to die after SIGTERM before SIGKILL.
STOP_GRACE_PERIOD = 10.0
# Poll interval while waiting for process exit.
STOP_POLL_INTERVAL = 0.25


# ---------------------------------------------------------------------------
# Status result
# ---------------------------------------------------------------------------

@dataclass
class StatusInfo:
    running: bool
    pid: Optional[int]
    pid_file: Path
    log_file: Path
    mode: Optional[str]  # "daemon" | "subprocess" | None
    message: str

    def as_dict(self) -> dict:
        return {
            "running": self.running,
            "pid": self.pid,
            "pid_file": str(self.pid_file),
            "log_file": str(self.log_file),
            "mode": self.mode,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# PID file helpers
# ---------------------------------------------------------------------------

def _ensure_runtime_dir(runtime_dir: Path) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    # Best-effort restrictive perms on the runtime dir.
    try:
        os.chmod(runtime_dir, 0o700)
    except OSError:
        pass


def _write_pid_file(pid_file: Path, pid: int, mode: str) -> None:
    _ensure_runtime_dir(pid_file.parent)
    content = f"{pid}\n{mode}\n"
    # Write atomically.
    tmp = pid_file.with_suffix(pid_file.suffix + ".tmp")
    tmp.write_text(content)
    tmp.replace(pid_file)
    try:
        os.chmod(pid_file, 0o600)
    except OSError:
        pass


def _read_pid_file(pid_file: Path) -> tuple[Optional[int], Optional[str]]:
    """Return (pid, mode) or (None, None) if missing/invalid."""
    if not pid_file.exists():
        return None, None
    try:
        text = pid_file.read_text().strip().splitlines()
    except OSError:
        return None, None
    if not text:
        return None, None
    try:
        pid = int(text[0])
    except ValueError:
        return None, None
    mode = text[1] if len(text) > 1 else None
    return pid, mode


def _remove_pid_file(pid_file: Path) -> None:
    try:
        pid_file.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("Failed to remove PID file %s: %s", pid_file, e)


def _pid_alive(pid: int) -> bool:
    """Return True if a process with `pid` exists (signal 0 succeeds)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but not ours — treat as alive for status purposes.
        return True
    except OSError as e:
        if e.errno == errno.ESRCH:
            return False
        # Errno.EPERM means it exists.
        return e.errno == errno.EPERM
    return True


def _terminate_pid(pid: int, grace: float = STOP_GRACE_PERIOD) -> bool:
    """SIGTERM, wait, then SIGKILL if still alive. Returns True if dead."""
    if not _pid_alive(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    except PermissionError:
        log.error("Permission denied sending SIGTERM to pid %d", pid)
        return False

    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(STOP_POLL_INTERVAL)

    # Escalate to SIGKILL.
    log.warning("Process %d did not exit after SIGTERM; sending SIGKILL", pid)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False

    # Brief wait for reaping.
    for _ in range(int(grace / STOP_POLL_INTERVAL)):
        if not _pid_alive(pid):
            return True
        time.sleep(STOP_POLL_INTERVAL)
    return not _pid_alive(pid)


# ---------------------------------------------------------------------------
# Daemon (double-fork) implementation — Unix only
# ---------------------------------------------------------------------------

def _double_fork(config_path: str, log_file: Path, pid_file: Path) -> int:
    """
    Classic Unix double-fork to detach from controlling terminal.
    Returns the daemon PID (in the parent); the child executes the loop.
    """
    # Flush stdio before fork.
    sys.stdout.flush()
    sys.stderr.flush()

    # First fork.
    try:
        pid = os.fork()
    except OSError as e:
        raise RuntimeError(f"first fork failed: {e}") from e

    if pid > 0:
        # Parent: return child PID; child will fork again and exit.
        # Wait briefly for the intermediate child to exit so we don't leave
        # a zombie.
        try:
            os.waitpid(pid, 0)
        except ChildProcessError:
            pass
        # The actual daemon PID is not directly known here; we re-read it
        # from the PID file written by the daemon. Caller handles that.
        return pid

    # First child: become session leader, then fork again.
    os.setsid()

    # Second fork.
    try:
        pid2 = os.fork()
    except OSError as e:
        raise RuntimeError(f"second fork failed: {e}") from e

    if pid2 > 0:
        # Intermediate child exits immediately.
        os._exit(0)

    # We are now the daemon (grandchild).
    # Reset umask, change cwd.
    os.umask(0o077)
    try:
        os.chdir("/")
    except OSError:
        pass

    # Redirect stdio to log file.
    _ensure_runtime_dir(log_file.parent)
    fd = os.open(str(log_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.dup2(fd, sys.stdout.fileno())
    os.dup2(fd, sys.stderr.fileno())
    os.dup2(fd, sys.stdin.fileno())
    if fd > 2:
        os.close(fd)

    # Write PID file.
    _write_pid_file(pid_file, os.getpid(), "daemon")

    # Install SIGTERM handler to clean up PID file on exit.
    _install_signal_handlers(pid_file)

    # Run the loop. Import lazily to avoid heavy import at module load.
    from jobstar.idle.loop import run_idle_loop
    try:
        run_idle_loop(config_path)
    except Exception:
        log.exception("idle loop crashed")
        raise
    finally:
        _remove_pid_file(pid_file)
        os._exit(0)


def _install_signal_handlers(pid_file: Path) -> None:
    def _cleanup(signum, frame):
        _remove_pid_file(pid_file)
        raise SystemExit(0)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _cleanup)
        except (ValueError, OSError):
            # Not in main thread sometimes.
            pass


# ---------------------------------------------------------------------------
# Subprocess implementation (cross-platform fallback)
# ---------------------------------------------------------------------------

def _spawn_subprocess(
    config_path: str,
    log_file: Path,
    pid_file: Path,
    python_executable: Optional[str] = None,
) -> int:
    """Spawn the idle loop as a detached subprocess; return its PID."""
    _ensure_runtime_dir(log_file.parent)
    py = python_executable or sys.executable

    # We invoke the loop's __main__ entry via -m so it runs in background.
    # The loop module is expected to support `python -m jobstar.idle.loop CONFIG`.
    cmd = [py, "-m", "jobstar.idle.loop", config_path]

    log_fh = open(log_file, "ab")
    try:
        kwargs = dict(
            stdout=log_fh,
            stderr=log_fh,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        if os.name == "posix":
            kwargs["start_new_session"] = True
        else:
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

        proc = subprocess.Popen(cmd, **kwargs)
    finally:
        # Popen dup'd the fd; safe to close in parent.
        log_fh.close()

    _write_pid_file(pid_file, proc.pid, "subprocess")
    return proc.pid


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start(
    config_path: str = DEFAULT_CONFIG_PATH,
    mode: str = "daemon",
    runtime_dir: Optional[Path] = None,
    python_executable: Optional[str] = None,
) -> StatusInfo:
    """
    Start the idle loop in the background.

    mode: "daemon" (double-fork, Unix) or "subprocess" (Popen, cross-platform).
    Returns a StatusInfo describing the (hopefully running) process.
    """
    runtime_dir = Path(runtime_dir) if runtime_dir else DEFAULT_RUNTIME_DIR
    pid_file = runtime_dir / "idle.pid"
    log_file = runtime_dir / "idle.log"

    # Refuse to start if already running.
    existing_pid, _ = _read_pid_file(pid_file)
    if existing_pid and _pid_alive(existing_pid):
        return StatusInfo(
            running=True,
            pid=existing_pid,
            pid_file=pid_file,
            log_file=log_file,
            mode=None,
            message=f"Already running (pid {existing_pid}).",
        )
    # Stale PID file — clean it.
    if existing_pid:
        _remove_pid_file(pid_file)

    if mode == "daemon":
        if os.name != "posix":
            # Fall back to subprocess on non-Unix.
            mode = "subprocess"
        else:
            _double_fork(config_path, log_file, pid_file)
            # The daemon wrote its own PID file. Re-read it.
            time.sleep(0.3)
            pid, _ = _read_pid_file(pid_file)
            alive = bool(pid and _pid_alive(pid))
            return StatusInfo(
                running=alive,
                pid=pid,
                pid_file=pid_file,
                log_file=log_file,
                mode="daemon",
                message=("Started idle daemon." if alive
                         else "Daemon may have failed to start; check logs."),
            )

    if mode == "subprocess":
        pid = _spawn_subprocess(config_path, log_file, pid_file, python_executable)
        time.sleep(0.3)
        alive = _pid_alive(pid)
        return StatusInfo(
            running=alive,
            pid=pid,
            pid_file=pid_file,
            log_file=log_file,
            mode="subprocess",
            message=("Started idle subprocess." if alive
                     else "Subprocess may have failed; check logs."),
        )

    return StatusInfo(
        running=False, pid=None, pid_file=pid_file, log_file=log_file,
        mode=None, message=f"Unknown mode: {mode!r}",
    )


def stop(
    runtime_dir: Optional[Path] = None,
    grace: float = STOP_GRACE_PERIOD,
) -> StatusInfo:
    """Stop the running idle loop via SIGTERM (then SIGKILL if needed)."""
    runtime_dir = Path(runtime_dir) if runtime_dir else DEFAULT_RUNTIME_DIR
    pid_file = runtime_dir / "idle.pid"
    log_file = runtime_dir / "idle.log"

    pid, mode = _read_pid_file(pid_file)
    if not pid:
        return StatusInfo(
            running=False, pid=None, pid_file=pid_file, log_file=log_file,
            mode=None, message="Not running (no PID file).",
        )
    if not _pid_alive(pid):
        _remove_pid_file(pid_file)
        return StatusInfo(
            running=False, pid=pid, pid_file=pid_file, log_file=log_file,
            mode=mode, message=f"Process {pid} not alive (stale PID file removed).",
        )

    dead = _terminate_pid(pid, grace=grace)
    _remove_pid_file(pid_file)
    return StatusInfo(
        running=not dead,
        pid=pid,
        pid_file=pid_file,
        log_file=log_file,
        mode=mode,
        message=(f"Stopped pid {pid}." if dead
                 else f"Failed to stop pid {pid} (permission or timeout)."),
    )


def status(runtime_dir: Optional[Path] = None) -> StatusInfo:
    """Report current status of the idle loop process."""
    runtime_dir = Path(runtime_dir) if runtime_dir else DEFAULT_RUNTIME_DIR
    pid_file = runtime_dir / "idle.pid"
    log_file = runtime_dir / "idle.log"

    pid, mode = _read_pid_file(pid_file)
    if not pid:
        return StatusInfo(
            running=False, pid=None, pid_file=pid_file, log_file=log_file,
            mode=None, message="Not running.",
        )
    alive = _pid_alive(pid)
    if not alive:
        return StatusInfo(
            running=False, pid=pid, pid_file=pid_file, log_file=log_file,
            mode=mode, message=f"Stale PID file (pid {pid} not alive).",
        )
    return StatusInfo(
        running=True, pid=pid, pid_file=pid_file, log_file=log_file,
        mode=mode, message=f"Running (pid {pid}, mode={mode}).",
    )


def restart(
    config_path: str = DEFAULT_CONFIG_PATH,
    mode: str = "daemon",
    runtime_dir: Optional[Path] = None,
    python_executable: Optional[str] = None,
) -> StatusInfo:
    """Stop then start."""
    stop(runtime_dir=runtime_dir)
    time.sleep(0.5)
    return start(
        config_path=config_path,
        mode=mode,
        runtime_dir=runtime_dir,
        python_executable=python_executable,
    )


def tail_logs(
    runtime_dir: Optional[Path] = None,
    n: int = 50,
    follow: bool = False,
) -> None:
    """Print the last n lines of the idle log, optionally following."""
    runtime_dir = Path(runtime_dir) if runtime_dir else DEFAULT_RUNTIME_DIR
    log_file = runtime_dir / "idle.log"
    if not log_file.exists():
        print(f"No log file at {log_file}", file=sys.stderr)
        return
    # Print last n lines.
    with open(log_file, "rb") as fh:
        lines = fh.readlines()[-n:]
    for line in lines:
        sys.stdout.write(line.decode(errors="replace"))
        if not line.endswith(b"\n"):
            sys.stdout.write("\n")
    sys.stdout.flush()
    if not follow:
        return
    try:
        with open(log_file, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            while True:
                chunk = fh.readline()
                if chunk:
                    sys.stdout.write(chunk.decode(errors="replace"))
                    sys.stdout.flush()
                else:
                    time.sleep(0.5)
    except KeyboardInterrupt:
        pass
