"""
Conflict scoring model.

Assigns severity (how bad the conflict is) and confidence (how sure we are
it's real) to each detected conflict. Produces a composite score used for
prioritization in reports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional

from jobstar.conflict.base import ConflictResult


class Severity(str, Enum):
    """How impactful a conflict is on goal achievement."""

    INFO = "info"          # No real impact, just awareness
    LOW = "low"            # Minor friction, easily worked around
    MEDIUM = "medium"      # Noticeable drag, should be addressed
    HIGH = "high"          # Significant blocker, address soon
    CRITICAL = "critical"  # Goals cannot both succeed as-is


class Confidence(str, Enum):
    """How certain we are the conflict is real (not a false positive)."""

    LOW = "low"          # Heuristic guess, may be noise
    MEDIUM = "medium"    # Pattern matched but ambiguous
    HIGH = "high"        # Strong signal, likely real
    CERTAIN = "certain"  # Definitive (e.g. exact duplicate IDs)


# Numeric mappings for composite scoring
_SEVERITY_WEIGHT: Dict[Severity, float] = {
    Severity.INFO: 0.1,
    Severity.LOW: 0.3,
    Severity.MEDIUM: 0.6,
    Severity.HIGH: 0.85,
    Severity.CRITICAL: 1.0,
}

_CONFIDENCE_WEIGHT: Dict[Confidence, float] = {
    Confidence.LOW: 0.3,
    Confidence.MEDIUM: 0.6,
    Confidence.HIGH: 0.85,
    Confidence.CERTAIN: 1.0,
}


@dataclass
class ConflictScore:
    """Composite score for a single conflict."""

    severity: Severity
    confidence: Confidence
    composite: float  # 0.0–1.0, severity * confidence
    rationale: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def compute(
        cls,
        severity: Severity,
        confidence: Confidence,
        rationale: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ConflictScore":
        composite = round(_SEVERITY_WEIGHT[severity] * _CONFIDENCE_WEIGHT[confidence], 4)
        return cls(
            severity=severity,
            confidence=confidence,
            composite=composite,
            rationale=rationale,
            metadata=metadata or {},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity.value,
            "confidence": self.confidence.value,
            "composite": self.composite,
            "rationale": self.rationale,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Default scoring rules per conflict type
# ---------------------------------------------------------------------------

_DEFAULT_RULES: Dict[str, Dict[str, Any]] = {
    "duplicate": {
        "severity": Severity.MEDIUM,
        "confidence": Confidence.HIGH,
        "rationale": "Duplicate goals waste resources and create ambiguity.",
    },
    "contradiction": {
        "severity": Severity.CRITICAL,
        "confidence": Confidence.MEDIUM,
        "rationale": "Contradictory goals cannot both succeed simultaneously.",
    },
    "resource": {
        "severity": Severity.HIGH,
        "confidence": Confidence.MEDIUM,
        "rationale": "Competing resources create scheduling or capacity contention.",
    },
    "tension": {
        "severity": Severity.LOW,
        "confidence": Confidence.LOW,
        "rationale": "Soft tension may cause friction but is not a hard blocker.",
    },
    "cross_domain": {
        "severity": Severity.MEDIUM,
        "confidence": Confidence.MEDIUM,
        "rationale": "Cross-domain conflict may drain shared bandwidth.",
    },
}


def score_conflict(result: ConflictResult) -> ConflictScore:
    """
    Score a ConflictResult using default rules, then refine with any
    signal-specific metadata the detector attached.
    """
    ctype = result.conflict_type
    base = _DEFAULT_RULES.get(ctype, {
        "severity": Severity.LOW,
        "confidence": Confidence.LOW,
        "rationale": "Unknown conflict type; defaulting to low scores.",
    })

    severity = base["severity"]
    confidence = base["confidence"]
    rationale = base["rationale"]
    metadata: Dict[str, Any] = dict(result.metadata) if hasattr(result, "metadata") else {}

    # Refine: exact duplicate IDs → certain
    if ctype == "duplicate" and metadata.get("match_method") == "exact_id":
        confidence = Confidence.CERTAIN
        severity = Severity.HIGH
        rationale = "Exact goal ID match — definitive duplicate."

    # Refine: contradiction with explicit negation → certain
    if ctype == "contradiction" and metadata.get("negation_detected"):
        confidence = Confidence.HIGH

    # Refine: resource conflict with zero available capacity → critical
    if ctype == "resource" and metadata.get("available_capacity") == 0:
        severity = Severity.CRITICAL

    # Refine: tension with repeated history → bump confidence
    if ctype == "tension" and metadata.get("occurrence_count", 0) >= 3:
        confidence = Confidence.MEDIUM

    return ConflictScore.compute(
        severity=severity,
        confidence=confidence,
        rationale=rationale,
        metadata=metadata,
    )
