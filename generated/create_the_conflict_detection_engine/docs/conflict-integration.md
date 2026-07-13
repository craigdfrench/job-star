# Conflict Detection — System Integration

## Overview

The conflict detection engine is wired into the Job-Star system at two key
points:

1. **Goal Store** — Conflict reports are generated and stored automatically
   when goals are added or updated.
2. **Orchestrator Planner** — Active conflicts are consulted before planning
   work sessions, blocking or deferring goals with unresolved conflicts.

## Architecture
