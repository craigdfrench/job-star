"""TriageEngine: combines rules + scoring into a TriageResult."""

from __future__ import annotations

from jobstar.context_gatherer.models import ContextBundle
from jobstar.triage.models import TriageResult
from jobstar.triage import rules


class TriageEngine:
    """Scores a ContextBundle and produces a TriageResult."""

    def triage(self, bundle: ContextBundle) -> TriageResult:
        text = bundle.raw_input
        urgency, domain, confidence, rationale = rules.evaluate(text)
        suggested = self._suggest_route(urgency, domain)
        return TriageResult(
            request_id=bundle.request_id,
            urgency=urgency,
            domain=domain,
            confidence=confidence,
            rationale=rationale,
            suggested_route=suggested,
        )

    @staticmethod
    def _suggest_route(urgency, domain) -> str:
        if urgency.value in ("now", "soon") and domain.value == "ops":
            return "ops_handler"
        if domain.value == "code":
            return "code_handler"
        if domain.value == "meta":
            return "meta_handler"
        return "default_handler"
