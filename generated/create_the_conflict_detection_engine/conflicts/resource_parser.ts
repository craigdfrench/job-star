/**
 * Resource Parser
 *
 * Extracts resource references from goal text. Combines two strategies:
 *  1. Pattern-based extraction for common resource types (time, money, files,
 *     @mentions) that don't require a pre-registered resource.
 *  2. Registry lookup for named resources that have been explicitly registered.
 *
 * Each extracted reference includes an estimated demand — how much of the
 * resource the goal is likely to consume — expressed in the resource's unit.
 */

import {
  ResourceEntry,
  ResourceRegistry,
  ResourceCategory,
} from "./resource_registry";

export interface ParsedResourceReference {
  /** The registry entry if this reference matched a known resource. */
  resourceId?: string;
  /** Category inferred from text patterns or from the registry entry. */
  category: ResourceCategory;
  /** Human-readable label for the resource (e.g. "daily time", "budget"). */
  label: string;
  /** Estimated amount demanded by the goal. */
  estimatedDemand: number;
  /** Unit of the demand (e.g. "hours", "USD"). */
  unit: string;
  /** Scarcity weight (from registry if matched, otherwise a default). */
  scarcity: number;
  /** The text snippet that triggered this reference. */
  matchedText: string;
  /** How confident we are this is a real resource reference (0.0–1.0). */
  confidence: number;
}

// ---------------------------------------------------------------------------
// Pattern definitions
// ---------------------------------------------------------------------------

interface PatternRule {
  category: ResourceCategory;
  label: string;
  unit: string;
  /** Default scarcity if not found in registry. */
  defaultScarcity: number;
  /** Regex with capture groups for amount and resource label. */
  regex: RegExp;
  /** Extract the numeric demand from the regex match. */
  extractDemand: (match: RegExpMatchArray) => number;
  /** Build a human-readable label from the match. */
  buildLabel: (match: RegExpMatchArray) => string;
}

const PATTERN_RULES: PatternRule[] = [
  // "3 hours", "2.5 hrs", "30 minutes"
  {
    category: "time",
    label: "time",
    unit: "hours",
    defaultScarcity: 0.7,
    regex: /(\d+(?:\.\d+)?)\s*(hours?|hrs?|minutes?|mins?)/gi,
    extractDemand: (m) => {
      const value = parseFloat(m[1]);
      const unit = m[2].toLowerCase();
      if (unit.startsWith("min")) return value / 60;
      return value;
    },
    buildLabel: (m) => `${m[1]} ${m[2]}`,
  },
  // "$500", "$1,200", "100 USD", "50 dollars"
  {
    category: "money",
    label: "money",
    unit: "USD",
    defaultScarcity: 0.8,
    regex: /\$\s?(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?)\s*(USD|dollars?)/gi,
    extractDemand: (m) => {
      const raw = m[1] ?? m[2];
      return parseFloat(raw.replace(/,/g, ""));
    },
    buildLabel: (m) => (m[1] ? `$${m[1]}` : `${m[2]} ${m[3]}`),
  },
  // "@alice", "@bob_smith"
  {
    category: "person",
    label: "person",
    unit: "interactions",
    defaultScarcity: 0.6,
    regex: /@([a-zA-Z0-9_]+)/g,
    extractDemand: () => 1,
    buildLabel: (m) => `@${m[1]}`,
  },
  // File paths: "src/index.ts", "config.yaml", "/etc/hosts"
  {
    category: "file",
    label: "file",
    unit: "files",
    defaultScarcity: 0.5,
    regex: /([\w./-]+\.\w{1,6})\b/g,
    extractDemand: () => 1,
    buildLabel: (m) => m[1],
  },
  // "deep work", "focus session", "concentration"
  {
    category: "attention",
    label: "focused attention",
    unit: "deep-work sessions",
    defaultScarcity: 0.9,
    regex: /\b(deep\s+work|focus(?:ed)?\s+session|concentration\s+block)\b/gi,
    extractDemand: () => 1,
    buildLabel: (m) => m[1],
  },
];

// ---------------------------------------------------------------------------
// Parser
// ---------------------------------------------------------------------------

export interface ResourceParserOptions {
  /** Registry to consult for named-resource matching. */
  registry?: ResourceRegistry;
}

export class ResourceParser {
  private registry: ResourceRegistry | undefined;

  constructor(options: ResourceParserOptions = {}) {
    this.registry = options.registry;
  }

  /**
   * Set or replace the registry used for named-resource lookups.
   */
  setRegistry(registry: ResourceRegistry): void {
    this.registry = registry;
  }

  /**
   * Parse a goal's text and return all detected resource references.
   */
  parse(text: string): ParsedResourceReference[] {
    const refs: ParsedResourceReference[] = [];
    const seen = new Set<string>();

    // --- 1. Registry-based extraction ---------------------------------------
    if (this.registry) {
      const registryMatches = this.registry.findInText(text);
      for (const entry of registryMatches) {
        const key = `registry:${entry.id}`;
        if (seen.has(key)) continue;
        seen.add(key);

        refs.push({
          resourceId: entry.id,
          category: entry.category,
          label: entry.name,
          estimatedDemand: this.estimateDemandFromText(text, entry),
          unit: entry.unit,
          scarcity: entry.scarcity,
          matchedText: entry.name,
          confidence: 0.8,
        });
      }
    }

    // --- 2. Pattern-based extraction ----------------------------------------
    for (const rule of PATTERN_RULES) {
      // Reset lastIndex because regexes are global
      rule.regex.lastIndex = 0;
      let match: RegExpExecArray | null;
      while ((match = rule.regex.exec(text)) !== null) {
        const matchedText = match[0];
        const key = `pattern:${rule.category}:${matchedText.toLowerCase()}`;
        if (seen.has(key)) continue;
        seen.add(key);

        const label = rule.buildLabel(match);
        const demand = rule.extractDemand(match);

        // If the registry already captured this as a named resource, skip
        // to avoid double-counting. We check by seeing if any existing ref
        // for the same category overlaps.
        const alreadyCovered = refs.some(
          (r) =>
            r.category === rule.category &&
            r.resourceId !== undefined &&
            matchedText.toLowerCase().includes(r.label.toLowerCase())
        );
        if (alreadyCovered) continue;

        refs.push({
          category: rule.category,
          label,
          estimatedDemand: demand,
          unit: rule.unit,
          scarcity: rule.defaultScarcity,
          matchedText,
          confidence: 0.65,
        });
      }
    }

    return refs;
  }

  /**
   * Heuristically estimate how much demand a goal places on a registered
   * resource by scanning the text for numeric quantities near the resource name.
   */
  private estimateDemandFromText(text: string, entry: ResourceEntry): number {
    const candidates = [entry.name, ...(entry.aliases ?? [])];
    for (const candidate of candidates) {
      // Look for a number within ~30 chars before the resource name
      const escaped = candidate.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const re = new RegExp(`(\\d+(?:\\.\\d+)?)\\s*\\w{0,15}\\s*${escaped}`, "i");
      const m = text.match(re);
      if (m) {
        return parseFloat(m[1]);
      }
    }
    // Default demand of 1 unit if we can't find a quantity
    return 1;
  }
}
