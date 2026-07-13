"""
Job-Star Conflict Detection Engine.

Detects four types of conflicts between goals:
- Duplicates: Two goals that are essentially the same
- Contradictions: Two goals that directly oppose each other
- Competing resources: Two goals that need the same limited resource
- Tensions: Two goals that create friction or trade-offs

Cross-domain aware: conflicts between goals in different domains (work, health,
personal, etc.) are detected, not just within-domain conflicts.
"""

from .detector import ConflictDetector
from .types import (
    ConflictEvidence,
    ConflictReport,
    ConflictSeverity,
    ConflictType,
    GoalSnapshot,
)

__all__ = [
    "ConflictDetector",
    "ConflictType",
    "ConflictSeverity",
    "ConflictReport",
    "ConflictEvidence",
    "GoalSnapshot",
]


// --- DUPLICATE BLOCK ---

"""Job-Star conflict detection engine.

This package contains the conflict detection system for Job-Star, which
identifies conflicts between goals including:

- Duplicates: semantically equivalent goals
- Contradictions: goals with logically opposing outcomes
- Competing resources: goals demanding more of a resource than available
- Tensions: goals that create decision friction or priority conflicts

Cross-domain awareness is a key component: goals in different life domains
(work, personal, health, etc.) can conflict in ways that aren't visible
when analyzing a single domain in isolation.
"""

from job_star.conflict.cross_domain import (
    CrossDomainConflict,
    CrossDomainDetector,
    Domain,
    Goal,
    ResourceDemand,
    get_domain_relationship,
)

__all__ = [
    "CrossDomainConflict",
    "CrossDomainDetector",
    "Domain",
    "Goal",
    "ResourceDemand",
    "get_domain_relationship",
]


// --- DUPLICATE BLOCK ---

"""
Conflict detection engine for Job-Star.

Detects four types of goal conflict:
- Duplicates: same goal stated differently
- Contradictions: goals that directly oppose each other
- Competing resources: goals needing the same limited resource
- Tensions: goals that create subtle friction when pursued together

This package currently implements tension detection. Other detection
types will be added as sibling modules.
"""

from .tension_detector import TensionDetector
from .tension_patterns import GoalProxy, TENSION_PATTERNS
from .tension_types import (
    TensionCategory,
    TensionResult,
    TensionSeverity,
    TensionSignal,
)

__all__ = [
    "TensionDetector",
    "GoalProxy",
    "TENSION_PATTERNS",
    "TensionCategory",
    "TensionResult",
    "TensionSeverity",
    "TensionSignal",
]
