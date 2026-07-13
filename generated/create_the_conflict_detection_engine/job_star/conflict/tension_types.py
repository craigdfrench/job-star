"""
Tension type taxonomy for Job-Star conflict detection.

Tensions are the most subtle form of goal conflict. They don't represent
direct contradictions or resource competition, but rather friction that
emerges when two goals pull a person in different directions.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class TensionCategory(Enum):
    """High-level categories of goal tension."""
    ATTENTION = "attention"          # Competing cognitive modes
    TEMPORAL = "temporal"            # Misaligned timelines / deadline clustering
    VALUE = "value"                  # Goals serving competing underlying values
    ENERGY = "energy"                # Incompatible energy states required
    IDENTITY = "identity"            # Different self-concepts implied
    PROGRESS = "progress"            # Progress on one creates drag on another
    CONTEXT = "context"              # Require incompatible environments
    RELATIONAL = "relational"        # Pull toward vs. away from people


class TensionSeverity(Enum):
    """How strongly two goals create tension."""
    NEGLIGIBLE = 0      # Technically present but unlikely to cause friction
    LOW = 1             # Minor friction, easily managed
    MODERATE = 2        # Noticeable friction, requires conscious balancing
    HIGH = 3            # Significant friction, one goal likely to suffer
    CRITICAL = 4        # Near-contradictory tension, sustained pursuit of both is unsustainable


@dataclass
class TensionSignal:
    """A single detected tension signal between two goals.
    
    Multiple signals between the same goal pair combine into a TensionResult.
    """
    category: TensionCategory
    severity: TensionSeverity
    description: str
    evidence: str  # What specifically triggered this signal
    confidence: float = 0.0  # 0.0 to 1.0
    source: str = ""  # Which detector/pattern produced this

    def __post_init__(self):
        if self.confidence == 0.0:
            self.confidence = 0.5  # Default moderate confidence


@dataclass
class TensionResult:
    """The combined tension assessment between two goals."""
    goal_a_id: str
    goal_b_id: str
    signals: list[TensionSignal] = field(default_factory=list)
    
    @property
    def max_severity(self) -> TensionSeverity:
        if not self.signals:
            return TensionSeverity.NEGLIGIBLE
        return max(s.severity for s in self.signals)
    
    @property
    def combined_confidence(self) -> float:
        """Confidence that a real tension exists, considering all signals."""
        if not self.signals:
            return 0.0
        # Confidence increases with multiple independent signals
        base = max(s.confidence for s in self.signals)
        signal_count = len(self.signals)
        boost = min(0.2, (signal_count - 1) * 0.05)
        return min(1.0, base + boost)
    
    @property
    def categories(self) -> list[TensionCategory]:
        return list(set(s.category for s in self.signals))
    
    @property
    def is_actionable(self) -> bool:
        """Whether this tension is strong enough to warrant attention."""
        return self.max_severity.value >= TensionSeverity.MODERATE.value
    
    def summary(self) -> str:
        if not self.signals:
            return "No tension detected"
        cats = ", ".join(c.value for c in self.categories)
        return f"Tension ({cats}) — max severity: {self.max_severity.name.lower()}, confidence: {self.combined_confidence:.2f}"
