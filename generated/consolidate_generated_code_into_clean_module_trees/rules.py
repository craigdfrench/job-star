"""Triage rules (merged unique file from v1).

Rules are simple keyword/pattern matchers that contribute to scoring.
Kept separate from the engine so rules can evolve independently.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from jobstar.triage.models import Domain, Urgency


# (pattern, urgency_weight, domain)
_RULES: List[Tuple[re.Pattern, float, Domain]] = [
    (re.compile(r"\b(urgent|asap|now|immediately|blocker)\b", re.I), 1.0, Domain.OPS),
    (re.compile(r"\b(soon|today|shortly)\b", re.I), 0.6, Domain.OPS),
    (re.compile(r"\b(later|whenever|no rush)\b", re.I), -0.5, Domain.UNKNOWN),
    (re.compile(r"\b(deploy|rollback|incident|outage)\b", re.I), 0.8, Domain.OPS),
    (re.compile(r"\b(refactor|function|class|module|test)\b", re.I), 0.3, Domain.CODE),
    (re.compile(r"\b(jobstar|bootstrap|meta)\b", re.I), 0.2, Domain.META),
]


def evaluate(text: str) -> Tuple[Urgency, Domain, float, str]:
    """Return (urgency, domain, confidence, rationale) for a text blob."""
    score = 0.0
    domain_votes: dict = {}
    matched: List[str] = []
    for pattern, weight, domain in _RULES:
        if pattern.search(text):
            score += weight
            domain_votes[domain.value] = domain_votes.get(domain.value, 0) + weight
            matched.append(pattern.pattern)

    if score >= 0.8:
        urgency = Urgency.NOW
    elif score >= 0.3:
        urgency = Urgency.SOON
    elif score >= -0.2:
        urgency = Urgency.LATER
    else:
        urgency = Urgency.BACKGROUND

    domain = (
        Domain(max(domain_votes, key=domain_votes.get))
        if domain_votes
        else Domain.UNKNOWN
    )
    confidence = min(1.0, max(0.0, abs(score)))
    rationale = f"matched: {', '.join(matched) if matched else 'no rules'}"
    return urgency, domain, confidence, rationale
