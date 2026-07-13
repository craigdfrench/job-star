/**
 * src/conflicts/detectors/duplicate.ts
 *
 * DuplicateDetector — detects goals that are semantically the same or
 * near-identical by comparing title + description using text similarity
 * (token overlap and/or embedding cosine similarity).
 *
 * Pairs above a configurable threshold are flagged as duplicates with
 * confidence proportional to the similarity score.
 *
 * Part of Job-Star's conflict detection engine.
 */

import {
  compositeSimilarity,
  cosineSimilarity,
  jaccardSimilarity,
  type CompositeSimilarityResult,
  type EmbeddingVector,
} from '../similarity';

// ---------------------------------------------------------------------------
// Core types — ConflictDetector interface
// ---------------------------------------------------------------------------

/**
 * Represents a goal in the Job-Star system.
 */
export interface Goal {
  id: string;
  title: string;
  description?: string;
  domain?: string;
  tags?: string[];
  embedding?: EmbeddingVector;
  createdAt?: string | number | Date;
}

/**
 * Severity levels for detected conflicts.
 */
export type ConflictSeverity = 'low' | 'medium' | 'high' | 'critical';

/**
 * The type of conflict detected.
 */
export type ConflictType =
  | 'duplicate'
  | 'contradiction'
  | 'resource_competition'
  | 'tension';

/**
 * A detected conflict between two goals.
 */
export interface Conflict {
  id: string;
  type: ConflictType;
  severity: ConflictSeverity;
  confidence: number; // 0..1
  goalIds: [string, string];
  description: string;
  evidence?: ConflictEvidence;
  detectedAt: string;
  detector: string;
}

export interface ConflictEvidence {
  similarityScore?: number;
  components?: CompositeSimilarityResult['components'];
  matchedTokens?: string[];
  threshold: number;
  method: string;
}

/**
 * Interface that all conflict detectors must implement.
 */
export interface ConflictDetector {
  readonly name: string;
  readonly conflictType: ConflictType;
  detect(goals: Goal[]): Promise<Conflict[]> | Conflict[];
}

// ---------------------------------------------------------------------------
// DuplicateDetector options
// ---------------------------------------------------------------------------

export interface DuplicateDetectorOptions {
  /**
   * Minimum composite similarity score (0..1) to flag a pair as duplicate.
   * Default: 0.82
   */
  threshold?: number;

  /**
   * Minimum Jaccard token overlap to even consider a pair (pre-filter for performance).
   * Set to 0 to disable pre-filtering. Default: 0.3
   */
  preFilterThreshold?: number;

  /**
   * Whether to use embeddings if available on goals.
   * Default: true
   */
  useEmbeddings?: boolean;

  /**
   * Severity mapping based on confidence score.
   * Default: { critical: 0.95, high: 0.90, medium: 0.85 }
   * Scores below 'medium' threshold but above `threshold` are 'low'.
   */
  severityThresholds?: {
    critical: number;
    high: number;
    medium: number;
  };

  /**
   * Whether to compare goals across different domains.
   * Default: true (duplicates can span domains)
   */
  crossDomain?: boolean;

  /**
   * Whether to include tag overlap in the comparison.
   * Default: true
   */
  includeTags?: boolean;

  /**
   * Weight multiplier for title similarity vs description.
   * Title is weighted more heavily. Default: 0.6 (title), 0.4 (description)
   */
  titleWeight?: number;
  descriptionWeight?: number;
}

// ---------------------------------------------------------------------------
// DuplicateDetector
// ---------------------------------------------------------------------------

/**
 * Detects duplicate goals by comparing title + description text similarity.
 *
 * Uses a composite similarity score combining:
 *   - Jaccard token overlap
 *   - Dice coefficient
 *   - Character n-gram similarity
 *   - Embedding cosine similarity (when embeddings are available)
 *
 * Confidence is proportional to the similarity score.
 */
export class DuplicateDetector implements ConflictDetector {
  readonly name = 'DuplicateDetector';
  readonly conflictType: ConflictType = 'duplicate';

  private readonly threshold: number;
  private readonly preFilterThreshold: number;
  private readonly useEmbeddings: boolean;
  private readonly severityThresholds: Required<
    NonNullable<DuplicateDetectorOptions['severityThresholds']>
  >;
  private readonly crossDomain: boolean;
  private readonly includeTags: boolean;
  private readonly titleWeight: number;
  private readonly descriptionWeight: number;

  constructor(options: DuplicateDetectorOptions = {}) {
    this.threshold = options.threshold ?? 0.82;
    this.preFilterThreshold = options.preFilterThreshold ?? 0.3;
    this.useEmbeddings = options.useEmbeddings ?? true;
    this.crossDomain = options.crossDomain ?? true;
    this.includeTags = options.includeTags ?? true;
    this.titleWeight = options.titleWeight ?? 0.6;
    this.descriptionWeight = options.descriptionWeight ?? 0.4;

    this.severityThresholds = {
      critical: options.severityThresholds?.critical ?? 0.95,
      high: options.severityThresholds?.high ?? 0.90,
      medium: options.severityThresholds?.medium ?? 0.85,
    };
  }

  /**
   * Run duplicate detection across all goals.
   * Compares every unique pair (O(n²)) — suitable for moderate goal counts.
   * For large goal sets, consider pre-clustering or blocking.
   */
  async detect(goals: Goal[]): Promise<Conflict[]> {
    if (goals.length < 2) return [];

    const conflicts: Conflict[] = [];

    for (let i = 0; i < goals.length; i++) {
      for (let j = i + 1; j < goals.length; j++) {
        const goalA = goals[i];
        const goalB = goals[j];

        // Skip same-domain restriction if crossDomain is false
        if (!this.crossDomain && goalA.domain && goalB.domain && goalA.domain !== goalB.domain) {
          continue;
        }

        // Skip if either goal lacks minimum text
        if (!this.hasSufficientText(goalA) || !this.hasSufficientText(goalB)) {
          continue;
        }

        // Pre-filter: quick Jaccard check on titles for performance
        if (this.preFilterThreshold > 0) {
          const quickScore = jaccardSimilarity(goalA.title, goalB.title);
          if (quickScore < this.preFilterThreshold) {
            // Also check description overlap as a secondary pre-filter
            const descScore = jaccardSimilarity(
              goalA.description ?? '',
              goalB.description ?? '',
            );
            if (quickScore < this.preFilterThreshold * 0.5 && descScore < this.preFilterThreshold) {
              continue;
            }
          }
        }

        const result = this.compareGoals(goalA, goalB);

        if (result.score >= this.threshold) {
          conflicts.push(this.buildConflict(goalA, goalB, result));
        }
      }
    }

    return conflicts;
  }

  /**
   * Compare two goals and return a composite similarity score.
   */
  private compareGoals(goalA: Goal, goalB: Goal): CompositeSimilarityResult {
    const titleSim = compositeSimilarity(goalA.title, goalB.title, {
      embeddingA: this.useEmbeddings ? goalA.embedding : undefined,
      embeddingB: this.useEmbeddings ? goalB.embedding : undefined,
    });

    const descA = goalA.description ?? '';
    const descB = goalB.description ?? '';
    const descSim =
      descA && descB
        ? compositeSimilarity(descA, descB)
        : { score: titleSim.score, components: titleSim.components, usedEmbeddings: false };

    // Tag overlap (optional)
    let tagScore = 0;
    if (this.includeTags && goalA.tags && goalB.tags && goalA.tags.length > 0 && goalB.tags.length > 0) {
      tagScore = jaccardSimilarity(goalA.tags.join(' '), goalB.tags.join(' '), {
        removeStopWords: false,
      });
    }

    // Weighted combination: title + description + small tag contribution
    const titlePortion = titleSim.score * this.titleWeight;
    const descPortion = descSim.score * this.descriptionWeight;
    const tagPortion = tagScore * 0.1;
    const totalWeight = this.titleWeight + this.descriptionWeight + 0.1;

    const combinedScore = (titlePortion + descPortion + tagPortion) / totalWeight;

    return {
      score: Math.min(combinedScore, 1.0),
      components: {
        jaccard: (titleSim.components.jaccard + descSim.components.jaccard) / 2,
        dice: (titleSim.components.dice + descSim.components.dice) / 2,
        ngram: (titleSim.components.ngram + descSim.components.ngram) / 2,
        embedding: titleSim.components.embedding ?? descSim.components.embedding,
      },
      usedEmbeddings: titleSim.usedEmbeddings || descSim.usedEmbeddings,
    };
  }

  /**
   * Build a Conflict object from a detected duplicate pair.
   */
  private buildConflict(
    goalA: Goal,
    goalB: Goal,
    result: CompositeSimilarityResult,
  ): Conflict {
    const severity = this.scoreToSeverity(result.score);
    const matchedTokens = this.findMatchedTokens(goalA, goalB);

    return {
      id: `dup-${goalA.id}-${goalB.id}`,
      type: 'duplicate',
      severity,
      confidence: result.score,
      goalIds: [goalA.id, goalB.id],
      description: this.buildDescription(goalA, goalB, result.score),
      evidence: {
        similarityScore: result.score,
        components: result.components,
        matchedTokens,
        threshold: this.threshold,
        method: result.usedEmbeddings ? 'composite+embedding' : 'composite-lexical',
      },
      detectedAt: new Date().toISOString(),
      detector: this.name,
    };
  }

  /**
   * Map a similarity score to a severity level.
   */
  private scoreToSeverity(score: number): ConflictSeverity {
    if (score >= this.severityThresholds.critical) return 'critical';
    if (score >= this.severityThresholds.high) return 'high';
    if (score >= this.severityThresholds.medium) return 'medium';
    return 'low';
  }

  /**
   * Find overlapping tokens between two goals (for evidence).
   */
  private findMatchedTokens(goalA: Goal, goalB: Goal): string[] {
    const textA = `${goalA.title} ${goalA.description ?? ''}`;
    const textB = `${goalB.title} ${goalB.description ?? ''}`;
    const tokensA = new Set(
      textA.toLowerCase().replace(/[^\w\s]/g, ' ').split(/\s+/).filter(Boolean),
    );
    const tokensB = new Set(
      textB.toLowerCase().replace(/[^\w\s]/g, ' ').split(/\s+/).filter(Boolean),
    );
    const matched: string[] = [];
    for (const t of tokensA) {
      if (tokensB.has(t) && t.length > 2) matched.push(t);
    }
    return matched.slice(0, 20); // cap for readability
  }

  /**
   * Build a human-readable description of the duplicate conflict.
   */
  private buildDescription(goalA: Goal, goalB: Goal, score: number): string {
    const pct = Math.round(score * 100);
    return (
      `Goals "${goalA.title}" and "${goalB.title}" are ${pct}% similar ` +
      `and likely represent the same objective. Consider merging or ` +
      `differentiating them with more specific descriptions.`
    );
  }

  /**
   * Check if a goal has enough text to meaningfully compare.
   */
  private hasSufficientText(goal: Goal): boolean {
    return goal.title && goal.title.trim().length >= 3;
  }
}

// ---------------------------------------------------------------------------
// Convenience function
// ---------------------------------------------------------------------------

/**
 * Quick utility: check if two goals are duplicates using default settings.
 */
export async function areDuplicates(
  goalA: Goal,
  goalB: Goal,
  options?: DuplicateDetectorOptions,
): Promise<boolean> {
  const detector = new DuplicateDetector(options);
  const conflicts = await detector.detect([goalA, goalB]);
  return conflicts.length > 0;
}
