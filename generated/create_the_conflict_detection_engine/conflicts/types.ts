/**
 * src/conflicts/constants.ts
 *
 * Canonical constants for the Job-Star conflict detection engine.
 * These values define severity levels, confidence thresholds, supported
 * domains, and conflict type identifiers. They are the single source of
 * truth for tuning detection behavior.
 */

// ---------------------------------------------------------------------------
// Conflict Types
// ---------------------------------------------------------------------------

/**
 * The four canonical conflict types the engine detects.
 *
 * - DUPLICATE:          Two goals describe the same underlying intent.
 * - CONTRADICTION:      Two goals cannot both be achieved simultaneously.
 * - COMPETING_RESOURCE: Two goals draw on the same finite resource.
 * - TENSION:            Two goals create friction without being strictly
 *                       contradictory (e.g. value misalignment, timing).
 */
export const CONFLICT_TYPE = {
  DUPLICATE: "duplicate",
  CONTRADICTION: "contradiction",
  COMPETING_RESOURCE: "competing_resource",
  TENSION: "tension",
} as const;

export type ConflictTypeId = typeof CONFLICT_TYPE[keyof typeof CONFLICT_TYPE];

// ---------------------------------------------------------------------------
// Severity Scale
// ---------------------------------------------------------------------------

/**
 * Severity is an integer 0–4 representing the impact of a conflict on
 * goal achievement.
 *
 * 0 = INFO      — No action needed; logged for awareness.
 * 1 = LOW       — Minor friction; user may want to review.
 * 2 = MEDIUM    — Meaningful impact; recommend mitigation.
 * 3 = HIGH      — Significant impact; mitigation strongly advised.
 * 4 = CRITICAL  — Goals are mutually exclusive; one must change.
 */
export const SEVERITY = {
  INFO: 0,
  LOW: 1,
  MEDIUM: 2,
  HIGH: 3,
  CRITICAL: 4,
} as const;

export type SeverityLevel = (typeof SEVERITY)[keyof typeof SEVERITY];

/** Human-readable labels for each severity level. */
export const SEVERITY_LABELS: Record<SeverityLevel, string> = {
  [SEVERITY.INFO]: "info",
  [SEVERITY.LOW]: "low",
  [SEVERITY.MEDIUM]: "medium",
  [SEVERITY.HIGH]: "high",
  [SEVERITY.CRITICAL]: "critical",
};

/** Inverse lookup: label -> numeric severity. */
export const SEVERITY_BY_LABEL: Record<string, SeverityLevel> = Object.entries(
  SEVERITY_LABELS,
).reduce(
  (acc, [num, label]) => {
    acc[label] = Number(num) as SeverityLevel;
    return acc;
  },
  {} as Record<string, SeverityLevel>,
);

// ---------------------------------------------------------------------------
// Confidence Thresholds
// ---------------------------------------------------------------------------

/**
 * Confidence is a float in [0.0, 1.0] representing how certain the
 * detector is that a real conflict exists.
 *
 * Thresholds control how conflicts are surfaced:
 *
 * - AUTO_FILE:   Confidence >= this → automatically recorded without
 *                human review.
 * - REVIEW:      Confidence >= this but < AUTO_FILE → surfaced for
 *                human review before filing.
 * - LOG_ONLY:    Confidence >= this but < REVIEW → logged for analytics
 *                but not surfaced.
 * - DISCARD:     Confidence < this → discarded entirely.
 */
export const CONFIDENCE_THRESHOLDS = {
  AUTO_FILE: 0.85,
  REVIEW: 0.6,
  LOG_ONLY: 0.35,
  DISCARD: 0.0,
} as const;

export type ConfidenceThresholdKey =
  | "AUTO_FILE"
  | "REVIEW"
  | "LOG_ONLY"
  | "DISCARD";

// ---------------------------------------------------------------------------
// Domains
// ---------------------------------------------------------------------------

/**
 * The set of domains Job-Star tracks. Cross-domain conflict detection
 * compares goals across these domains.
 */
export const DOMAINS = [
  "meta",      // Job-Star system operations and self-improvement
  "work",      // Professional / career goals
  "personal",  // Personal life, health, relationships
  "learning",  // Skill acquisition and education
  "financial", // Money, budgeting, investments
  "creative",  // Art, hobbies, side projects
  "health",    // Physical and mental wellbeing
  "social",    // Community, networking, family
] as const;

export type Domain = (typeof DOMAINS)[number];

// ---------------------------------------------------------------------------
// Conflict Status Lifecycle
// ---------------------------------------------------------------------------

/**
 * The lifecycle states a conflict can move through after detection.
 */
export const CONFLICT_STATUS = {
  DETECTED: "detected",       // Just found, not yet reviewed
  CONFIRMED: "confirmed",     // Reviewed and accepted as real
  DISMISSED: "dismissed",     // Reviewed and rejected
  MITIGATED: "mitigated",     // A mitigation action was taken
  RESOLVED: "resolved",       // The conflict no longer applies
} as const;

export type ConflictStatus =
  (typeof CONFLICT_STATUS)[keyof typeof CONFLICT_STATUS];

// ---------------------------------------------------------------------------
// Detector Source Identifiers
// ---------------------------------------------------------------------------

/**
 * Identifies which detection strategy produced a conflict.
 * Useful for analytics and tuning individual detectors.
 */
export const DETECTOR_SOURCE = {
  DUPLICATE_EXACT: "duplicate:exact",
  DUPLICATE_SEMANTIC: "duplicate:semantic",
  CONTRADICTION_KEYWORD: "contradiction:keyword",
  CONTRADICTION_SEMANTIC: "contradiction:semantic",
  RESOURCE_BUDGET: "resource:budget",
  RESOURCE_CALENDAR: "resource:calendar",
  RESOURCE_ATTENTION: "resource:attention",
  TENSION_VALUE: "tension:value",
  TENSION_TIMING: "tension:timing",
  TENSION_CROSS_DOMAIN: "tension:cross_domain",
} as const;

export type DetectorSource = (typeof DETECTOR_SOURCE)[keyof typeof DETECTOR_SOURCE];


// --- DUPLICATE BLOCK ---

/**
 * src/conflicts/types.ts
 *
 * Formal type schema for the Job-Star conflict detection engine.
 *
 * This file is the data contract. Every detector, reporter, storage
 * adapter, and UI consumer imports from here. Do not add runtime
 * logic to this file — it is types only.
 */

import type {
  ConflictTypeId,
  SeverityLevel,
  Domain,
  ConflictStatus,
  DetectorSource,
} from "./constants";

// ---------------------------------------------------------------------------
// Core Primitives
// ---------------------------------------------------------------------------

/**
 * A unique identifier for a conflict. Format: `conf_<ulid>`.
 */
export type ConflictId = string;

/**
 * A reference to a goal. At minimum we need its ID; we include the
 * domain so cross-domain logic can operate without re-fetching.
 */
export interface GoalRef {
  goalId: string;
  domain: Domain;
  /** Optional human-readable title for logging / display. */
  title?: string;
}

/**
 * A finite resource that two or more goals compete for.
 */
export interface CompetingResource {
  /** What the resource is: "time", "money", "attention", "energy", etc. */
  kind: string;
  /** Total available budget, if quantifiable. */
  totalBudget?: number;
  /** Unit label, e.g. "hours/week", "USD/month". */
  unit?: string;
  /** How much goal A demands. */
  demandA: number;
  /** How much goal B demands. */
  demandB: number;
}

// ---------------------------------------------------------------------------
// Conflict Detail Payloads (one per conflict type)
// ---------------------------------------------------------------------------

/**
 * Details specific to a DUPLICATE conflict.
 */
export interface DuplicateDetail {
  /** "exact" for string/keyword match, "semantic" for embedding similarity. */
  matchType: "exact" | "semantic";
  /** Similarity score in [0,1] if semantic. */
  similarity?: number;
  /** Which goal is recommended to be kept (the "canonical" one). */
  canonicalGoalId?: string;
}

/**
 * Details specific to a CONTRADICTION conflict.
 */
export interface ContradictionDetail {
  /** Why the two goals contradict — a short explanation. */
  reason: string;
  /** Keywords or phrases that signaled the contradiction. */
  triggerPhrases?: string[];
  /** Whether the contradiction is absolute (cannot both succeed) or
   *  partial (success of one degrades the other). */
  mode: "absolute" | "partial";
}

/**
 * Details specific to a COMPETING_RESOURCE conflict.
 */
export interface CompetingResourceDetail {
  resource: CompetingResource;
  /** Combined demand vs. available budget, if quantifiable. */
  overflow?: number;
  /** Percentage of budget consumed by the two goals together. */
  utilizationPct?: number;
}

/**
 * Details specific to a TENSION conflict.
 */
export interface TensionDetail {
  /** The nature of the tension. */
  tensionType: "value" | "timing" | "priority" | "emotional" | "cross_domain";
  /** Human-readable explanation of the friction. */
  description: string;
  /** Domains involved, relevant for cross_domain tension. */
  domains?: [Domain, Domain];
}

/**
 * Discriminated union of all detail payloads.
 */
export type ConflictDetail =
  | DuplicateDetail
  | ContradictionDetail
  | CompetingResourceDetail
  | TensionDetail;

// ---------------------------------------------------------------------------
// The Conflict Object
// ---------------------------------------------------------------------------

/**
 * A single detected conflict between two goals.
 *
 * This is the central data structure of the engine. It is produced by
 * detectors and consumed by reporters, storage, and the UI.
 */
export interface Conflict {
  /** Unique identifier for this conflict. */
  id: ConflictId;

  /** Which of the four conflict types this is. */
  type: ConflictTypeId;

  /** The two goals in conflict. `goalA` is the newer / triggering goal. */
  goalA: GoalRef;
  goalB: GoalRef;

  /** Numeric severity 0–4 (see SEVERITY constant). */
  severity: SeverityLevel;

  /** Detector confidence in [0,1] that this is a real conflict. */
  confidence: number;

  /** Which detector strategy produced this conflict. */
  source: DetectorSource;

  /** Type-specific details. */
  detail: ConflictDetail;

  /** Lifecycle status. */
  status: ConflictStatus;

  /** ISO-8601 timestamp of detection. */
  detectedAt: string;

  /** ISO-8601 timestamp of last status change. */
  updatedAt: string;

  /** Optional human-readable summary for display. */
  summary?: string;

  /** Optional mitigation suggestion. */
  suggestedAction?: string;
}

// ---------------------------------------------------------------------------
// Conflict Report (aggregate)
// ---------------------------------------------------------------------------

/**
 * A batch report of all conflicts detected for a goal (or set of goals)
 * during a single detection pass.
 */
export interface ConflictReport {
  /** The goal that triggered this detection pass. */
  triggeringGoalId: string;

  /** All conflicts found, sorted by severity descending then confidence. */
  conflicts: Conflict[];

  /** Count of conflicts by type. */
  counts: Record<ConflictTypeId, number>;

  /** Highest severity among all conflicts, or SEVERITY.INFO if none. */
  maxSeverity: SeverityLevel;

  /** Average confidence across all conflicts, or 0 if none. */
  averageConfidence: number;

  /** ISO-8601 timestamp the report was generated. */
  generatedAt: string;

  /** Detector version / commit hash for reproducibility. */
  detectorVersion: string;
}

// ---------------------------------------------------------------------------
// Detection Request (input to the engine)
// ---------------------------------------------------------------------------

/**
 * Input passed to the conflict detection engine.
 */
export interface DetectionRequest {
  /** The goal to check against existing goals. */
  goal: GoalRef;

  /** Full text / description of the goal for semantic analysis. */
  goalText: string;

  /** Resources this goal claims, if known. */
  claimedResources?: CompetingResource[];

  /** Which conflict types to check. Defaults to all four. */
  enabledDetectors?: ConflictTypeId[];

  /** Domains to search across. Defaults to all domains. */
  searchDomains?: Domain[];
}

// ---------------------------------------------------------------------------
// Type Guards
// ---------------------------------------------------------------------------

export function isDuplicateDetail(
  d: ConflictDetail,
): d is DuplicateDetail {
  return (d as DuplicateDetail).matchType !== undefined;
}

export function isContradictionDetail(
  d: ConflictDetail,
): d is ContradictionDetail {
  return (d as ContradictionDetail).mode !== undefined;
}

export function isCompetingResourceDetail(
  d: ConflictDetail,
): d is CompetingResourceDetail {
  return (d as CompetingResourceDetail).resource !== undefined;
}

export function isTensionDetail(
  d: ConflictDetail,
): d is TensionDetail {
  return (d as TensionDetail).tensionType !== undefined;
}


// --- DUPLICATE BLOCK ---

// src/conflicts/__tests__/jest.config.json
{
  "displayName": "conflict-engine",
  "testEnvironment": "node",
  "testMatch": ["**/__tests__/**/*.test.ts"],
  "transform": {
    "^.+\\.ts$": "ts-jest"
  },
  "moduleFileExtensions": ["ts", "js", "json"]
}


// --- DUPLICATE BLOCK ---

// src/conflicts/types.ts
/**
 * Core type definitions for the conflict detection engine.
 * These types define the contracts that all conflict detectors must follow.
 */

/**
 * Represents a goal in the Job-Star system.
 */
export interface Goal {
  id: string;
  title: string;
  description: string;
  domain: string;
  urgency?: string;
  status?: string;
  metadata?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
}

/**
 * The severity level of a detected conflict.
 */
export enum ConflictSeverity {
  LOW = 'low',
  MEDIUM = 'medium',
  HIGH = 'high',
  CRITICAL = 'critical',
}

/**
 * The type of conflict detected.
 */
export enum ConflictType {
  DUPLICATE = 'duplicate',
  CONTRADICTION = 'contradiction',
  COMPETING_RESOURCE = 'competing_resource',
  TENSION = 'tension',
}

/**
 * The result of a conflict detection analysis between two goals.
 */
export interface ConflictResult {
  /** The type of conflict detected. */
  type: ConflictType;
  /** The IDs of the two goals in conflict. */
  goal_ids: [string, string];
  /** A human-readable explanation of why these goals conflict. */
  reasoning: string;
  /** Confidence score from 0.0 to 1.0. */
  confidence: number;
  /** Severity of the conflict. */
  severity: ConflictSeverity;
  /** Additional metadata about the detection. */
  metadata?: {
    /** The model used for detection. */
    model?: string;
    /** Raw LLM response for auditability. */
    raw_response?: string;
    /** Timestamp of detection. */
    detected_at?: string;
    [key: string]: unknown;
  };
}

/**
 * Interface that all conflict detectors must implement.
 * Each detector specializes in one type of conflict.
 */
export interface ConflictDetector {
  /** The type of conflict this detector handles. */
  readonly conflictType: ConflictType;

  /**
   * Analyze two goals for a specific type of conflict.
   * Returns a ConflictResult if a conflict is detected, or null if not.
   *
   * @param goalA - The first goal to analyze.
   * @param goalB - The second goal to analyze.
   * @returns A ConflictResult if conflict detected, null otherwise.
   */
  detect(goalA: Goal, goalB: Goal): Promise<ConflictResult | null>;
}

/**
 * Configuration for LLM-based detectors.
 */
export interface LLMDetectorConfig {
  /** The LLM client to use for analysis. */
  llmClient: LLMClient;
  /** Minimum confidence threshold to report a conflict (default: 0.5). */
  confidenceThreshold?: number;
  /** Model identifier to use for analysis. */
  model?: string;
  /** Temperature for LLM calls (default: 0.1 for deterministic output). */
  temperature?: number;
}

/**
 * Interface for LLM clients used by conflict detectors.
 * This abstracts the specific LLM provider (OpenAI, Anthropic, local, etc.)
 */
export interface LLMClient {
  /**
   * Send a prompt to the LLM and get a response.
   *
   * @param prompt - The prompt string to send.
   * @param options - Optional configuration (model, temperature, etc.)
   * @returns The LLM's response text.
   */
  complete(prompt: string, options?: {
    model?: string;
    temperature?: number;
    maxTokens?: number;
  }): Promise<string>;
}


// --- DUPLICATE BLOCK ---

/**
 * Conflict Detection — Shared Types
 *
 * These types are used across all detectors, the aggregator, and the report
 * formatter.
 */

// ---------------------------------------------------------------------------
// Conflict types
// ---------------------------------------------------------------------------

export type ConflictType =
  | 'duplicate'
  | 'contradiction'
  | 'competing_resource'
  | 'tension';

export const ALL_CONFLICT_TYPES: ConflictType[] = [
  'duplicate',
  'contradiction',
  'competing_resource',
  'tension',
];

// ---------------------------------------------------------------------------
// Severity
// ---------------------------------------------------------------------------

export type ConflictSeverity = 'critical' | 'high' | 'medium' | 'low' | 'info';

export const ALL_SEVERITIES: ConflictSeverity[] = [
  'critical',
  'high',
  'medium',
  'low',
  'info',
];

// ---------------------------------------------------------------------------
// Conflict Finding — output of a single detector
// ---------------------------------------------------------------------------

export interface ConflictFinding {
  /** Unique identifier for this finding (e.g. UUID or deterministic hash). */
  id: string;

  /** Goal IDs involved in this conflict (usually 2, but can be more). */
  goalIds: string[];

  /** One or more conflict types detected for this goal set. */
  types: ConflictType[];

  /** Severity level. */
  severity: ConflictSeverity;

  /** Confidence score 0–1. */
  confidence: number;

  /** Human-readable explanation of the conflict. */
  description: string;

  /** Names of the detector(s) that produced this finding. */
  detectors: string[];

  /** ISO timestamp when the finding was generated. */
  detectedAt: string;

  /** Optional actionable suggestions for resolving the conflict. */
  suggestions?: string[];

  /** Optional detector-specific metadata. */
  metadata?: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Conflict Report — aggregated output
// ---------------------------------------------------------------------------

export interface ConflictReportSummary {
  bySeverity: Record<ConflictSeverity, number>;
  byType: Record<ConflictType, number>;
  maxSeverity: ConflictSeverity;
  detectorCount: number;
}

export interface ConflictReport {
  /** ISO timestamp when the report was generated. */
  generatedAt: string;

  /** Total number of findings in this report. */
  totalFindings: number;

  /** Summary statistics. */
  summary: ConflictReportSummary;

  /** The findings, sorted by severity (desc) then confidence (desc). */
  findings: ConflictFinding[];
}

// ---------------------------------------------------------------------------
// Detector interface
// ---------------------------------------------------------------------------

export interface ConflictDetector {
  /** Detector name (used in finding.detectors). */
  name: string;

  /** Conflict types this detector can produce. */
  types: ConflictType[];

  /**
   * Run the detector against a set of goals.
   * Returns an array of findings.
   */
  detect(goals: Goal[]): Promise<ConflictFinding[]>;
}

// ---------------------------------------------------------------------------
// Goal (minimal representation for conflict detection)
// ---------------------------------------------------------------------------

export interface Goal {
  id: string;
  title: string;
  description?: string;
  domain?: string;
  priority?: string;
  status?: string;
  resources?: string[];
  tags?: string[];
  createdAt?: string;
  updatedAt?: string;
  [key: string]: unknown;
}
