"""
High-level conflict reporting facade.

Provides a simple API: run detectors → score → report.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from jobstar.conflict.base import ConflictResult
from jobstar.conflict.report import ConflictReport, build_report


def generate_conflict_report(
    results: List[ConflictResult],
    top_n: int = 10,
    context: Optional[Dict[str, Any]] = None,
) -> ConflictReport:
    """
    Take raw detector results and produce a full scored report.

    This is the primary entry point for the reporting subsystem.
    """
    return build_report(results, top_n=top_n, context=context)


def report_to_markdown(report: ConflictReport) -> str:
    """Render a ConflictReport as a markdown string for human review."""
    lines: List[str] = []
    lines.append("# Conflict Report")
    lines.append("")
    lines.append(f"**Generated:** {report.generated_at}")
    lines.append(f"**Total conflicts:** {report.total_conflicts}")
    lines.append("")
    lines.append("## Summary")
    lines.append(report.summary)
    lines.append("")

    lines.append("## By Severity")
    for sev, count in report.by_severity.items():
        if count:
            lines.append(f"- **{sev}**: {count}")
    lines.append("")

    lines.append("## By Type")
    for ctype, count in report.by_type.items():
        lines.append(f"- {ctype}: {count}")
    lines.append("")

    if report.by_domain:
        lines.append("## By Domain")
        for domain, count in report.by_domain.items():
            lines.append(f"- {domain}: {count}")
        lines.append("")

    lines.append("## Top Conflicts")
    for i, sc in enumerate(report.top_conflicts, 1):
        score = sc.get("score", {})
        lines.append(
            f"{i}. [{score.get('severity', '?').upper()}] "
            f"{sc.get('conflict', {}).get('conflict_type', 'unknown')} "
            f"(composite: {score.get('composite', 0)}) — "
            f"{score.get('rationale', '')}"
        )
    lines.append("")

    return "\n".join(lines)
