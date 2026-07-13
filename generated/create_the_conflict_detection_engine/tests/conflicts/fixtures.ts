// tests/conflicts/engine.test.ts
import { describe, it, expect, beforeEach } from 'vitest';
import { ConflictEngine } from '../../src/conflicts/engine';
import {
  duplicateGoals,
  contradictoryGoals,
  competingResourceGoals,
  tensionGoals,
  crossDomainGoals,
  mixedConflictGoals,
  noConflictGoals,
  emptyGoalSet,
  singleGoal,
  circularConflictGoals,
} from './fixtures';

describe('ConflictEngine (integration)', () => {
  let engine: ConflictEngine;

  beforeEach(() => {
    engine = new ConflictEngine();
  });

  // ─── Aggregation ──────────────────────────────────────────────────────────

  describe('aggregation', () => {
    it('should aggregate conflicts from all detectors', () => {
      const report = engine.analyze(mixedConflictGoals);

      expect(report.conflicts.length).toBeGreaterThan(0);

      const types = new Set(report.conflicts.map((c) => c.type));
      expect(types.has('duplicate')).toBe(true);
      expect(types.has('contradiction')).toBe(true);
      expect(types.has('competing_resource')).toBe(true);
    });

    it('should not produce duplicate conflict entries for the same pair', () => {
      const report = engine.analyze(mixedConflictGoals);

      const pairKeys = report.conflicts.map((c) => {
        const sorted = [...c.goalIds].sort();
        return `${sorted.join('|')}|${c.type}`;
      });

      const uniqueKeys = new Set(pairKeys);
      expect(pairKeys.length).toBe(uniqueKeys.size);
    });

    it('should sort conflicts by severity (highest first)', () => {
      const report = engine.analyze(mixedConflictGoals);

      for (let i = 1; i < report.conflicts.length; i++) {
        expect(report.conflicts[i].severity).toBeLessThanOrEqual(
          report.conflicts[i - 1].severity
        );
      }
    });
  });

  // ─── Cross-domain awareness ───────────────────────────────────────────────

  describe('cross-domain awareness', () => {
    it('should detect conflicts spanning multiple domains', () => {
      const report = engine.analyze(crossDomainGoals);

      const crossDomainConflicts = report.conflicts.filter((c) => {
        const domains = c.goalIds.map((id) => {
          const goal = crossDomainGoals.find((g) => g.id === id);
          return goal?.domain;
        });
        return new Set(domains).size > 1;
      });

      expect(crossDomainConflicts.length).toBeGreaterThan(0);
    });

    it('should tag cross-domain conflicts in metadata', () => {
      const report = engine.analyze(crossDomainGoals);

      const crossDomain = report.conflicts.filter((c) => c.metadata?.crossDomain === true);
      expect(crossDomain.length).toBeGreaterThan(0);
    });

    it('should include domain information in the report summary', () => {
      const report = engine.analyze(crossDomainGoals);

      expect(report.summary).toBeDefined();
      expect(report.summary.domainsAffected).toBeDefined();
      expect(report.summary.domainsAffected.length).toBeGreaterThan(1);
    });
  });

  // ─── Edge cases ───────────────────────────────────────────────────────────

  describe('edge cases', () => {
    it('should handle empty goal set gracefully', () => {
      const report = engine.analyze(emptyGoalSet);

      expect(report.conflicts).toHaveLength(0);
      expect(report.summary.totalConflicts).toBe(0);
      expect(report.summary.goalsAnalyzed).toBe(0);
    });

    it('should handle single goal gracefully', () => {
      const report = engine.analyze(singleGoal);

      expect(report.conflicts).toHaveLength(0);
      expect(report.summary.totalConflicts).toBe(0);
      expect(report.summary.goalsAnalyzed).toBe(1);
    });

    it('should handle goals with no conflicts', () => {
      const report = engine.analyze(noConflictGoals);

      expect(report.conflicts).toHaveLength(0);
      expect(report.summary.totalConflicts).toBe(0);
      expect(report.summary.goalsAnalyzed).toBe(3);
    });

    it('should handle circular conflicts without infinite loops', () => {
      const report = engine.analyze(circularConflictGoals);

      // Should complete without hanging
      expect(report).toBeDefined();
      expect(report.summary.totalConflicts).toBeGreaterThan(0);

      // All three goals should appear in at least one conflict
      const involvedGoals = new Set(report.conflicts.flatMap((c) => c.goalIds));
      expect(involvedGoals.has('circ-1')).toBe(true);
      expect(involvedGoals.has('circ-2')).toBe(true);
      expect(involvedGoals.has('circ-3')).toBe(true);
    });

    it('should not crash on goals with missing optional fields', () => {
      const minimalGoals = [
        { id: 'min-1', title: 'Goal A', domain: 'work' },
        { id: 'min-2', title: 'Goal B', domain: 'work' },
      ] as any[];

      expect(() => engine.analyze(minimalGoals)).not.toThrow();
    });
  });

  // ─── Per-type detection through the engine ────────────────────────────────

  describe('per-type detection through engine', () => {
    it('should detect duplicates via the full pipeline', () => {
      const report = engine.analyze(duplicateGoals);
      const duplicates = report.conflicts.filter((c) => c.type === 'duplicate');

      expect(duplicates.length).toBeGreaterThan(0);
      expect(duplicates[0].goalIds).toContain('dup-1');
      expect(duplicates[0].goalIds).toContain('dup-2');
    });

    it('should detect contradictions via the full pipeline', () => {
      const report = engine.analyze(contradictoryGoals);
      const contradictions = report.conflicts.filter((c) => c.type === 'contradiction');

      expect(contradictions.length).toBeGreaterThan(0);
    });

    it('should detect competing resources via the full pipeline', () => {
      const report = engine.analyze(competingResourceGoals);
      const resourceConflicts = report.conflicts.filter((c) => c.type === 'competing_resource');

      expect(resourceConflicts.length).toBeGreaterThan(0);
    });

    it('should detect tensions via the full pipeline', () => {
      const report = engine.analyze(tensionGoals);
      const tensions = report.conflicts.filter((c) => c.type === 'tension');

      expect(tensions.length).toBeGreaterThan(0);
    });
  });

  // ─── Report structure ─────────────────────────────────────────────────────

  describe('report structure', () => {
    it('should produce a well-formed conflict report', () => {
      const report = engine.analyze(mixedConflictGoals);

      expect(report).toHaveProperty('conflicts');
      expect(report).toHaveProperty('summary');
      expect(report).toHaveProperty('timestamp');
      expect(report).toHaveProperty('id');

      expect(Array.isArray(report.conflicts)).toBe(true);
    });

    it('should include counts by conflict type in summary', () => {
      const report = engine.analyze(mixedConflictGoals);

      expect(report.summary.countsByType).toBeDefined();
      expect(report.summary.countsByType.duplicate).toBeGreaterThanOrEqual(0);
      expect(report.summary.countsByType.contradiction).toBeGreaterThanOrEqual(0);
      expect(report.summary.countsByType.competing_resource).toBeGreaterThanOrEqual(0);
      expect(report.summary.countsByType.tension).toBeGreaterThanOrEqual(0);

      const total =
        report.summary.countsByType.duplicate +
        report.summary.countsByType.contradiction +
        report.summary.countsByType.competing_resource +
        report.summary.countsByType.tension;

      expect(total).toBe(report.summary.totalConflicts);
    });

    it('should include affected goal IDs in summary', () => {
      const report = engine.analyze(mixedConflictGoals);

      expect(report.summary.affectedGoalIds).toBeDefined();
      expect(Array.isArray(report.summary.affectedGoalIds)).toBe(true);
    });
  });
});


// --- DUPLICATE BLOCK ---

// tests/conflicts/setup.ts
// Shared test setup for conflict detection tests

import { beforeAll } from 'vitest';

beforeAll(() => {
  // Ensure consistent timezone for date comparisons in tests
  process.env.TZ = 'UTC';
});
