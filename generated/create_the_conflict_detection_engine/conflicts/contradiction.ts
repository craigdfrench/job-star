// src/conflicts/detectors/contradiction.ts
/**
 * ContradictionDetector — Detects goals whose outcomes are mutually exclusive
 * or directly opposed.
 *
 * This detector uses an LLM-based analysis pass: given two goals, it asks the
 * LLM whether achieving one precludes achieving the other. It returns
 * contradictions with the reasoning and a confidence score.
 *
 * Usage:
 *   const detector = new ContradictionDetector({
 *     llmClient: myLLMClient,
 *     confidenceThreshold: 0.6,
 *   });
 *   const result = await detector.detect(goalA, goalB);
 *   if (result) {
 *     console.log(`Contradiction detected: ${result.reasoning}`);
 *   }
 */

import {
  ConflictDetector,
  ConflictResult,
  ConflictSeverity,
  ConflictType,
  Goal,
  LLMClient,
} from '../types';
import {
  buildContradictionPrompt,
  ContradictionLLMResponse,
  parseContradictionResponse,
} from '../prompts/contradiction_prompt';

/**
 * Configuration specific to the ContradictionDetector.
 */
export interface ContradictionDetectorConfig {
  /** The LLM client to use for analysis. */
  llmClient: LLMClient;
  /** Minimum confidence threshold to report a conflict (default: 0.5). */
  confidenceThreshold?: number;
  /** Model identifier to use for analysis. */
  model?: string;
  /** Temperature for LLM calls (default: 0.1 for deterministic output). */
  temperature?: number;
  /** Maximum tokens for LLM response (default: 800). */
  maxTokens?: number;
}

/**
 * Default configuration values.
 */
const DEFAULTS = {
  confidenceThreshold: 0.5,
  temperature: 0.1,
  maxTokens: 800,
} as const;

/**
 * Detects contradiction conflicts between two goals using LLM analysis.
 *
 * A contradiction means the goals are mutually exclusive — achieving one
 * fully precludes achieving the other. This is stronger than a resource
 * conflict or tension; it means the desired end states are incompatible.
 */
export class ContradictionDetector implements ConflictDetector {
  readonly conflictType = ConflictType.CONTRADICTION;

  private readonly llmClient: LLMClient;
  private readonly confidenceThreshold: number;
  private readonly model?: string;
  private readonly temperature: number;
  private readonly maxTokens: number;

  constructor(config: ContradictionDetectorConfig) {
    if (!config.llmClient) {
      throw new Error('ContradictionDetector requires an llmClient in config');
    }

    this.llmClient = config.llmClient;
    this.confidenceThreshold = config.confidenceThreshold ?? DEFAULTS.confidenceThreshold;
    this.model = config.model;
    this.temperature = config.temperature ?? DEFAULTS.temperature;
    this.maxTokens = config.maxTokens ?? DEFAULTS.maxTokens;
  }

  /**
   * Analyze two goals for a contradiction conflict.
   *
   * @param goalA - The first goal to analyze.
   * @param goalB - The second goal to analyze.
   * @returns A ConflictResult if a contradiction is detected above the
   *          confidence threshold, or null if no contradiction is found.
   */
  async detect(goalA: Goal, goalB: Goal): Promise<ConflictResult | null> {
    // Skip self-comparison
    if (goalA.id === goalB.id) {
      return null;
    }

    // Skip if either goal is already completed/cancelled
    if (this.isTerminalStatus(goalA) || this.isTerminalStatus(goalB)) {
      return null;
    }

    // Build the prompt
    const prompt = buildContradictionPrompt(goalA, goalB);

    // Call the LLM
    let rawResponse: string;
    try {
      rawResponse = await this.llmClient.complete(prompt, {
        model: this.model,
        temperature: this.temperature,
        maxTokens: this.maxTokens,
      });
    } catch (error) {
      console.error(
        `[ContradictionDetector] LLM call failed for goals ${goalA.id} and ${goalB.id}:`,
        error,
      );
      return null;
    }

    // Parse the response
    const parsed = parseContradictionResponse(rawResponse);
    if (!parsed) {
      console.warn(
        `[ContradictionDetector] Failed to parse LLM response for goals ${goalA.id} and ${goalB.id}. ` +
          `Raw response: ${rawResponse.substring(0, 200)}...`,
      );
      return null;
    }

    // Check if a contradiction was detected
    if (!parsed.is_contradiction) {
      return null;
    }

    // Check confidence threshold
    if (parsed.confidence < this.confidenceThreshold) {
      return null;
    }

    // Build the conflict result
    const result: ConflictResult = {
      type: ConflictType.CONTRADICTION,
      goal_ids: [goalA.id, goalB.id],
      reasoning: this.buildReasoning(parsed),
      confidence: parsed.confidence,
      severity: this.mapSeverity(parsed.severity),
      metadata: {
        model: this.model,
        raw_response: rawResponse,
        detected_at: new Date().toISOString(),
        cross_domain: parsed.cross_domain,
        mutual_exclusivity_explanation: parsed.mutual_exclusivity_explanation,
        confidence_threshold: this.confidenceThreshold,
      },
    };

    return result;
  }

  /**
   * Build a comprehensive reasoning string from the parsed LLM response.
   * Combines the general reasoning with the mutual exclusivity explanation
   * for a richer conflict report.
   */
  private buildReasoning(parsed: ContradictionLLMResponse): string {
    const parts: string[] = [];

    if (parsed.reasoning) {
      parts.push(parsed.reasoning);
    }

    if (parsed.mutual_exclusivity_explanation) {
      parts.push(`Mutual exclusivity: ${parsed.mutual_exclusivity_explanation}`);
    }

    if (parsed.cross_domain) {
      parts.push('Note: This contradiction spans multiple domains.');
    }

    return parts.join(' ');
  }

  /**
   * Map the LLM's severity string to the ConflictSeverity enum.
   */
  private mapSeverity(severity: string): ConflictSeverity {
    switch (severity) {
      case 'critical':
        return ConflictSeverity.CRITICAL;
      case 'high':
        return ConflictSeverity.HIGH;
      case 'medium':
        return ConflictSeverity.MEDIUM;
      case 'low':
        return ConflictSeverity.LOW;
      default:
        return ConflictSeverity.MEDIUM;
    }
  }

  /**
   * Check if a goal is in a terminal status (completed or cancelled),
   * in which case contradiction detection is not meaningful.
   */
  private isTerminalStatus(goal: Goal): boolean {
    const status = goal.status?.toLowerCase();
    return status === 'completed' || status === 'cancelled' || status === 'done';
  }
}
