"""Resource and demand model for Job-Star."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class ResourceKind(Enum):
    TIME = "time"
    ATTENTION = "attention"
    MONEY = "money"
    ENERGY = "energy"
    EQUIPMENT = "equipment"
    SPACE = "space"
    SOCIAL = "social"      # favors, relationships, goodwill
    CUSTOM = "custom"


@dataclass(frozen=True)
class Resource:
    """A finite resource that goals may consume."""
    name: str
    kind: ResourceKind
    unit: str = "units"
    # optional domain scope; None means global/cross-domain
    domain: Optional[str] = None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Resource):
            return NotImplemented
        return (
            self.name == other.name
            and self.kind == other.kind
            and self.domain == other.domain
        )

    def __hash__(self) -> int:
        return hash((self.name, self.kind, self.domain))


@dataclass(frozen=True)
class ResourceDemand:
    """A goal's declared need for a resource."""
    resource: Resource
    amount: float
    # optional rate: amount per day if recurring; None = one-time over window
    per_day: Optional[float] = None
    priority: int = 0  # higher = more important to satisfy
