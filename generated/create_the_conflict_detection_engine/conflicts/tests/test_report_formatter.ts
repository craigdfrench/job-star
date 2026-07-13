/**
 * Tests for the Conflict Report Formatter
 */

import {
  toJSON,
  toJSONObject,
  toMarkdown,
  toPlainText,
  toLogLine,
  findingToLogLine,
  formatReport,
} from '../report_formatter';
import { ConflictReport, ConflictFinding } from '../types';

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

function makeReport(): ConflictReport {
  return {
    generatedAt: '2025-01-15T10:30:00.000Z',
    totalFindings: 2,
    summary: {
      bySeverity: {
        critical: 1,
        high: 1,
        medium: 0,
        low: 0,
        info: 0,
      },
      byType: {
        duplicate: 1,
        contradiction: 1,
        competing_resource: 0,
        tension: 0,
      },
      maxSeverity: 'critical',
      detectorCount: 2,
    },
    findings: [
      {
        id: 'finding-1',
        goalIds: ['goal-a', 'goal-b'],
        types: ['contradiction'],
        severity: 'critical',
        confidence: 0.95,
        description: 'Goal A and Goal B have contradictory success criteria.',
        detectors: ['ContradictionDetector'],
        detectedAt: '2025-01-15T10:29:00.000Z',
        suggestions: ['Resolve the conflicting criteria before proceeding.'],
        metadata: { domain: 'work' },
      },
      {
        id: 'finding-2',
        goalIds: ['goal-c', 'goal-d'],
        types: ['duplicate'],
        severity: 'high',
        confidence: 0.88,
        description: 'Goal C and Goal D are likely duplicates.',
        detectors: ['DuplicateDetector'],
        detectedAt: '2025-01-15T10:29:30.000Z',
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('Report Formatter', () => {
  const report = makeReport();

  describe('toJSON', () => {
    it('should produce valid JSON', () => {
      const json = toJSON(report);
      const parsed = JSON.parse(json);
      expect(parsed.totalFindings).toBe(2);
      expect(parsed.findings).toHaveLength(2);
    });

    it('should include summary', () => {
      const json = toJSON(report);
      const parsed = JSON.parse(json);
      expect(parsed.summary.bySeverity.critical).toBe(1);
      expect(parsed.summary.maxSeverity).toBe('critical');
    });
  });

  describe('toJSONObject', () => {
    it('should return a parsed object', () => {
      const obj = toJSONObject(report);
      expect(obj.totalFindings).toBe(2);
      expect(obj.findings[0].id).toBe('finding-1');
    });
  });

  describe('toMarkdown', () => {
    it('should include a header', () => {
      const md = toMarkdown(report);
      expect(md).toContain('# Conflict Detection Report');
      expect(md).toContain('**Total Findings:** 2');
    });

    it('should include summary table', () => {
      const md = toMarkdown(report);
      expect(md).toContain('## Summary');
      expect(md).toContain('CRITICAL');
      expect(md).toContain('Contradiction');
    });

    it('should include findings with severity icons', () => {
      const md = toMarkdown(report);
      expect(md).toContain('🔴');
      expect(md).toContain('🟠');
      expect(md).toContain('finding-1');
      expect(md).toContain('finding-2');
    });

    it('should include suggestions when present', () => {
      const md = toMarkdown(report);
      expect(md).toContain('**Suggestions:**');
      expect(md).toContain('Resolve the conflicting criteria');
    });

    it('should show no-conflicts message for empty report', () => {
      const emptyReport: ConflictReport = {
        generatedAt: '2025-01-15T10:30:00.000Z',
        totalFindings: 0,
        summary: {
          bySeverity: { critical: 0, high: 0, medium: 0, low: 0, info: 0 },
          byType: { duplicate: 0, contradiction: 0, competing_resource: 0, tension: 0 },
          maxSeverity: 'info',
          detectorCount: 0,
        },
        findings: [],
      };
      const md = toMarkdown(emptyReport);
      expect(md).toContain('No conflicts detected');
    });
  });

  describe('toPlainText', () => {
    it('should include header and summary', () => {
      const text = toPlainText(report);
      expect(text).toContain('=== Conflict Detection Report ===');
      expect(text).toContain('Total:        2 finding(s)');
      expect(text).toContain('Max Severity: CRITICAL');
    });

    it('should include findings', () => {
      const text = toPlainText(report);
      expect(text).toContain('[1] CRITICAL');
      expect(text).toContain('[2] HIGH');
      expect(text).toContain('goal-a');
      expect(text).toContain('goal-c');
    });

    it('should show no-conflicts message for empty report', () => {
      const emptyReport: ConflictReport = {
        generatedAt: '2025-01-15T10:30:00.000Z',
        totalFindings: 0,
        summary: {
          bySeverity: { critical: 0, high: 0, medium: 0, low: 0, info: 0 },
          byType: { duplicate: 0, contradiction: 0, competing_resource: 0, tension: 0 },
          maxSeverity: 'info',
          detectorCount: 0,
        },
        findings: [],
      };
      const text = toPlainText(emptyReport);
      expect(text).toContain('No conflicts detected');
    });
  });

  describe('toLogLine', () => {
    it('should produce a single-line summary', () => {
      const line = toLogLine(report);
      expect(line).toContain('conflict_report');
      expect(line).toContain('total=2');
      expect(line).toContain('max_severity=critical');
      expect(line).toContain('critical=1');
      expect(line).toContain('high=1');
      // Should be a single line
      expect(line.split('\n')).toHaveLength(1);
    });
  });

  describe('findingToLogLine', () => {
    it('should produce a single-line finding summary', () => {
      const finding = report.findings[0];
      const line = findingToLogLine(finding);
      expect(line).toContain('conflict_finding');
      expect(line).toContain('id=finding-1');
      expect(line).toContain('severity=critical');
      expect(line).toContain('types=contradiction');
      expect(line).toContain('goals=goal-a,goal-b');
      expect(line.split('\n')).toHaveLength(1);
    });
  });

  describe('formatReport', () => {
    it('should format as JSON', () => {
      const result = formatReport(report, 'json');
      expect(() => JSON.parse(result)).not.toThrow();
    });

    it('should format as markdown', () => {
      const result = formatReport(report, 'markdown');
      expect(result).toContain('# Conflict Detection Report');
    });

    it('should format as text', () => {
      const result = formatReport(report, 'text');
      expect(result).toContain('=== Conflict Detection Report ===');
    });

    it('should format as logline', () => {
      const result = formatReport(report, 'logline');
      expect(result).toContain('conflict_report');
    });

    it('should throw for unknown format', () => {
      expect(() => formatReport(report, 'unknown' as never)).toThrow();
    });
  });
});
