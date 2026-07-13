# Conflict Detection Engine

## Overview

Job-Star's conflict detection engine identifies four types of conflict between goals:

| Type | Description | Example |
|------|-------------|---------|
| **Duplicate** | Same goal stated differently | "Learn Python" vs "Study Python programming" |
| **Contradiction** | Goals directly oppose each other | "Save $10k" vs "Spend savings on travel" |
| **Resource Conflict** | Goals need the same limited resource | Two goals each requiring 30 hrs/week |
| **Tension** | Subtle friction when pursued together | "Build startup" vs "Maintain work-life balance" |

## Tension Detection (Implemented)

### Tension Categories

- **Attention**: Goals requiring incompatible cognitive modes (deep focus vs. reactive, creative vs. analytical)
- **Temporal**: Deadline clustering or timeline misalignment
- **Value**: Goals serving competing underlying values (security vs. freedom, growth vs. contentment)
- **Energy**: Incompatible energy states (not yet fully implemented — partially covered by attention)
- **Identity**: Goals implying different self-concepts (leader vs. individual contributor, specialist vs. generalist)
- **Progress**: Progress on one goal creates drag on another
- **Context**: Goals requiring incompatible environments (solo vs. social, office vs. home)
- **Relational**: Goals pulling toward vs. away from people

### Severity Levels

| Level | Meaning |
|-------|---------|
| NEGLIGIBLE | Technically present but unlikely to cause friction |
| LOW | Minor friction, easily managed |
| MODERATE | Noticeable friction, requires conscious balancing |
| HIGH | Significant friction, one goal likely to suffer |
| CRITICAL | Near-contradictory, sustained pursuit of both is unsustainable |

### Usage
