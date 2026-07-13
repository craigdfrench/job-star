"""Contradiction detection between goals.

A contradiction exists when two goals assert mutually exclusive outcomes:
achieving one logically prevents achieving the other, or they require
the world to be in incompatible states simultaneously.

Detection strategies (applied in order, results merged):
  1. LexicalNegationStrategy   — direct negation pairs ("increase X" / "decrease X")
  2. DirectionalOppositionStrategy — opposing movement along a shared metric axis
  3. StateIncompatibilityStrategy   — mutually exclusive target states
  4. SemanticOppositionStrategy     — high semantic relevance + opposition cue words
  5. ConstraintViolationStrategy    — one goal's constraint negates another's success
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from job_star.conflict_detection.types import (
    Conflict,
    ConflictSeverity,
    ConflictType,
    Goal,
)


# ---------------------------------------------------------------------------
# Lexicon of opposition
# ---------------------------------------------------------------------------

# Directional verb pairs — (positive_direction, negative_direction).
# Each pair is ordered so the first moves a metric "up" and the second "down".
DIRECTIONAL_VERB_PAIRS: list[tuple[str, str]] = [
    ("increase", "decrease"),
    ("raise", "lower"),
    ("grow", "shrink"),
    ("expand", "reduce"),
    ("accelerate", "decelerate"),
    ("maximize", "minimize"),
    ("amplify", "attenuate"),
    ("strengthen", "weaken"),
    ("boost", "cut"),
    ("add", "remove"),
    ("build", "tear down"),
    ("enable", "disable"),
    ("start", "stop"),
    ("open", "close"),
    ("hire", "fire"),
    ("centralize", "decentralize"),
    ("consolidate", "split"),
    ("automate", "manualize"),
    ("upgrade", "downgrade"),
    ("promote", "demote"),
    ("accumulate", "deplete"),
    ("produce", "consume"),
    ("save", "spend"),
    ("invest", "divest"),
]

# Build a lookup: verb -> (partner, direction_sign)  where +1 = up, -1 = down
_VERB_DIRECTION: dict[str, tuple[str, int]] = {}
for up, down in DIRECTIONAL_VERB_PAIRS:
    _VERB_DIRECTION[up] = (down, +1)
    _VERB_DIRECTION[down] = (up, -1)

# State incompatibility phrases — if both goals assert one of these as a
# target for the *same subject*, they contradict.
EXCLUSIVE_STATE_PREDICATES: list[str] = [
    "primary",
    "secondary",
    "main",
    "sole",
    "exclusive",
    "dominant",
    "default",
    "mandatory",
    "optional",
    "deprecated",
    "retired",
    "active",
    "inactive",
    "permanent",
    "temporary",
    "public",
    "private",
    "open",
    "closed",
    "centralized",
    "decentralized",
    "monolith",
    "microservice",
]

# Cue words that, when present alongside high semantic similarity,
# raise the likelihood of contradiction rather than mere duplication.
OPPOSITION_CUES: list[str] = [
    "not", "never", "avoid", "prevent", "stop", "eliminate",
    "remove", "cancel", "reject", "oppose", "instead of",
    "rather than", "no longer", "without", "unless", "except",
]


# ---------------------------------------------------------------------------
# Candidate + detector plumbing
# ---------------------------------------------------------------------------

@dataclass
class Contradiction:
    """A contradiction conflict between two goals."""
    goal_a_id: str
    goal_b_id: str
    severity: ConflictSeverity
    confidence: float
    explanation: str
    evidence: list[str] = field(default_factory=list)
    detector: str = ""

    def to_conflict(self) -> Conflict:
        return Conflict(
            type=ConflictType.CONTRADICTION,
            goal_a_id=self.goal_a_id,
            goal_b_id=self.goal_b_id,
            severity=self.severity,
            confidence=self.confidence,
            explanation=self.explanation,
            evidence=self.evidence,
            detector=self.detector,
        )


class _Strategy:
    """Base class for contradiction detection strategies."""
    name: str = "base"

    def detect(self, a: Goal, b: Goal) -> Contradiction | None:
        raise NotImplementedError


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z]+(?:\s+[a-z]+){0,3}", _normalize(text))


def _verb_in_text(verb: str, text: str) -> bool:
    # Match as a whole word, allowing common inflections.
    pattern = r"\b" + re.escape(verb) + r"(?:s|ed|ing|es|d)?\b"
    return bool(re.search(pattern, _normalize(text)))


def _shared_subject(a_text: str, b_text: str, verb_a: str, verb_b: str) -> bool:
    """Heuristic: do the two verb phrases plausibly act on the same subject?

    We extract the noun-ish tokens that follow each verb and check overlap.
    This is deliberately conservative — it only confirms shared subject when
    there is token overlap, which avoids false positives on unrelated goals.
    """
    def object_tokens(text: str, verb: str) -> set[str]:
        norm = _normalize(text)
        pattern = r"\b" + re.escape(verb) + r"(?:s|ed|ing|es|d)?\b\s+([a-z]+(?:\s+[a-z]+){0,3})"
        m = re.search(pattern, norm)
        if not m:
            return set()
        return set(m.group(1).split()) - {"the", "a", "an", "of", "and", "to", "in", "on"}

    objs_a = object_tokens(a_text, verb_a)
    objs_b = object_tokens(b_text, verb_b)
    return bool(objs_a & objs_b)


# ---------------------------------------------------------------------------
# Strategy 1: Lexical negation
# ---------------------------------------------------------------------------

class LexicalNegationStrategy(_Strategy):
    """Detects direct verb-negation pairs acting on a shared subject.

    Example: "increase latency budget" vs "decrease latency budget".
    """

    name = "lexical_negation"

    def detect(self, a: Goal, b: Goal) -> Contradiction | None:
        a_text = f"{a.title} {a.description} {' '.join(a.success_criteria)}"
        b_text = f"{b.title} {b.description} {' '.join(b.success_criteria)}"

        for verb_a, (partner, dir_a) in _VERB_DIRECTION.items():
            if not _verb_in_text(verb_a, a_text):
                continue
            if not _verb_in_text(partner, b_text):
                continue
            if not _shared_subject(a_text, b_text, verb_a, partner):
                continue
            return Contradiction(
                goal_a_id=a.id,
                goal_b_id=b.id,
                severity=ConflictSeverity.BLOCKING,
                confidence=0.9,
                explanation=(
                    f"Goal A uses directional verb '{verb_a}' while Goal B uses "
                    f"its opposite '{partner}' on the same subject — the two "
                    f"target movements are mutually exclusive."
                ),
                evidence=[
                    f"Goal A verb: '{verb_a}' (direction {'up' if dir_a > 0 else 'down'})",
                    f"Goal B verb: '{partner}' (direction {'down' if dir_a > 0 else 'up'})",
                    "Shared subject detected in object phrases.",
                ],
                detector=self.name,
            )
        return None


# ---------------------------------------------------------------------------
# Strategy 2: Directional opposition along a metric axis
# ---------------------------------------------------------------------------

class DirectionalOppositionStrategy(_Strategy):
    """Detects opposing numeric targets on a shared metric.

    Example: Goal A sets "latency < 100ms", Goal B sets "latency > 500ms".
    """

    name = "directional_opposition"

    # Patterns: (label, regex, comparison_direction)
    # direction = "lt" means a "<" target; "gt" means a ">" target.
    _METRIC_RE = re.compile(
        r"([a-z][a-z _-]{1,40?[a-z])\s*(?:should be|must be|target(?:ed)?(?:at)?|of|<|>|≤|≥|at least|at most|below|above|under|over)\s*([0-9]+(?:\.[0-9]+)?)",
        re.IGNORECASE,
    )

    def detect(self, a: Goal, b: Goal) -> Contradiction | None:
        a_metrics = self._extract_metrics(a)
        b_metrics = self._extract_metrics(b)
        if not a_metrics or not b_metrics:
            return None

        for metric_name, (a_val, a_dir) in a_metrics.items():
            if metric_name not in b_metrics:
                continue
            b_val, b_dir = b_metrics[metric_name]
            if a_dir == b_dir:
                continue  # same direction, not a contradiction
            # Opposing directions on the same metric.
            # Contradiction if the targets are incompatible.
            if a_dir == "lt" and b_dir == "gt" and a_val <= b_val:
                severity = ConflictSeverity.BLOCKING
                conf = 0.92
                expl = (
                    f"Both goals target metric '{metric_name}' but in opposite "
                    f"directions with incompatible thresholds: Goal A requires "
                    f"≤ {a_val}, Goal B requires ≥ {b_val}."
                )
            elif a_dir == "gt" and b_dir == "lt" and a_val >= b_val:
                severity = ConflictSeverity.BLOCKING
                conf = 0.92
                expl = (
                    f"Both goals target metric '{metric_name}' but in opposite "
                    f"directions with incompatible thresholds: Goal A requires "
                    f"≥ {a_val}, Goal B requires ≤ {b_val}."
                )
            else:
                # Opposite directions but thresholds don't strictly conflict.
                severity = ConflictSeverity.MEDIUM
                conf = 0.55
                expl = (
                    f"Goals target metric '{metric_name}' in opposite directions "
                    f"(A: {a_dir} {a_val}, B: {b_dir} {b_val}); thresholds do not "
                    f"strictly overlap but the directional intent conflicts."
                )
            return Contradiction(
                goal_a_id=a.id,
                goal_b_id=b.id,
                severity=severity,
                confidence=conf,
                explanation=expl,
                evidence=[
                    f"Metric '{metric_name}': A={a_dir} {a_val}, B={b_dir} {b_val}",
                ],
                detector=self.name,
            )
        return None

    def _extract_metrics(self, goal: Goal) -> dict[str, tuple[float, str]]:
        """Return {metric_name: (value, direction)} where direction is 'lt' or 'gt'."""
        text = f"{goal.title} {goal.description} {' '.join(goal.success_criteria)}"
        results: dict[str, tuple[float, str]] = {}
        norm = _normalize(text)
        for m in self._METRIC_RE.finditer(norm):
            name = m.group(1).strip().rstrip(" -_")
            try:
                val = float(m.group(2))
            except ValueError:
                continue
            direction = self._infer_direction(norm, m.start(), name)
            if direction:
                results[name] = (val, direction)
        # Also pull from structured metrics if present.
        for k, v in goal.metrics.items():
            if isinstance(v, (int, float)):
                results.setdefault(k, (float(v), "eq"))
        return results

    def _infer_direction(self, text: str, pos: int, metric: str) -> str | None:
        """Infer whether the metric target is an upper bound (lt) or lower bound (gt)."""
        window = text[max(0, pos - 12): pos + len(metric) + 30]
        if re.search(r"\b(below|under|<|≤|at most|no more than|less than)\b", window):
            return "lt"
        if re.search(r"\b(above|over|>|≥|at least|no less than|more than)\b", window):
            return "gt"
        return None


# ---------------------------------------------------------------------------
# Strategy 3: State incompatibility
# ---------------------------------------------------------------------------

class StateIncompatibilityStrategy(_Strategy):
    """Detects goals assigning mutually exclusive states to the same subject.

    Example: "make service A the primary" vs "make service B the primary".
    """

    name = "state_incompatibility"

    def detect(self, a: Goal, b: Goal) -> Contradiction | None:
        a_states = self._extract_state_claims(a)
        b_states = self._extract_state_claims(b)
        if not a_states or not b_states:
            return None

        for subj_a, state_a in a_states:
            for subj_b, state_b in b_states:
                if subj_a != subj_b:
                    continue
                if state_a == state_b:
                    continue  # same state, not contradictory (maybe duplicate)
                if self._mutually_exclusive(state_a, state_b):
                    return Contradiction(
                        goal_a_id=a.id,
                        goal_b_id=b.id,
                        severity=ConflictSeverity.BLOCKING,
                        confidence=0.85,
                        explanation=(
                            f"Both goals assign mutually exclusive states to "
                            f"'{subj_a}': Goal A wants '{state_a}', Goal B wants "
                            f"'{state_b}'."
                        ),
                        evidence=[
                            f"Subject: {subj_a}",
                            f"State A: {state_a}",
                            f"State B: {state_b}",
                        ],
                        detector=self.name,
                    )
        return None

    def _extract_state_claims(self, goal: Goal) -> list[tuple[str, str]]:
        """Return list of (subject, state_predicate) found in the goal text."""
        text = _normalize(f"{goal.title} {goal.description} {goal.target_state or ''}")
        claims: list[tuple[str, str]] = []
        for state in EXCLUSIVE_STATE_PREDICATES:
            # "make X the primary", "X is the primary", "X becomes primary"
            pattern = rf"(?:make|set|designate|become(?:s)?|is|are|as the|the)\s+([a-z][a-z0-9 _-]{{1,40}})\s+(?:the\s+)?{re.escape(state)}\b"
            for m in re.finditer(pattern, text):
                subj = m.group(1).strip().rstrip(" as")
                # Trim filler words at the end.
                subj = re.sub(r"\b(?:a|an|the|to|as|be)\b\s*$", "", subj).strip()
                if subj:
                    claims.append((subj, state))
        return claims

    @staticmethod
    def _mutually_exclusive(s1: str, s2: str) -> bool:
        pairs = {
            frozenset({"primary", "secondary"}),
            frozenset({"main", "secondary"}),
            frozenset({"sole", "secondary"}),
            frozenset({"exclusive", "shared"}),
            frozenset({"active", "inactive"}),
            frozenset({"public", "private"}),
            frozenset({"open", "closed"}),
            frozenset({"centralized", "decentralized"}),
            frozenset({"monolith", "microservice"}),
            frozenset({"permanent", "temporary"}),
            frozenset({"mandatory", "optional"}),
            frozenset({"active", "retired"}),
            frozenset({"active", "deprecated"}),
        }
        return frozenset({s1, s2}) in pairs


# ---------------------------------------------------------------------------
# Strategy 4: Semantic opposition (embedding-based, with fallback)
# ---------------------------------------------------------------------------

class SemanticOppositionStrategy(_Strategy):
    """Uses semantic similarity + opposition cue words.

    Full implementation requires an embedding model. We provide a lightweight
    fallback using token overlap so the engine works without a model, and a
    hook to inject a real embedder for higher accuracy.
    """

    name = "semantic_opposition"

    def __init__(self, embedder=None):
        self._embedder = embedder

    def detect(self, a: Goal, b: Goal) -> Contradiction | None:
        a_text = _normalize(f"{a.title} {a.description}")
        b_text = _normalize(f"{b.title} {b.description}")

        similarity = self._similarity(a_text, b_text)
        if similarity < 0.25:
            return None  # not semantically related enough to contradict

        a_cues = sum(cue in a_text for cue in OPPOSITION_CUES)
        b_cues = sum(cue in b_text for cue in OPPOSITION_CUES)
        total_cues = a_cues + b_cues
        if total_cues == 0:
            return None

        # Confidence scales with both similarity and cue count.
        conf = min(0.8, similarity * 0.5 + min(total_cues, 4) * 0.15)
        severity = (
            ConflictSeverity.HIGH if conf >= 0.65
            else ConflictSeverity.MEDIUM if conf >= 0.45
            else ConflictSeverity.LOW
        )
        return Contradiction(
            goal_a_id=a.id,
            goal_b_id=b.id,
            severity=severity,
            confidence=round(conf, 3),
            explanation=(
                f"Goals are semantically related (similarity={similarity:.2f}) "
                f"and contain {total_cues} opposition cue(s), suggesting "
                f"contradictory intent."
            ),
            evidence=[
                f"Similarity: {similarity:.3f}",
                f"Opposition cues in A: {a_cues}",
                f"Opposition cues in B: {b_cues}",
            ],
            detector=self.name,
        )

    def _similarity(self, a: str, b: str) -> float:
        if self._embedder is not None:
            return self._embedder.similarity(a, b)
        # Fallback: Jaccard over token bigrams.
        def bigrams(text: str) -> set[str]:
            toks = text.split()
            return {f"{toks[i]} {toks[i+1]}" for i in range(len(toks) - 1)}
        ba, bb = bigrams(a), bigrams(b)
        if not ba or not bb:
            return 0.0
        return len(ba & bb) / len(ba | bb)


# ---------------------------------------------------------------------------
# Strategy 5: Constraint violation
# ---------------------------------------------------------------------------

class ConstraintViolationStrategy(_Strategy):
    """Detects when one goal's constraint directly negates another's success.

    Example: Goal A success = "deploy to production"; Goal B constraint =
    "no production deployments in Q3".
    """

    name = "constraint_violation"

    def detect(self, a: Goal, b: Goal) -> Contradiction | None:
        if not a.constraints or not b.success_criteria:
            pass
        # Check A's constraints vs B's success, and vice versa.
        for constraint_owner, success_owner, c_constraints, s_success, label in (
            (a, b, a.constraints, b.success_criteria, "A-constraint vs B-success"),
            (b, a, b.constraints, a.success_criteria, "B-constraint vs A-success"),
        ):
            for constraint in c_constraints:
                for success in s_success:
                    if self._negates(constraint, success):
                        return Contradiction(
                            goal_a_id=a.id,
                            goal_b_id=b.id,
                            severity=ConflictSeverity.BLOCKING,
                            confidence=0.8,
                            explanation=(
                                f"Goal constraint negates another goal's success "
                                f"criterion ({label}): '{constraint}' vs '{success}'."
                            ),
                            evidence=[
                                f"Constraint: {constraint}",
                                f"Success criterion: {success}",
                            ],
                            detector=self.name,
                        )
        return None

    def _negates(self, constraint: str, success: str) -> bool:
        c = _normalize(constraint)
        s = _normalize(success)
        # If the constraint contains a prohibition cue and shares substantial
        # tokens with the success criterion, treat as negation.
        prohibition_cues = ("no ", "not ", "never ", "must not", "cannot", "forbid", "prohibit", "avoid")
        has_prohibition = any(cue in c for cue in prohibition_cues)
        if not has_prohibition:
            return False
        c_tokens = set(c.split()) - {"no", "not", "never", "must", "cannot", "the", "a", "an", "to", "in", "on", "of", "and"}
        s_tokens = set(s.split()) - {"the", "a", "an", "to", "in", "on", "of", "and"}
        if not c_tokens or not s_tokens:
            return False
        overlap = len(c_tokens & s_tokens) / min(len(c_tokens), len(s_tokens))
        return overlap >= 0.5


# ---------------------------------------------------------------------------
# The detector
# ---------------------------------------------------------------------------

class ContradictionDetector:
    """Orchestrates contradiction detection across all strategies.

    Usage:
        detector = ContradictionDetector()
        conflicts = detector.detect_all(goals)
        # or pair-wise:
        conflict = detector.detect(goal_a, goal_b)
    """

    def __init__(self, embedder=None):
        self.strategies: list[_Strategy] = [
            LexicalNegationStrategy(),
            DirectionalOppositionStrategy(),
            StateIncompatibilityStrategy(),
            SemanticOppositionStrategy(embedder=embedder),
            ConstraintViolationStrategy(),
        ]

    def detect(self, a: Goal, b: Goal) -> Conflict | None:
        """Detect contradiction between a single pair. Returns the strongest."""
        candidates: list[Contradiction] = []
        for strategy in self.strategies:
            result = strategy.detect(a, b)
            if result is not None:
                candidates.append(result)
        if not candidates:
            return None
        best = max(candidates, key=lambda c: c.confidence)
        return best.to_conflict()

    def detect_all(self, goals: Iterable[Goal]) -> list[Conflict]:
        """Detect contradictions across all unique pairs."""
        goals = list(goals)
        conflicts: list[Conflict] = []
        for i in range(len(goals)):
            for j in range(i + 1, len(goals)):
                conflict = self.detect(goals[i], goals[j])
                if conflict is not None:
                    conflicts.append(conflict)
        return conflicts
