import { ConflictEngine } from './conflicts';

const engine = new ConflictEngine({
  strictValidation: false,
  deduplicateReports: true,
  minConfidence: 0.5,
});

// Register detectors (to be implemented)
// engine.registerDetector(new DuplicateDetector());
// engine.registerDetector(new ContradictionDetector());

const result = await engine.detectConflicts(goals);

console.log(`Found ${result.summary.totalConflicts} conflicts`);
for (const report of result.reports) {
  console.log(`  [${report.severity}] ${report.type}: ${report.title}`);
}


// --- DUPLICATE BLOCK ---

interface ConflictDetector {
  readonly name: string;
  readonly conflictTypes: ConflictType[];
  readonly priority?: number;     // lower = runs first
  readonly enabled?: boolean;
  detect(goals: Goal[]): Promise<ConflictReport[]> | ConflictReport[];
}


// --- DUPLICATE BLOCK ---

// src/conflicts/__tests__/engine.test.ts

import { ConflictEngine } from '../engine';
import { ConflictDetector } from '../detector';
import {
  Goal,
  ConflictReport,
  ConflictType,
  ConflictEngineResult,
} from '../types';

// ─── Test Helpers ────────────────────────────────────────────────

function makeGoal(overrides: Partial<Goal> = {}): Goal {
  return {
    id: `goal-${Math.random().toString(36).slice(2, 9)}`,
    title: 'Test goal',
    domain: 'work',
    status: 'active',
    ...overrides,
  };
}

function makeReport(
  type: ConflictType,
  goalIds: string[],
  overrides: Partial<ConflictReport> = {},
): ConflictReport {
  return {
    id: `report-${Math.random().toString(36).slice(2, 9)}`,
    type,
    severity: 'medium',
    confidence: 0.8,
    title: `Test ${type} conflict`,
    description: 'Test conflict description',
    goalIds,
    domainCrossing: false,
    domains: ['work'],
    detectedBy: 'test-detector',
    detectedAt: new Date().toISOString(),
    ...overrides,
  };
}

// ─── Mock Detectors ──────────────────────────────────────────────

class MockDetector implements ConflictDetector {
  readonly name: string;
  readonly conflictTypes: ConflictType[];
  readonly priority?: number;
  readonly enabled?: boolean;
  private reports: ConflictReport[];

  constructor(
    name: string,
    reports: ConflictReport[] = [],
    opts: { priority?: number; enabled?: boolean; types?: ConflictType[] } = {},
  ) {
    this.name = name;
    this.reports = reports;
    this.priority = opts.priority ?? 100;
    this.enabled = opts.enabled ?? true;
    this.conflictTypes = opts.types ?? ['tension'];
  }

  detect(): ConflictReport[] {
    return [...this.reports];
  }
}

class FailingDetector implements ConflictDetector {
  readonly name = 'failing-detector';
  readonly conflictTypes: ConflictType[] = ['tension'];
  readonly enabled = true;

  detect(): ConflictReport[] {
    throw new Error('Detector intentionally failed');
  }
}

// ─── Tests ───────────────────────────────────────────────────────

describe('ConflictEngine', () => {
  describe('detectConflicts', () => {
    it('returns empty result when no goals provided', async () => {
      const engine = new ConflictEngine();
      const result = await engine.detectConflicts([]);

      expect(result.reports).toEqual([]);
      expect(result.summary.totalConflicts).toBe(0);
      expect(result.metadata.inputGoalCount).toBe(0);
    });

    it('returns empty result when only one goal provided', async () => {
      const engine = new ConflictEngine();
      const result = await engine.detectConflicts([makeGoal()]);

      expect(result.reports).toEqual([]);
      expect(result.summary.totalConflicts).toBe(0);
    });

    it('returns empty result when no detectors registered', async () => {
      const engine = new ConflictEngine();
      const goals = [makeGoal({ id: 'g1' }), makeGoal({ id: 'g2' })];
      const result = await engine.detectConflicts(goals);

      expect(result.reports).toEqual([]);
      expect(result.metadata.detectorsRun).toEqual([]);
    });

    it('runs registered detectors and aggregates reports', async () => {
      const goals = [makeGoal({ id: 'g1' }), makeGoal({ id: 'g2' })];
      const reports = [
        makeReport('duplicate', ['g1', 'g2'], { severity: 'high' }),
      ];
      const detector = new MockDetector('dup-detector', reports, {
        types: ['duplicate'],
      });

      const engine = new ConflictEngine();
      engine.registerDetector(detector);

      const result = await engine.detectConflicts(goals);

      expect(result.reports).toHaveLength(1);
      expect(result.reports[0].type).toBe('duplicate');
      expect(result.metadata.detectorsRun).toEqual(['dup-detector']);
    });

    it('runs multiple detectors in priority order', async () => {
      const executionOrder: string[] = [];

      class OrderedDetector extends MockDetector {
        detect(): ConflictReport[] {
          executionOrder.push(this.name);
          return super.detect();
        }
      }

      const d1 = new OrderedDetector('low-priority', [], { priority: 200 });
      const d2 = new OrderedDetector('high-priority', [], { priority: 10 });
      const d3 = new OrderedDetector('mid-priority', [], { priority: 100 });

      const engine = new ConflictEngine();
      engine.registerDetector(d1);
      engine.registerDetector(d2);
      engine.registerDetector(d3);

      await engine.detectConflicts([makeGoal({ id: 'g1' }), makeGoal({ id: 'g2' })]);

      expect(executionOrder).toEqual(['high-priority', 'mid-priority', 'low-priority']);
    });

    it('skips disabled detectors', async () => {
      const detector = new MockDetector('disabled', [], { enabled: false });

      const engine = new ConflictEngine();
      engine.registerDetector(detector);

      const result = await engine.detectConflicts([
        makeGoal({ id: 'g1' }),
        makeGoal({ id: 'g2' }),
      ]);

      expect(result.metadata.detectorsRun).toEqual([]);
    });

    it('continues when a detector fails (non-strict mode)', async () => {
      const goodReports = [makeReport('tension', ['g1', 'g2'])];
      const goodDetector = new MockDetector('good', goodReports);
      const badDetector = new FailingDetector();

      const engine = new ConflictEngine({ strictValidation: false });
      engine.registerDetector(badDetector);
      engine.registerDetector(goodDetector);

      const result = await engine.detectConflicts([
        makeGoal({ id: 'g1' }),
        makeGoal({ id: 'g2' }),
      ]);

      expect(result.reports).toHaveLength(1);
      expect(result.metadata.detectorsRun).toEqual(['good']);
    });

    it('throws when a detector fails in strict mode', async () => {
      const badDetector = new FailingDetector();
      const engine = new ConflictEngine({ strictValidation: true });
      engine.registerDetector(badDetector);

      await expect(
        engine.detectConflicts([makeGoal({ id: 'g1' }), makeGoal({ id: 'g2' })]),
      ).rejects.toThrow('Detector intentionally failed');
    });

    it('deduplicates reports with same type and goal IDs', async () => {
      const goals = [makeGoal({ id: 'g1' }), makeGoal({ id: 'g2' })];
      const report1 = makeReport('duplicate', ['g1', 'g2']);
      const report2 = makeReport('duplicate', ['g2', 'g1']); // same goals, different order

      const engine = new ConflictEngine({ deduplicateReports: true });
      engine.registerDetector(new MockDetector('d1', [report1]));
      engine.registerDetector(new MockDetector('d2', [report2]));

      const result = await engine.detectConflicts(goals);

      expect(result.reports).toHaveLength(1);
    });

    it('does not deduplicate when disabled', async () => {
      const goals = [makeGoal({ id: 'g1' }), makeGoal({ id: 'g2' })];
      const report1 = makeReport('duplicate', ['g1', 'g2']);
      const report2 = makeReport('duplicate', ['g1', 'g2']);

      const engine = new ConflictEngine({ deduplicateReports: false });
      engine.registerDetector(new MockDetector('d1', [report1]));
      engine.registerDetector(new MockDetector('d2', [report2]));

      const result = await engine.detectConflicts(goals);

      expect(result.reports).toHaveLength(2);
    });

    it('filters reports below minConfidence threshold', async () => {
      const goals = [makeGoal({ id: 'g1' }), makeGoal({ id: 'g2' })];
      const lowConfidence = makeReport('tension', ['g1', 'g2'], { confidence: 0.3 });
      const highConfidence = makeReport('tension', ['g1', 'g2'], { confidence: 0.9 });

      const engine = new ConflictEngine({ minConfidence: 0.5, deduplicateReports: false });
      engine.registerDetector(
        new MockDetector('d1', [lowConfidence, highConfidence]),
      );

      const result = await engine.detectConflicts(goals);

      expect(result.reports).toHaveLength(1);
      expect(result.reports[0].confidence).toBe(0.9);
    });

    it('sorts reports by severity then confidence', async () => {
      const goals = [makeGoal({ id: 'g1' }), makeGoal({ id: 'g2' })];
      const reports = [
        makeReport('tension', ['g1', 'g2'], { severity: 'low', confidence: 0.9 }),
        makeReport('duplicate', ['g1', 'g2'], { severity: 'critical', confidence: 0.5 }),
        makeReport('contradiction', ['g1', 'g2'], { severity: 'high', confidence: 0.8 }),
        makeReport('resource_competition', ['g1', 'g2'], { severity: 'high', confidence: 0.95 }),
      ];

      const engine = new ConflictEngine({ deduplicateReports: false });
      engine.registerDetector(new MockDetector('d1', reports));

      const result = await engine.detectConflicts(goals);

      expect(result.reports[0].severity).toBe('critical');
      expect(result.reports[1].severity).toBe('high');
      expect(result.reports[1].confidence).toBe(0.95); // higher confidence first
      expect(result.reports[2].severity).toBe('high');
      expect(result.reports[2].confidence).toBe(0.8);
      expect(result.reports[3].severity).toBe('low');
    });

    it('builds correct summary statistics', async () => {
      const goals = [
        makeGoal({ id: 'g1', domain: 'work' }),
        makeGoal({ id: 'g2', domain: 'personal' }),
        makeGoal({ id: 'g3', domain: 'work' }),
      ];

      const reports = [
        makeReport('duplicate', ['g1', 'g3'], { severity: 'high' }),
        makeReport('tension', ['g1', 'g2'], {
          severity: 'medium',
          domainCrossing: true,
          domains: ['work', 'personal'],
        }),
      ];

      const engine = new ConflictEngine();
      engine.registerDetector(new MockDetector('d1', reports));

      const result = await engine.detectConflicts(goals);

      expect(result.summary.totalConflicts).toBe(2);
      expect(result.summary.byType.duplicate).toBe(1);
      expect(result.summary.byType.tension).toBe(1);
      expect(result.summary.bySeverity.high).toBe(1);
      expect(result.summary.bySeverity.medium).toBe(1);
      expect(result.summary.crossDomainConflicts).toBe(1);
      expect(result.summary.goalsWithConflicts).toBe(3); // g1, g2, g3
    });

    it('filters out goals with missing IDs', async () => {
      const goals = [
        makeGoal({ id: 'g1' }),
        { ...makeGoal(), id: '' } as Goal, // invalid
        makeGoal({ id: 'g2' }),
      ];

      const engine = new ConflictEngine();
      const result = await engine.detectConflicts(goals);

      expect(result.metadata.inputGoalCount).toBe(2);
    });

    it('deduplicates goals with same ID', async () => {
      const goals = [
        makeGoal({ id: 'g1', title: 'First' }),
        makeGoal({ id: 'g1', title: 'Duplicate' }),
        makeGoal({ id: 'g2' }),
      ];

      const engine = new ConflictEngine();
      const result = await engine.detectConflicts(goals);

      expect(result.metadata.inputGoalCount).toBe(2);
    });
  });

  describe('registerDetector / unregisterDetector', () => {
    it('registers and retrieves detectors', () => {
      const engine = new ConflictEngine();
      const detector = new MockDetector('test');

      engine.registerDetector(detector);

      expect(engine.getDetector('test')).toBe(detector);
      expect(engine.getDetectors()).toHaveLength(1);
    });

    it('replaces detector with same name', () => {
      const engine = new ConflictEngine();
      const d1 = new MockDetector('same-name');
      const d2 = new MockDetector('same-name');

      engine.registerDetector(d1);
      engine.registerDetector(d2);

      expect(engine.getDetectors()).toHaveLength(1);
      expect(engine.getDetector('same-name')).toBe(d2);
    });

    it('unregisters detectors by name', () => {
      const engine = new ConflictEngine();
      const detector = new MockDetector('test');

      engine.registerDetector(detector);
      const removed = engine.unregisterDetector('test');

      expect(removed).toBe(true);
      expect(engine.getDetectors()).toHaveLength(0);
    });

    it('throws when registering detector with empty name', () => {
      const engine = new ConflictEngine();
      const detector = new MockDetector('');

      expect(() => engine.registerDetector(detector)).toThrow(
        'Detector must have a non-empty name',
      );
    });
  });

  describe('metadata', () => {
    it('includes engine version in result', async () => {
      const engine = new ConflictEngine();
      const result = await engine.detectConflicts([]);

      expect(result.metadata.engineVersion).toBe(ConflictEngine.VERSION);
    });

    it('includes processing time', async () => {
      const engine = new ConflictEngine();
      const result = await engine.detectConflicts([]);

      expect(result.metadata.processingTimeMs).toBeGreaterThanOrEqual(0);
    });
  });
});
