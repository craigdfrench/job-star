"""Router: resolves a target and dispatches the payload."""

from __future__ import annotations

from typing import Any, Dict, Optional

from jobstar.router.models import Target
from jobstar.router.targets import TargetRegistry
from jobstar.triage.models import TriageResult


class Router:
    """Routes triaged requests to handlers via the TargetRegistry."""

    def __init__(self, registry: Optional[TargetRegistry] = None) -> None:
        self.registry = registry or TargetRegistry()

    def route(self, result: TriageResult, payload: Dict[str, Any]) -> Dict[str, Any]:
        target = self.registry.resolve(
            result.suggested_route, result.domain.value, result.urgency.value
        )
        if target is None:
            return {
                "ok": False,
                "error": "no_target",
                "request_id": result.request_id,
            }
        enriched = {**payload, "_triage": result.to_dict()}
        return target.handler(enriched)
