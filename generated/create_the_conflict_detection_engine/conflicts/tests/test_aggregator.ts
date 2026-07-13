/**
 * Tests for the Conflict Report Aggregator
 */

import { ConflictAggregator, aggregateConflicts } from '../aggregator';
import { ConflictFinding, ConflictReport } from '../types';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeFinding(
  id: string,
  goalIds: string[],
  types: ConflictFinding['types'],
  severity: ConflictFinding['severity'],
  confidence: number,
  detectors: string[],
  description: string = 'test finding',
): ConflictFinding {
  return {
    id,
    goalIds,
    types,
    severity,
    confidence,
    description,
    detectors,
    detectedAt: '2025-01-01T00:00:00.000Z',
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('ConflictAggregator', () => {
  let aggregator: ConflictAggregator;

  beforeEach(() => {
    aggregator = new ConflictAggregator();
  });

  describe('basic aggregation', () => {
    it('should flatten multiple detector outputs into one report', () => {
      const detectorA: ConflictFinding[] = [
        makeFinding('a1', ['g1', 'g2'], ['duplicate'], 'high', 0.9, ['DuplicateDetector']),
      ];
      const detectorB: ConflictFinding[] = [
        makeFinding('b1', ['g3', 'g4'], ['contradiction'], 'critical', 0.95, ['ContradictionDetector']),
      ];

      const report = aggregator.aggregate([detectorA, detectorB]);

      expect(report.totalFindings).toBe(2);
      expect(report.findings).toHaveLength(2);
    });

    it('should return empty report for no inputs', () => {
      const report = aggregator.aggregate([]);

      expect(report.totalFindings).toBe(0);
      expect(report.findings).toHaveLength(0);
      expect(report.summary.maxSeverity).toBe('info');
    });
  });

  describe('deduplication', () => {
    it('should merge findings with the same goal pair', () => {
      const detectorA: ConflictFinding[] = [
        makeFinding('a1', ['g1', 'g2'], ['duplicate'], 'high', 0.9, ['DuplicateDetector'], 'dup finding'),
      ];
      const detectorB: ConflictFinding[] = [
        makeFinding('b1', ['g2', 'g1'], ['tension'], 'medium', 0.7, ['TensionDetector'], 'tension finding'),
      ];

      const report = aggregator.aggregate([detectorA, detectorB]);

      expect(report.totalFindings).toBe(1);
      const finding = report.findings[0];
      expect(finding.types).toContain('duplicate');
      expect(finding.types).toContain('tension');
      expect(finding.severity).toBe('high'); // max of high and medium
      expect(finding.confidence).toBeCloseTo(0.9);
      expect(finding.detectors).toContain('DuplicateDetector');
      expect(finding.detectors).toContain('TensionDetector');
    });

    it('should not merge findings with different goal pairs', () => {
      const detectorA: ConflictFinding[] = [
        makeFinding('a1', ['g1', 'g2'], ['duplicate'], 'high', 0.9, ['DuplicateDetector']),
      ];
      const detectorB: ConflictFinding[] = [
        makeFinding('b1', ['g1', 'g3'], ['duplicate'], 'high', 0.9, ['DuplicateDetector']),
      ];

      const report = aggregator.aggregate([detectorA, detectorB]);

      expect(report.totalFindings).toBe(2);
    });

    it('should respect mergeOverlapping=false option', () => {
      const agg = new ConflictAggregator({ mergeOverlapping: false });
      const detectorA: ConflictFinding[] = [
        makeFinding('a1', ['g1', 'g2'], ['duplicate'], 'high', 0.9, ['DuplicateDetector']),
      ];
      const detectorB: ConflictFinding[] = [
        makeFinding('b1', ['g2', 'g1'], ['tension'], 'medium', 0.7, ['TensionDetector']),
      ];

      const report = agg.aggregate([detectorA, detectorB]);

      expect(report.totalFindings).toBe(2);
    });
  });

  describe('sorting', () => {
    it('should sort by severity descending', () => {
      const findings: ConflictFinding[][] = [
        [
          makeFinding('f1', ['g1', 'g2'], ['tension'], 'low', 0.5, ['TensionDetector']),
          makeFinding('f2', ['g3', 'g4'], ['duplicate'], 'critical', 0.95, ['DuplicateDetector']),
          makeFinding('f3', ['g5', 'g6'], ['contradiction'], 'medium', 0.7, ['ContradictionDetector']),
        ],
      ];

      const report = aggregator.aggregate(findings);

      expect(report.findings[0].severity).toBe('critical');
      expect(report.findings[1].severity).toBe('medium');
      expect(report.findings[2].severity).toBe('low');
    });

    it('should sort by confidence as tiebreaker', () => {
      const findings: ConflictFinding[][] = [
        [
          makeFinding('f1', ['g1', 'g2'], ['duplicate'], 'high', 0.7, ['DuplicateDetector']),
          makeFinding('f2', ['g3', 'g4'], ['duplicate'], 'high', 0.9, ['DuplicateDetector']),
        ],
      ];

      const report = aggregator.aggregate(findings);

      expect(report.findings[0].confidence).toBe(0.9);
      expect(report.findings[1].confidence).toBe(0.7);
    });
  });

  describe('filtering', () => {
    it('should filter by minimum severity', () => {
      const agg = new ConflictAggregator({ minSeverity: 'high' });
      const findings: ConflictFinding[][] = [
        [
          makeFinding('f1', ['g1', 'g2'], ['tension'], 'low', 0.5, ['TensionDetector']),
          makeFinding('f2', ['g3', 'g4'], ['duplicate'], 'critical', 0.95, ['DuplicateDetector']),
          makeFinding('f3', ['g5', 'g6'], ['contradiction'], 'high', 0.8, ['ContradictionDetector']),
          makeFinding('f4', ['g7', 'g8'], ['tension'], 'medium', 0.6, ['TensionDetector']),
        ],
      ];

      const report = agg.aggregate(findings);

      expect(report.totalFindings).toBe(2);
      expect(report.findings.every((f) => ['critical', 'high'].includes(f.severity))).toBe(true);
    });

    it('should limit findings with maxFindings', () => {
      const agg = new ConflictAggregator({ maxFindings: 2 });
      const findings: ConflictFinding[][] = [
        [
          makeFinding('f1', ['g1', 'g2'], ['tension'], 'low', 0.5, ['TensionDetector']),
          makeFinding('f2', ['g3', 'g4'], ['duplicate'], 'critical', 0.95, ['DuplicateDetector']),
          makeFinding('f3', ['g5', 'g6'], ['contradiction'], 'high', 0.8, ['ContradictionDetector']),
        ],
      ];

      const report = agg.aggregate(findings);

      expect(report.totalFindings).toBe(2);
      expect(report.findings[0].severity).toBe('critical');
      expect(report.findings[1].severity).toBe('high');
    });
  });

  describe('summary', () => {
    it('should count findings by severity', () => {
      const findings: ConflictFinding[][] = [
        [
          makeFinding('f1', ['g1', 'g2'], ['tension'], 'low', 0.5, ['TensionDetector']),
          makeFinding('f2', ['g3', 'g4'], ['duplicate'], 'critical', 0.95, ['DuplicateDetector']),
        ],
      ];

      const report = aggregator.aggregate(findings);

      expect(report.summary.bySeverity.critical).toBe(1);
      expect(report.summary.bySeverity.low).toBe(1);
      expect(report.summary.bySeverity.high).toBe(0);
      expect(report.summary.maxSeverity).toBe('critical');
    });

    it('should count findings by type', () => {
      const findings: ConflictFinding[][] = [
        [
          makeFinding('f1', ['g1', 'g2'], ['duplicate', 'tension'], 'high', 0.9, ['DuplicateDetector', 'TensionDetector']),
          makeFinding('f2', ['g3', 'g4'], ['contradiction'], 'critical', 0.95, ['ContradictionDetector']),
        ],
      ];

      const report = aggregator.aggregate(findings);

      expect(report.summary.byType.duplicate).toBe(1);
      expect(report.summary.byType.tension).toBe(1);
      expect(report.summary.byType.contradiction).toBe(1);
      expect(report.summary.byType.competing_resource).toBe(0);
    });

    it('should count unique detectors', () => {
      const findings: ConflictFinding[][] = [
        [
          makeFinding('f1', ['g1', 'g2'], ['duplicate'], 'high', 0.9, ['DuplicateDetector']),
          makeFinding('f2', ['g3', 'g4'], ['tension'], 'medium', 0.7, ['TensionDetector']),
          makeFinding('f3', ['g5', 'g6'], ['duplicate'], 'low', 0.5, ['DuplicateDetector']),
        ],
      ];

      const report = aggregator.aggregate(findings);

      expect(report.summary.detectorCount).toBe(2);
    });
  });

  describe('convenience function', () => {
    it('aggregateConflicts should work without instantiating', () => {
      const findings: ConflictFinding[][] = [
        [makeFinding('f1', ['g1', 'g2'], ['duplicate'], 'high', 0.9, ['DuplicateDetector'])],
      ];

      const report = aggregateConflicts(findings);

      expect(report.totalFindings).toBe(1);
    });
  });
});
