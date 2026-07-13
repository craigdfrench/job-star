/**
 * Goal Store — Persistence and retrieval for goals with integrated
 * conflict report storage.
 *
 * When goals are added or updated, the store automatically runs conflict
 * detection and stores the resulting conflict reports alongside the goals.
 * This keeps conflict data in sync without requiring callers to manage it
 * separately.
 *
 * The store uses an in-memory Map by default but is designed to be
 * swapped with a persistent backend (SQLite, Postgres, etc.) by
 * implementing the GoalStoreBackend interface.
 */

import {
  ConflictEngine,
  type ConflictReport,
  type ConflictReportSummary,
  ConflictSeverity,
  ConflictStatus,
  type Conflict,
} from "../conflicts";
import type { GoalContext } from "../conflict/types";

// ─── Types ───────────────────────────────────────────────────────────

export interface Goal {
  id: string;
  title: string;
  description: string;
  domain: string;
  urgency: "low" | "medium" | "high" | "critical" | "idle-opportunistic";
  status: "active" | "paused" | "completed" | "cancelled";
  createdAt: Date;
  updatedAt: Date;
  metadata?: Record<string, unknown>;
}

export interface GoalStoreBackend {
  getGoal(id: string): Promise<Goal | null>;
  getAllGoals(): Promise<Goal[]>;
  saveGoal(goal: Goal): Promise<void>;
  deleteGoal(id: string): Promise<void>;
  getConflictReport(goalId: string): Promise<ConflictReport | null>;
  saveConflictReport(goalId: string, report: ConflictReport): Promise<void>;
  getAllConflictReports(): Promise<Map<string, ConflictReport>>;
}

// ─── In-Memory Backend ───────────────────────────────────────────────

export class InMemoryGoalStoreBackend implements GoalStoreBackend {
  private goals = new Map<string, Goal>();
  private conflictReports = new Map<string, ConflictReport>();

  async getGoal(id: string): Promise<Goal | null> {
    return this.goals.get(id) ?? null;
  }

  async getAllGoals(): Promise<Goal[]> {
    return Array.from(this.goals.values());
  }

  async saveGoal(goal: Goal): Promise<void> {
    this.goals.set(goal.id, goal);
  }

  async deleteGoal(id: string): Promise<void> {
    this.goals.delete(id);
    this.conflictReports.delete(id);
  }

  async getConflictReport(goalId: string): Promise<ConflictReport | null> {
    return this.conflictReports.get(goalId) ?? null;
  }

  async saveConflictReport(goalId: string, report: ConflictReport): Promise<void> {
    this.conflictReports.set(goalId, report);
  }

  async getAllConflictReports(): Promise<Map<string, ConflictReport>> {
    return new Map(this.conflictReports);
  }
}

// ─── Goal Store ──────────────────────────────────────────────────────

export class GoalStore {
  private backend: GoalStoreBackend;
  private conflictEngine: ConflictEngine;
  private conflictCache = new Map<string, ConflictReport>();
  private cacheInvalidated = true;

  constructor(
    backend?: GoalStoreBackend,
    conflictEngine?: ConflictEngine,
  ) {
    this.backend = backend ?? new InMemoryGoalStoreBackend();
    this.conflictEngine = conflictEngine ?? new ConflictEngine();
  }

  // ─── Goal CRUD ─────────────────────────────────────────────────────

  /**
   * Add a new goal. Runs conflict detection against all existing goals
   * and stores the resulting conflict report for this goal.
   */
  async addGoal(
    goalData: Omit<Goal, "id" | "createdAt" | "updatedAt">,
  ): Promise<Goal> {
    const goal: Goal = {
      ...goalData,
      id: this.generateId(),
      createdAt: new Date(),
      updatedAt: new Date(),
    };

    await this.backend.saveGoal(goal);
    await this.refreshConflictsForGoal(goal);

    return goal;
  }

  /**
   * Update an existing goal. Re-runs conflict detection for the updated
   * goal and any goals it may now conflict with.
   */
  async updateGoal(
    id: string,
    updates: Partial<Omit<Goal, "id" | "createdAt">>,
  ): Promise<Goal | null> {
    const existing = await this.backend.getGoal(id);
    if (!existing) return null;

    const updated: Goal = {
      ...existing,
      ...updates,
      id: existing.id,
      createdAt: existing.createdAt,
      updatedAt: new Date(),
    };

    await this.backend.saveGoal(updated);
    await this.refreshConflictsForGoal(updated);

    // Also refresh conflicts for any goals that share conflicts with this one
    await this.refreshRelatedConflicts(id);

    return updated;
  }

  /**
   * Remove a goal and its associated conflict reports.
   */
  async deleteGoal(id: string): Promise<void> {
    await this.backend.deleteGoal(id);
    this.conflictCache.delete(id);
    this.cacheInvalidated = true;

    // Re-run detection for remaining goals since conflicts may have resolved
    await this.refreshAllConflicts();
  }

  async getGoal(id: string): Promise<Goal | null> {
    return this.backend.getGoal(id);
  }

  async getAllGoals(): Promise<Goal[]> {
    return this.backend.getAllGoals();
  }

  async getActiveGoals(): Promise<Goal[]> {
    const all = await this.backend.getAllGoals();
    return all.filter((g) => g.status === "active");
  }

  // ─── Conflict Report Storage ───────────────────────────────────────

  /**
   * Get the conflict report for a specific goal.
   */
  async getConflictReport(goalId: string): Promise<ConflictReport | null> {
    if (this.conflictCache.has(goalId)) {
      return this.conflictCache.get(goalId)!;
    }
    const report = await this.backend.getConflictReport(goalId);
    if (report) {
      this.conflictCache.set(goalId, report);
    }
    return report;
  }

  /**
   * Get all active conflict reports across all goals.
   */
  async getAllConflictReports(): Promise<Map<string, ConflictReport>> {
    if (!this.cacheInvalidated && this.conflictCache.size > 0) {
      return this.conflictCache;
    }
    const reports = await this.backend.getAllConflictReports();
    this.conflictCache = reports;
    this.cacheInvalidated = false;
    return reports;
  }

  /**
   * Get a summary of conflicts for a goal — useful for quick checks.
   */
  async getConflictSummary(goalId: string): Promise<ConflictReportSummary> {
    const report = await this.getConflictReport(goalId);
    if (!report) {
      return {
        goalId,
        totalConflicts: 0,
        activeConflicts: 0,
        highestSeverity: null,
        conflicts: [],
      };
    }

    const activeConflicts = report.conflicts.filter(
      (c) => c.status === ConflictStatus.Active,
    );

    const highestSeverity = activeConflicts.reduce<ConflictSeverity | null>(
      (highest, c) => {
        if (!highest) return c.severity;
        return c.severity > highest ? c.severity : highest;
      },
      null,
    );

    return {
      goalId,
      totalConflicts: report.conflicts.length,
      activeConflicts: activeConflicts.length,
      highestSeverity,
      conflicts: report.conflicts,
    };
  }

  /**
   * Get all goals that currently have active blocking conflicts
   * (severity high or critical).
   */
  async getGoalsWithBlockingConflicts(): Promise<
    Array<{ goal: Goal; conflicts: Conflict[] }>
  > {
    const reports = await this.getAllConflictReports();
    const allGoals = await this.backend.getAllGoals();
    const goalMap = new Map(allGoals.map((g) => [g.id, g]));

    const result: Array<{ goal: Goal; conflicts: Conflict[] }> = [];

    for (const [goalId, report] of reports) {
      const goal = goalMap.get(goalId);
      if (!goal) continue;

      const blocking = report.conflicts.filter(
        (c) =>
          c.status === ConflictStatus.Active &&
          (c.severity === ConflictSeverity.Critical ||
            c.severity === ConflictSeverity.High),
      );

      if (blocking.length > 0) {
        result.push({ goal, conflicts: blocking });
      }
    }

    return result;
  }

  // ─── Internal Conflict Refresh ─────────────────────────────────────

  /**
   * Run conflict detection for a single goal against all others,
   * then persist the result.
   */
  private async refreshConflictsForGoal(goal: Goal): Promise<void> {
    const allGoals = await this.backend.getAllGoals();
    const others = allGoals.filter((g) => g.id !== goal.id);

    const goalContexts: GoalContext[] = allGoals.map((g) => ({
      id: g.id,
      title: g.title,
      description: g.description,
      domain: g.domain,
      urgency: g.urgency,
      status: g.status,
      metadata: g.metadata,
    }));

    const targetContext: GoalContext = {
      id: goal.id,
      title: goal.title,
      description: goal.description,
      domain: goal.domain,
      urgency: goal.urgency,
      status: goal.status,
      metadata: goal.metadata,
    };

    const report = this.conflictEngine.detectForGoal(targetContext, goalContexts.filter((g) => g.id !== goal.id));

    await this.backend.saveConflictReport(goal.id, report);
    this.conflictCache.set(goal.id, report);
    this.cacheInvalidated = true;
  }

  /**
   * Refresh conflicts for goals that are related to the given goal
   * (i.e., share a conflict with it).
   */
  private async refreshRelatedConflicts(goalId: string): Promise<void> {
    const report = await this.getConflictReport(goalId);
    if (!report) return;

    const relatedGoalIds = new Set<string>();
    for (const conflict of report.conflicts) {
      for (const id of conflict.goalIds) {
        if (id !== goalId) relatedGoalIds.add(id);
      }
    }

    const allGoals = await this.backend.getAllGoals();
    for (const relatedId of relatedGoalIds) {
      const relatedGoal = allGoals.find((g) => g.id === relatedId);
      if (relatedGoal) {
        await this.refreshConflictsForGoal(relatedGoal);
      }
    }
  }

  /**
   * Re-run conflict detection across all goals. Used after deletions
   * or when the cache is stale.
   */
  private async refreshAllConflicts(): Promise<void> {
    const allGoals = await this.backend.getAllGoals();

    const goalContexts: GoalContext[] = allGoals.map((g) => ({
      id: g.id,
      title: g.title,
      description: g.description,
      domain: g.domain,
      urgency: g.urgency,
      status: g.status,
      metadata: g.metadata,
    }));

    const result = this.conflictEngine.detect(goalContexts);

    // Store per-goal reports
    for (const goal of allGoals) {
      const goalConflicts = result.report.conflicts.filter((c) =>
        c.goalIds.includes(goal.id),
      );

      const perGoalReport: ConflictReport = {
        ...result.report,
        conflicts: goalConflicts,
      };

      await this.backend.saveConflictReport(goal.id, perGoalReport);
      this.conflictCache.set(goal.id, perGoalReport);
    }

    this.cacheInvalidated = false;
  }

  // ─── Utilities ─────────────────────────────────────────────────────

  private generateId(): string {
    return `goal_${Date.now()}_${Math.random().toString(36).slice(2, 9)}`;
  }
}
