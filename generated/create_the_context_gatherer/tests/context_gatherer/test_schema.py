"""Smoke tests for the context gatherer schema.

These verify the contract is usable: round-trip serialization, optionality,
and the convenience helpers behave. They do not test the gatherer itself.
"""

from datetime import datetime, timedelta
from pathlib import Path

from context_gatherer.schema import (
    ContextBundle,
    ErrorEntry,
    FileMatch,
    GathererStats,
    GitHistory,
    IntakeRequest,
    RequestKind,
)


def make_request(**overrides) -> IntakeRequest:
    base = dict(
        id="req-1",
        title="Login button throws 500",
        description="Clicking login on staging returns a 500.",
        kind=RequestKind.BUG,
        repo_path=Path("/repos/app"),
        hints=["auth", "login"],
    )
    base.update(overrides)
    return IntakeRequest(**base)


def test_minimal_bundle_only_request():
    req = make_request()
    bundle = ContextBundle(request=req)
    assert bundle.is_empty()
    assert bundle.top_files() == []
    assert bundle.top_errors() == []


def test_bundle_round_trips_json():
    req = make_request()
    now = datetime.utcnow()
    bundle = ContextBundle(
        request=req,
        files=[
            FileMatch(
                path=Path("src/auth/login.py"),
                reason="keyword match: login",
                score=0.9,
                snippet="def login(...):",
                language="python",
            )
        ],
        git_history=[
            GitHistory(
                commit_hash="abc123",
                author="alice",
                authored_at=now - timedelta(days=1),
                summary="Refactor login handler",
                files_changed=[Path("src/auth/login.py")],
                relevance=0.8,
                touches_matched_files=True,
            )
        ],
        errors=[
            ErrorEntry(
                source="logs/app.log",
                message="KeyError: 'user'",
                timestamp=now - timedelta(hours=2),
                file=Path("src/auth/login.py"),
                line=42,
                score=0.75,
            )
        ],
        stats=GathererStats(
            started_at=now - timedelta(seconds=5),
            finished_at=now,
            files_considered=120,
            git_commits_scanned=30,
            errors_scanned=400,
            sources_tried=["filesystem", "git", "logs"],
            warnings=[],
        ),
    )

    data = bundle.model_dump_json()
    restored = ContextBundle.model_validate_json(data)

    assert restored.request.id == "req-1"
    assert len(restored.files) == 1
    assert restored.files[0].score == 0.9
    assert restored.git_history[0].touches_matched_files is True
    assert restored.errors[0].line == 42
    assert restored.stats is not None
    assert restored.stats.duration_seconds >= 0
    assert not restored.is_empty()
    assert restored.top_files(1)[0].path == Path("src/auth/login.py")


def test_request_kind_serializes_as_string():
    req = make_request(kind=RequestKind.FEATURE)
    data = req.model_dump()
    # use_enum_values=True means kind is stored as its string value
    assert data["kind"] == "feature"


def test_optional_repo_path_allows_none():
    req = make_request(repo_path=None)
    assert req.repo_path is None
    bundle = ContextBundle(request=req)
    assert bundle.is_empty()


def test_scores_are_bounded():
    import pytest

    with pytest.raises(ValueError):
        FileMatch(path=Path("x"), reason="r", score=1.5)
    with pytest.raises(ValueError):
        ErrorEntry(source="s", message="m", score=-0.1)
    with pytest.raises(ValueError):
        GitHistory(commit_hash="x", relevance=2.0)
