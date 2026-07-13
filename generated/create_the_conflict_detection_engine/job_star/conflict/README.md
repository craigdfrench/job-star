# Job-Star Conflict Detection Engine

## Overview

The conflict detection engine identifies four types of conflicts between goals:

| Type | Description | Severity Range | Recommendation |
|------|-------------|----------------|----------------|
| **Duplicate** | Two goals that are essentially the same | Medium–High | Merge |
| **Contradiction** | Two goals that directly oppose each other | High–Critical | Prioritize |
| **Competing Resource** | Two goals need the same limited resource | Low–Critical | Resource-allocate |
| **Tension** | Two goals create friction or trade-offs | Low–High | Sequence |

## Cross-Domain Awareness

Conflicts aren't limited to goals within the same domain. The engine explicitly
checks for cross-domain tensions (e.g., a work goal and a health goal can conflict
through time/energy competition even though they're in different domains).

## Usage

### Basic (heuristic-only)


// --- DUPLICATE BLOCK ---

# Job-Star Conflict Detection Engine

## Overview

The conflict detection engine identifies conflicts between goals in the
Job-Star system. It detects four primary types of conflict:

1. **Duplicates** — Semantically equivalent goals that should be merged
2. **Contradictions** — Goals with logically opposing outcomes
3. **Competing resources** — Goals demanding more of a resource than available
4. **Tensions** — Goals that create decision friction or priority conflicts

## Cross-Domain Awareness

A key insight: **goals in different life domains can conflict in ways that
aren't visible when analyzing a single domain in isolation.**

A work goal demanding 60 hours/week and a personal goal of training for a
marathon are in clear conflict, but only if you look across domains.

### Domains

Job-Star recognizes these domains:

| Domain | Description |
|--------|-------------|
| `meta` | Goals about the system itself, self-improvement, process |
| `work` | Career, job, professional projects |
| `personal` | General personal life, errands, life admin |
| `health` | Physical health, fitness, medical |
| `mental` | Mental health, therapy, mindfulness |
| `relationships` | Family, friends, romantic, social |
| `learning` | Education, skills, courses, reading |
| `creative` | Art, hobbies, side projects, expression |
| `financial` | Money, savings, investments, debt |
| `community` | Volunteering, civic, social impact |
| `spiritual` | Meaning, practice, faith, philosophy |

### Domain Relationships

Domains have baseline relationships: `compete`, `reinforce`, `tension`, or
`neutral`. These are heuristic defaults that the AI layer can override.

- **compete**: Goals in these domains inherently compete for resources
  (e.g., WORK and HEALTH compete for time)
- **reinforce**: Goals in these domains tend to support each other
  (e.g., LEARNING and WORK reinforce each other)
- **tension**: Goals in these domains create mild friction
  (e.g., CREATIVE and FINANCIAL — creative pursuits often don't pay)
- **neutral**: No significant baseline relationship

### Detection Strategies

The `CrossDomainDetector` runs six strategies:

1. **Resource competition** — Goals across domains competing for the same
   finite resource (time, energy, money). Detects both pool-level
   over-allocation and pairwise competition.

2. **Temporal overlap** — Goals in competing domains active in the same
   time window. Even without explicit resource tracking, concurrent
   competing-domain goals create tension.

3. **Priority tension** — Multiple high-priority (P1/P2) goals across
   competing domains. This creates chronic daily decision friction.

4. **Value friction** — Goals whose stated outcomes are philosophically
   opposed across domains. Uses AI-populated `value_friction_tags` metadata.
   Example: "reduce work hours" (meta) vs "get promoted" (work).

5. **Spillover risk** — Goals in one domain likely to negatively impact
   another domain. Uses AI-populated `spillover_risk` metadata.
   Example: high-stress work goal degrading health goals.

6. **Domain imbalance** — Total resource allocation heavily skewed toward
   one domain at the expense of others, compared to target budgets.

### Usage
