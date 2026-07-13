"""The main triage engine: ties classification + dedup together."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .classifier import (
    classify_domain,
    classify_type,
    classify_urgency,
    suggest_tags,
)
from .config import TriageConfig
from .duplicate_checker import check_duplicates
from .models import GoalRef, IntakeRequest, TriageResult


class TriageEngine:
    """Classifies intake requests and checks for duplicates.

    Usage:
        engine = TriageEngine.from_yaml("config/triage_config.yaml")
        result = engine.triage(request, goals=existing_goals)
    """

    def __init__(self, config: TriageConfig):
        self.config = config

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TriageEngine":
        return cls(TriageConfig.from_yaml(path))

    @classmethod
    def from_dict(cls, raw: dict) -> "TriageEngine":
        return cls(TriageConfig.from_dict(raw))

    def triage(
        self,
        request: IntakeRequest,
        goals: Iterable[GoalRef] | None = None,
    ) -> TriageResult:
        """Triage a single request."""
        domain = classify_domain(request, self.config)
        urgency = classify_urgency(request, self.config)
        req_type = classify_type(request, self.config)
        tags = suggest_tags(request, self.config)

        notes: list[str] = []
        dup_match = None
        if goals is not None:
            goals_list = list(goals)
            dup_match = check_duplicates(request, goals_list, self.config)
            if dup_match:
                notes.append(f"Duplicate detected: {dup_match.reason}")

        # Add notes for low-confidence classifications
        if domain.confidence < 0.5:
            notes.append(
                f"Low confidence domain classification ({domain.confidence}); "
                "consider manual review."
            )
        if req_type.label == "unknown":
            notes.append("Could not determine request type; defaulting to 'task'.")

        return TriageResult(
            request_id=request.id,
            domain=domain,
            urgency=urgency,
            type=req_type,
            suggested_tags=tags,
            duplicate_of=dup_match,
            notes=notes,
        )

    def triage_batch(
        self,
        requests: Iterable[IntakeRequest],
        goals: Iterable[GoalRef] | None = None,
    ) -> list[TriageResult]:
        """Triage multiple requests. Goals are shared across the batch."""
        goals_list = list(goals) if goals is not None else None
        return [self.triage(r, goals_list) for r in requests]


// --- DUPLICATE BLOCK ---

"""High-level triage orchestrator.

Ties together the classifier and duplicate detector, providing
a simple API for the rest of Job-Star to use.
"""

from __future__ import annotations

from .classifier import Classifier
from .models import ClassificationResult, IntakeRequest
from .registry import DuplicateDetector, GoalRegistry, InMemoryGoalRegistry


class TriageEngine:
    """Main entry point for triaging intake requests."""

    def __init__(
        self,
        registry: GoalRegistry | None = None,
    ) -> None:
        self._registry = registry or InMemoryGoalRegistry()
        self._dup_detector = DuplicateDetector(self._registry)
        self._classifier = Classifier(self._dup_detector)

    @property
    def registry(self) -> GoalRegistry:
        return self._registry

    def triage(self, request: IntakeRequest) -> ClassificationResult:
        """Classify a single request."""
        return self._classifier.classify(request)

    def triage_batch(
        self, requests: list[IntakeRequest]
    ) -> list[ClassificationResult]:
        """Classify multiple requests."""
        return [self.triage(r) for r in requests]

    def triage_and_report(
        self, request: IntakeRequest
    ) -> tuple[ClassificationResult, str]:
        """Classify and return a human-readable summary."""
        result = self.triage(request)

        lines = [
            f"Triage Report for: {request.title}",
            f"  Domain:     {result.domain.value}",
            f"  Urgency:    {result.urgency.value}",
            f"  Type:       {result.request_type.value}",
            f"  Confidence: {result.confidence:.1%}",
            f"  Duplicate:  {result.duplicate_status.value}",
        ]

        if result.duplicate_of:
            lines.append(f"  Dup Of:     {result.duplicate_of}")
        if result.related_goals:
            lines.append(f"  Related:    {', '.join(result.related_goals)}")
        if result.matched_signals:
            lines.append(f"  Signals:    {', '.join(result.matched_signals)}")

        return result, "\n".join(lines)


// --- DUPLICATE BLOCK ---

"""Triage engine — ties classifiers and duplicate detection together."""

from __future__ import annotations

from typing import Sequence

from .classifiers import classify_domain, classify_type, classify_urgency
from .duplicate import check_duplicate
from .models import (
    ClassificationResult,
    GoalRegistryEntry,
    IntakeRequest,
)


def triage(
    request: IntakeRequest,
    registry: Sequence[GoalRegistryEntry] | None = None,
    duplicate_threshold: float = 0.6,
) -> ClassificationResult:
    """Run full triage on a single IntakeRequest.

    Args:
        request: The incoming request to triage.
        registry: Existing goal registry entries for duplicate detection.
        duplicate_threshold: Jaccard similarity threshold for dup detection.

    Returns:
        ClassificationResult with domain, urgency, type, and dup info.
    """
    registry = registry or []

    domain, domain_conf = classify_domain(request)
    urgency, urgency_conf = classify_urgency(request)
    req_type, type_conf = classify_type(request)
    dup = check_duplicate(request, registry, duplicate_threshold)

    # Overall confidence is the average of individual confidences.
    confidence = round((domain_conf + urgency_conf + type_conf) / 3, 3)

    notes_parts = []
    if domain_conf < 0.3:
        notes_parts.append("low domain confidence")
    if urgency_conf < 0.3:
        notes_parts.append("urgency defaulted")
    if type_conf < 0.3:
        notes_parts.append("low type confidence")
    if dup.is_duplicate:
        notes_parts.append(f"possible duplicate of {dup.matched_goal_id}")

    return ClassificationResult(
        request_id=request.id,
        domain=domain,
        urgency=urgency,
        request_type=req_type,
        duplicate=dup,
        confidence=confidence,
        notes="; ".join(notes_parts) if notes_parts else "",
    )


def triage_batch(
    requests: Sequence[IntakeRequest],
    registry: Sequence[GoalRegistryEntry] | None = None,
    duplicate_threshold: float = 0.6,
) -> list[ClassificationResult]:
    """Triage multiple requests. Registry is checked as-is (no live updates)."""
    return [
        triage(r, registry, duplicate_threshold) for r in requests
    ]


// --- DUPLICATE BLOCK ---

"""Triage engine: classifies intake requests and checks for duplicates.

This module wires together the classifier and duplicate detector into
a single pipeline entry point.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from triage.models import (
    Classification,
    Domain,
    DuplicateMatch,
    RequestType,
    TriageResult,
    Urgency,
)

# ---------------------------------------------------------------------------
# Default registry location (relative to project root)
# ---------------------------------------------------------------------------
DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "goal_registry.json"


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------
# Keyword maps — simple rule-based classifier suitable for bootstrap.
# Each entry: (keyword_pattern, weight)

_DOMAIN_KEYWORDS: dict[Domain, list[tuple[str, float]]] = {
    Domain.META: [
        (r"\bjob[- ]?star\b", 3.0),
        (r"\bbootstrap\b", 2.0),
        (r"\bself[- ]?improv", 2.0),
        (r"\bmeta\b", 1.5),
        (r"\btriage\b", 2.0),
        (r"\bintake\b", 1.5),
    ],
    Domain.DEV: [
        (r"\bcode\b", 1.0),
        (r"\bfunction\b", 1.0),
        (r"\bclass\b", 1.0),
        (r"\bapi\b", 1.5),
        (r"\bbug\b", 1.5),
        (r"\btest\b", 1.0),
        (r"\brefactor\b", 1.5),
        (r"\bimplement\b", 1.0),
        (r"\bpython\b", 1.5),
    ],
    Domain.RESEARCH: [
        (r"\binvestigat", 2.0),
        (r"\banalyz", 1.5),
        (r"\bresearch\b", 2.0),
        (r"\bstudy\b", 1.5),
        (r"\bevaluat", 1.5),
        (r"\bcompar", 1.0),
    ],
    Domain.WRITING: [
        (r"\bdocument", 2.0),
        (r"\bwrite\b", 1.0),
        (r"\bREADME\b", 2.0),
        (r"\bguide\b", 1.5),
        (r"\btutorial\b", 1.5),
        (r"\bcontent\b", 1.0),
    ],
    Domain.OPS: [
        (r"\bdeploy", 2.0),
        (r"\bconfig", 1.5),
        (r"\bCI\b", 2.0),
        (r"\bmigrat", 2.0),
        (r"\bbackup\b", 2.0),
        (r"\bmonitor", 1.5),
        (r"\bserver\b", 1.0),
    ],
}

_URGENCY_KEYWORDS: dict[Urgency, list[tuple[str, float]]] = {
    Urgency.NOW: [
        (r"\bblock", 3.0),
        (r"\bcritical\b", 3.0),
        (r"\bbroken\b", 2.5),
        (r"\bdown\b", 2.5),
        (r"\bcrash", 2.5),
        (r"\burgent\b", 2.0),
        (r"\bASAP\b", 2.5),
        (r"\bcan'?t work\b", 2.5),
    ],
    Urgency.SOON: [
        (r"\bimportant\b", 2.0),
        (r"\bsoon\b", 2.0),
        (r"\bnext\b", 1.5),
        (r"\bshould\b", 1.0),
        (r"\bpriority\b", 1.5),
    ],
    Urgency.LATER: [
        (r"\blater\b", 2.0),
        (r"\bwhen\b", 1.0),
        (r"\beventually\b", 2.0),
        (r"\bbacklog\b", 2.0),
    ],
    Urgency.BACKLOG: [
        (r"\bnice to have\b", 2.5),
        (r"\boptional\b", 2.0),
        (r"\bsomeday\b", 2.0),
        (r"\bif time\b", 2.0),
    ],
}

_TYPE_KEYWORDS: dict[RequestType, list[tuple[str, float]]] = {
    RequestType.BUG: [
        (r"\bbug\b", 3.0),
        (r"\berror\b", 2.0),
        (r"\bfix\b", 2.0),
        (r"\bbroken\b", 2.5),
        (r"\bcrash", 2.5),
        (r"\bfail", 1.5),
        (r"\bwrong\b", 1.5),
    ],
    RequestType.FEATURE: [
        (r"\badd\b", 1.5),
        (r"\bfeature\b", 3.0),
        (r"\bimplement\b", 2.0),
        (r"\bcreate\b", 1.0),
        (r"\bsupport\b", 1.5),
        (r"\benable\b", 1.5),
    ],
    RequestType.REFACTOR: [
        (r"\brefactor\b", 3.0),
        (r"\bclean up\b", 2.5),
        (r"\brestructur", 2.5),
        (r"\bsimplify\b", 2.0),
        (r"\breorganiz", 2.0),
    ],
    RequestType.QUESTION: [
        (r"\bhow\b", 1.5),
        (r"\bwhy\b", 1.5),
        (r"\bwhat\b", 1.0),
        (r"\bquestion\b", 3.0),
        (r"\bexplain\b", 2.0),
        (r"\?\s*$", 1.5),
    ],
    RequestType.DOCS: [
        (r"\bdocument", 3.0),
        (r"\bREADME\b", 3.0),
        (r"\bguide\b", 2.0),
        (r"\bwiki\b", 2.5),
        (r"\bcomment", 1.5),
    ],
    RequestType.RESEARCH: [
        (r"\binvestigat", 3.0),
        (r"\bresearch\b", 3.0),
        (r"\bstudy\b", 2.0),
        (r"\bevaluat", 2.0),
        (r"\bspike\b", 2.5),
    ],
    RequestType.CHORE: [
        (r"\bupdat", 1.5),
        (r"\bupgrad", 2.0),
        (r"\bmainten", 2.5),
        (r"\bchore\b", 3.0),
        (r"\bcleanup\b", 2.0),
        (r"\bdependenc", 2.0),
    ],
}


def _score_categories(
    text: str,
    keyword_map: dict,
) -> tuple[object, float, list[str]]:
    """Score text against a keyword map and return (best_category, score, matched)."""
    text_lower = text.lower()
    scores: dict[object, float] = {}
    matched: dict[object, list[str]] = {}

    for category, patterns in keyword_map.items():
        for pattern, weight in patterns:
            if re.search(pattern, text_lower, re.IGNORECASE):
                scores[category] = scores.get(category, 0.0) + weight
                matched.setdefault(category, []).append(pattern)

    if not scores:
        # Return the first category as default (UNKNOWN-ish)
        return None, 0.0, []

    best = max(scores, key=lambda k: scores[k])
    total = sum(scores.values())
    confidence = scores[best] / total if total > 0 else 0.0
    return best, confidence, matched.get(best, [])


def classify(text: str) -> Classification:
    """Classify intake text into domain, urgency, and type."""
    domain, d_conf, d_kw = _score_categories(text, _DOMAIN_KEYWORDS)
    urgency, u_conf, u_kw = _score_categories(text, _URGENCY_KEYWORDS)
    req_type, t_conf, t_kw = _score_categories(text, _TYPE_KEYWORDS)

    # Defaults when no signal
    if domain is None:
        domain, d_conf, d_kw = Domain.UNKNOWN, 0.0, []
    if urgency is None:
        urgency, u_conf, u_kw = Urgency.LATER, 0.0, []
    if req_type is None:
        req_type, t_conf, t_kw = RequestType.UNKNOWN, 0.0, []

    # Overall confidence is the average of the three
    overall_confidence = (d_conf + u_conf + t_conf) / 3.0

    return Classification(
        domain=domain,
        urgency=urgency,
        request_type=req_type,
        confidence=round(overall_confidence, 3),
        matched_keywords=d_kw + u_kw + t_kw,
    )


# ---------------------------------------------------------------------------
# Duplicate Detector
# ---------------------------------------------------------------------------

def _normalize(text: str) -> set[str]:
    """Normalize text into a set of lowercase tokens for similarity comparison."""
    # Remove punctuation, split on whitespace
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    # Filter very short tokens
    return {t for t in tokens if len(t) > 2}


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def _load_registry(registry_path: Optional[Path]) -> list[dict]:
    """Load the goal registry from a JSON file.

    Expected format: a list of goal objects, each with at least
    'id' and 'title' fields. Also accepts 'description'.
    """
    path = registry_path or DEFAULT_REGISTRY_PATH
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "goals" in data:
            return data["goals"]
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def find_duplicates(
    text: str,
    registry_path: Optional[Path] = None,
    threshold: float = 0.4,
) -> list[DuplicateMatch]:
    """Check intake text against the goal registry for duplicates.

    Uses Jaccard token similarity on title + description.
    Returns matches above the threshold, sorted by similarity descending.
    """
    registry = _load_registry(registry_path)
    if not registry:
        return []

    intake_tokens = _normalize(text)
    matches: list[DuplicateMatch] = []

    for goal in registry:
        goal_title = goal.get("title", "")
        goal_desc = goal.get("description", "")
        goal_text = f"{goal_title} {goal_desc}"
        goal_tokens = _normalize(goal_text)

        similarity = _jaccard_similarity(intake_tokens, goal_tokens)

        if similarity >= threshold:
            # Build a human-readable reason
            shared = intake_tokens & goal_tokens
            reason = (
                f"Shares {len(shared)} tokens with '{goal_title}' "
                f"(similarity={similarity:.2f})"
            )
            matches.append(DuplicateMatch(
                goal_id=goal.get("id", "unknown"),
                title=goal_title,
                similarity=round(similarity, 3),
                reason=reason,
            ))

    matches.sort(key=lambda m: m.similarity, reverse=True)
    return matches


# ---------------------------------------------------------------------------
# Suggestion Generator
# ---------------------------------------------------------------------------

def _generate_suggestion(result: TriageResult) -> str:
    """Generate a human-readable suggestion based on triage results."""
    parts: list[str] = []

    if result.is_duplicate:
        top = result.duplicates[0]
        parts.append(
            f"Likely duplicate of goal {top.goal_id} ('{top.title}'). "
            f"Consider linking or closing."
        )

    cls = result.classification

    if cls.confidence < 0.3:
        parts.append(
            "Low classification confidence — manual review recommended."
        )

    if cls.domain == Domain.UNKNOWN:
        parts.append("Could not determine domain — please specify.")

    if cls.urgency == Urgency.NOW:
        parts.append("High urgency — consider immediate pickup.")
    elif cls.urgency == Urgency.BACKLOG:
        parts.append("Low urgency — suitable for backlog.")

    if not parts:
        parts.append(
            f"Classified as {cls.request_type.value} in {cls.domain.value} domain. "
            f"Ready for goal creation."
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def triage_request(
    text: str,
    registry_path: Optional[Path | str] = None,
    duplicate_threshold: float = 0.4,
) -> TriageResult:
    """Triage an intake request.

    Runs classification (domain, urgency, type) and duplicate detection
    against the goal registry, returning a combined TriageResult.

    Args:
        text: Raw intake text to triage.
        registry_path: Path to the goal registry JSON file. If None,
            uses the default location at data/goal_registry.json.
        duplicate_threshold: Jaccard similarity threshold for duplicate
            detection. Defaults to 0.4.

    Returns:
        TriageResult with classification, duplicates, and suggestion.
    """
    # Normalize registry_path to Path
    path = Path(registry_path) if registry_path else None

    # Step 1: Classify
    classification = classify(text)

    # Step 2: Check for duplicates
    duplicates = find_duplicates(text, path, threshold=duplicate_threshold)
    is_duplicate = len(duplicates) > 0

    # Step 3: Build result
    result = TriageResult(
        text=text,
        classification=classification,
        duplicates=duplicates,
        is_duplicate=is_duplicate,
    )

    # Step 4: Generate suggestion
    result.suggestion = _generate_suggestion(result)

    return result


// --- DUPLICATE BLOCK ---

"""The main triage engine: ties classification + dedup together."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .classifier import (
    classify_domain,
    classify_type,
    classify_urgency,
    suggest_tags,
)
from .config import TriageConfig
from .duplicate_checker import check_duplicates
from .models import GoalRef, IntakeRequest, TriageResult


class TriageEngine:
    """Classifies intake requests and checks for duplicates.

    Usage:
        engine = TriageEngine.from_yaml("config/triage_config.yaml")
        result = engine.triage(request, goals=existing_goals)
    """

    def __init__(self, config: TriageConfig):
        self.config = config

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TriageEngine":
        return cls(TriageConfig.from_yaml(path))

    @classmethod
    def from_dict(cls, raw: dict) -> "TriageEngine":
        return cls(TriageConfig.from_dict(raw))

    def triage(
        self,
        request: IntakeRequest,
        goals: Iterable[GoalRef] | None = None,
    ) -> TriageResult:
        """Triage a single request."""
        domain = classify_domain(request, self.config)
        urgency = classify_urgency(request, self.config)
        req_type = classify_type(request, self.config)
        tags = suggest_tags(request, self.config)

        notes: list[str] = []
        dup_match = None
        if goals is not None:
            goals_list = list(goals)
            dup_match = check_duplicates(request, goals_list, self.config)
            if dup_match:
                notes.append(f"Duplicate detected: {dup_match.reason}")

        # Add notes for low-confidence classifications
        if domain.confidence < 0.5:
            notes.append(
                f"Low confidence domain classification ({domain.confidence}); "
                "consider manual review."
            )
        if req_type.label == "unknown":
            notes.append("Could not determine request type; defaulting to 'task'.")

        return TriageResult(
            request_id=request.id,
            domain=domain,
            urgency=urgency,
            type=req_type,
            suggested_tags=tags,
            duplicate_of=dup_match,
            notes=notes,
        )

    def triage_batch(
        self,
        requests: Iterable[IntakeRequest],
        goals: Iterable[GoalRef] | None = None,
    ) -> list[TriageResult]:
        """Triage multiple requests. Goals are shared across the batch."""
        goals_list = list(goals) if goals is not None else None
        return [self.triage(r, goals_list) for r in requests]


// --- DUPLICATE BLOCK ---

"""High-level triage orchestrator.

Ties together the classifier and duplicate detector, providing
a simple API for the rest of Job-Star to use.
"""

from __future__ import annotations

from .classifier import Classifier
from .models import ClassificationResult, IntakeRequest
from .registry import DuplicateDetector, GoalRegistry, InMemoryGoalRegistry


class TriageEngine:
    """Main entry point for triaging intake requests."""

    def __init__(
        self,
        registry: GoalRegistry | None = None,
    ) -> None:
        self._registry = registry or InMemoryGoalRegistry()
        self._dup_detector = DuplicateDetector(self._registry)
        self._classifier = Classifier(self._dup_detector)

    @property
    def registry(self) -> GoalRegistry:
        return self._registry

    def triage(self, request: IntakeRequest) -> ClassificationResult:
        """Classify a single request."""
        return self._classifier.classify(request)

    def triage_batch(
        self, requests: list[IntakeRequest]
    ) -> list[ClassificationResult]:
        """Classify multiple requests."""
        return [self.triage(r) for r in requests]

    def triage_and_report(
        self, request: IntakeRequest
    ) -> tuple[ClassificationResult, str]:
        """Classify and return a human-readable summary."""
        result = self.triage(request)

        lines = [
            f"Triage Report for: {request.title}",
            f"  Domain:     {result.domain.value}",
            f"  Urgency:    {result.urgency.value}",
            f"  Type:       {result.request_type.value}",
            f"  Confidence: {result.confidence:.1%}",
            f"  Duplicate:  {result.duplicate_status.value}",
        ]

        if result.duplicate_of:
            lines.append(f"  Dup Of:     {result.duplicate_of}")
        if result.related_goals:
            lines.append(f"  Related:    {', '.join(result.related_goals)}")
        if result.matched_signals:
            lines.append(f"  Signals:    {', '.join(result.matched_signals)}")

        return result, "\n".join(lines)


// --- DUPLICATE BLOCK ---

"""Triage engine — ties classifiers and duplicate detection together."""

from __future__ import annotations

from typing import Sequence

from .classifiers import classify_domain, classify_type, classify_urgency
from .duplicate import check_duplicate
from .models import (
    ClassificationResult,
    GoalRegistryEntry,
    IntakeRequest,
)


def triage(
    request: IntakeRequest,
    registry: Sequence[GoalRegistryEntry] | None = None,
    duplicate_threshold: float = 0.6,
) -> ClassificationResult:
    """Run full triage on a single IntakeRequest.

    Args:
        request: The incoming request to triage.
        registry: Existing goal registry entries for duplicate detection.
        duplicate_threshold: Jaccard similarity threshold for dup detection.

    Returns:
        ClassificationResult with domain, urgency, type, and dup info.
    """
    registry = registry or []

    domain, domain_conf = classify_domain(request)
    urgency, urgency_conf = classify_urgency(request)
    req_type, type_conf = classify_type(request)
    dup = check_duplicate(request, registry, duplicate_threshold)

    # Overall confidence is the average of individual confidences.
    confidence = round((domain_conf + urgency_conf + type_conf) / 3, 3)

    notes_parts = []
    if domain_conf < 0.3:
        notes_parts.append("low domain confidence")
    if urgency_conf < 0.3:
        notes_parts.append("urgency defaulted")
    if type_conf < 0.3:
        notes_parts.append("low type confidence")
    if dup.is_duplicate:
        notes_parts.append(f"possible duplicate of {dup.matched_goal_id}")

    return ClassificationResult(
        request_id=request.id,
        domain=domain,
        urgency=urgency,
        request_type=req_type,
        duplicate=dup,
        confidence=confidence,
        notes="; ".join(notes_parts) if notes_parts else "",
    )


def triage_batch(
    requests: Sequence[IntakeRequest],
    registry: Sequence[GoalRegistryEntry] | None = None,
    duplicate_threshold: float = 0.6,
) -> list[ClassificationResult]:
    """Triage multiple requests. Registry is checked as-is (no live updates)."""
    return [
        triage(r, registry, duplicate_threshold) for r in requests
    ]


// --- DUPLICATE BLOCK ---

"""Triage engine: classifies intake requests and checks for duplicates.

This module wires together the classifier and duplicate detector into
a single pipeline entry point.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

from triage.models import (
    Classification,
    Domain,
    DuplicateMatch,
    RequestType,
    TriageResult,
    Urgency,
)

# ---------------------------------------------------------------------------
# Default registry location (relative to project root)
# ---------------------------------------------------------------------------
DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parent.parent / "data" / "goal_registry.json"


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------
# Keyword maps — simple rule-based classifier suitable for bootstrap.
# Each entry: (keyword_pattern, weight)

_DOMAIN_KEYWORDS: dict[Domain, list[tuple[str, float]]] = {
    Domain.META: [
        (r"\bjob[- ]?star\b", 3.0),
        (r"\bbootstrap\b", 2.0),
        (r"\bself[- ]?improv", 2.0),
        (r"\bmeta\b", 1.5),
        (r"\btriage\b", 2.0),
        (r"\bintake\b", 1.5),
    ],
    Domain.DEV: [
        (r"\bcode\b", 1.0),
        (r"\bfunction\b", 1.0),
        (r"\bclass\b", 1.0),
        (r"\bapi\b", 1.5),
        (r"\bbug\b", 1.5),
        (r"\btest\b", 1.0),
        (r"\brefactor\b", 1.5),
        (r"\bimplement\b", 1.0),
        (r"\bpython\b", 1.5),
    ],
    Domain.RESEARCH: [
        (r"\binvestigat", 2.0),
        (r"\banalyz", 1.5),
        (r"\bresearch\b", 2.0),
        (r"\bstudy\b", 1.5),
        (r"\bevaluat", 1.5),
        (r"\bcompar", 1.0),
    ],
    Domain.WRITING: [
        (r"\bdocument", 2.0),
        (r"\bwrite\b", 1.0),
        (r"\bREADME\b", 2.0),
        (r"\bguide\b", 1.5),
        (r"\btutorial\b", 1.5),
        (r"\bcontent\b", 1.0),
    ],
    Domain.OPS: [
        (r"\bdeploy", 2.0),
        (r"\bconfig", 1.5),
        (r"\bCI\b", 2.0),
        (r"\bmigrat", 2.0),
        (r"\bbackup\b", 2.0),
        (r"\bmonitor", 1.5),
        (r"\bserver\b", 1.0),
    ],
}

_URGENCY_KEYWORDS: dict[Urgency, list[tuple[str, float]]] = {
    Urgency.NOW: [
        (r"\bblock", 3.0),
        (r"\bcritical\b", 3.0),
        (r"\bbroken\b", 2.5),
        (r"\bdown\b", 2.5),
        (r"\bcrash", 2.5),
        (r"\burgent\b", 2.0),
        (r"\bASAP\b", 2.5),
        (r"\bcan'?t work\b", 2.5),
    ],
    Urgency.SOON: [
        (r"\bimportant\b", 2.0),
        (r"\bsoon\b", 2.0),
        (r"\bnext\b", 1.5),
        (r"\bshould\b", 1.0),
        (r"\bpriority\b", 1.5),
    ],
    Urgency.LATER: [
        (r"\blater\b", 2.0),
        (r"\bwhen\b", 1.0),
        (r"\beventually\b", 2.0),
        (r"\bbacklog\b", 2.0),
    ],
    Urgency.BACKLOG: [
        (r"\bnice to have\b", 2.5),
        (r"\boptional\b", 2.0),
        (r"\bsomeday\b", 2.0),
        (r"\bif time\b", 2.0),
    ],
}

_TYPE_KEYWORDS: dict[RequestType, list[tuple[str, float]]] = {
    RequestType.BUG: [
        (r"\bbug\b", 3.0),
        (r"\berror\b", 2.0),
        (r"\bfix\b", 2.0),
        (r"\bbroken\b", 2.5),
        (r"\bcrash", 2.5),
        (r"\bfail", 1.5),
        (r"\bwrong\b", 1.5),
    ],
    RequestType.FEATURE: [
        (r"\badd\b", 1.5),
        (r"\bfeature\b", 3.0),
        (r"\bimplement\b", 2.0),
        (r"\bcreate\b", 1.0),
        (r"\bsupport\b", 1.5),
        (r"\benable\b", 1.5),
    ],
    RequestType.REFACTOR: [
        (r"\brefactor\b", 3.0),
        (r"\bclean up\b", 2.5),
        (r"\brestructur", 2.5),
        (r"\bsimplify\b", 2.0),
        (r"\breorganiz", 2.0),
    ],
    RequestType.QUESTION: [
        (r"\bhow\b", 1.5),
        (r"\bwhy\b", 1.5),
        (r"\bwhat\b", 1.0),
        (r"\bquestion\b", 3.0),
        (r"\bexplain\b", 2.0),
        (r"\?\s*$", 1.5),
    ],
    RequestType.DOCS: [
        (r"\bdocument", 3.0),
        (r"\bREADME\b", 3.0),
        (r"\bguide\b", 2.0),
        (r"\bwiki\b", 2.5),
        (r"\bcomment", 1.5),
    ],
    RequestType.RESEARCH: [
        (r"\binvestigat", 3.0),
        (r"\bresearch\b", 3.0),
        (r"\bstudy\b", 2.0),
        (r"\bevaluat", 2.0),
        (r"\bspike\b", 2.5),
    ],
    RequestType.CHORE: [
        (r"\bupdat", 1.5),
        (r"\bupgrad", 2.0),
        (r"\bmainten", 2.5),
        (r"\bchore\b", 3.0),
        (r"\bcleanup\b", 2.0),
        (r"\bdependenc", 2.0),
    ],
}


def _score_categories(
    text: str,
    keyword_map: dict,
) -> tuple[object, float, list[str]]:
    """Score text against a keyword map and return (best_category, score, matched)."""
    text_lower = text.lower()
    scores: dict[object, float] = {}
    matched: dict[object, list[str]] = {}

    for category, patterns in keyword_map.items():
        for pattern, weight in patterns:
            if re.search(pattern, text_lower, re.IGNORECASE):
                scores[category] = scores.get(category, 0.0) + weight
                matched.setdefault(category, []).append(pattern)

    if not scores:
        # Return the first category as default (UNKNOWN-ish)
        return None, 0.0, []

    best = max(scores, key=lambda k: scores[k])
    total = sum(scores.values())
    confidence = scores[best] / total if total > 0 else 0.0
    return best, confidence, matched.get(best, [])


def classify(text: str) -> Classification:
    """Classify intake text into domain, urgency, and type."""
    domain, d_conf, d_kw = _score_categories(text, _DOMAIN_KEYWORDS)
    urgency, u_conf, u_kw = _score_categories(text, _URGENCY_KEYWORDS)
    req_type, t_conf, t_kw = _score_categories(text, _TYPE_KEYWORDS)

    # Defaults when no signal
    if domain is None:
        domain, d_conf, d_kw = Domain.UNKNOWN, 0.0, []
    if urgency is None:
        urgency, u_conf, u_kw = Urgency.LATER, 0.0, []
    if req_type is None:
        req_type, t_conf, t_kw = RequestType.UNKNOWN, 0.0, []

    # Overall confidence is the average of the three
    overall_confidence = (d_conf + u_conf + t_conf) / 3.0

    return Classification(
        domain=domain,
        urgency=urgency,
        request_type=req_type,
        confidence=round(overall_confidence, 3),
        matched_keywords=d_kw + u_kw + t_kw,
    )


# ---------------------------------------------------------------------------
# Duplicate Detector
# ---------------------------------------------------------------------------

def _normalize(text: str) -> set[str]:
    """Normalize text into a set of lowercase tokens for similarity comparison."""
    # Remove punctuation, split on whitespace
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    # Filter very short tokens
    return {t for t in tokens if len(t) > 2}


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def _load_registry(registry_path: Optional[Path]) -> list[dict]:
    """Load the goal registry from a JSON file.

    Expected format: a list of goal objects, each with at least
    'id' and 'title' fields. Also accepts 'description'.
    """
    path = registry_path or DEFAULT_REGISTRY_PATH
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "goals" in data:
            return data["goals"]
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def find_duplicates(
    text: str,
    registry_path: Optional[Path] = None,
    threshold: float = 0.4,
) -> list[DuplicateMatch]:
    """Check intake text against the goal registry for duplicates.

    Uses Jaccard token similarity on title + description.
    Returns matches above the threshold, sorted by similarity descending.
    """
    registry = _load_registry(registry_path)
    if not registry:
        return []

    intake_tokens = _normalize(text)
    matches: list[DuplicateMatch] = []

    for goal in registry:
        goal_title = goal.get("title", "")
        goal_desc = goal.get("description", "")
        goal_text = f"{goal_title} {goal_desc}"
        goal_tokens = _normalize(goal_text)

        similarity = _jaccard_similarity(intake_tokens, goal_tokens)

        if similarity >= threshold:
            # Build a human-readable reason
            shared = intake_tokens & goal_tokens
            reason = (
                f"Shares {len(shared)} tokens with '{goal_title}' "
                f"(similarity={similarity:.2f})"
            )
            matches.append(DuplicateMatch(
                goal_id=goal.get("id", "unknown"),
                title=goal_title,
                similarity=round(similarity, 3),
                reason=reason,
            ))

    matches.sort(key=lambda m: m.similarity, reverse=True)
    return matches


# ---------------------------------------------------------------------------
# Suggestion Generator
# ---------------------------------------------------------------------------

def _generate_suggestion(result: TriageResult) -> str:
    """Generate a human-readable suggestion based on triage results."""
    parts: list[str] = []

    if result.is_duplicate:
        top = result.duplicates[0]
        parts.append(
            f"Likely duplicate of goal {top.goal_id} ('{top.title}'). "
            f"Consider linking or closing."
        )

    cls = result.classification

    if cls.confidence < 0.3:
        parts.append(
            "Low classification confidence — manual review recommended."
        )

    if cls.domain == Domain.UNKNOWN:
        parts.append("Could not determine domain — please specify.")

    if cls.urgency == Urgency.NOW:
        parts.append("High urgency — consider immediate pickup.")
    elif cls.urgency == Urgency.BACKLOG:
        parts.append("Low urgency — suitable for backlog.")

    if not parts:
        parts.append(
            f"Classified as {cls.request_type.value} in {cls.domain.value} domain. "
            f"Ready for goal creation."
        )

    return " ".join(parts)


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def triage_request(
    text: str,
    registry_path: Optional[Path | str] = None,
    duplicate_threshold: float = 0.4,
) -> TriageResult:
    """Triage an intake request.

    Runs classification (domain, urgency, type) and duplicate detection
    against the goal registry, returning a combined TriageResult.

    Args:
        text: Raw intake text to triage.
        registry_path: Path to the goal registry JSON file. If None,
            uses the default location at data/goal_registry.json.
        duplicate_threshold: Jaccard similarity threshold for duplicate
            detection. Defaults to 0.4.

    Returns:
        TriageResult with classification, duplicates, and suggestion.
    """
    # Normalize registry_path to Path
    path = Path(registry_path) if registry_path else None

    # Step 1: Classify
    classification = classify(text)

    # Step 2: Check for duplicates
    duplicates = find_duplicates(text, path, threshold=duplicate_threshold)
    is_duplicate = len(duplicates) > 0

    # Step 3: Build result
    result = TriageResult(
        text=text,
        classification=classification,
        duplicates=duplicates,
        is_duplicate=is_duplicate,
    )

    # Step 4: Generate suggestion
    result.suggestion = _generate_suggestion(result)

    return result
