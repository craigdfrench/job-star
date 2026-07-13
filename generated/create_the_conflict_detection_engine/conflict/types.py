"""
Conflict type definitions and severity levels for Job-Star's conflict detection engine.
"""

from enum import Enum


class ConflictType(str, Enum):
    """The category of conflict detected between two or more goals."""
    DUPLICATE = "duplicate"
    CONTRADICTION = "contradiction"
    RESOURCE_COMPETITION = "resource_competition"
    TENSION = "tension"


class Severity(str, Enum):
    """How serious a conflict is, from informational to blocking."""
    INFO = "info"           # No action needed, just awareness
    LOW = "low"             # Minor friction, worth noting
    MEDIUM = "medium"       # Should be reviewed, may need adjustment
    HIGH = "high"           # Likely needs resolution before proceeding
    BLOCKING = "blocking"   # Goals cannot coexist, must choose


# Default severity mapping per conflict type
DEFAULT_SEVERITY = {
    ConflictType.DUPLICATE: Severity.LOW,
    ConflictType.CONTRADICTION: Severity.BLOCKING,
    ConflictType.RESOURCE_COMPETITION: Severity.MEDIUM,
    ConflictType.TENSION: Severity.LOW,
}


class ConflictStatus(str, Enum):
    """Lifecycle status of a detected conflict."""
    DETECTED = "detected"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    IGNORED = "ignored"
