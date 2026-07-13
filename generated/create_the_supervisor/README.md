# Job-Star Supervisor

The supervisor is the **safety boundary** between planning and execution in Job-Star.

## Responsibilities

1. **Constraint Enforcement** — Every action is checked against the intersection of
   domain-level and goal-level capabilities before execution.
2. **Progress Monitoring** — Tracks action counts, time elapsed, and per-domain/goal
   breakdowns.
3. **Loop Detection** — Detects when the agent repeats the same action, indicating
   it's stuck.
4. **Budget Enforcement** — Pauses the system when action count or time limits are
   exceeded.
5. **Escalation** — Creates human-reviewable escalations when the supervisor detects
   problems or is uncertain.

## Capability Model

Capabilities are **additive at the domain level** and **restrictive at the goal level**.
The effective permission set is `domain_caps ∩ goal_caps`.
