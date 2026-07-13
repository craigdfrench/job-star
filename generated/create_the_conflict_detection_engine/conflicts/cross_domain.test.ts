/**
 * Tests for the Cross-Domain Awareness Layer
 *
 * @module src/conflicts/cross_domain.test
 */

import {
  scoreRelevance,
  scoreRelevanceDefault,
  getRelevantCrossDomainPairs,
  getRelevantCrossDomainPairsDefault,
  adjustSeverityForDomains,
  adjustSeverityForDomainsDefault,
  getDomainMetadata,
  DEFAULT_DOMAIN_CONFIG,
  getCoupling,
  getCouplingWeight,
  getDomainWeight,
  domainPairKey,
  type DomainAwareGoal,
  type DomainConfig,
} from "./cross_domain";

import { describe, it, expect } from "vitest";

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

function makeGoal(
  id: string,
  domain: string,
  overrides: Partial<DomainAwareGoal> = {},
): DomainAwareGoal {
  return {
    id,
    domain,
    title: `Goal ${id}`,
    ...overrides,
  };
}

const sampleGoals: DomainAwareGoal[] = [
  makeGoal("g1", "work", { hoursPerWeek: 45, priority: 0.8 }),
  makeGoal("g2", "health", { hoursPerWeek: 6, priority: 0.9 }),
  makeGoal("g3", "personal", { hoursPerWeek: 10, priority: 0.6 }),
  makeGoal("g4", "hobby", { hoursPerWeek: 4, priority: 0.3 }),
  makeGoal("g5", "meta", { hoursPerWeek: 8, priority: 0.95 }),
  makeGoal("g6", "learning", { hoursPerWeek: 5, priority: 0.5 }),
];

// ---------------------------------------------------------------------------
// domain_config helpers
// ---------------------------------------------------------------------------

describe("domainPairKey", () => {
  it("produces canonical unordered keys", () => {
    expect(domainPairKey("work", "personal")).toBe(domainPairKey("personal", "work"));
    expect(domainPairKey("work", "personal")).toBe("personal|work");
  });
});

describe("getCoupling", () => {
  it("returns 'strong' for same domain", () => {
    expect(getCoupling(DEFAULT_DOMAIN_CONFIG, "work", "work")).toBe("strong");
  });

  it("returns configured coupling for known pairs", () => {
    expect(getCoupling(DEFAULT_DOMAIN_CONFIG, "work", "personal")).toBe("strong");
    expect(getCoupling(DEFAULT_DOMAIN_CONFIG, "work", "health")).toBe("strong");
    expect(getCoupling(DEFAULT_DOMAIN_CONFIG, "learning", "personal")).toBe("weak");
  });

  it("returns default coupling for unknown pairs", () => {
    expect(getCoupling(DEFAULT_DOMAIN_CONFIG, "hobby", "finance")).toBe("weak");
  });

  it("returns 'none' when default is none", () => {
    const config: DomainConfig = {
      ...DEFAULT_DOMAIN_CONFIG,
      defaultCoupling: "none",
    };
    expect(getCoupling(config, "hobby", "finance")).toBe("none");
  });
});

describe("getCouplingWeight", () => {
  it("returns numeric weights", () => {
    expect(getCouplingWeight(DEFAULT_DOMAIN_CONFIG, "work", "personal")).toBe(1.0);
    expect(getCouplingWeight(DEFAULT_DOMAIN_CONFIG, "learning", "personal")).toBe(0.3);
    expect(getCouplingWeight(DEFAULT_DOMAIN_CONFIG, "work", "work")).toBe(1.0);
  });
});

describe("getDomainWeight", () => {
  it("returns configured weight for known domain", () => {
    expect(getDomainWeight(DEFAULT_DOMAIN_CONFIG, "meta")).toBe(0.95);
    expect(getDomainWeight(DEFAULT_DOMAIN_CONFIG, "work")).toBe(0.80);
  });

  it("returns fallback for unknown domain", () => {
    expect(getDomainWeight(DEFAULT_DOMAIN_CONFIG, "unknown", 0.4)).toBe(0.4);
  });
});

// ---------------------------------------------------------------------------
// scoreRelevance
// ---------------------------------------------------------------------------

describe("scoreRelevance", () => {
  it("returns shouldCompare=false for none coupling", () => {
    const config: DomainConfig = {
      ...DEFAULT_DOMAIN_CONFIG,
      defaultCoupling: "none",
    };
    const result = scoreRelevance(
      config,
      makeGoal("a", "hobby"),
      makeGoal("b", "finance"),
    );
    expect(result.shouldCompare).toBe(false);
    expect(result.relevanceScore).toBe(0);
  });

  it("returns shouldCompare=true for strongly coupled domains", () => {
    const result = scoreRelevanceDefault(
      makeGoal("a", "work", { hoursPerWeek: 45 }),
      makeGoal("b", "health", { hoursPerWeek: 6 }),
    );
    expect(result.shouldCompare).toBe(true);
    expect(result.relevanceScore).toBeGreaterThan(0.5);
    expect(result.coupling).toBe("strong");
  });

  it("boosts relevance when combined hours are high", () => {
    const lowHours = scoreRelevanceDefault(
      makeGoal("a", "work", { hoursPerWeek: 20 }),
      makeGoal("b", "personal", { hoursPerWeek: 10 }),
    );
    const highHours = scoreRelevanceDefault(
      makeGoal("a", "work", { hoursPerWeek: 45 }),
      makeGoal("b", "personal", { hoursPerWeek: 30 }),
    );
    expect(highHours.relevanceScore).toBeGreaterThan(lowHours.relevanceScore);
  });

  it("skips same-domain pairs only when coupling is none (same domain is always strong)", () => {
    // Same domain returns "strong" coupling, so should always compare
    const result = scoreRelevanceDefault(
      makeGoal("a", "work"),
      makeGoal("b", "work"),
    );
    expect(result.coupling).toBe("strong");
    expect(result.shouldCompare).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// getRelevantCrossDomainPairs
// ---------------------------------------------------------------------------

describe("getRelevantCrossDomainPairs", () => {
  it("excludes same-domain pairs", () => {
    const goals = [
      makeGoal("a", "work"),
      makeGoal("b", "work"),
      makeGoal("c", "personal"),
    ];
    const pairs = getRelevantCrossDomainPairsDefault(goals);
    // a-b is same domain, excluded
    expect(pairs.find(p => p.goalA.id === "a" && p.goalB.id === "b")).toBeUndefined();
    // a-c and b-c are cross-domain
    expect(pairs.length).toBeGreaterThanOrEqual(2);
  });

  it("sorts pairs by relevance descending", () => {
    const pairs = getRelevantCrossDomainPairsDefault(sampleGoals);
    for (let i = 1; i < pairs.length; i++) {
      expect(pairs[i].relevance.relevanceScore).toBeLessThanOrEqual(
        pairs[i - 1].relevance.relevanceScore,
      );
    }
  });

  it("returns empty array for single domain goals", () => {
    const goals = [
      makeGoal("a", "work"),
      makeGoal("b", "work"),
    ];
    const pairs = getRelevantCrossDomainPairsDefault(goals);
    expect(pairs).toHaveLength(0);
  });

  it("filters out irrelevant pairs when default coupling is none", () => {
    const config: DomainConfig = {
      ...DEFAULT_DOMAIN_CONFIG,
      defaultCoupling: "none",
    };
    // hobby and finance have no explicit relationship, so with none default
    // they should be excluded
    const goals = [
      makeGoal("a", "hobby"),
      makeGoal("b", "finance"),
    ];
    const pairs = getRelevantCrossDomainPairs(config, goals);
    expect(pairs).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// adjustSeverityForDomains
// ---------------------------------------------------------------------------

describe("adjustSeverityForDomains", () => {
  it("adjusts severity based on domain weights and coupling", () => {
    const result = adjustSeverityForDomainsDefault(
      0.8,
      makeGoal("a", "work"),
      makeGoal("b", "health"),
    );
    expect(result.rawSeverity).toBe(0.8);
    expect(result.adjustedSeverity).toBeGreaterThan(0);
    expect(result.adjustedSeverity).toBeLessThanOrEqual(1);
    expect(result.domainA).toBe("work");
    expect(result.domainB).toBe("health");
  });

  it("identifies higher priority domain correctly", () => {
    // meta has rank 1, work has rank 3
    const result = adjustSeverityForDomainsDefault(
      0.7,
      makeGoal("a", "work"),
      makeGoal("b", "meta"),
    );
    expect(result.higherPriorityDomain).toBe("meta");
    expect(result.suggestedPriorityDomain).toBe("meta");
  });

  it("handles unknown domains with fallback weights", () => {
    const result = adjustSeverityForDomainsDefault(
      0.5,
      makeGoal("a", "unknownDomain"),
      makeGoal("b", "work"),
    );
    expect(result.adjustedSeverity).toBeGreaterThan(0);
    // unknownDomain has no rank, so work (rank 3) should be higher priority
    expect(result.higherPriorityDomain).toBe("work");
  });

  it("clamps adjusted severity to [0, 1]", () => {
    const high = adjustSeverityForDomainsDefault(
      1.5,
      makeGoal("a", "work"),
      makeGoal("b", "personal"),
    );
    expect(high.adjustedSeverity).toBeLessThanOrEqual(1);

    const low = adjustSeverityForDomainsDefault(
      -0.5,
      makeGoal("a", "work"),
      makeGoal("b", "personal"),
    );
    expect(low.adjustedSeverity).toBeGreaterThanOrEqual(0);
  });
});

// ---------------------------------------------------------------------------
// getDomainMetadata
// ---------------------------------------------------------------------------

describe("getDomainMetadata", () => {
  it("returns metadata for known domain", () => {
    const meta = getDomainMetadata(DEFAULT_DOMAIN_CONFIG, "work");
    expect(meta).not.toBeNull();
    expect(meta!.domain).toBe("work");
    expect(meta!.rank).toBe(3);
    expect(meta!.weight).toBe(0.80);
    expect(meta!.relatedDomains.length).toBeGreaterThan(0);
  });

  it("sorts related domains by coupling weight descending", () => {
    const meta = getDomainMetadata(DEFAULT_DOMAIN_CONFIG, "work");
    expect(meta).not.toBeNull();
    for (let i = 1; i < meta!.relatedDomains.length; i++) {
      expect(
        meta!.relatedDomains[i].couplingWeight,
      ).toBeLessThanOrEqual(
        meta!.relatedDomains[i - 1].couplingWeight,
      );
    }
  });

  it("returns null for unknown domain", () => {
    expect(getDomainMetadata(DEFAULT_DOMAIN_CONFIG, "nonexistent")).toBeNull();
  });

  it("includes meta domain with highest priority", () => {
    const meta = getDomainMetadata(DEFAULT_DOMAIN_CONFIG, "meta");
    expect(meta).not.toBeNull();
    expect(meta!.rank).toBe(1);
    expect(meta!.weight).toBe(0.95);
  });
});
