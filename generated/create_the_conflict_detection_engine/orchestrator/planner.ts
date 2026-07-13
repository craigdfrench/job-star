/**
 * Orchestrator Planner — Plans work sessions by selecting goals to work on,
 * consulting active conflict reports to avoid scheduling conflicting work.
 *
 * The planner:
 *   1. Retrieves active goals from the GoalStore.
 *   2. Retrieves conflict reports for each goal.
 *   3. Filters out goals with blocking conflicts (or flags them for resolution).
 *   4. Ranks remaining goals by urgency and conflict load.
 *   5. Produces a WorkSessionPlan with selected goals, notes on conflicts,
 *      and any recommended resolutions.
 */

import type { GoalStore, Goal } from "../goals/store";
import {
  type ConflictReport,
  type ConflictReportSummary,
  ConflictSeverity,
  ConflictStatus,
  type Conflict,
} from "../conflicts";
import type { ConflictEngine } from "../conflicts";

// ─── Types ───────────────────────────────────────────────────────────

export interface WorkSessionGoal {
  goal: Goal;
  conflictSummary: ConflictReportSummary;
  /** Whether this goal was selected for the current session */
  selected: boolean;
  /** Reason for selection or exclusion */
  reason: string;
}

export interface ConflictResolution {
  conflictId: string;
  conflictType: string;
  description: string;
  affectedGoalIds: string[];
  recommendation: string;
  /** "block" = cannot proceed until resolved; "warn" = proceed with caution */
  action: "block" | "warn";
}

export interface WorkSessionPlan {
  /** Timestamp when the plan was generated */
  generatedAt: Date;
  /** Goals selected for work in this session, in priority order */
  selectedGoals: WorkSessionGoal[];
  /** Goals considered but not selected, with reasons */
  deferredGoals: WorkSessionGoal[];
  /** Conflicts that need attention before or during the session */
  conflictResolutions: ConflictResolution[];
  /** Overall session notes */
  notes: string;
  /** Whether the plan is blocked by unresolved critical conflicts */
  isBlocked: boolean;
}

export interface PlannerConfig {
  /** Maximum number of goals to select per session */
  maxGoalsPerSession: number;
  /** Whether to exclude goals with high-severity conflicts */
  excludeHighSeverityConflicts: boolean;
  /** Whether to exclude goals with critical conflicts */
  excludeCriticalConflicts: boolean;
  /** Whether to auto-run conflict detection before planning */
  autoRunConflictDetection: boolean;
  /** Domains to prioritize (in order) */
  priorityDomains: string[];
}

const DEFAULT_CONFIG: PlannerConfig = {
  maxGoalsPerSession: 3,
  excludeHighSeverityConflicts: false,
  excludeCriticalConflicts: true,
  autoRunConflictDetection: true,
  priorityDomains: [],
};

// ─── Planner ─────────────────────────────────────────────────────────

export class Planner {
  private goalStore: GoalStore;
  private conflictEngine: ConflictEngine;
  private config: PlannerConfig;

  constructor(
    goalStore: GoalStore,
    conflictEngine: ConflictEngine,
    config?: Partial<PlannerConfig>,
  ) {
    this.goalStore = goalStore;
    this.conflictEngine = conflictEngine;
    this.config = { ...DEFAULT_CONFIG, ...config };
  }

  /**
   * Generate a work session plan by consulting active goals and their
   * conflict reports.
   */
  async planSession(): Promise<WorkSessionPlan> {
    const generatedAt = new Date();

    // 1. Get all active goals
    const activeGoals = await this.goalStore.getActiveGoals();
    if (activeGoals.length === 0) {
      return {
        generatedAt,
        selectedGoals: [],
        deferredGoals: [],
        conflictResolutions: [],
        notes: "No active goals to plan.",
        isBlocked: false,
      };
    }

    // 2. Optionally re-run conflict detection for freshness
    if (this.config.autoRunConflictDetection) {
      await this.refreshAllConflictReports();
    }

    // 3. Get conflict summaries for each goal
    const goalSummaries = await Promise.all(
      activeGoals.map(async (goal) => {
        const summary = await this.goalStore.getConflictSummary(goal.id);
        return { goal, summary };
      }),
    );

    // 4. Categorize goals: selected vs deferred
    const selectedGoals: WorkSessionGoal[] = [];
    const deferredGoals: WorkSessionGoal[] = [];
    const conflictResolutions: ConflictResolution[] = [];
    const blockedGoalIds = new Set<string>();

    // Collect conflict resolutions for all active conflicts
    for (const { goal, summary } of goalSummaries) {
      for (const conflict of summary.conflicts) {
        if (conflict.status !== ConflictStatus.Active) continue;

        const resolution = this.createResolution(conflict);
        conflictResolutions.push(resolution);

        if (resolution.action === "block") {
          blockedGoalIds.add(goal.id);
        }
      }
    }

    // 5. Sort goals by priority (urgency + domain priority + conflict load)
    const sortedGoals = this.sortGoalsByPriority(goalSummaries);

    // 6. Select goals for the session
    for (const { goal, summary } of sortedGoals) {
      const hasBlockingConflicts = blockedGoalIds.has(goal.id);
      const hasCriticalConflicts = summary.highestSeverity === ConflictSeverity.Critical;
      const hasHighConflicts = summary.highestSeverity === ConflictSeverity.High;

      let selected = false;
      let reason = "";

      if (hasBlockingConflicts) {
        reason = "Deferred: has blocking conflicts that must be resolved first";
      } else if (hasCriticalConflicts && this.config.excludeCriticalConflicts) {
        reason = "Deferred: has unresolved critical-severity conflicts";
      } else if (hasHighConflicts && this.config.excludeHighSeverityConflicts) {
        reason = "Deferred: has unresolved high-severity conflicts";
      } else if (selectedGoals.length >= this.config.maxGoalsPerSession) {
        reason = "Deferred: session goal limit reached";
      } else {
        // Check if this goal conflicts with already-selected goals
        const conflictsWithSelected = this.conflictsWithSelectedGoals(
          goal.id,
          summary.conflicts,
          selectedGoals,
        );

        if (conflictsWithSelected) {
          reason = `Deferred: conflicts with already-selected goal(s) in this session`;
        } else {
          selected = true;
          const conflictNote =
            summary.activeConflicts > 0
              ? ` (proceeding with ${summary.activeConflicts} active non-blocking conflict(s))`
              : "";
          reason = `Selected: urgency=${goal.urgency}, domain=${goal.domain}${conflictNote}`;
        }
      }

      const sessionGoal: WorkSessionGoal = {
        goal,
        conflictSummary: summary,
        selected,
        reason,
      };

      if (selected) {
        selectedGoals.push(sessionGoal);
      } else {
        deferredGoals.push(sessionGoal);
      }
    }

    // 7. Determine if the plan is blocked
    const hasBlockingResolutions = conflictResolutions.some(
      (r) => r.action === "block",
    );
    const allGoalsBlocked =
      activeGoals.length > 0 &&
      selectedGoals.length === 0 &&
      blockedGoalIds.size === activeGoals.length;

    const isBlocked = allGoalsBlocked || (hasBlockingResolutions && selectedGoals.length === 0);

    // 8. Generate notes
    const notes = this.generateSessionNotes(
      selectedGoals,
      deferredGoals,
      conflictResolutions,
      isBlocked,
    );

    return {
      generatedAt,
      selectedGoals,
      deferredGoals,
      conflictResolutions,
      notes,
      isBlocked,
    };
  }

  /**
   * Get a quick assessment of whether a specific goal can be worked on
   * right now, considering its conflict status.
   */
  async assessGoal(goalId: string): Promise<{
    canWorkOn: boolean;
    blockingConflicts: Conflict[];
    warningConflicts: Conflict[];
  }> {
    const summary = await this.goalStore.getConflictSummary(goalId);

    const blockingConflicts = summary.conflicts.filter(
      (c) =>
        c.status === ConflictStatus.Active &&
        (c.severity === ConflictSeverity.Critical ||
          c.severity === ConflictSeverity.High),
    );

    const warningConflicts = summary.conflicts.filter(
      (c) =>
        c.status === ConflictStatus.Active &&
        c.severity !== ConflictSeverity.Critical &&
        c.severity !== ConflictSeverity.High,
    );

    return {
      canWorkOn: blockingConflicts.length === 0,
      blockingConflicts,
      warningConflicts,
    };
  }

  // ─── Internal Helpers ──────────────────────────────────────────────

  private async refreshAllConflictReports(): Promise<void> {
    // Force the store to re-run conflict detection by deleting and re-adding
    // In a real implementation, the store would have a refresh method
    // For now, we rely on the store's internal cache invalidation
    // The store's getAllConflictReports will handle freshness
    await this.goalStore.getAllConflictReports();
  }

  private sortGoalsByPriority(
    goals: Array<{ goal: Goal; summary: ConflictReportSummary }>,
  ): Array<{ goal: Goal; summary: ConflictReportSummary }> {
    const urgencyRank: Record<string, number> = {
      critical: 0,
      high: 1,
      "idle-opportunistic": 2,
      medium: 3,
      low: 4,
    };

    return [...goals].sort((a, b) => {
      // 1. Domain priority
      const aDomainPriority = this.config.priorityDomains.indexOf(a.goal.domain);
      const bDomainPriority = this.config.priorityDomains.indexOf(b.goal.domain);
      const aDom = aDomainPriority === -1 ? 999 : aDomainPriority;
      const bDom = bDomainPriority === -1 ? 999 : bDomainPriority;
      if (aDom !== bDom) return aDom - bDom;

      // 2. Urgency
      const aUrgency = urgencyRank[a.goal.urgency] ?? 99;
      const bUrgency = urgencyRank[b.goal.urgency] ?? 99;
      if (aUrgency !== bUrgency) return aUrgency - bUrgency;

      // 3. Fewer conflicts = higher priority
      const aConflicts = a.summary.activeConflicts;
      const bConflicts = b.summary.activeConflicts;
      if (aConflicts !== bConflicts) return aConflicts - bConflicts;

      // 4. Most recently updated
      return b.goal.updatedAt.getTime() - a.goal.updatedAt.getTime();
    });
  }

  private conflictsWithSelectedGoals(
    goalId: string,
    conflicts: Conflict[],
    selectedGoals: WorkSessionGoal[],
  ): boolean {
    const selectedGoalIds = new Set(
      selectedGoals.map((sg) => sg.goal.id),
    );

    return conflicts.some(
      (c) =>
        c.status === ConflictStatus.Active &&
        c.goalIds.some((id) => id !== goalId && selectedGoalIds.has(id)),
    );
  }

  private createResolution(conflict: Conflict): ConflictResolution {
    const isCritical = conflict.severity === ConflictSeverity.Critical;
    const isHigh = conflict.severity === ConflictSeverity.High;

    const action: "block" | "warn" = isCritical ? "block" : isHigh ? "warn" : "warn";

    let recommendation = "";
    switch (conflict.type) {
      case "duplicate":
        recommendation =
          "Merge these duplicate goals into a single goal, or cancel one of them.";
        break;
      case "contradiction":
        recommendation =
          "These goals directly contradict each other. Decide which goal to pursue and cancel or pause the other.";
        break;
      case "competing_resource":
        recommendation =
          "These goals compete for the same resource. Stagger their timelines or allocate additional resources.";
        break;
      case "tension":
        recommendation =
          "These goals create tension (e.g., time/energy trade-offs). Consider sequencing them or adjusting scope.";
        break;
      default:
        recommendation = "Review this conflict and decide how to proceed.";
    }

    return {
      conflictId: conflict.id,
      conflictType: conflict.type,
      description: conflict.description,
      affectedGoalIds: conflict.goalIds,
      recommendation,
      action,
    };
  }

  private generateSessionNotes(
    selected: WorkSessionGoal[],
    deferred: WorkSessionGoal[],
    resolutions: ConflictResolution[],
    isBlocked: boolean,
  ): string {
    const lines: string[] = [];

    if (isBlocked) {
      lines.push("⚠️ SESSION BLOCKED: All goals have unresolved blocking conflicts.");
      lines.push("Resolve the blocking conflicts below before planning work.");
      return lines.join(" ");
    }

    lines.push(`Selected ${selected.length} goal(s) for this session.`);

    const blockingCount = resolutions.filter((r) => r.action === "block").length;
    const warningCount = resolutions.filter((r) => r.action === "warn").length;

    if (blockingCount > 0) {
      lines.push(`${blockingCount} blocking conflict(s) need resolution.`);
    }
    if (warningCount > 0) {
      lines.push(`${warningCount} warning-level conflict(s) to be aware of.`);
    }
    if (deferred.length > 0) {
      lines.push(`${deferred.length} goal(s) deferred.`);
    }

    if (selected.length === 0 && !isBlocked) {
      lines.push("No goals selected — check goal statuses and conflict reports.");
    }

    return lines.join(" ");
  }
}
