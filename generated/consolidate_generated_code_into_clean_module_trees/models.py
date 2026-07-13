"""Triage data models (merged from v3 + v4)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Urgency(str, Enum):
    NOW = "now"
    SOON = "soon"
    LATER = "later"
    BACKGROUND = "background"


class Domain(str, Enum):
    META = "meta"
    CODE = "code"
    OPS = "ops"
    UNKNOWN = "unknown"


@dataclass
class TriageResult:
    request_id: str
    urgency: Urgency
    domain: Domain
    confidence: float
    rationale: str
    suggested_route: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "urgency": self.urgency.value,
            "domain": self.domain.value,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "suggested_route": self.suggested_route,
        }
