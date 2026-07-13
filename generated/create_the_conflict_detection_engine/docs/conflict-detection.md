## Cross-Domain Awareness

### Overview

Goals don't exist in isolation — they live within life domains (work, health,
family, finance, etc.). The cross-domain awareness layer understands the
structural relationships between these domains and detects conflicts that
arise from their interaction.

### Domain Model

Each domain has a `DomainProfile` that captures:

- **Resource consumption**: How much of each shared resource the domain
  typically uses (normalized 0.0–1.0). For example, WORK consumes 60% of
  available mental energy at baseline intensity.
- **Resource production**: Some domains replenish resources. REST produces
  physical, mental, and emotional energy. FINANCE produces money.
- **Aligned domains**: Domains that naturally support each other
  (HEALTH ↔ FITNESS, WORK ↔ CAREER).
- **Tension domains**: Domains that structurally pull against each other
  (WORK ↔ REST, CAREER ↔ FAMILY).

### Shared Resources

The system tracks these finite resources across all domains:

| Resource | Description | Replenished by |
|---|---|---|
| `time_daily` | Hours in a day | (not replenishable) |
| `time_weekly` | Hours in a week | (not replenishable) |
| `energy_physical` | Physical energy | REST, HEALTH, FITNESS |
| `energy_mental` | Cognitive capacity | REST, MENTAL_HEALTH |
| `energy_emotional` | Emotional bandwidth | REST, RELATIONSHIPS, MENTAL_HEALTH |
| `money` | Financial resources | WORK, CAREER, FINANCE |
| `attention` | Focus capacity | REST, MENTAL_HEALTH |
| `willpower` | Self-control reserves | REST, SPIRITUAL, PERSONAL_GROWTH |
| `social_capital` | Relationship goodwill | RELATIONSHIPS, SOCIAL, COMMUNITY |

### Conflict Types Detected

1. **Resource Competition** — Two goals in different domains compete for the
   same shared resource. Example: a WORK goal and a LEARNING goal both demand
   heavy mental energy.

2. **Domain Tension** — Goals exist in domains with known structural friction.
   Example: a CAREER goal and a FAMILY goal. Even without specific resource
   overlap, these domains tend to pull in opposite directions.

3. **Schedule Collision** — Two cross-domain goals require the same time
   window. Example: a FITNESS goal and a WORK goal both need weekday mornings.

4. **Resource Depletion** — The aggregate demand across all goals exceeds
   sustainable capacity for a resource. This is systemic: no single pair of
   goals is the problem, but the total load is unsustainable.

### Severity Calculation

Severity is determined by:
- **Demand magnitude**: How much of a resource is being consumed
- **Priority amplification**: When both goals are high-priority, severity
  is bumped up one level
- **Overload ratio**: For depletion, how far over capacity the demand is

### Extending Domains

Custom domains can be registered at runtime:
