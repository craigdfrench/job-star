"""
Job-Star Conflict Detection Engine.

Detects conflicts between goals:
- Duplicates: same goal submitted multiple times
- Contradictions: goals that directly oppose each other
- Resource competition: goals competing for limited resources
- Tensions: goals pulling in different strategic directions

Designed with cross-domain awareness.
"""

from job_star.conflict.engine import ConflictEngine
from job_star.conflict.models import Conflict, ConflictReport
from job_star.conflict.types import (
    ConflictStatus,
    ConflictType,
    DEFAULT_SEVERITY,
    Severity,
)

__all__ = [
    "ConflictEngine",
    "Conflict",
    "ConflictReport",
    "ConflictType",
    "ConflictStatus",
    "Severity",
    "DEFAULT_SEVERITY",
]
