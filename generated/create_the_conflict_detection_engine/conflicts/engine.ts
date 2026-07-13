/**
 * ConflictEngine — Facade for the full conflict detection pipeline.
 *
 * Orchestrates:
 *   1. Duplicate detection
 *   2. Contradiction detection
 *   3. Competing resource detection
 *   4. Tension detection
 *   5. Cross-domain awareness pass
 *   6. Aggregation / dedup / scoring
 *
 * The engine is stateless per invocation — it takes goals + context and
 * returns a ConflictDetectionResult. Persistence is handled by the caller
 * (typically the GoalStore).
 */

import {
  type Conflict,
  type ConflictDetectionResult,
  type ConflictReport,
  type GoalContext,
  ConflictType,
  ConflictSeverity,
  ConflictStatus,
} from "../conflict/types";

import { DuplicateDetector } from "../conflict/detectors/duplicate";
import { ContradictionDetector } from "../conflict/detectors/contradiction";
import { CompetingResourceDetector } from "../conflict/detectors/competing_resource";
import { TensionDetector } from "../conflict/detectors/tension";
import { CrossDomainAwarenessLayer } from "../conflict/cross_domain";
import { ConflictReportAggregator } from "../conflict/aggregator";

export interface ConflictEngineConfig {
  /** Enable or disable individual detectors */
  enableDuplicate: boolean;
  enableContradiction: boolean;
  enableCompetingResource: boolean;
  enableTension: boolean;
  enableCrossDomain: boolean;

  /** Minimum severity to include in results (default: low) */
  minSeverity: ConflictSeverity;

  /** Whether to include resolved conflicts in the report (default: false) */
  includeResolved: boolean;
}

const DEFAULT_CONFIG: ConflictEngineConfig = {
  enableDuplicate: true,
  enableContradiction: true,
  enableCompetingResource: true,
  enableTension: true,
  enableCrossDomain: true,
  minSeverity: ConflictSeverity.Low,
  includeResolved: false,
};

export class ConflictEngine {
  private readonly duplicateDetector: DuplicateDetector;
  private readonly contradictionDetector: ContradictionDetector;
  private readonly resourceDetector: CompetingResourceDetector;
  private readonly tensionDetector: TensionDetector;
  private readonly crossDomain: CrossDomainAwarenessLayer;
  private readonly aggregator: ConflictReportAggregator;
  private readonly config: ConflictEngineConfig;

  constructor(config?: Partial<ConflictEngineConfig>) {
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.duplicateDetector = new DuplicateDetector();
    this.contradictionDetector = new ContradictionDetector();
    this.resourceDetector = new CompetingResourceDetector();
    this.tensionDetector = new TensionDetector();
    this.crossDomain = new CrossDomainAwarenessLayer();
    this.aggregator = new ConflictReportAggregator();
  }

  /**
   * Run conflict detection against a set of goals.
   *
   * @param goals  All goals to check (typically the active goal set).
   * @param context  Optional context (resources, schedules, domain info).
   * @returns Detection result with all conflicts found.
   */
  detect(
    goals: GoalContext[],
    context?: Record<string, unknown>,
  ): ConflictDetectionResult {
    const allConflicts: Conflict[] = [];
    const startedAt = new Date();

    // 1. Duplicate detection
    if (this.config.enableDuplicate) {
      const duplicates = this.duplicateDetector.detect(goals);
      allConflicts.push(...duplicates);
    }

    // 2. Contradiction detection
    if (this.config.enableContradiction) {
      const contradictions = this.contradictionDetector.detect(goals);
      allConflicts.push(...contradictions);
    }

    // 3. Competing resource detection
    if (this.config.enableCompetingResource) {
      const resourceConflicts = this.resourceDetector.detect(goals, context);
      allConflicts.push(...resourceConflicts);
    }

    // 4. Tension detection
    if (this.config.enableTension) {
      const tensions = this.tensionDetector.detect(goals);
      allConflicts.push(...tensions);
    }

    // 5. Cross-domain awareness pass — may upgrade severity or add notes
    if (this.config.enableCrossDomain) {
      const crossDomainConflicts = this.crossDomain.analyze(goals, allConflicts);
      allConflicts.push(...crossDomainConflicts);
    }

    // 6. Aggregate, deduplicate, sort, and score
    const report = this.aggregator.aggregate(allConflicts, {
      minSeverity: this.config.minSeverity,
      includeResolved: this.config.includeResolved,
    });

    const completedAt = new Date();

    return {
      report,
      metadata: {
        goalsAnalyzed: goals.length,
        conflictsFound: report.conflicts.length,
        detectionDurationMs: completedAt.getTime() - startedAt.getTime(),
        detectorsRun: this.getEnabledDetectors(),
        timestamp: completedAt,
      },
    };
  }

  /**
   * Run conflict detection focused on a single goal vs. the rest.
   * Useful when a goal is added or updated and we want a quick check.
   */
  detectForGoal(
    targetGoal: GoalContext,
    otherGoals: GoalContext[],
    context?: Record<string, unknown>,
  ): ConflictReport {
    const allGoals = [targetGoal, ...otherGoals];
    const result = this.detect(allGoals, context);

    // Filter to only conflicts involving the target goal
    const relevantConflicts = result.report.conflicts.filter(
      (c) => c.goalIds.includes(targetGoal.id),
    );

    return {
      ...result.report,
      conflicts: relevantConflicts,
      summary: this.aggregator.summarize(relevantConflicts),
    };
  }

  /**
   * Quick check: does a goal have any active blocking conflicts?
   */
  hasBlockingConflicts(goalId: string, report: ConflictReport): boolean {
    return report.conflicts.some(
      (c) =>
        c.goalIds.includes(goalId) &&
        c.status === ConflictStatus.Active &&
        (c.severity === ConflictSeverity.Critical ||
          c.severity === ConflictSeverity.High),
    );
  }

  private getEnabledDetectors(): string[] {
    const detectors: string[] = [];
    if (this.config.enableDuplicate) detectors.push("duplicate");
    if (this.config.enableContradiction) detectors.push("contradiction");
    if (this.config.enableCompetingResource) detectors.push("competing_resource");
    if (this.config.enableTension) detectors.push("tension");
    if (this.config.enableCrossDomain) detectors.push("cross_domain");
    return detectors;
  }
}
