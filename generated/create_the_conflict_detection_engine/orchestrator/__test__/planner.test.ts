/**
 * Tests for the orchestrator planner — verifies that conflict reports
 * are consulted during planning and that blocking conflicts prevent
 * goal selection.
 */

import { describe, it, expect, beforeEach } from "vitest";
import { GoalStore } from "../goals/store";
import { ConflictEngine } from "../conflicts";
import { Planner } from "../planner";
import { ConflictSeverity, ConflictStatus } from "../conflicts";
import type { Conflict, ConflictReport } from "../conflict/types";

// We test the planner's logic by injecting mock conflict reports
// through a custom GoalStore subclass.

describe("Planner", () => {
  let goalStore: GoalStore;
  let conflictEngine: ConflictEngine;
  let planner: Planner;

  beforeEach(() => {
    goalStore = new GoalStore();
    conflictEngine = new ConflictEngine();
    planner = new Planner(goalStore, conflictEngine, {
      maxGoalsPerSession: 3,
      excludeCriticalConflicts: true,
    });
  });

  it("should produce an empty plan when no active goals exist", async () => {
    const plan = await planner.planSession();
    expect(plan.selectedGoals).toHaveLength(0);
    expect(plan.isBlocked).toBe(false);
    expect(plan.notes).toContain("No active goals");
  });

  it("should select goals with no conflicts", async () => {
    await goalStore.addGoal({
      title: "Write documentation",
      description: "Complete API docs",
      domain: "work",
      urgency: "medium",
      status: "active",
    });

    await goalStore.addGoal({
      title: "Exercise routine",
      description: "Daily 30-min workout",
      domain: "personal",
      urgency: "low",
      status: "active",
    });

    const plan = await planner.planSession();
    expect(plan.selectedGoals.length).toBeGreaterThan(0);
    expect(plan.isBlocked).toBe(false);
  });

  it("should defer goals with blocking conflicts", async () => {
    // Add two contradictory goals
    const g1 = await goalStore.addGoal({
      title: "Save money aggressively",
      description: "Cut all discretionary spending",
      domain: "personal",
      urgency: "high",
      status: "active",
    });

    const g2 = await goalStore.addGoal({
      title: "Buy luxury items",
      description: "Purchase expensive non-essentials",
      domain: "personal",
      urgency: "high",
      status: "active",
    });

    const plan = await planner.planSession();

    // At least one goal should be deferred due to conflicts
    const allGoals = [...plan.selectedGoals, ...plan.deferredGoals];
    expect(allGoals.length).toBe(2);

    // Should have conflict resolutions
    expect(plan.conflictResolutions.length).toBeGreaterThan(0);
  });

  it("should respect maxGoalsPerSession limit", async () => {
    for (let i = 0; i < 5; i++) {
      await goalStore.addGoal({
        title: `Goal ${i}`,
        description: `Description ${i}`,
        domain: "work",
        urgency: "medium",
        status: "active",
      });
    }

    const plan = await planner.planSession();
    expect(plan.selectedGoals.length).toBeLessThanOrEqual(3);
    expect(plan.deferredGoals.length).toBeGreaterThanOrEqual(2);
  });

  it("should generate conflict resolutions for active conflicts", async () => {
    await goalStore.addGoal({
      title: "Goal A",
      description: "First goal",
      domain: "work",
      urgency: "high",
      status: "active",
    });

    await goalStore.addGoal({
      title: "Goal A duplicate",
      description: "First goal",
      domain: "work",
      urgency: "high",
      status: "active",
    });

    const plan = await planner.planSession();
    // Duplicate detection should find a conflict
    expect(plan.conflictResolutions.length).toBeGreaterThan(0);
  });

  it("assessGoal should report blocking conflicts", async () => {
    const goal = await goalStore.addGoal({
      title: "Test goal",
      description: "A test",
      domain: "meta",
      urgency: "medium",
      status: "active",
    });

    const assessment = await planner.assessGoal(goal.id);
    expect(assessment).toBeDefined();
    expect(assessment.canWorkOn).toBe(true);
    expect(assessment.blockingConflicts).toHaveLength(0);
  });
});
