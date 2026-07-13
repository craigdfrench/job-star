/**
 * Cross-Domain Awareness Layer
 *
 * Wraps the conflict detection engine with domain-aware logic:
 *  - Decides which cross-domain goal pairs are worth comparing.
 *  - Scores cross-domain conflicts with priority weighting.
 *  - Provides relevance scoring so the engine can skip low-value
 *    comparisons at scale.
 *
 * @module src/conflicts/cross_domain
 */

import {
  DomainConfig,
  DomainId,
  DEFAULT_DOMAIN_CONFIG,
  getCoupling,
  getCouplingWeight,
  getDomainWeight,
  getPriority,
  domainPairKey,
  CouplingStrength,
} from "./domain_config";

// ---------------------------------------------------------------------------
// Domain-aware goal interface
// ---------------------------------------------------------------------------

/**
 * Minimal goal shape needed for cross-domain analysis.
 * The full Goal type in the system will satisfy this.
 */
export interface DomainAwareGoal {
  id: string;
  domain: DomainId;
  /** Optional sub-domain for finer granularity. */
  subDomain?: string;
  title: string;
  description?: string;
  /** Estimated time commitment per week in hours, if known. */
  hoursPerWeek?: number;
  /** Priority of this specific goal (0-1), if set. */
  priority?: number;
}

// ---------------------------------------------------------------------------
// Relevance scoring
// ---------------------------------------------------------------------------

/**
 * Result of evaluating whether two goals from different domains
 * should be compared for conflicts.
 */
export interface RelevanceResult {
  /** Should the engine run conflict detectors on this pair? */
  shouldCompare: boolean;
  /** 0.0 – 1.0, how relevant the comparison is. */
  relevanceScore: number;
  /** Coupling strength between the two domains. */
  coupling: CouplingStrength;
  /** Human-readable reason. */
  reason: string;
}

/**
 * Determines whether two goals from different domains should be
 * compared, and how relevant that comparison is.
 *
 * Scoring factors:
 *  1. Domain coupling weight (from config)
 *  2. Time overlap — if both goals have hoursPerWeek, high combined
 *     hours increase relevance.
 *  3. Priority of individual goals — high-priority goals in weakly
 *     coupled domains may still be worth checking.
 */
export function scoreRelevance(
  config: DomainConfig,
  goalA: DomainAwareGoal,
  goalB: DomainAwareGoal,
): RelevanceResult {
  const coupling = getCoupling(config, goalA.domain, goalB.domain);
  const couplingWeight = getCouplingWeight(config, goalA.domain, goalB.domain);

  // If coupling is none, skip entirely.
  if (coupling === "none" || couplingWeight === 0) {
    return {
      shouldCompare: false,
      relevanceScore: 0,
      coupling,
      reason: `Domains "${goalA.domain}" and "${goalB.domain}" have no configured coupling.`,
    };
  }

  let score = couplingWeight;

  // Factor in time overlap
  if (goalA.hoursPerWeek != null && goalB.hoursPerWeek != null) {
    const combined = goalA.hoursPerWeek + goalB.hoursWeek ?? 0;
    const combinedB = goalA.hoursPerWeek + (goalB.hoursPerWeek ?? 0);
    const totalHours = combinedB;
    // If combined hours exceed 50/week, boost relevance
    if (totalHours > 50) {
      score += 0.15 * Math.min(1, (totalHours - 50) / 30);
    }
  }

  // Factor in individual goal priorities
  const goalPriorityA = goalA.priority ?? getDomainWeight(config, goalA.domain);
  const goalPriorityB = goalB.priority ?? getDomainWeight(config, goalB.domain);
  const avgPriority = (goalPriorityA + goalPriorityB) / 2;
  // Blend: 70% coupling, 30% goal priority
  score = score * 0.7 + avgPriority * 0.3;

  // Clamp
  score = Math.max(0, Math.min(1, score));

  // Decide threshold based on coupling
  const threshold = coupling === "weak" ? 0.25 : 0.15;

  return {
    shouldCompare: score >= threshold,
    relevanceScore: Math.round(score * 1000) / 1000,
    coupling,
    reason: `Coupling=${coupling} (${couplingWeight}), avgGoalPriority=${avgPriority.toFixed(2)}, score=${score.toFixed(2)}`,
  };
}

// ---------------------------------------------------------------------------
// Priority-weighted conflict scoring
// ---------------------------------------------------------------------------

/**
 * Adjusts a raw conflict severity score based on domain priorities.
 *
 * The adjustment considers:
 *  - The coupling weight between the two domains.
 *  - The relative priority of each domain.
 *  - Which domain is higher priority (for resolution suggestions).
 *
 * @param rawSeverity  Base severity from the detector (0.0 – 1.0)
 * @param config       Domain configuration
 * @param goalA        First goal
 * @param goalB        Second goal
 * @returns Adjusted severity and metadata
 */
export interface AdjustedConflictScore {
  adjustedSeverity: number;
  rawSeverity: number;
  domainA: DomainId;
  domainB: DomainId;
  higherPriorityDomain: DomainId;
  lowerPriorityDomain: DomainId;
  domainWeightA: number;
  domainWeightB: number;
  couplingWeight: number;
  /** Suggested domain to prioritise when resolving. */
  suggestedPriorityDomain: DomainId;
}

export function adjustSeverityForDomains(
  rawSeverity: number,
  config: DomainConfig,
  goalA: DomainAwareGoal,
  goalB: DomainAwareGoal,
): AdjustedConflictScore {
  const weightA = getDomainWeight(config, goalA.domain);
  const weightB = getDomainWeight(config, goalB.domain);
  const couplingWeight = getCouplingWeight(config, goalA.domain, goalB.domain);

  // Determine higher/lower priority domain
  const rankA = getPriority(config, goalA.domain)?.rank ?? 99;
  const rankB = getPriority(config, goalB.domain)?.rank ?? 99;
  const higherPriorityDomain = rankA <= rankB ? goalA.domain : goalB.domain;
  const lowerPriorityDomain = rankA <= rankB ? goalB.domain : goalA.domain;

  // Adjusted severity:
  //   base = rawSeverity
  //   scaled by average domain weight
  //   scaled by coupling weight
  const avgDomainWeight = (weightA + weightB) / 2;
  let adjusted = rawSeverity * (0.5 + 0.3 * avgDomainWeight + 0.2 * couplingWeight);
  adjusted = Math.max(0, Math.min(1, adjusted));
  adjusted = Math.round(adjusted * 1000) / 1000;

  return {
    adjustedSeverity: adjusted,
    rawSeverity,
    domainA: goalA.domain,
    domainB: goalB.domain,
    higherPriorityDomain,
    lowerPriorityDomain,
    domainWeightA: weightA,
    domainWeightB: weightB,
    couplingWeight,
    suggestedPriorityDomain: higherPriorityDomain,
  };
}

// ---------------------------------------------------------------------------
// Pair filtering for batch processing
// ---------------------------------------------------------------------------

/**
 * Given a list of goals, returns all cross-domain pairs that are
 * relevant enough to compare, along with their relevance scores.
 *
 * Same-domain pairs are excluded — those are handled by the
 * standard within-domain detectors.
 */
export interface RelevantPair {
  goalA: DomainAwareGoal;
  goalB: DomainAwareGoal;
  relevance: RelevanceResult;
}

export function getRelevantCrossDomainPairs(
  config: DomainConfig,
  goals: DomainAwareGoal[],
): RelevantPair[] {
  const pairs: RelevantPair[] = [];

  for (let i = 0; i < goals.length; i++) {
    for (let j = i + 1; j < goals.length; j++) {
      const a = goals[i];
      const b = goals[j];

      // Skip same-domain pairs
  if (a.domain === b.domain) continue;

      const relevance = scoreRelevance(config, a, b);
      if (relevance.shouldCompare) {
        pairs.push({ goalA: a, goalB: b, relevance });
      }
    }
  }

  // Sort by relevance descending — most relevant pairs first
  pairs.sort((a, b) => b.relevance.relevanceScore - a.relevance.relevanceScore);

  return pairs;
}

// ---------------------------------------------------------------------------
// Domain metadata enrichment
// ---------------------------------------------------------------------------

/**
 * Enriches a goal with domain metadata from the config.
 * Returns a summary of the domain's priority and known relationships.
 */
export interface DomainMetadata {
  domain: DomainId;
  rank: number;
  weight: number;
  relatedDomains: Array<{
    domain: DomainId;
    coupling: CouplingStrength;
    couplingWeight: number;
  }>;
}

export function getDomainMetadata(
  config: DomainConfig,
  domain: DomainId,
): DomainMetadata | null {
  const priority = getPriority(config, domain);
  if (!priority) return null;

  const relatedDomains: DomainMetadata["relatedDomains"] = [];

  for (const rel of config.relationships) {
    if (rel.domainA === domain || rel.domainB === domain) {
      const otherDomain = rel.domainA === domain ? rel.domainB : rel.domainA;
      relatedDomains.push({
        domain: otherDomain,
        coupling: rel.coupling,
        couplingWeight: getCouplingWeight(config, domain, otherDomain),
      });
    }
  }

  // Sort related domains by coupling weight descending
  relatedDomains.sort((a, b) => b.couplingWeight - a.couplingWeight);

  return {
    domain,
    rank: priority.rank,
    weight: priority.defaultWeight,
    relatedDomains,
  };
}

// ---------------------------------------------------------------------------
// Convenience: default-config-aware functions
// ---------------------------------------------------------------------------

/**
 * Score relevance using the default domain config.
 */
export function scoreRelevanceDefault(
  goalA: DomainAwareGoal,
  goalB: DomainAwareGoal,
): RelevanceResult {
  return scoreRelevance(DEFAULT_DOMAIN_CONFIG, goalA, goalB);
}

/**
 * Get relevant cross-domain pairs using the default config.
 */
export function getRelevantCrossDomainPairsDefault(
  goals: DomainAwareGoal[],
): RelevantPair[] {
  return getRelevantCrossDomainPairs(DEFAULT_DOMAIN_CONFIG, goals);
}

/**
 * Adjust severity using the default config.
 */
export function adjustSeverityForDomainsDefault(
  rawSeverity: number,
  goalA: DomainAwareGoal,
  goalB: DomainAwareGoal,
): AdjustedConflictScore {
  return adjustSeverityForDomains(
    rawSeverity,
    DEFAULT_DOMAIN_CONFIG,
    goalA,
    goalB,
  );
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

export { DEFAULT_DOMAIN_CONFIG } from "./domain_config";
export type {
  DomainConfig,
  DomainId,
  DomainPriority,
  DomainRelationship,
  CouplingStrength,
} from "./domain_config";
