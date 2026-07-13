"""
Pattern library for tension detection.

Defines heuristics and signature patterns that indicate different types
of goal tension. Patterns are matched against goal metadata including:
- text/description
- domain
- tags
- timeline
- stated values
- energy requirements (if annotated)
- context requirements (if annotated)
"""

from dataclasses import dataclass
from typing import Callable, Optional
from .tension_types import TensionCategory, TensionSeverity, TensionSignal


@dataclass
class GoalProxy:
    """Lightweight representation of a goal for tension analysis.
    
    This abstraction lets the detector work with goals regardless of
    the full Goal model implementation.
    """
    id: str
    title: str = ""
    description: str = ""
    domain: str = ""
    tags: list[str] = None
    values: list[str] = None  # Stated or inferred values this goal serves
    timeline_start: Optional[str] = None
    timeline_end: Optional[str] = None
    energy_mode: str = ""  # e.g., "deep-focus", "creative", "social", "reactive"
    context: str = ""  # e.g., "office", "home", "travel", "solo"
    metadata: dict = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []
        if self.values is None:
            self.values = []
        if self.metadata is None:
            self.metadata = {}

    @property
    def text(self) -> str:
        """Combined text for pattern matching."""
        parts = [self.title, self.description]
        parts.extend(self.tags)
        parts.extend(self.values)
        return " ".join(p for p in parts if p).lower()


# --- Tension Pattern Definitions ---

# Each pattern is a function that takes (goal_a, goal_b) and returns
# an Optional[TensionSignal]. If None, no signal from this pattern.


def _attention_tension(a: GoalProxy, b: GoalProxy) -> Optional[TensionSignal]:
    """Detect when goals require incompatible attention modes."""
    deep_focus_signals = {"deep-focus", "deep work", "concentration", "flow", "focused"}
    reactive_signals = {"reactive", "interrupt-driven", "always-on", "responsive", "monitoring"}
    creative_signals = {"creative", "exploratory", "divergent", "brainstorm"}
    analytical_signals = {"analytical", "systematic", "convergent", "structured"}

    a_energy = a.energy_mode.lower() if a.energy_mode else ""
    b_energy = b.energy_mode.lower() if b.energy_mode else ""
    a_text = a.text
    b_text = b.text

    # Deep focus vs reactive
    a_deep = a_energy in deep_focus_signals or any(s in a_text for s in deep_focus_signals)
    b_reactive = b_energy in reactive_signals or any(s in b_text for s in reactive_signals)
    b_deep = b_energy in deep_focus_signals or any(s in b_text for s in deep_focus_signals)
    a_reactive = a_energy in reactive_signals or any(s in a_text for s in reactive_signals)

    if (a_deep and b_reactive) or (b_deep and a_reactive):
        return TensionSignal(
            category=TensionCategory.ATTENTION,
            severity=TensionSeverity.HIGH,
            description="Deep-focus goal conflicts with reactive/interrupt-driven goal",
            evidence=f"Energy modes: '{a_energy or a.title}' vs '{b_energy or b.title}'",
            confidence=0.7,
            source="attention_pattern",
        )

    # Creative vs analytical (moderate tension)
    a_creative = a_energy in creative_signals or any(s in a_text for s in creative_signals)
    b_analytical = b_energy in analytical_signals or any(s in b_text for s in analytical_signals)
    b_creative = b_energy in creative_signals or any(s in b_text for s in creative_signals)
    a_analytical = a_energy in analytical_signals or any(s in a_text for s in analytical_signals)

    if (a_creative and b_analytical) or (b_creative and a_analytical):
        return TensionSignal(
            category=TensionCategory.ATTENTION,
            severity=TensionSeverity.MODERATE,
            description="Creative/exploratory goal tensions with analytical/structured goal",
            evidence=f"Mode mismatch between '{a.title}' and '{b.title}'",
            confidence=0.6,
            source="attention_pattern",
        )

    return None


def _value_tension(a: GoalProxy, b: GoalProxy) -> Optional[TensionSignal]:
    """Detect when goals serve competing values."""
    # Value pairs that create tension when split across goals
    value_tensions = [
        ({"security", "stability", "safety"}, {"freedom", "autonomy", "independence"}, "Security vs. Freedom"),
        ({"growth", "ambition", "achievement"}, {"contentment", "peace", "simplicity"}, "Growth vs. Contentment"),
        ({"community", "belonging", "connection"}, {"independence", "self-reliance", "autonomy"}, "Community vs. Independence"),
        ({"novelty", "adventure", "exploration"}, {"stability", "routine", "consistency"}, "Novelty vs. Stability"),
        ({"excellence", "perfection", "mastery"}, {"speed", "efficiency", "done"}, "Excellence vs. Speed"),
        ({"control", "ownership"}, {"collaboration", "shared"}, "Control vs. Collaboration"),
    ]

    a_vals = set(v.lower() for v in a.values)
    b_vals = set(v.lower() for v in b.values)

    # Also check text for value keywords
    for val_set_a, val_set_b, label in value_tensions:
        a_match = a_vals & val_set_a or any(v in a.text for v in val_set_a)
        b_match = b_vals & val_set_b or any(v in b.text for v in val_set_b)
        b_match_rev = b_vals & val_set_a or any(v in b.text for v in val_set_a)
        a_match_rev = a_vals & val_set_b or any(v in a.text for v in val_set_b)

        if (a_match and b_match) or (a_match_rev and b_match_rev):
            return TensionSignal(
                category=TensionCategory.VALUE,
                severity=TensionSeverity.MODERATE,
                description=f"Value tension: {label}",
                evidence=f"'{a.title}' pulls toward one value, '{b.title}' toward the competing value",
                confidence=0.65,
                source="value_pattern",
            )

    return None


def _temporal_tension(a: GoalProxy, b: GoalProxy) -> Optional[TensionSignal]:
    """Detect timeline misalignment creating pressure."""
    a_end = a.metadata.get("deadline") or a.timeline_end
    b_end = b.metadata.get("deadline") or b.timeline_end
    a_start = a.metadata.get("start") or a.timeline_start
    b_start = b.metadata.get("start") or b.timeline_start

    if not a_end or not b_end:
        return None

    # Parse and compare — simplified; real implementation would use date parsing
    try:
        from datetime import datetime
        a_d = datetime.fromisoformat(a_end) if isinstance(a_end, str) else a_end
        b_d = datetime.fromisoformat(b_end) if isinstance(b_end, str) else b_end
        delta = abs((a_d - b_d).days)
    except (ValueError, TypeError):
        return None

    # Goals ending within 7 days of each other create temporal tension
    if delta <= 7:
        severity = TensionSeverity.HIGH if delta <= 2 else TensionSeverity.MODERATE
        return TensionSignal(
            category=TensionCategory.TEMPORAL,
            severity=severity,
            description=f"Deadline clustering: both goals converge within {delta} days",
            evidence=f"'{a.title}' deadline ~{a_end}, '{b.title}' deadline ~{b_end}",
            confidence=0.75,
            source="temporal_pattern",
        )

    return None


def _context_tension(a: GoalProxy, b: GoalProxy) -> Optional[TensionSignal]:
    """Detect when goals require incompatible environments."""
    context_oppositions = [
        ({"solo", "alone", "isolated", "private"}, {"social", "team", "collaborative", "group"}, "Solo vs. Social"),
        ({"office", "workplace"}, {"home", "remote"}, "Office vs. Home"),
        ({"travel", "nomadic", "mobile"}, {"stationary", "fixed-location", "rooted"}, "Travel vs. Stationary"),
        ({"quiet", "silent"}, {"loud", "busy", "active"}, "Quiet vs. Busy"),
    ]

    a_ctx = (a.context or "").lower()
    b_ctx = (b.context or "").lower()

    for set_a, set_b, label in context_oppositions:
        a_match = any(s in a_ctx or s in a.text for s in set_a)
        b_match = any(s in b_ctx or s in b.text for s in set_b)
        b_match_rev = any(s in b_ctx or s in b.text for s in set_a)
        a_match_rev = any(s in a_ctx or s in a.text for s in set_b)

        if (a_match and b_match) or (a_match_rev and b_match_rev):
            return TensionSignal(
                category=TensionCategory.CONTEXT,
                severity=TensionSeverity.MODERATE,
                description=f"Context tension: {label}",
                evidence=f"'{a.title}' requires one context, '{b.title}' requires the opposing context",
                confidence=0.6,
                source="context_pattern",
            )

    return None


def _identity_tension(a: GoalProxy, b: GoalProxy) -> Optional[TensionSignal]:
    """Detect when goals imply different self-concepts or roles."""
    identity_pairs = [
        ({"leader", "manager", "director", "boss"}, {"individual-contributor", "ic", "craftsperson", "practitioner"}, "Leader vs. Individual Contributor"),
        ({"expert", "specialist", "authority"}, {"generalist", "polymath", "jack-of-all-trades"}, "Specialist vs. Generalist"),
        ({"creator", "artist", "maker", "builder"}, {"consumer", "curator", "collector"}, "Creator vs. Consumer"),
        ({"teacher", "mentor", "educator"}, {"student", "learner", "novice"}, "Teacher vs. Student"),
        ({"public", "visible", "known", "celebrity"}, {"private", "anonymous", "behind-the-scenes"}, "Public vs. Private"),
    ]

    a_text = a.text
    b_text = b.text

    for set_a, set_b, label in identity_pairs:
        a_match = any(s in a_text for s in set_a)
        b_match = any(s in b_text for s in set_b)
        b_match_rev = any(s in b_text for s in set_a)
        a_match_rev = any(s in a_text for s in set_b)

        if (a_match and b_match) or (a_match_rev and b_match_rev):
            return TensionSignal(
                category=TensionCategory.IDENTITY,
                severity=TensionSeverity.MODERATE,
                description=f"Identity tension: {label}",
                evidence=f"'{a.title}' implies one identity, '{b.title}' implies the opposing identity",
                confidence=0.55,
                source="identity_pattern",
            )

    return None


def _progress_tension(a: GoalProxy, b: GoalProxy) -> Optional[TensionSignal]:
    """Detect when progress on one goal creates drag on another."""
    # Check for explicit "blocks" or "slows" metadata
    a_blocks = set(a.metadata.get("slows", []) + a.metadata.get("drags", []))
    b_blocks = set(b.metadata.get("slows", []) + b.metadata.get("drags", []))

    if b.id in a_blocks or a.id in b_blocks:
        return TensionSignal(
            category=TensionCategory.PROGRESS,
            severity=TensionSeverity.HIGH,
            description="Progress on one goal explicitly slows the other",
            evidence=f"Explicit drag relationship between '{a.title}' and '{b.title}'",
            confidence=0.9,
            source="progress_pattern",
        )

    # Heuristic: goals in same domain with high effort estimates
    if a.domain and b.domain and a.domain == b.domain:
        a_effort = a.metadata.get("effort_hours", 0)
        b_effort = b.metadata.get("effort_hours", 0)
        if a_effort and b_effort and (a_effort + b_effort) > 40:
            return TensionSignal(
                category=TensionCategory.PROGRESS,
                severity=TensionSeverity.MODERATE,
                description=f"High combined effort in same domain ({a.domain}) creates progress drag",
                evidence=f"Combined effort: {a_effort + b_effort}h/week in '{a.domain}'",
                confidence=0.6,
                source="progress_pattern",
            )

    return None


def _relational_tension(a: GoalProxy, b: GoalProxy) -> Optional[TensionSignal]:
    """Detect when goals pull toward vs. away from people."""
    toward_signals = {"community", "family", "friends", "team", "social", "network", "relationship"}
    away_signals = {"solitude", "hermit", "alone", "isolated", "independent", "solo retreat"}

    a_toward = any(s in a.text for s in toward_signals)
    a_away = any(s in a.text for s in away_signals)
    b_toward = any(s in b.text for s in toward_signals)
    b_away = any(s in b.text for s in away_signals)

    if (a_toward and b_away) or (a_away and b_toward):
        return TensionSignal(
            category=TensionCategory.RELATIONAL,
            severity=TensionSeverity.LOW,
            description="One goal pulls toward people, the other toward solitude",
            evidence=f"'{a.title}' vs '{b.title}' relational direction mismatch",
            confidence=0.5,
            source="relational_pattern",
        )

    return None


# Registry of all tension patterns
TENSION_PATTERNS: list[Callable[[GoalProxy, GoalProxy], Optional[TensionSignal]]] = [
    _attention_tension,
    _value_tension,
    _temporal_tension,
    _context_tension,
    _identity_tension,
    _progress_tension,
    _relational_tension,
]
