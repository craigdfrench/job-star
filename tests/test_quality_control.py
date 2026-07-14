"""
Tests for the quality-control gate for goal completion.

These tests verify that the verification functions correctly assess
goal completeness, handle failure modes, and enforce the gate logic.
"""

import uuid
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime, timedelta

from job_star.db import get_pool, close_pool
from job_star.models import Goal, GoalStep
from job_star.orchestrator import verify_goal_completion, complete_goal
from job_star.checkin.engine import create_checkin


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
async def setup_db():
    """Ensure the test database is clean and pool is available."""
    pool = await get_pool()
    # Clean up any leftover test data
    await pool.execute("DELETE FROM goal_steps")
    await pool.execute("DELETE FROM check_ins")
    await pool.execute("DELETE FROM goals")
    yield
    await close_pool()


async def create_test_goal(pool, **overrides):
    """Helper to create a goal with default test values."""
    goal_id = overrides.get("id", uuid.uuid4())
    await pool.execute("""
        INSERT INTO goals (id, title, domain, status, urgency, progress, source, expert, requested_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
    """,
        goal_id,
        overrides.get("title", "Test Goal"),
        overrides.get("domain", "coding"),
        overrides.get("status", "active"),
        overrides.get("urgency", "soon"),
        overrides.get("progress", 0.0),
        overrides.get("source", "intake"),
        overrides.get("expert", None),
        overrides.get("requested_by", "test@example.com"),
    )
    return goal_id


async def create_test_step(pool, goal_id, **overrides):
    """Helper to create a step with default test values."""
    step_id = overrides.get("id", uuid.uuid4())
    await pool.execute("""
        INSERT INTO goal_steps (id, goal_id, title, description, status, order_index, result, model)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
    """,
        step_id,
        goal_id,
        overrides.get("title", "Test Step"),
        overrides.get("description", "A step"),
        overrides.get("status", "completed"),
        overrides.get("order_index", 0),
        overrides.get("result", None),
        overrides.get("model", "glm-5-2"),
    )
    return step_id


# ---------------------------------------------------------------------------
# Unit tests for verify_goal_completion
# ---------------------------------------------------------------------------

class TestVerifyGoalCompletion:
    """Tests for the core verification function."""

    @patch("job_star.orchestrator.subprocess.run")
    async def test_all_steps_evidenced_passes(self, mock_run, setup_db):
        """A goal with all steps properly evidenced should complete."""
        pool = await get_pool()
        goal_id = await create_test_goal(pool, status="active", progress=1.0)
        # Create a step with a result containing a commit hash
        step_id = await create_test_step(pool, goal_id, result={"commit": "abc123", "pr_url": "https://github.com/org/repo/pull/1"})

        # Mock git log to show a commit with passing tests
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="commit abc123\nAuthor: job-star\n    Fix login bug\n    Tests: all passing\n",
            stderr=""
        )

        result = await verify_goal_completion(goal_id)
        assert result["passed"] is True
        assert result["reason"] == "All evidence verified"

    @patch("job_star.orchestrator.subprocess.run")
    async def test_failing_test_rejected(self, mock_run, setup_db):
        """A goal with a failing test should move to failed_review."""
        pool = await get_pool()
        goal_id = await create_test_goal(pool, status="active", progress=1.0)
        step_id = await create_test_step(pool, goal_id, result={"commit": "abc123"})

        # Mock git log to show a commit with failing tests
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="commit abc123\nAuthor: job-star\n    Fix login bug\n    Tests: failing\n",
            stderr=""
        )

        result = await verify_goal_completion(goal_id)
        assert result["passed"] is False
        assert "failing" in result["reason"].lower()

    @patch("job_star.orchestrator.subprocess.run")
    async def test_no_commits_rejected(self, mock_run, setup_db):
        """A goal with no commits should be rejected."""
        pool = await get_pool()
        goal_id = await create_test_goal(pool, status="active", progress=1.0)
        step_id = await create_test_step(pool, goal_id, result={})  # no commit

        # Mock git log to return empty (no commits)
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr=""
        )

        result = await verify_goal_completion(goal_id)
        assert result["passed"] is False
        assert "no commits" in result["reason"].lower()

    @patch("job_star.orchestrator.subprocess.run")
    async def test_high_risk_goal_requires_approval(self, mock_run, setup_db):
        """A high-risk goal (infra domain) should require human approval."""
        pool = await get_pool()
        goal_id = await create_test_goal(pool, status="active", progress=1.0, domain="infra")
        step_id = await create_test_step(pool, goal_id, result={"commit": "abc123"})

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="commit abc123\nAuthor: job-star\n    Deploy new infra\n    Tests: all passing\n",
            stderr=""
        )

        result = await verify_goal_completion(goal_id)
        assert result["passed"] is False
        assert "approval" in result["reason"].lower()
        # Check that a check-in was created
        checkins = await pool.fetch("SELECT * FROM check_ins WHERE goal_id = $1", goal_id)
        assert len(checkins) > 0
        assert checkins[0]["checkin_type"] == "completion"

    @patch("job_star.orchestrator.subprocess.run")
    async def test_mostly_planning_rejected(self, mock_run, setup_db):
        """A goal that is mostly planning with no execution should be rejected."""
        pool = await get_pool()
        goal_id = await create_test_goal(pool, status="active", progress=1.0)
        # Create multiple steps that are all "plan" or "research"
        for i in range(3):
            await create_test_step(pool, goal_id, title=f"Plan phase {i}", result={"text": "planning document"})

        # No commits, no PRs
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr=""
        )

        result = await verify_goal_completion(goal_id)
        assert result["passed"] is False
        assert "planning" in result["reason"].lower()


# ---------------------------------------------------------------------------
# Integration tests for the orchestrator's complete_goal flow
# ---------------------------------------------------------------------------

class TestCompleteGoalFlow:
    """Integration tests for the full completion flow."""

    @patch("job_star.orchestrator.verify_goal_completion")
    async def test_complete_goal_with_verification_passes(self, mock_verify, setup_db):
        """When verification passes, the goal should be marked completed."""
        pool = await get_pool()
        goal_id = await create_test_goal(pool, status="active", progress=1.0)
        step_id = await create_test_step(pool, goal_id, result={"commit": "abc123"})

        mock_verify.return_value = {"passed": True, "reason": "All evidence verified"}

        await complete_goal(goal_id)

        # Check goal status
        goal = await pool.fetchrow("SELECT status, progress FROM goals WHERE id = $1", goal_id)
        assert goal["status"] == "completed"
        assert goal["progress"] == 1.0

    @patch("job_star.orchestrator.verify_goal_completion")
    async def test_complete_goal_with_verification_fails(self, mock_verify, setup_db):
        """When verification fails, the goal should move to failed_review."""
        pool = await get_pool()
        goal_id = await create_test_goal(pool, status="active", progress=1.0)
        step_id = await create_test_step(pool, goal_id, result={"commit": "abc123"})

        mock_verify.return_value = {"passed": False, "reason": "Tests failing"}

        await complete_goal(goal_id)

        # Check goal status
        goal = await pool.fetchrow("SELECT status, progress FROM goals WHERE id = $1", goal_id)
        assert goal["status"] == "failed_review"
        # Progress should not be 1.0 (maybe unchanged or set to 0.9)
        assert goal["progress"] < 1.0

    @patch("job_star.orchestrator.verify_goal_completion")
    async def test_complete_goal_creates_audit_entry(self, mock_verify, setup_db):
        """Completion attempts should be logged in the audit trail."""
        pool = await get_pool()
        goal_id = await create_test_goal(pool, status="active", progress=1.0)
        step_id = await create_test_step(pool, goal_id, result={"commit": "abc123"})

        mock_verify.return_value = {"passed": True, "reason": "All evidence verified"}

        await complete_goal(goal_id)

        # Check audit trail
        audit = await pool.fetchrow(
            "SELECT event, details FROM audit_trail WHERE goal_id = $1 AND event = 'goal_completed'",
            goal_id
        )
        assert audit is not None
        assert audit["details"]["verification_passed"] is True

    @patch("job_star.orchestrator.verify_goal_completion")
    async def test_complete_goal_failed_review_creates_checkin(self, mock_verify, setup_db):
        """When verification fails, a check-in should be created for the user."""
        pool = await get_pool()
        goal_id = await create_test_goal(pool, status="active", progress=1.0)
        step_id = await create_test_step(pool, goal_id, result={"commit": "abc123"})

        mock_verify.return_value = {"passed": False, "reason": "Tests failing"}

        await complete_goal(goal_id)

        # Check that a check-in was created
        checkins = await pool.fetch("SELECT * FROM check_ins WHERE goal_id = $1", goal_id)
        assert len(checkins) > 0
        assert checkins[0]["checkin_type"] == "clarification"
        assert "failed_review" in checkins[0]["message"].lower()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge cases for the quality-control gate."""

    @patch("job_star.orchestrator.subprocess.run")
    async def test_goal_with_no_steps(self, mock_run, setup_db):
        """A goal with no steps should not be completable."""
        pool = await get_pool()
        goal_id = await create_test_goal(pool, status="active", progress=0.0)

        result = await verify_goal_completion(goal_id)
        assert result["passed"] is False
        assert "no steps" in result["reason"].lower()

    @patch("job_star.orchestrator.subprocess.run")
    async def test_goal_with_pending_steps(self, mock_run, setup_db):
        """A goal with pending steps should not be verified."""
        pool = await get_pool()
        goal_id = await create_test_goal(pool, status="active", progress=0.5)
        step_id = await create_test_step(pool, goal_id, status="pending")

        result = await verify_goal_completion(goal_id)
        assert result["passed"] is False
        assert "pending" in result["reason"].lower()

    @patch("job_star.orchestrator.subprocess.run")
    async def test_goal_with_empty_result(self, mock_run, setup_db):
        """A step with an empty result should be flagged."""
        pool = await get_pool()
        goal_id = await create_test_goal(pool, status="active", progress=1.0)
        step_id = await create_test_step(pool, goal_id, result={})  # empty result

        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="",
            stderr=""
        )

        result = await verify_goal_completion(goal_id)
        assert result["passed"] is False
        assert "empty" in result["reason"].lower() or "no evidence" in result["reason"].lower()

    @patch("job_star.orchestrator.subprocess.run")
    async def test_goal_with_unmerged_pr(self, mock_run, setup_db):
        """A goal with a PR that is not merged should be rejected."""
        pool = await get_pool()
        goal_id = await create_test_goal(pool, status="active", progress=1.0)
        step_id = await create_test_step(pool, goal_id, result={"pr_url": "https://github.com/org/repo/pull/1"})

        # Mock git log to show a commit but PR not merged (simulate by checking PR status)
        # We'll mock the PR check function
        with patch("job_star.orchestrator.check_pr_merged") as mock_pr:
            mock_pr.return_value = False
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="commit abc123\nAuthor: job-star\n    Fix bug\n    Tests: all passing\n",
                stderr=""
            )
            result = await verify_goal_completion(goal_id)
            assert result["passed"] is False
            assert "merged" in result["reason"].lower()