/**
 * Domain Configuration for Cross-Domain Conflict Detection
 *
 * Defines which domains interact, how strongly they couple,
 * and priority weights for scoring cross-domain conflicts.
 *
 * @module src/conflicts/domain_config
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/**
 * A domain is a top-level area of life / work that a goal belongs to.
 * Examples: "work", "personal", "health", "meta", "learning", "finance".
 */
export type DomainId = string;

/**
 * Strength of coupling between two domains.
 * - "none"     — domains never interact; skip cross-domain checks
 * - "weak"     — rarely conflict; low-priority comparisons
 * - "moderate" — sometimes conflict; standard comparisons
 * - "strong"   — frequently conflict; high-priority comparisons
 */
export type CouplingStrength = "none" | "weak" | "moderate" | "strong";

/**
 * Numeric weight associated with each coupling strength.
 * Used to scale conflict severity for cross-domain pairs.
 */
export const COUPLING_WEIGHT: Record<CouplingStrength, number> = {
  none: 0.0,
  weak: 0.3,
  moderate: 0.6,
  strong: 1.0,
};

/**
 * Priority rank for a domain. Higher = more important.
 * When two domains conflict, the higher-priority domain's goals
 * are weighted more heavily in resolution suggestions.
 */
export interface DomainPriority {
  domain: DomainId;
  rank: number;          // 1 = highest
  defaultWeight: number; // 0.0 – 1.0
}

/**
 * Describes the relationship between two domains.
 * The pair is unordered — (A, B) is equivalent to (B, A).
 */
export interface DomainRelationship {
  domainA: DomainId;
  domainB: DomainId;
  coupling: CouplingStrength;
  /** Human-readable note on why these domains interact. */
  note?: string;
}

/**
 * Full configuration for cross-domain awareness.
 */
export interface DomainConfig {
  /** Known domains and their priorities. */
  priorities: DomainPriority[];
  /** Pairwise relationships between domains. */
  relationships: DomainRelationship[];
  /**
   * When true, any domain pair not explicitly listed is treated
   * as "weak" coupling. When false, unlisted pairs are "none".
   */
  defaultCoupling: CouplingStrength;
}

// ---------------------------------------------------------------------------
// Default Configuration
// ---------------------------------------------------------------------------

/**
 * Built-in default domain configuration.
 *
 * This covers the common domains in Job-Star and their typical
 * interactions. Consumers can override or extend this config.
 */
export const DEFAULT_DOMAIN_CONFIG: DomainConfig = {
  defaultCoupling: "weak",

  priorities: [
    { domain: "meta",       rank: 1, defaultWeight: 0.95 },
    { domain: "health",     rank: 2, defaultWeight: 0.85 },
    { domain: "work",       rank: 3, defaultWeight: 0.80 },
    { domain: "personal",   rank: 4, defaultWeight: 0.70 },
    { domain: "finance",    rank: 5, defaultWeight: 0.65 },
    { domain: "learning",   rank: 6, defaultWeight: 0.55 },
    { domain: "social",     rank: 7, defaultWeight: 0.45 },
    { domain: "hobby",      rank: 8, defaultWeight: 0.35 },
  ],

  relationships: [
    // Work ↔ Personal — classic time/energy tension
    {
      domainA: "work",
      domainB: "personal",
      coupling: "strong",
      note: "Work and personal goals frequently compete for time and energy.",
    },
    // Work ↔ Health — overwork vs self-care
    {
      domainA: "work",
      domainB: "health",
      coupling: "strong",
      note: "Work intensity often conflicts with health routines.",
    },
    // Health ↔ Personal
    {
      domainA: "health",
      domainB: "personal",
      coupling: "moderate",
      note: "Health goals can constrain personal lifestyle choices.",
    },
    // Finance ↔ Personal
    {
      domainA: "finance",
      domainB: "personal",
      coupling: "moderate",
      note: "Financial constraints affect personal goal feasibility.",
    },
    // Finance ↔ Work
    {
      domainA: "finance",
      domainB: "work",
      coupling: "moderate",
      note: "Career decisions impact financial goals.",
    },
    // Learning ↔ Work
    {
      domainA: "learning",
      domainB: "work",
      coupling: "moderate",
      note: "Learning goals may compete with work delivery time.",
    },
    // Learning ↔ Personal
    {
      domainA: "learning",
      domainB: "personal",
      coupling: "weak",
      note: "Learning can be personal but may compete for free time.",
    },
    // Meta ↔ Work — building the system vs doing the work
    {
      domainA: "meta",
      domainB: "work",
      coupling: "strong",
      note: "Meta goals (building Job-Star) compete with work goals for time.",
    },
    // Meta ↔ Personal
    {
      domainA: "meta",
      domainB: "personal",
      coupling: "moderate",
      note: "Meta goals can encroach on personal time.",
    },
    // Meta ↔ Learning
    {
      domainA: "meta",
      domainB: "learning",
      coupling: "moderate",
      note: "Meta goals often involve learning, but can conflict on focus.",
    },
    // Social ↔ Work
    {
      domainA: "social",
      domainB: "work",
      coupling: "weak",
      note: "Social activities can distract from work, but are also restorative.",
    },
    // Social ↔ Personal
    {
      domainA: "social",
      domainB: "personal",
      coupling: "moderate",
      note: "Social commitments affect personal time allocation.",
    },
    // Hobby ↔ Work
    {
      domainA: "hobby",
      domainB: "work",
      coupling: "weak",
      note: "Hobbies rarely directly conflict with work.",
    },
    // Hobby ↔ Personal
    {
      domainA: "hobby",
      domainB: "personal",
      coupling: "weak",
      note: "Hobbies are a subset of personal time.",
    },
    // Health ↔ Finance
    {
      domainA: "health",
      domainB: "finance",
      coupling: "weak",
      note: "Health expenses can strain finances, but rarely goal-level conflict.",
    },
  ],
};

// ---------------------------------------------------------------------------
// Lookup Helpers
// ---------------------------------------------------------------------------

/**
 * Normalises a domain pair into a canonical key "A|B" where A <= B
 * lexicographically, so (work, personal) and (personal, work) map
 * to the same key.
 */
export function domainPairKey(a: DomainId, b: DomainId): string {
  const sorted = [a, b].sort();
  return `${sorted[0]}|${sorted[1]}`;
}

/**
 * Looks up the coupling strength between two domains.
 * Falls back to defaultCoupling if no explicit relationship exists.
 * Returns "none" if either domain is unknown and default is none.
 */
export function getCoupling(
  config: DomainConfig,
  a: DomainId,
  b: DomainId,
): CouplingStrength {
  if (a === b) return "strong"; // same domain — always compare
  const key = domainPairKey(a, b);
  const rel = config.relationships.find(
    (r) => domainPairKey(r.domainA, r.domainB) === key,
  );
  if (rel) return rel.coupling;
  return config.defaultCoupling;
}

/**
 * Returns the priority info for a domain, or undefined if unknown.
 */
export function getPriority(
  config: DomainConfig,
  domain: DomainId,
): DomainPriority | undefined {
  return config.priorities.find((p) => p.domain === domain);
}

/**
 * Returns the weight for a domain, falling back to a default
 * if the domain is not in the priority list.
 */
export function getDomainWeight(
  config: DomainConfig,
  domain: DomainId,
  fallback = 0.5,
): number {
  const p = getPriority(config, domain);
  return p ? p.defaultWeight : fallback;
}

/**
 * Returns the numeric coupling weight between two domains.
 */
export function getCouplingWeight(
  config: DomainConfig,
  a: DomainId,
  b: DomainId,
): number {
  return COUPLING_WEIGHT[getCoupling(config, a, b)];
}
