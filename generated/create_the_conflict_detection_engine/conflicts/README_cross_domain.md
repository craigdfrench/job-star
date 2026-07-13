# Cross-Domain Awareness Layer

## Overview

The cross-domain awareness layer ensures the conflict detection engine
evaluates conflicts **across** domain boundaries, not just within a
single domain.

For example, a goal to "work 60 hours/week" (domain: `work`) and a goal
to "exercise 5x/week" (domain: `health`) live in different domains but
clearly conflict. Without cross-domain awareness, a within-domain-only
engine would miss this.

## Components

### `domain_config.ts`

Defines:

- **Domain priorities** — a ranked list of domains with default weights.
  Higher rank = more important. When two domains conflict, the
  higher-priority domain is suggested for resolution.
- **Domain relationships** — pairwise coupling strengths (`none`, `weak`,
  `moderate`, `strong`) describing how likely two domains are to conflict.
- **Default coupling** — the fallback coupling for unlisted domain pairs.

The default configuration covers: `meta`, `health`, `work`, `personal`,
`finance`, `learning`, `social`, `hobby`.

### `cross_domain.ts`

Provides:

1. **`scoreRelevance(config, goalA, goalB)`** — Decides whether two
   cross-domain goals are worth comparing and returns a relevance score.
   Factors: domain coupling, time overlap, individual goal priorities.

2. **`getRelevantCrossDomainPairs(config, goals)`** — Given a list of
   goals, returns all cross-domain pairs worth comparing, sorted by
   relevance. Same-domain pairs are excluded.

3. **`adjustSeverityForDomains(rawSeverity, config, goalA, goalB)`** —
   Takes a raw conflict severity from a detector and adjusts it based
   on domain weights and coupling. Returns metadata including which
   domain to prioritise when resolving.

4. **`getDomainMetadata(config, domain)`** — Returns enriched metadata
   for a domain: rank, weight, and all related domains sorted by
   coupling strength.

## Usage
