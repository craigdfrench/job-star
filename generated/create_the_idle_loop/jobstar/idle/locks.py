"""jobstar/idle/locks.py

Simple lock manager for the idle loop.

Supports two backends:
  * in-memory  (default, fast, single-process)
  * file-based (cross-process, survives crashes)

A lock is identified by a string key. Acquisition is non-blocking: if the
lock is already held, acquire() returns False immediately. This matches the
"opportunistic" nature of the idle loop — we never want to *wait* for a
resource, only grab it if it's free right now.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class LockHandle:
    """Opaque handle returned by acquire(). Pass to release()."""
    key: str
    owner: str
    acquired_at: float
    backend: str  # "memory" | "file"
    token: str    # unique per acquisition, used for stale detection


class InMemoryLockManager:
    """Process-local lock table. Good enough for single-process Job-Star."""

    def __init__(self) -> None:
        self._held: dict[str, LockHandle] = {}

    def acquire(self, key: str, owner: str) -> Optional[LockHandle]:
        if key in self._held:
            return None
        handle = LockHandle(
            key=key,
            owner=owner,
            acquired_at=time.time(),
            backend="memory",
            token=uuid.uuid4().hex,
        )
        self._held[key] = handle
        return handle

    def release(self, handle: LockHandle) -> bool:
        current = self._held.get(handle.key)
        if current is None or current.token != handle.token:
            return False  # not ours or already released
        del self._held[handle.key]
        return True

    def is_locked(self, key: str) -> bool:
        return key in self._held

    def held_keys(self) -> list[str]:
        return list(self._held.keys())


class FileLockManager:
    """Cross-process lock manager using lockfiles in a directory.

    Each lock is a file whose name is the (sanitized) key and whose contents
    are "<owner>:<token>:<timestamp>". Stale locks older than `stale_seconds`
    are considered dead and can be reclaimed.
    """

    def __init__(self, lock_dir: str | Path, stale_seconds: float = 3600.0) -> None:
        self.lock_dir = Path(lock_dir)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.stale_seconds = stale_seconds

    def _path_for(self, key: str) -> Path:
        safe = key.replace("/", "_").replace("\\", "_").replace(":", "_")
        return self.lock_dir / f"{safe}.lock"

    def _read(self, path: Path) -> Optional[LockHandle]:
        if not path.exists():
            return None
        try:
            parts = path.read_text().split(":")
            owner, token, ts = parts[0], parts[1], float(parts[2])
        except (ValueError, IndexError, OSError):
            return None
        return LockHandle(
            key=path.stem.removesuffix(".lock") if False else path.name.removesuffix(".lock"),
            owner=owner,
            acquired_at=ts,
            backend="file",
            token=token,
        )

    def _is_stale(self, handle: LockHandle) -> bool:
        return (time.time() - handle.acquired_at) > self.stale_seconds

    def acquire(self, key: str, owner: str) -> Optional[LockHandle]:
        path = self._path_for(key)
        # Try atomic create
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            existing = self._read(path)
            if existing and self._is_stale(existing):
                # Reclaim stale lock
                try:
                    path.unlink()
                except OSError:
                    return None
                return self.acquire(key, owner)
            return None
        token = uuid.uuid4().hex
        os.write(fd, f"{owner}:{token}:{time.time()}".encode())
        os.close(fd)
        return LockHandle(
            key=key,
            owner=owner,
            acquired_at=time.time(),
            backend="file",
            token=token,
        )

    def release(self, handle: LockHandle) -> bool:
        path = self._path_for(handle.key)
        existing = self._read(path)
        if existing is None or existing.token != handle.token:
            return False
        try:
            path.unlink()
            return True
        except OSError:
            return False

    def is_locked(self, key: str) -> bool:
        existing = self._read(self._path_for(key))
        if existing is None:
            return False
        if self._is_stale(existing):
            try:
                self._path_for(key).unlink()
            except OSError:
                pass
            return False
        return True

    def held_keys(self) -> list[str]:
        keys = []
        for p in self.lock_dir.glob("*.lock"):
            h = self._read(p)
            if h and not self._is_stale(h):
                keys.append(h.key)
        return keys


class LockManager:
    """Facade that picks backend based on config.

    Usage:
        mgr = LockManager(backend="memory")
        h = mgr.acquire("gpu-0", owner="step-42")
        if h: ... mgr.release(h)
    """

    def __init__(self, backend: str = "memory", lock_dir: str | Path = ".jobstar/locks",
                 stale_seconds: float = 3600.0) -> None:
        if backend == "file":
            self._impl: InMemoryLockManager | FileLockManager = FileLockManager(lock_dir, stale_seconds)
        else:
            self._impl = InMemoryLockManager()

    def acquire(self, key: str, owner: str) -> Optional[LockHandle]:
        return self._impl.acquire(key, owner)

    def release(self, handle: LockHandle) -> bool:
        return self._impl.release(handle)

    def is_locked(self, key: str) -> bool:
        return self._impl.is_locked(key)

    def held_keys(self) -> list[str]:
        return self._impl.held_keys()


# Module-level default instance for convenience
_default: Optional[LockManager] = None


def get_default_lock_manager(backend: str = "memory") -> LockManager:
    global _default
    if _default is None:
        _default = LockManager(backend=backend)
    return _default
