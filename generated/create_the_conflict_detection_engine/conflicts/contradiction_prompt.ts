// src/conflicts/prompts/contradiction_prompt.ts
/**
 * Prompt template for LLM-based contradiction detection.
 *
 * A contradiction exists when achieving one goal logically precludes
 * achieving the other — the outcomes are mutually exclusive or directly opposed.
 */

import { Goal } from '../types';

/**
 * Builds the prompt for asking an LLM whether two goals are contradictory.
 *
 * The prompt is designed to:
 * 1. Clearly present both goals with their context
 * 2. Define what constitutes a contradiction
 * 3. Request structured JSON output for reliable parsing
 *
 * @param goalA - The first goal to analyze.
 * @param goalB - The second goal to analyze.
 * @returns A prompt string ready to send to an LLM.
 */
export function buildContradictionPrompt(goalA: Goal, goalB: Goal): string {
  return `You are a conflict detection analyst for a goal management system. Your task is to determine whether two goals are contradictory.

## Definition of Contradiction

A contradiction exists when achieving one goal logically precludes achieving the other. This means:
- The outcomes are mutually exclusive: if you fully achieve goal A, you cannot fully achieve goal B (or vice versa).
- The goals are directly opposed: success in one means failure in the other.
- The goals require incompatible states of the world.

Important distinctions:
- A contradiction is NOT merely a resource conflict (both needing time or money). That is a "competing resource" conflict, not a contradiction.
- A contradiction is NOT a tension (goals that are difficult but possible to balance). That is a "tension" conflict.
- A contradiction IS when the goals cannot both be fully achieved, no matter how resources are allocated.

## Goals to Analyze

### Goal A
- ID: ${goalA.id}
- Title: ${goalA.title}
- Description: ${goalA.description}
- Domain: ${goalA.domain}
- Urgency: ${goalA.urgency ?? 'unspecified'}
- Status: ${goalA.status ?? 'unspecified'}

### Goal B
- ID: ${goalB.id}
- Title: ${goalB.title}
- Description: ${goalB.description}
- Domain: ${goalB.domain}
- Urgency: ${goalB.urgency ?? 'unspecified'}
- Status: ${goalB.status ?? 'unspecified'}

## Analysis Instructions

Consider the following questions:
1. If goal A is fully achieved, can goal B also be fully achieved? Why or why not?
2. If goal B is fully achieved, can goal A also be fully achieved? Why or why not?
3. Are the desired end states of both goals compatible?
4. Is there any way to reframe or adjust either goal so both could be achieved? (If yes, this may be a tension rather than a contradiction.)
5. Are the goals in different domains but still mutually exclusive? (Cross-domain contradictions are possible.)

## Output Format

Respond with ONLY a JSON object, no other text. The JSON must have this exact structure:

{
  "is_contradiction": boolean,
  "reasoning": "A clear, specific explanation of why these goals are or are not contradictory. Reference specific aspects of both goals.",
  "confidence": <number between 0.0 and 1.0>,
  "severity": "low" | "medium" | "high" | "critical",
  "cross_domain": boolean,
  "mutual_exclusivity_explanation": "If contradictory, explain exactly what makes the outcomes mutually exclusive. If not, explain why they can coexist."
}

### Severity Guidelines
- "critical": The goals are diametrically opposed; achieving one guarantees failure of the other. No workaround exists.
- "high": The goals are very likely mutually exclusive, but there may be a narrow edge case where both could partially succeed.
- "medium": The goals are likely mutually exclusive in their current form, but could potentially be reconciled with significant reframing.
- "low": There is a possible contradiction, but it depends on interpretation or specific conditions.

### Confidence Guidelines
- 0.9-1.0: The contradiction (or lack thereof) is logically certain.
- 0.7-0.89: Strong evidence of contradiction (or compatibility), but some ambiguity exists.
- 0.5-0.69: Moderate evidence; the analysis depends on assumptions.
- Below 0.5: Weak evidence; the relationship is unclear.

Remember: Respond with ONLY the JSON object. No markdown, no explanation outside the JSON.`;
}

/**
 * Expected structure of the LLM's response for contradiction analysis.
 */
export interface ContradictionLLMResponse {
  is_contradiction: boolean;
  reasoning: string;
  confidence: number;
  severity: 'low' | 'medium' | 'high' | 'critical';
  cross_domain: boolean;
  mutual_exclusivity_explanation: string;
}

/**
 * Parses the LLM's response into a structured ContradictionLLMResponse.
 *
 * Handles common issues:
 * - JSON wrapped in markdown code fences
 * - Extra text before/after the JSON
 * - Missing or malformed fields
 *
 * @param rawResponse - The raw text response from the LLM.
 * @returns Parsed response, or null if parsing fails.
 */
export function parseContradictionResponse(rawResponse: string): ContradictionLLMResponse | null {
  if (!rawResponse || typeof rawResponse !== 'string') {
    return null;
  }

  let jsonStr = rawResponse.trim();

  // Strip markdown code fences if present
  const fenceMatch = jsonStr.match(/
