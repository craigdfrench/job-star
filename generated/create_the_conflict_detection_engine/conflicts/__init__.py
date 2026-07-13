"""
Job-Star conflict detection engine.

Detects conflicts between goals:
- Duplicate detection (this module)
- Contradiction detection (future)
- Competing resource detection (future)
- Tension detection (future)
"""

from jobstar.conflict.duplicate import DuplicateDetector, DuplicateResult

__all__ = ["DuplicateDetector", "DuplicateResult"]


// --- DUPLICATE BLOCK ---

"""Job-Star conflict detection engine."""

from jobstar.conflict.base import (
    Conflict,
    ConflictKind,
    ConflictSeverity,
    Detector,
    GoalRef,
)
from jobstar.conflict.resources import (
    CompetitionMode,
    CompetingResourceDetector,
    ResourceCompetition,
    ResourcePool,
)

__all__ = [
    "Conflict",
    "ConflictKind",
    "ConflictSeverity",
    "Detector",
    "GoalRef",
    "CompetitionMode",
    "CompetingResourceDetector",
    "ResourceCompetition",
    "ResourcePool",
]


// --- DUPLICATE BLOCK ---

"""
Job-Star Conflict Detection Engine.

Detects conflicts between goals:
- Duplicates (same goal stated differently)
- Contradictions (goals that logically oppose)
- Competing resources (goals competing for finite resources)
- Tensions (structural friction between domains)

Cross-domain awareness layer: understands how goals in different life
domains (work, health, relationships, finance, etc.) interact and conflict.
"""

from .domains import (
    Domain,
    DomainProfile,
    DomainRegistry,
    SHARED_RESOURCES,
    DEFAULT_PROFILES,
)
from .cross_domain_detector import (
    CrossDomainDetector,
    CrossDomainConflict,
    GoalContext,
    ConflictType,
    Severity,
)

__all__ = [
    "Domain",
    "DomainProfile",
    "DomainRegistry",
    "SHARED_RESOURCES",
    "DEFAULT_PROFILES",
    "CrossDomainDetector",
    "CrossDomainConflict",
    "GoalContext",
    "ConflictType",
    "Severity",
]


// --- DUPLICATE BLOCK ---

"""
Job-Star Conflict Detection Engine.

This package provides AI-driven conflict detection between goals, including:
- Duplicate detection
- Contradiction detection
- Competing resource detection
- Tension detection
- Cross-domain awareness

Storage integration is provided via the storage_integration module.
"""

from jobstar.conflict.base import (
    Conflict,
    ConflictResult,
    ConflictSeverity,
    ConflictType,
)
from jobstar.conflict.duplicate import DuplicateDetector
from jobstar.conflict.cross_domain_detector import CrossDomainConflictDetector
from jobstar.conflict.domains import Domain
from jobstar.conflict.storage_integration import (
    ConflictDetectionService,
    ConflictRepository,
    DetectionContext,
    DetectionReport,
    DetectionTrigger,
    GoalQueryAdapter,
    GoalStore,
    InMemoryGoalStore,
    StorageHooks,
)

__all__ = [
    # Base types
    "Conflict",
    "ConflictResult",
    "ConflictSeverity",
    "ConflictType",
    # Detectors
    "DuplicateDetector",
    "CrossDomainConflictDetector",
    # Domain
    "Domain",
    # Storage integration
    "ConflictDetectionService",
    "ConflictRepository",
    "DetectionContext",
    "DetectionReport",
    "DetectionTrigger",
    "GoalQueryAdapter",
    "GoalStore",
    "InMemoryGoalStore",
    "StorageHooks",
]
