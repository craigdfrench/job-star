/**
 * Conflict Report Aggregator
 *
 * Merges outputs from multiple conflict detectors into a unified report.
 * Responsibilities:
 *   1. Merge all detector outputs into a single list
 *   2. Deduplicate overlapping findings (same goal pair flagged by multiple detectors)
 *   3. Sort by severity (critical > high > medium > low > info)
 *   4. Produce a structured ConflictReport for downstream consumption
 */

import { ConflictReport, ConflictFinding, ConflictSeverity, ConflictType } from './types';

// ---------------------------------------------------------------------------
// Severity ordering — higher number = more severe
// ---------------------------------------------------------------------------

const SEVERITY_RANK: Record<ConflictSeverity, number> = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
  info: 0,
};

const SEVERITY_ORDER: ConflictSeverity[] = ['critical', 'high', 'medium', 'low', 'info'];

// ---------------------------------------------------------------------------
// Deduplication key
// ---------------------------------------------------------------------------

/**
 * Build a stable deduplication key from the set of goal IDs involved in a
 * finding.  Two findings that reference the same set of goals (regardless of
 * order) are considered candidates for merging.
 */
function dedupKey(goalIds: string[]): string {
  return [...goalIds].sort().join('::');
}

// ---------------------------------------------------------------------------
// Merge helpers
// ---------------------------------------------------------------------------

/**
 * Pick the higher of two severities.
 */
function maxSeverity(a: ConflictSeverity, b: ConflictSeverity): ConflictSeverity {
  return SEVERITY_RANK[a] >= SEVERITY_RANK[b] ? a : b;
}

/**
 * Merge two findings that share the same goal-pair key.
 *
 * The merged finding retains the higher severity, the union of conflict types,
 * the higher confidence, and a combined description.
 */
function mergeFindings(a: ConflictFinding, b: ConflictFinding): ConflictFinding {
  const types = new Set<ConflictType>([...a.types, ...b.types]);
  const detectors = new Set<string>([...a.detectors, ...b.detectors]);

  const descriptionParts = [a.description];
  if (b.description && b.description !== a.description) {
    descriptionParts.push(b.description);
  }

  return {
    id: a.id, // keep first id; stable
    goalIds: [...new Set([...a.goalIds, ...b.goalIds])],
    types: Array.from(types),
    severity: maxSeverity(a.severity, b.severity),
    confidence: Math.max(a.confidence, b.confidence),
    description: descriptionParts.join(' | '),
    detectors: Array.from(detectors),
    detectedAt: a.detectedAt, // earliest
    suggestions: [...(a.suggestions ?? []), ...(b.suggestions ?? [])],
    metadata: { ...a.metadata, ...b.metadata },
  };
}

// ---------------------------------------------------------------------------
// Aggregator
// ---------------------------------------------------------------------------

export interface AggregatorOptions {
  /** When true, findings with the same goal-pair but different conflict types
   *  are still merged into a single multi-type finding.  Default: true. */
  mergeOverlapping?: boolean;
  /** Minimum severity to include in the output.  Default: 'info' (all). */
  minSeverity?: ConflictSeverity;
  /** Maximum number of findings to return (after sorting).  0 = no limit. */
  maxFindings?: number;
}

export const DEFAULT_AGGREGATOR_OPTIONS: AggregatorOptions = {
  mergeOverlapping: true,
  minSeverity: 'info',
  maxFindings: 0,
};

export class ConflictAggregator {
  private options: AggregatorOptions;

  constructor(options: Partial<AggregatorOptions> = {}) {
    this.options = { ...DEFAULT_AGGREGATOR_OPTIONS, ...options };
  }

  /**
   * Aggregate an array of detector outputs (each output is itself an array of
   * findings) into a single deduplicated, sorted ConflictReport.
   */
  aggregate(detectorOutputs: ConflictFinding[][]): ConflictReport {
    // 1. Flatten
    let allFindings: ConflictFinding[] = detectorOutputs.flat();

    // 2. Filter by minimum severity
    const minRank = SEVERITY_RANK[this.options.minSeverity!];
    allFindings = allFindings.filter(
      (f) => SEVERITY_RANK[f.severity] >= minRank,
    );

    // 3. Deduplicate / merge
    if (this.options.mergeOverlapping) {
      allFindings = this.deduplicate(allFindings);
    }

    // 4. Sort by severity (desc), then confidence (desc), then goalIds
    allFindings.sort((a, b) => {
      const sevDiff = SEVERITY_RANK[b.severity] - SEVERITY_RANK[a.severity];
      if (sevDiff !== 0) return sevDiff;
      const confDiff = b.confidence - a.confidence;
      if (Math.abs(confDiff) > 0.001) return confDiff;
      return dedupKey(a.goalIds).localeCompare(dedupKey(b.goalIds));
    });

    // 5. Limit
    if (this.options.maxFindings && this.options.maxFindings > 0) {
      allFindings = allFindings.slice(0, this.options.maxFindings);
    }

    // 6. Build summary
    const summary = this.buildSummary(allFindings);

    return {
      generatedAt: new Date().toISOString(),
      totalFindings: allFindings.length,
      summary,
      findings: allFindings,
    };
  }

  /**
   * Deduplicate findings by goal-pair key, merging overlaps.
   */
  private deduplicate(findings: ConflictFinding[]): ConflictFinding[] {
    const buckets = new Map<string, ConflictFinding>();

    for (const finding of findings) {
      const key = dedupKey(finding.goalIds);
      const existing = buckets.get(key);
      if (existing) {
        buckets.set(key, mergeFindings(existing, finding));
      } else {
        buckets.set(key, { ...finding });
      }
    }

    return Array.from(buckets.values());
  }

  /**
   * Build a summary object with counts per severity and per type.
   */
  private buildSummary(findings: ConflictFinding[]): ConflictReportSummary {
    const bySeverity: Record<ConflictSeverity, number> = {
      critical: 0,
      high: 0,
      medium: 0,
      low: 0,
      info: 0,
    };
    const byType: Record<ConflictType, number> = {
      duplicate: 0,
      contradiction: 0,
      competing_resource: 0,
      tension: 0,
    };

    let maxSeverity: ConflictSeverity = 'info';
    let maxSeverityRank = -1;

    for (const f of findings) {
      bySeverity[f.severity]++;
      for (const t of f.types) {
        byType[t]++;
      }
      const rank = SEVERITY_RANK[f.severity];
      if (rank > maxSeverityRank) {
        maxSeverityRank = rank;
        maxSeverity = f.severity;
      }
    }

    return {
      bySeverity,
      byType,
      maxSeverity,
      detectorCount: new Set(findings.flatMap((f) => f.detectors)).size,
    };
  }
}

// ---------------------------------------------------------------------------
// Types used by the report (re-exported for convenience)
// ---------------------------------------------------------------------------

export interface ConflictReportSummary {
  bySeverity: Record<ConflictSeverity, number>;
  byType: Record<ConflictType, number>;
  maxSeverity: ConflictSeverity;
  detectorCount: number;
}

// ---------------------------------------------------------------------------
// Convenience function
// ---------------------------------------------------------------------------

/**
 * One-shot aggregation without instantiating a ConflictAggregator.
 */
export function aggregateConflicts(
  detectorOutputs: ConflictFinding[][],
  options?: Partial<AggregatorOptions>,
): ConflictReport {
  const aggregator = new ConflictAggregator(options);
  return aggregator.aggregate(detectorOutputs);
}
