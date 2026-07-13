# Job-Star Conflict Detection Engine

Detects conflicts between goals to prevent redundant work, identify
contradictions, and surface resource tensions.

## Conflict Types

| Type | Status | Description |
|------|--------|-------------|
| **Duplicate** | ✅ Implemented | Two goals are semantically/structurally redundant |
| Contradiction | 🚧 Planned | Goals have mutually exclusive outcomes |
| Competing Resource | 🚧 Planned | Goals compete for the same limited resource |
| Tension | 🚧 Planned | Goals pull in different directions (soft conflict) |

## Duplicate Detection

### How It Works

The `DuplicateDetector` compares goal pairs using four weighted signals:

1. **Semantic similarity (45%)** — Compares title and description text.
   Uses `SequenceMatcher` as a lightweight fallback; designed to be
   replaced with embedding-based similarity in production.

2. **Structural similarity (30%)** — Compares steps, resources, and
   expected outputs using fuzzy list overlap matching.

3. **Temporal overlap (10%)** — Compares urgency levels. Same urgency
   increases likelihood of duplication.

4. **Domain match (15%)** — Same domain + overlapping tags increases
   confidence. Different domains reduce it.

### Thresholds

- **≥ 0.75** confidence → `is_duplicate = True` (HIGH severity)
- **≥ 0.60** confidence → `is_likely = True` (MEDIUM severity)
- **≥ 0.85** semantic + domain match → automatic duplicate (strong signal override)

### Usage
