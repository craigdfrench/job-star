"""
Tension detection strategy.

Identifies goals that create friction or trade-offs — not impossible to
combine, but harder than pursuing either alone. This is the most nuanced
conflict type, often cross-domain.
"""

from __future__ import annotations

from typing import Optional

from ..types import ConflictEvidence, ConflictReport, ConflictSeverity, ConflictType, GoalSnapshot


# Known cross-domain tension patterns — domains that commonly create friction
# when goals exist in both simultaneously.
CROSS_DOMAIN_TENSIONS = {
    frozenset({"work", "health"}): 0.6,
    frozenset({"work", "personal"}): 0.5,
    frozenset({"work", "family"}): 0.65,
    frozenset({"financial", "lifestyle"}): 0.55,
    frozenset({"career", "health"}): 0.6,
    frozenset({"learning", "work"}): 0.4,
    frozenset({"social", "health"}): 0.35,
    frozenset({"creative", "financial"}): 0.45,
}

# Temporal tension: goals with nearby deadlines create pressure
DEADLINE_PROXIMITY_DAYS = 14
DEADLINE_PROXIMITY_CONFIDENCE = 0.55

# Priority tension: two high-priority goals compete for attention
HIGH_PRIORITY_THRESHOLD = 2  # priority 1 or 2


class TensionDetector:
    """Detects tension and trade-offs between goals."""

    def detect(
        self,
        a: GoalSnapshot,
        b: GoalSnapshot,
        semantic_tension: Optional[float] = None,
    ) -> Optional[ConflictReport]:
        """
        Check if goals a and b create tension or trade-offs.

        Args:
            a, b: Goal snapshots to compare.
            semantic_tension: Pre-computed tension score (0-1) from LLM if available.

        Returns:
            ConflictReport if tension detected, None otherwise.
        """
        evidence: list[ConflictEvidence] = []
        confidence_scores: list[float] = []

        # Signal 1: LLM-based semantic tension
        if semantic_tension is not None and semantic_tension >= 0.5:
            evidence.append(
                ConflictEvidence(
                    source="semantic",
                    description=f"Semantic analysis indicates tension: {semantic_tension:.2f}",
                    confidence=semantic_tension,
                    metadata={"tension_score": semantic_tension},
                )
            )
            confidence_scores.append(semantic_tension)

        # Signal 2: Cross-domain tension
        if a.domain and b.domain and a.domain != b.domain:
            domain_pair = frozenset({a.domain, b.domain})
            if domain_pair in CROSS_DOMAIN_TENSIONS:
                base_conf = CROSS_DOMAIN_TENSIONS[domain_pair]
                evidence.append(
                    ConflictEvidence(
                        source="domain",
                        description=(
                            f"Cross-domain tension between '{a.domain}' and '{b.domain}': "
                            f"goals in these domains commonly create trade-offs"
                        ),
                        confidence=base_conf,
                        metadata={
                            "domain_a": a.domain,
                            "domain_b": b.domain,
                            "base_tension": base_conf,
                        },
                    )
                )
                confidence_scores.append(base_conf)

        # Signal 3: Deadline proximity
        if a.deadline and b.deadline:
            days_apart = abs((a.deadline - b.deadline).days)
            if days_apart <= DEADLINE_PROXIMITY_DAYS:
                conf = DEADLINE_PROXIMITY_CONFIDENCE * (1 - days_apart / DEADLINE_PROXIMITY_DAYS)
                evidence.append(
                    ConflictEvidence(
                        source="temporal",
                        description=(
                            f"Deadlines are only {days_apart} days apart: "
                            f"{a.deadline.date()} vs {b.deadline.date()}"
                        ),
                        confidence=conf,
                        metadata={
                            "days_apart": days_apart,
                            "deadline_a": a.deadline.isoformat(),
                            "deadline_b": b.deadline.isoformat(),
                        },
                    )
                )
                confidence_scores.append(conf)

        # Signal 4: Dual high-priority goals compete for attention
        if a.priority <= HIGH_PRIORITY_THRESHOLD and b.priority <= HIGH_PRIORITY_THRESHOLD:
            conf = 0.55
            evidence.append(
                ConflictEvidence(
                    source="heuristic",
                    description=(
                        f"Both goals are high priority (P{a.priority} and P{b.priority}): "
                        f"competing for top attention"
                    ),
                    confidence=conf,
                    metadata={"priority_a": a.priority, "priority_b": b.priority},
                )
            )
            confidence_scores.append(conf)

        # Signal 5: Shared resource without over-commit (mild tension)
        shared_resources = set(a.resources.keys()) & set(b.resources.keys())
        if shared_resources and not semantic_tension:
            # If resource conflict detector didn't already catch this as critical,
            # it's still a tension signal
            conf = 0.40
            evidence.append(
                ConflictEvidence(
                    source="resource",
                    description=f"Shared resources create mild tension: {shared_resources}",
                    confidence=conf,
                    metadata={"shared_resources": list(shared_resources)},
                )
            )
            confidence_scores.append(conf)

        if not evidence:
            return None

        avg_confidence = sum(confidence_scores) / len(confidence_scores)
        if avg_confidence < 0.40:
            return None

        # Severity based on confidence and number of signals
        if avg_confidence >= 0.70 or len(evidence) >= 3:
            severity = ConflictSeverity.HIGH
        elif avg_confidence >= 0.55:
            severity = ConflictSeverity.MEDIUM
        else:
            severity = ConflictSeverity.LOW

        return ConflictReport(
            conflict_type=ConflictType.TENSION,
            severity=severity,
            goal_ids=[a.id, b.id],
            title=f"Tension: '{a.title}' ↔ '{b.title}'",
            description=(
                f"Goals '{a.title}' and '{b.title}' create tension. "
                f"Both can be pursued, but expect trade-offs across {len(evidence)} dimension(s). "
                f"Consider sequencing, reducing scope, or explicitly accepting the trade-off."
            ),
            evidence=evidence,
            recommendation="sequence",
        )
