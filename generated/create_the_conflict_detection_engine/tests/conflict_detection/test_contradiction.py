"""Tests for the contradiction detection engine."""

import pytest

from job_star.conflict_detection.types import Goal, ConflictSeverity, ConflictType
from job_star.conflict_detection.contradiction import ContradictionDetector


def _goal(id_: str, title: str, **kwargs) -> Goal:
    return Goal(id=id_, title=title, **kwargs)


# --- Lexical negation -------------------------------------------------------

def test_lexical_negation_increase_decrease():
    detector = ContradictionDetector()
    a = _goal("g1", "Increase response time budget", description="increase response time budget to 500ms")
    b = _goal("g2", "Decrease response time budget", description="decrease response time budget to 100ms")
    conflict = detector.detect(a, b)
    assert conflict is not None
    assert conflict.type == ConflictType.CONTRADICTION
    assert conflict.severity == ConflictSeverity.BLOCKING
    assert conflict.confidence >= 0.85


def test_lexical_negation_no_shared_subject_no_contradiction():
    detector = ContradictionDetector()
    a = _goal("g1", "Increase marketing spend", description="increase marketing spend")
    b = _goal("g2", "Decrease technical debt", description="decrease technical debt")
    # Different subjects — should not flag as contradiction via lexical strategy.
    conflict = detector.detect(a, b)
    # May still be caught by semantic strategy at low confidence; ensure not blocking.
    if conflict is not None:
        assert conflict.severity != ConflictSeverity.BLOCKING


# --- Directional opposition --------------------------------------------------

def test_directional_opposition_incompatible_thresholds():
    detector = ContradictionDetector()
    a = _goal("g1", "Reduce latency", description="latency should be below 100ms")
    b = _goal("g2", "Improve throughput", description="latency must be above 500ms")
    conflict = detector.detect(a, b)
    assert conflict is not None
    assert conflict.severity == ConflictSeverity.BLOCKING


def test_directional_opposition_compatible_thresholds_no_block():
    detector = ContradictionDetector()
    a = _goal("g1", "Reduce latency", description="latency should be below 500ms")
    b = _goal("g2", "Ensure minimum latency", description="latency must be above 100ms")
    conflict = detector.detect(a, b)
    # Thresholds overlap (100 < x < 500 is feasible) — not blocking.
    if conflict is not None:
        assert conflict.severity != ConflictSeverity.BLOCKING


# --- State incompatibility ---------------------------------------------------

def test_state_incompatibility_primary():
    detector = ContradictionDetector()
    a = _goal("g1", "Make service A the primary", description="make service A the primary database")
    b = _goal("g2", "Make service B the primary", description="make service B the primary database")
    conflict = detector.detect(a, b)
    assert conflict is not None
    assert conflict.severity == ConflictSeverity.BLOCKING


def test_state_incompatibility_same_state_not_contradiction():
    detector = ContradictionDetector()
    a = _goal("g1", "Make service A the primary")
    b = _goal("g2", "Make service A the primary")
    conflict = detector.detect(a, b)
    # Same state on same subject — that's a duplicate, not a contradiction.
    if conflict is not None and conflict.type == ConflictType.CONTRADICTION:
        assert conflict.severity != ConflictSeverity.BLOCKING


# --- Constraint violation ---------------------------------------------------

def test_constraint_violation():
    detector = ContradictionDetector()
    a = _goal(
        "g1",
        "Deploy feature X",
        success_criteria=["deploy feature X to production"],
    )
    b = _goal(
        "g2",
        "Q3 stability freeze",
        constraints=["no production deployments in Q3"],
    )
    conflict = detector.detect(a, b)
    assert conflict is not None
    assert conflict.severity == ConflictSeverity.BLOCKING


# --- No contradiction -------------------------------------------------------

def test_unrelated_goals_no_contradiction():
    detector = ContradictionDetector()
    a = _goal("g1", "Learn Spanish", description="become conversational in Spanish")
    b = _goal("g2", "Run a marathon", description="complete a marathon this year")
    conflict = detector.detect(a, b)
    assert conflict is None


# --- detect_all -------------------------------------------------------------

def test_detect_all_finds_pair():
    detector = ContradictionDetector()
    goals = [
        _goal("g1", "Increase cache size", description="increase cache size to 10GB"),
        _goal("g2", "Decrease cache size", description="decrease cache size to 1GB"),
        _goal("g3", "Write documentation", description="write API docs"),
    ]
    conflicts = detector.detect_all(goals)
    assert len(conflicts) == 1
    assert {conflicts[0].goal_a_id, conflicts[0].goal_b_id} == {"g1", "g2"}
