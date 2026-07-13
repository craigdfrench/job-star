/**
 * Conflict Report Formatter
 *
 * Produces human-readable and machine-readable representations of a
 * ConflictReport for consumption by:
 *   - The orchestration layer (JSON)
 *   - Human reviewers (Markdown / plain text)
 *   - Logging pipelines (compact one-line summaries)
 */

import {
  ConflictReport,
  ConflictFinding,
  ConflictSeverity,
  ConflictType,
} from './types';

// ---------------------------------------------------------------------------
// Severity display helpers
// ---------------------------------------------------------------------------

const SEVERITY_LABEL: Record<ConflictSeverity, string> = {
  critical: 'CRITICAL',
  high: 'HIGH',
  medium: 'MEDIUM',
  low: 'LOW',
  info: 'INFO',
};

const SEVERITY_ICON: Record<ConflictSeverity, string> = {
  critical: '🔴',
  high: '🟠',
  medium: '🟡',
  low: '🟢',
  info: '⚪',
};

const TYPE_LABEL: Record<ConflictType, string> = {
  duplicate: 'Duplicate',
  contradiction: 'Contradiction',
  competing_resource: 'Competing Resource',
  tension: 'Tension',
};

// ---------------------------------------------------------------------------
// Machine-readable: JSON
// ---------------------------------------------------------------------------

/**
 * Produce a compact JSON string suitable for the orchestration layer.
 */
export function toJSON(report: ConflictReport): string {
  return JSON.stringify(report, null, 2);
}

/**
 * Produce a JSON object (for embedding in other structures).
 */
export function toJSONObject(report: ConflictReport): ConflictReport {
  return JSON.parse(JSON.stringify(report));
}

// ---------------------------------------------------------------------------
// Human-readable: Markdown
// ---------------------------------------------------------------------------

/**
 * Produce a full Markdown report for human review.
 */
export function toMarkdown(report: ConflictReport): string {
  const lines: string[] = [];

  // Header
  lines.push('# Conflict Detection Report');
  lines.push('');
  lines.push(`**Generated:** ${report.generatedAt}`);
  lines.push(`**Total Findings:** ${report.totalFindings}`);
  lines.push(`**Max Severity:** ${SEVERITY_LABEL[report.summary.maxSeverity]}`);
  lines.push('');

  // Summary table
  lines.push('## Summary');
  lines.push('');
  lines.push('| Severity | Count |');
  lines.push('|----------|-------|');
  for (const sev of ['critical', 'high', 'medium', 'low', 'info'] as ConflictSeverity[]) {
    const count = report.summary.bySeverity[sev];
    if (count > 0) {
      lines.push(`| ${SEVERITY_ICON[sev]} ${SEVERITY_LABEL[sev]} | ${count} |`);
    }
  }
  lines.push('');

  lines.push('| Conflict Type | Count |');
  lines.push('|---------------|-------|');
  for (const t of ['duplicate', 'contradiction', 'competing_resource', 'tension'] as ConflictType[]) {
    const count = report.summary.byType[t];
    if (count > 0) {
      lines.push(`| ${TYPE_LABEL[t]} | ${count} |`);
    }
  }
  lines.push('');

  // Findings
  if (report.findings.length === 0) {
    lines.push('## Findings');
    lines.push('');
    lines.push('✅ No conflicts detected.');
    lines.push('');
    return lines.join('\n');
  }

  lines.push('## Findings');
  lines.push('');

  report.findings.forEach((finding, idx) => {
    lines.push(formatFindingMarkdown(finding, idx + 1));
    lines.push('');
  });

  return lines.join('\n');
}

function formatFindingMarkdown(finding: ConflictFinding, index: number): string {
  const lines: string[] = [];
  const sevLabel = SEVERITY_LABEL[finding.severity];
  const icon = SEVERITY_ICON[finding.severity];
  const typeLabels = finding.types.map((t) => TYPE_LABEL[t]).join(', ');

  lines.push(`### ${index}. ${icon} ${sevLabel} — ${typeLabels}`);
  lines.push('');
  lines.push(`- **Finding ID:** \`${finding.id}\``);
  lines.push(`- **Goals:** ${finding.goalIds.map((id) => `\`${id}\``).join(', ')}`);
  lines.push(`- **Confidence:** ${(finding.confidence * 100).toFixed(0)}%`);
  lines.push(`- **Detected by:** ${finding.detectors.join(', ')}`);
  lines.push(`- **Detected at:** ${finding.detectedAt}`);
  lines.push(`- **Description:** ${finding.description}`);

  if (finding.suggestions && finding.suggestions.length > 0) {
    lines.push('');
    lines.push('**Suggestions:**');
    for (const s of finding.suggestions) {
      lines.push(`  - ${s}`);
    }
  }

  if (finding.metadata && Object.keys(finding.metadata).length > 0) {
    lines.push('');
    lines.push('**Metadata:**');
    lines.push('
