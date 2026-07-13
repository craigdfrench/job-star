"""Target registry (merged unique file from v3)."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from jobstar.router.models import Target


class TargetRegistry:
    """Holds named targets and resolves the best match for a request."""

    def __init__(self) -> None:
        self._targets: Dict[str, Target] = {}

    def register(self, target: Target) -> None:
        self._targets[target.name] = target

    def get(self, name: str) -> Optional[Target]:
        return self._targets.get(name)

    def all(self) -> Iterable[Target]:
        return self._targets.values()

    def resolve(self, name: Optional[str], domain: str, urgency: str) -> Optional[Target]:
        if name and name in self._targets:
            return self._targets[name]
        # fall back to first matching target
        candidates: List[Target] = [
            t for t in self._targets.values() if t.matches(domain, urgency)
        ]
        return candidates[0] if candidates else None
