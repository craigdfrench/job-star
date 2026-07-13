"""
Conflict reporting.

Aggregates scored conflicts into structured reports suitable for triage,
dashboards, and the resolution pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from jobstar.conflict.base import ConflictResult
from jobstar.conflict.scoring import (
    ConflictScore,
    Severity,
    score_conflict,
)


@dataclass
class ScoredConflict:
    """A conflict result paired with its score."""

    result: ConflictResult
    score: ConflictScore

    @property
    def composite(self) -> float:
        return self.score.composite

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conflict": self.result.to_dict() if hasattr(self.result, "to_dict") else vars(self.result),
            "score": self.score.to_dict(),
        }


@dataclass
class ConflictReport:
    """Aggregated report of all conflicts found in a batch."""

    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    total_conflicts: int = 0
    by_severity: Dict[str, int] = field(default_factory=dict)
    by_type: Dict[str, int] = field(default_factory=dict)
    by_domain: Dict[str, int] = field(default_factory=dict)
    top_conflicts: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    conflicts: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "total_conflicts": self.total_conflicts,
            "by_severity": self.by_severity,
            "by_type": self.by_type,
            "by_domain": self.by_domain,
            "top_conflicts": self.top_conflicts,
            "summary": self.summary,
            "conflicts": self.conflicts,
        }


def build_report(
    results: List[ConflictResult],
    top_n: int = 10,
    context: Optional[Dict[str, Any]] = None,
) -> ConflictReport:
    """
    Score every conflict, aggregate stats, and produce a prioritized report.

    Args:
        results: Raw conflict results from detectors.
        top_n: How many highest-composite conflicts to surface.
        context: Optional metadata about the scan (e.g. goal count, domains scanned).
    """
    scored: List[ScoredConflict] = [ScoredConflict(r, score_conflict(r)) for r in results]

    # Sort by composite descending
    scored.sort(key=lambda sc: sc.composite, reverse=True)

    by_severity: Dict[str, int] = {s.value: 0 for s in Severity}
    by_type: Dict[str, int] = {}
    by_domain: Dict[str, int] = {}

    for sc in scored:
        sev = sc.score.severity.value
        by_severity[sev] = by_severity.get(sev, 0) + 1

        ctype = sc.result.conflict_type
        by_type[ctype] = by_type.get(ctype, 0) + 1

        # Domain extraction — goals may carry a domain attribute
        domains = _extract_domains(sc.result)
        for d in domains:
            by_domain[d] = by_domain.get(d, 0) + 1

    top_conflicts = [sc.to_dict() for sc in scored[:top_n]]
    all_conflicts = [sc.to_dict() for sc in scored]

    summary = _generate_summary(scored, by_severity, by_type, context)

    return ConflictReport(
        total_conflicts=len(scored),
        by_severity=by_severity,
        by_type=by_type,
        by_domain=by_domain,
        top_conflicts=top_conflicts,
        summary=summary,
        conflicts=all_conflicts,
    )


def _extract_domains(result: ConflictResult) -> List[str]:
    """Pull domain labels from a conflict result's goals."""
    domains: List[str] = []
    # ConflictResult may store goal_a/goal_b or a goals list
    for attr in ("goal_a", "goal_b"):
        goal = getattr(result, attr, None)
        if goal and hasattr(goal, "domain"):
            domains.append(goal.domain)
    goals = getattr(result, "goals", None)
    if goals and isinstance(goals, list):
        for g in goals:
            if hasattr(g, "domain"):
                domains.append(g.domain)
            elif isinstance(g, dict) and "domain" in g:
                domains.append(g["domain"])
    # Fallback: metadata
    if not domains and hasattr(result, "metadata"):
        meta = result.metadata or {}
        if "domains" in meta and isinstance(meta["domains"], list):
            domains.extend(meta["domains"])
    return domains or ["unknown"]


def _generate_summary(
    scored: List[ScoredConflict],
    by_severity: Dict[str, int],
    by_type: Dict[str, int],
    context: Optional[Dict[str, Any]],
) -> str:
    """Human-readable summary line for the report."""
    total = len(scored)
    critical = by_severity.get("critical", 0)
    high = by_severity.get("high", 0)

    parts: List[str] = []
    parts.append(f"Found {total} conflict(s).")
    if critical:
        parts.append(f"{critical} critical.")
    if high:
        parts.append(f"{high} high-severity.")

    type_summary = ", ".join(f"{k}:{v}" for k, v in sorted(by_type.items(), key=lambda x: -x[1]))
    if type_summary:
        parts.append(f"Types: {type_summary}.")

    if context:
        goal_count = context.get("goal_count")
        if goal_count:
            parts.append(f"Scanned {goal_count} goal(s).")

    return " ".join(parts)
