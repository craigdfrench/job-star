// src/conflicts/types.ts

/**
 * Core type definitions for the Conflict Detection Engine.
 * These types define the contract between the engine, its detectors,
 * and the consumers of conflict reports.
 */

/**
 * The domain a goal belongs to. Cross-domain awareness means
 * the engine can detect conflicts between goals in different domains.
 */
export type GoalDomain =
  | 'meta'
  | 'work'
  | 'personal'
  | 'health'
  | 'learning'
  | 'financial'
  | 'social'
  | 'creative'
  | 'other';

/**
 * The lifecycle status of a goal.
 */
export type GoalStatus =
  | 'proposed'
  | 'active'
  | 'paused'
  | 'completed'
  | 'abandoned';

/**
 * A goal in the Job-Star system. This is the input to the conflict engine.
 */
export interface Goal {
  id: string;
  title: string;
  description?: string;
  domain: GoalDomain;
  status: GoalStatus;
  priority?: number;
  tags?: string[];
  resources?: GoalResource[];
  deadline?: string; // ISO 8601
  createdAt?: string;
  updatedAt?: string;
  metadata?: Record<string, unknown>;
}

/**
 * A resource that a goal requires (time, money, attention, etc.)
 */
export interface GoalResource {
  type: string;       // e.g. 'time', 'money', 'attention', 'energy'
  amount?: number;    // quantitative amount if applicable
  unit?: string;      // unit of measurement
  period?: string;    // e.g. 'daily', 'weekly', 'monthly'
}

/**
 * The four conflict types the engine can detect.
 */
export type ConflictType =
  | 'duplicate'
  | 'contradiction'
  | 'resource_competition'
  | 'tension';

/**
 * Severity levels for conflict reports.
 */
export type ConflictSeverity = 'low' | 'medium' | 'high' | 'critical';

/**
 * Confidence score for a detected conflict, from 0.0 to 1.0.
 */
export type Confidence = number;

/**
 * A conflict report produced by a detector.
 */
export interface ConflictReport {
  id: string;
  type: ConflictType;
  severity: ConflictSeverity;
  confidence: Confidence;
  title: string;
  description: string;
  goalIds: string[];           // IDs of goals involved in the conflict
  domainCrossing: boolean;     // true if goals are in different domains
  domains: GoalDomain[];       // domains of the involved goals
  detectedBy: string;          // detector name that produced this report
  detectedAt: string;          // ISO 8601 timestamp
  evidence?: ConflictEvidence; // supporting evidence
  suggestedActions?: string[]; // optional resolution suggestions
  metadata?: Record<string, unknown>;
}

/**
 * Evidence supporting a conflict detection.
 */
export interface ConflictEvidence {
  summary: string;
  details?: Record<string, unknown>;
  matchedPatterns?: string[];
  relevantFields?: string[];
}

/**
 * Result of input validation.
 */
export interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}

/**
 * The aggregated output of the engine for a set of goals.
 */
export interface ConflictEngineResult {
  reports: ConflictReport[];
  summary: ConflictEngineSummary;
  metadata: {
    engineVersion: string;
    detectorsRun: string[];
    processingTimeMs: number;
    inputGoalCount: number;
  };
}

/**
 * Summary statistics of the conflict detection run.
 */
export interface ConflictEngineSummary {
  totalConflicts: number;
  byType: Record<ConflictType, number>;
  bySeverity: Record<ConflictSeverity, number>;
  crossDomainConflicts: number;
  goalsWithConflicts: number;
}


// --- DUPLICATE BLOCK ---

/**
 * Conflict Detection Module — Public API
 */

// Types
export * from './types';

// Aggregator
export {
  ConflictAggregator,
  aggregateConflicts,
  DEFAULT_AGGREGATOR_OPTIONS,
} from './aggregator';
export type { AggregatorOptions, ConflictReportSummary } from './aggregator';

// Report Formatter
export {
  toJSON,
  toJSONObject,
  toMarkdown,
  toPlainText,
  toLogLine,
  findingToLogLine,
  formatReport,
} from './report_formatter';
export type { ReportFormat } from './report_formatter';

// Detectors (re-exported for convenience)
export { DuplicateDetector } from './detectors/duplicate';
export { ContradictionDetector } from './detectors/contradiction';
export { CompetingResourceDetector } from './detectors/competing_resource';


// --- DUPLICATE BLOCK ---

// src/conflicts/index.ts


// --- DUPLICATE BLOCK ---

// src/goals/store.ts


// --- DUPLICATE BLOCK ---

// src/orchestrator/planner.ts


// --- DUPLICATE BLOCK ---

/**
 * Job-Star Conflict Detection — Public API Barrel Export
 *
 * Re-exports the conflict detection engine's public surface so consumers
 * (goal store, orchestrator, etc.) can import from a single entry point:
 *
 *   import { ConflictEngine, ConflictReport, ConflictSeverity } from "@/conflicts";
 *
 * This barrel aggregates the detectors, types, cross-domain layer, and
 * aggregator that were built in previous steps.
 */

// ─── Types ───────────────────────────────────────────────────────────

export {
  ConflictType,
  ConflictSeverity,
  ConflictStatus,
  type Conflict,
  type ConflictReport,
  type ConflictDetectionResult,
  type GoalContext,
  type DomainRelation,
} from "../conflict/types";

// ─── Detectors ───────────────────────────────────────────────────────

export { DuplicateDetector } from "../conflict/detectors/duplicate";
export { ContradictionDetector } from "../conflict/detectors/contradiction";
export { CompetingResourceDetector } from "../conflict/detectors/competing_resource";
export { TensionDetector } from "../conflict/detectors/tension";

// ─── Cross-Domain Awareness ──────────────────────────────────────────

export { CrossDomainAwarenessLayer } from "../conflict/cross_domain";

// ─── Aggregator ──────────────────────────────────────────────────────

export { ConflictReportAggregator } from "../conflict/aggregator";

// ─── Engine Facade ───────────────────────────────────────────────────

export { ConflictEngine } from "./engine";

// ─── Convenience re-exports for common type aliases ──────────────────

export type ConflictReportSummary = {
  goalId: string;
  totalConflicts: number;
  activeConflicts: number;
  highestSeverity: ConflictSeverity | null;
  conflicts: Conflict[];
};


// --- DUPLICATE BLOCK ---

import {
  ConflictEngine,
  ConflictReport,
  ConflictSeverity,
  ConflictStatus,
  ConflictType,
  DuplicateDetector,
  ContradictionDetector,
  CompetingResourceDetector,
  TensionDetector,
  CrossDomainAwarenessLayer,
  ConflictReportAggregator,
} from "@/conflicts";
