# Job-Star Conflict Detection Engine

## Overview

The conflict detection engine identifies when goals interfere with each other.
It detects four categories of conflict:

### Conflict Categories

1. **Duplicate** - Two goals describe the same outcome. Detection uses semantic
   similarity (embeddings) and tag overlap. Resolution: merge or drop one.

2. **Contradiction** - Two goals have mutually exclusive outcomes. Detection uses
   AI analysis of goal descriptions to identify opposing outcomes. Resolution:
   drop one, reframe, or sequence.

3. **Competing Resources** - Two goals need the same scarce resource. Detection
   compares resource requirements and time windows. Resolution: acquire more
   resources, reschedule, or reduce scope.

4. **Tension** - Two goals pull in different directions without strict
   exclusivity. Detection uses AI analysis for directional conflicts and
   cross-domain pattern matching. Resolution: accept with awareness, reduce
   scope, or reframe.

### Cross-Domain Awareness

Conflicts between goals in different life domains (e.g., career vs. health)
are flagged with cross-domain context. Known patterns are identified to help
users understand common life tensions.

### Architecture

- `types.ts` - All type definitions and data models
- `engine.ts` - Main detection orchestrator (to be implemented)
- `detectors/` - Individual conflict detectors (to be implemented)
  - `duplicate.ts` - Semantic similarity detection
  - `contradiction.ts` - AI-based contradiction detection
  - `resources.ts` - Resource and time overlap detection
  - `tension.ts` - Soft conflict and tension detection

### Detection Flow

1. Goals are loaded as `GoalRef` objects (minimal projection)
2. The engine runs all applicable detectors on each goal pair
3. Detectors return `Conflict` objects with evidence and resolutions
4. Results are deduplicated and ranked by severity
5. Cross-domain context is applied where relevant
