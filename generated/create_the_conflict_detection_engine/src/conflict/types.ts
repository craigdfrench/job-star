/**
 * Job-Star Conflict Detection Engine
 * Type definitions and data models
 *
 * This module defines the complete type contract for conflict detection
 * between goals. All detection strategies, storage, and UI depend on these types.
 */

// ============================================================================
// CONFLICT CATEGORIES
// ============================================================================

/**
 * The four fundamental categories of goal conflicts.
 * Each requires a different detection strategy and has different resolution paths.
 */
export enum ConflictCategory {
  /** Two goals describe the same or substantially overlapping outcomes */
  DUPLICATE = 'duplicate',

  /** Two goals have mutually exclusive outcomes - achieving one prevents the other */
  CONTRADICTION = 'contradiction',

  /** Two goals require the same scarce resource (time, money, energy) in overlapping windows */
  COMPETING_RESOURCES = 'competing_resources',

  /** Two goals pull in different directions without strict mutual exclusivity (soft conflict) */
  TENSION = 'tension',
}

// ============================================================================
// SEVERITY
// ============================================================================

/**
 * How significantly a conflict impacts goal achievement.
 * Used for prioritization and UI presentation.
 */
export enum ConflictSeverity {
  /** Goals are nearly identical - merge recommended */
  CRITICAL = 'critical',

  /** Achieving one goal makes the other impossible */
  HIGH = 'high',

  /** Goals interfere significantly but both might be partially achievable */
  MEDIUM = 'medium',

  /** Minor overlap or tension - awareness is sufficient */
  LOW = 'low',

  /** Informational - potential future conflict worth monitoring */
  INFO = 'info',
}

// ============================================================================
// DOMAINS (for cross-domain awareness)
// ============================================================================

/**
 * Life domains for cross-domain conflict detection.
 * Conflicts within the same domain are expected; conflicts across domains
 * often reveal deeper tensions (e.g., work vs. health).
 */
export enum GoalDomain {
  CAREER = 'career',
  HEALTH = 'health',
  RELATIONSHIPS = 'relationships',
  FINANCES = 'finances',
  PERSONAL_GROWTH = 'personal_growth',
  CREATIVE = 'creative',
  COMMUNITY = 'community',
  LEARNING = 'learning',
  OTHER = 'other',
}

/**
 * Whether a cross-domain conflict is expected or surprising.
 * Expected cross-domain tensions (like career vs. health) are common;
 * unexpected ones (like creative vs. finances) may warrant more attention.
 */
export enum CrossDomainSurprise {
  EXPECTED = 'expected',
  NEUTRAL = 'neutral',
  UNEXPECTED = 'unexpected',
}

// ============================================================================
// RESOURCE TYPES
// ============================================================================

/**
 * Types of scarce resources that goals may compete for.
 */
export enum ResourceType {
  TIME = 'time',
  MONEY = 'money',
  ENERGY = 'energy', // mental/physical energy
  ATTENTION = 'attention', // focus/cognitive bandwidth
  SPACE = 'space', // physical or digital space
  SOCIAL_CAPITAL = 'social_capital', // favors, relationships, reputation
  EQUIPMENT = 'equipment', // shared physical resources
}

/**
 * A resource requirement for a goal.
 */
export interface ResourceRequirement {
  type: ResourceType;
  /** Estimated amount needed (units depend on resource type) */
  amount: number;
  /** Unit label for display, e.g. "hours/week", "USD", "kcal" */
  unit: string;
  /** When the resource is needed, if time-bound */
  window?: TimeWindow;
  /** Whether this is a hard requirement or a soft preference */
  hardRequirement: boolean;
}

// ============================================================================
// TIME WINDOWS
// ============================================================================

/**
 * A time window for when a goal is active or when resources are needed.
 */
export interface TimeWindow {
  start: Date;
  end: Date;
  /** Recurrence pattern if this repeats, e.g. "weekly", "daily" */
  recurrence?: string;
  /** ISO day-of-week if specific days matter (1=Monday ... 7=Sunday) */
  daysOfWeek?: number[];
  /** Time-of-day window in minutes from midnight, e.g. [540, 1020] for 9am-5pm */
  timeOfDayMinutes?: [number, number];
}

// ============================================================================
// GOAL REFERENCE (minimal view for conflict detection)
// ============================================================================

/**
 * Minimal goal representation needed for conflict detection.
 * The full Goal type lives elsewhere; this is the subset the engine needs.
 */
export interface GoalRef {
  id: string;
  title: string;
  description: string;
  domain: GoalDomain;
  /** Tags/keywords for semantic matching */
  tags: string[];
  /** Resource requirements */
  resources: ResourceRequirement[];
  /** When the goal is active */
  activeWindow?: TimeWindow;
  /** Priority 0-100, higher = more important */
  priority: number;
  /** Current status */
  status: GoalStatus;
  /** Embedding vector for semantic similarity (if available) */
  embedding?: number[];
}

export enum GoalStatus {
  PROPOSED = 'proposed',
  ACTIVE = 'active',
  PAUSED = 'paused',
  COMPLETED = 'completed',
  ABANDONED = 'abandoned',
}

// ============================================================================
// CONFLICT DETECTION RESULT
// ============================================================================

/**
 * A detected conflict between two (or more) goals.
 */
export interface Conflict {
  /** Unique identifier for this conflict */
  id: string;

  /** The category of conflict */
  category: ConflictCategory;

  /** Severity level */
  severity: ConflictSeverity;

  /** The goals involved in this conflict (typically 2, but could be more) */
  goalIds: string[];

  /** Human-readable explanation of the conflict */
  explanation: string;

  /** Machine-readable evidence supporting this conflict detection */
  evidence: ConflictEvidence;

  /** Cross-domain context, if the conflict spans domains */
  crossDomain?: CrossDomainContext;

  /** Suggested resolutions, ordered by recommendation strength */
  suggestedResolutions: ConflictResolution[];

  /** Confidence score 0-1 for the detection */
  confidence: number;

  /** When this conflict was detected */
  detectedAt: Date;

  /** Whether the user has acknowledged this conflict */
  acknowledged: boolean;

  /** Whether the user has resolved this conflict */
  resolved: boolean;

  /** How the user resolved it, if applicable */
  resolutionAction?: string;
}

// ============================================================================
// EVIDENCE
// ============================================================================

/**
 * Machine-readable evidence supporting a conflict detection.
 * Different evidence types apply to different conflict categories.
 */
export interface ConflictEvidence {
  /** Semantic similarity score 0-1 (for duplicates) */
  semanticSimilarity?: number;

  /** Overlapping tags between goals */
  sharedTags?: string[];

  /** Overlapping resource requirements */
  resourceOverlap?: ResourceOverlap[];

  /** Overlapping time windows */
  timeOverlap?: TimeOverlap;

  /** Contradictory outcome descriptions (extracted phrases) */
  contradictoryOutcomes?: ContradictoryOutcome[];

  /** Tension signals from goal descriptions */
  tensionSignals?: string[];

  /** The detection strategy that produced this evidence */
  detectionStrategy: string;

  /** Raw scores from individual detectors */
  detectorScores?: Record<string, number>;
}

/**
 * Overlap between two resource requirements.
 */
export interface ResourceOverlap {
  resourceType: ResourceType;
  goal1Amount: number;
  goal2Amount: number;
  /** Combined demand vs. available supply, if known */
  availableSupply?: number;
  /** Overlap ratio 0-1 */
  overlapRatio: number;
  unit: string;
}

/**
 * Overlap between two time windows.
 */
export interface TimeOverlap {
  /** Overlapping duration in minutes */
  overlapMinutes: number;
  /** Ratio of overlap to the smaller window 0-1 */
  overlapRatio: number;
  /** The overlapping window itself */
  window: TimeWindow;
}

/**
 * A pair of contradictory outcome phrases extracted from goal descriptions.
 */
export interface ContradictoryOutcome {
  goal1Phrase: string;
  goal2Phrase: string;
  /** Why these are contradictory */
  reasoning: string;
  /** Confidence in the contradiction 0-1 */
  confidence: number;
}

// ============================================================================
// CROSS-DOMAIN CONTEXT
// ============================================================================

/**
 * Context for conflicts that span multiple life domains.
 */
export interface CrossDomainContext {
  domains: GoalDomain[];
  /** Whether this cross-domain tension is expected or surprising */
  surprise: CrossDomainSurprise;
  /** Common cross-domain tension patterns this matches */
  pattern?: CrossDomainPattern;
  /** Additional context about why this matters */
  notes: string;
}

/**
 * Known cross-domain tension patterns.
 */
export enum CrossDomainPattern {
  /** Work demands vs. health needs */
  WORK_HEALTH = 'work_health',

  /** Career ambition vs. relationship time */
  CAREER_RELATIONSHIPS = 'career_relationships',

  /** Financial goals vs. personal growth spending */
  FINANCE_GROWTH = 'finance_growth',

  /** Creative pursuits vs. financial stability */
  CREATIVE_FINANCE = 'creative_finance',

  /** Community involvement vs. personal time */
  COMMUNITY_PERSONAL = 'community_personal',
}

// ============================================================================
// RESOLUTIONS
// ============================================================================

/**
 * A suggested way to resolve a conflict.
 */
export interface ConflictResolution {
  id: string;
  /** Short title for the resolution */
  title: string;
  /** Detailed description of what to do */
  description: string;
  /** The type of resolution action */
  type: ResolutionType;
  /** Which goal(s) this resolution primarily affects */
  affectedGoalIds: string[];
  /** Estimated effort to implement 1-10 */
  effort: number;
  /** Estimated benefit of resolving 1-10 */
  benefit: number;
}

/**
 * Types of conflict resolutions.
 */
export enum ResolutionType {
  /** Merge the two goals into one */
  MERGE = 'merge',

  /** Drop one of the goals */
  DROP_ONE = 'drop_one',

  /** Sequence the goals (do one after the other) */
  SEQUENCE = 'sequence',

  /** Reduce scope of one or both goals */
  REDUCE_SCOPE = 'reduce_scope',

  /** Find additional resources to satisfy both */
  ACQUIRE_RESOURCES = 'acquire_resources',

  /** Adjust timing to avoid overlap */
  RESCHEDULE = 'reschedule',

  /** Accept the tension and proceed with awareness */
  ACCEPT_TENSION = 'accept_tension',

  /** Reframe the goals to eliminate the conflict */
  REFRAME = 'reframe',
}

// ============================================================================
// DETECTION ENGINE INTERFACE
// ============================================================================

/**
 * Interface for a conflict detector.
 * Each detector handles one or more conflict categories.
 */
export interface ConflictDetector {
  /** Unique name for this detector */
  name: string;

  /** Which conflict categories this detector handles */
  categories: ConflictCategory[];

  /**
   * Detect conflicts between a pair of goals.
   * Returns null if no conflict detected.
   */
  detect(goal1: GoalRef, goal2: GoalRef): Promise<Conflict | null>;

  /** Whether this detector requires embeddings */
  requiresEmbeddings: boolean;
}

/**
 * Configuration for the conflict detection engine.
 */
export interface ConflictEngineConfig {
  /** Minimum semantic similarity to flag as duplicate (0-1) */
  duplicateThreshold: number;

  /** Minimum confidence to report a contradiction (0-1) */
  contradictionThreshold: number;

  /** Resource overlap ratio threshold (0-1) */
  resourceOverlapThreshold: number;

  /** Time overlap ratio threshold (0-1) */
  timeOverlapThreshold: number;

  /** Minimum tension score to report (0-1) */
  tensionThreshold: number;

  /** Whether to use AI/LLM for deeper analysis */
  useAIAnalysis: boolean;

  /** Whether to detect cross-domain conflicts specifically */
  enableCrossDomain: boolean;

  /** Maximum number of goals to compare pairwise before switching to batching */
  pairwiseLimit: number;
}

/**
 * Default configuration values.
 */
export const DEFAULT_CONFLICT_ENGINE_CONFIG: ConflictEngineConfig = {
  duplicateThreshold: 0.85,
  contradictionThreshold: 0.7,
  resourceOverlapThreshold: 0.6,
  timeOverlapThreshold: 0.5,
  tensionThreshold: 0.5,
  useAIAnalysis: true,
  enableCrossDomain: true,
  pairwiseLimit: 100,
};

// ============================================================================
// DETECTION RESULT BATCH
// ============================================================================

/**
 * Result of running conflict detection across a set of goals.
 */
export interface ConflictDetectionResult {
  /** All conflicts found */
  conflicts: Conflict[];

  /** Goals that were analyzed */
  goalIds: string[];

  /** Pairs that were checked but had no conflict */
  cleanPairs: number;

  /** Total pairs checked */
  totalPairs: number;

  /** Time taken in milliseconds */
  durationMs: number;

  /** Detectors that were run */
  detectorsUsed: string[];

  /** Configuration used */
  config: ConflictEngineConfig;
}
