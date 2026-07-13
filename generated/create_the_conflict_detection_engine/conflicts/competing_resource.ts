/**
 * Competing Resource Detector
 *
 * Detects goals that draw from the same limited resource pool. For each pair
 * of goals, the detector:
 *  1. Parses resource references from both goals using ResourceParser.
 *  2. Identifies shared resources (same resourceId, or same category + label).
 *  3. Sums the estimated demand from both goals for each shared resource.
 *  4. Compares total demand against the resource's capacity (if known).
 *  5. Computes a severity score based on scarcity and demand/capacity ratio.
 *
 * Severity scale:
 *   0.0 – 0.3  low      (overlap exists but resource is not strained)
 *   0.3 – 0.6  medium   (demand approaches capacity)
 *   0.6 – 1.0  high     (demand exceeds or nearly exceeds capacity)
 */

import {
  ResourceRegistry,
  ResourceEntry,
} from "../resource_registry";
import {
  ResourceParser,
  ParsedResourceReference,
} from "../resource_parser";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface GoalForDetection {
  id: string;
  text: string;
  domain?: string;
}

export interface ResourceConflict {
  /** IDs of the two goals in conflict. */
  goalIds: [string, string];
  /** The shared resource that both goals demand. */
  resourceId?: string;
  resourceLabel: string;
  resourceCategory: string;
  /** Demand from goal A. */
  demandA: number;
  /** Demand from goal B. */
  demandB: number;
  /** Combined demand. */
  totalDemand: number;
  /** Capacity of the resource (if known from registry). */
  capacity?: number;
  unit: string;
  /** Scarcity weight of the resource (0–1). */
  scarcity: number;
  /** Computed severity (0–1). */
  severity: number;
  /** Human-readable explanation. */
  description: string;
}

export interface CompetingResourceDetectorOptions {
  registry?: ResourceRegistry;
  parser?: ResourceParser;
  /** Minimum severity to include in results (default 0.1). */
  minSeverity?: number;
}

// ---------------------------------------------------------------------------
// Detector
// ---------------------------------------------------------------------------

export class CompetingResourceDetector {
  private registry: ResourceRegistry;
  private parser: ResourceParser;
  private minSeverity: number;

  constructor(options: CompetingResourceDetectorOptions = {}) {
    this.registry = options.registry ?? ResourceRegistry.withDefaults();
    this.parser = options.parser ?? new ResourceParser({ registry: this.registry });
    this.minSeverity = options.minSeverity ?? 0.1;
  }

  /**
   * Detect competing-resource conflicts among a set of goals.
   * Compares every pair of goals.
   */
  detect(goals: GoalForDetection[]): ResourceConflict[] {
    // Pre-parse all goals
    const parsed = new Map<string, ParsedResourceReference[]>();
    for (const goal of goals) {
      parsed.set(goal.id, this.parser.parse(goal.text));
    }

    const conflicts: ResourceConflict[] = [];

    for (let i = 0; i < goals.length; i++) {
      for (let j = i + 1; j < goals.length; j++) {
        const a = goals[i];
        const b = goals[j];
        const refsA = parsed.get(a.id) ?? [];
        const refsB = parsed.get(b.id) ?? [];

        const pairConflicts = this.detectPair(a, b, refsA, refsB);
        conflicts.push(...pairConflicts);
      }
    }

    // Filter by minimum severity and sort descending
    return conflicts
      .filter((c) => c.severity >= this.minSeverity)
      .sort((a, b) => b.severity - a.severity);
  }

  /**
   * Detect conflicts between a single pair of goals.
   */
  private detectPair(
    a: GoalForDetection,
    b: GoalForDetection,
    refsA: ParsedResourceReference[],
    refsB: ParsedResourceReference[]
  ): ResourceConflict[] {
    const conflicts: ResourceConflict[] = [];

    for (const refA of refsA) {
      for (const refB of refsB) {
        if (!this.isSameResource(refA, refB)) continue;

        const totalDemand = refA.estimatedDemand + refB.estimatedDemand;
        const entry = refA.resourceId
          ? this.registry.get(refA.resourceId)
          : refB.resourceId
            ? this.registry.get(refB.resourceId)
            : undefined;

        const capacity = entry?.capacity;
        const scarcity = Math.max(refA.scarcity, refB.scarcity);
        const severity = this.computeSeverity(totalDemand, capacity, scarcity);

        conflicts.push({
          goalIds: [a.id, b.id],
          resourceId: entry?.id ?? refA.resourceId ?? refB.resourceId,
          resourceLabel: refA.label,
          resourceCategory: refA.category,
          demandA: refA.estimatedDemand,
          demandB: refB.estimatedDemand,
          totalDemand,
          capacity,
          unit: refA.unit,
          scarcity,
          severity,
          description: this.buildDescription(
            a.id,
            b.id,
            refA.label,
            refA.estimatedDemand,
            refB.estimatedDemand,
            totalDemand,
            capacity,
            refA.unit
          ),
        });
      }
    }

    return conflicts;
  }

  /**
   * Determine whether two parsed references point to the same resource.
   * They match if they share a resourceId, or if they have the same category
   * and the same (normalized) label.
   */
  private isSameResource(
    a: ParsedResourceReference,
    b: ParsedResourceReference
  ): boolean {
    if (a.resourceId && b.resourceId && a.resourceId === b.resourceId) {
      return true;
    }
    // For time/money/attention categories, same category is enough to flag
    // competition (e.g., two goals both needing "time" compete for the
    // same daily/weekly pool).
    if (
      (a.category === "time" && b.category === "time") ||
      (a.category === "money" && b.category === "money") ||
      (a.category === "attention" && b.category === "attention")
    ) {
      return true;
    }
    // For file/person/equipment, match on label as well
    if (
      a.category === b.category &&
      a.label.toLowerCase() === b.label.toLowerCase()
    ) {
      return true;
    }
    return false;
  }

  /**
   * Compute severity from demand, capacity, and scarcity.
   *
   * - If capacity is known: severity = scarcity * min(1, demand / capacity)
   * - If capacity is unknown: severity = scarcity * 0.5 (overlap penalty)
   */
  private computeSeverity(
    totalDemand: number,
    capacity: number | undefined,
    scarcity: number
  ): number {
    if (capacity !== undefined && capacity > 0) {
      const ratio = Math.min(1, totalDemand / capacity);
      return Math.round(scarcity * ratio * 100) / 100;
    }
    // No capacity info — penalize based on scarcity alone
    return Math.round(scarcity * 0.5 * 100) / 100;
  }

  private buildDescription(
    goalAId: string,
    goalBId: string,
    resourceLabel: string,
    demandA: number,
    demandB: number,
    totalDemand: number,
    capacity: number | undefined,
    unit: string
  ): string {
    const capText =
      capacity !== undefined
        ? ` (capacity: ${capacity} ${unit})`
        : "";
    return (
      `Goals "${goalAId}" and "${goalBId}" both demand "${resourceLabel}"` +
      ` — combined demand ${totalDemand} ${unit}${capText}.`
    );
  }
}
