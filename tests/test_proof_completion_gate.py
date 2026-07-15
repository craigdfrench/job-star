"""Tests for the proof-of-work completion gate.

Tests that the completion check-in:
  1. Escalates when no verified artifacts exist (text-only goals).
  2. Shows verified proof when artifacts verify.
  3. Handles the "send back for repairs" response by appending a repair step.
"""

from __future__ import annotations

import json
from unittest.mock import patch, AsyncMock, MagicMock

import pytest

from job_star.models import Goal, GoalStatus, Step, StepStatus, Artifact, Domain, Urgency
from job_star.checkin import CheckInType, CheckInStatus
from job_star.checkin.engine import (
    CheckInEngine,
    _collect_artifacts,
    _format_missing_proof,
    _format_artifacts_for_results,
)
from job_star.proof.verifier import VerificationResult


class TestCollectArtifacts:
    """Tests for artifact collection from step results."""

    def test_collects_from_completed_steps(self):
        """Artifacts from completed steps should be collected."""
        steps = [
            Step(
                id="s1", goal_id="g1", title="step 1",
                status=StepStatus.COMPLETED,
                result={"content": "done", "artifacts": [
                    {"kind": "pr", "value": "https://github.com/o/r/pull/1", "repo": "/repo"},
                    {"kind": "file", "value": "src/main.py", "repo": "/repo"},
                ]},
            ),
            Step(
                id="s2", goal_id="g1", title="step 2",
                status=StepStatus.COMPLETED,
                result={"content": "done", "artifacts": [
                    {"kind": "test_pass", "value": "pytest", "repo": "/repo"},
                ]},
            ),
        ]
        artifacts = _collect_artifacts(steps)
        assert len(artifacts) == 3
        assert artifacts[0].kind == "pr"
        assert artifacts[1].kind == "file"
        assert artifacts[2].kind == "test_pass"

    def test_ignores_non_completed_steps(self):
        """Artifacts from non-completed steps should be ignored."""
        steps = [
            Step(
                id="s1", goal_id="g1", title="step 1",
                status=StepStatus.FAILED,
                result={"content": "failed", "artifacts": [
                    {"kind": "pr", "value": "https://github.com/o/r/pull/1"},
                ]},
            ),
        ]
        artifacts = _collect_artifacts(steps)
        assert len(artifacts) == 0

    def test_ignores_steps_without_artifacts(self):
        """Steps with no artifacts key should be skipped."""
        steps = [
            Step(id="s1", goal_id="g1", title="step 1",
                 status=StepStatus.COMPLETED, result={"content": "just text"}),
        ]
        artifacts = _collect_artifacts(steps)
        assert len(artifacts) == 0

    def test_handles_empty_result(self):
        """Steps with no result should be skipped."""
        steps = [Step(id="s1", goal_id="g1", title="step 1", status=StepStatus.COMPLETED)]
        artifacts = _collect_artifacts(steps)
        assert len(artifacts) == 0


class TestFormatMissingProof:
    """Tests for the escalation message formatting."""

    def test_no_artifacts_message(self):
        """The message should say no artifacts were produced."""
        verification = VerificationResult(artifacts=[], verified_count=0, failed_count=0, unverified_count=0)
        msg = _format_missing_proof(MagicMock(), verification)
        assert "No verifiable artifacts" in msg
        assert "text output" in msg

    def test_failed_artifacts_message(self):
        """The message should list failed claims."""
        artifacts = [Artifact(kind="pr", value="https://github.com/o/r/pull/999",
                              verification_note="false: PR #999 not found")]
        verification = VerificationResult(artifacts=artifacts, verified_count=0, failed_count=1, unverified_count=0)
        msg = _format_missing_proof(MagicMock(), verification)
        assert "could not independently confirm" in msg
        assert "PR #999 not found" in msg


class TestFormatArtifactsForResults:
    """Tests for the verified-proof results formatting."""

    def test_verified_artifacts_displayed(self):
        """Verified artifacts should be shown with checkmarks."""
        artifacts = [
            Artifact(kind="pr", value="https://github.com/o/r/pull/1",
                     verified=True, verification_note="PR #1 merged"),
            Artifact(kind="file", value="src/main.py",
                     verified=True, verification_note="file exists"),
        ]
        verification = VerificationResult(artifacts=artifacts, verified_count=2, failed_count=0, unverified_count=0)
        text = _format_artifacts_for_results(verification)
        assert "✅" in text
        assert "PR #1 merged" in text
        assert "2 verified" in text


class TestCompletionGateEscalation:
    """Tests that the completion gate escalates when no proof exists."""

    async def test_escalation_for_text_only_goal(self):
        """A goal with no artifacts should get an escalation check-in."""
        goal = Goal(id="g1", title="text-only goal", domain=Domain.CODING)
        steps = [
            Step(id="s1", goal_id="g1", title="step 1",
                 status=StepStatus.COMPLETED, result={"content": "just text, no artifacts"}),
        ]

        engine = CheckInEngine()

        # Mock the AI content generation and DB
        async def mock_generate(*args, **kwargs):
            return {"progress_summary": "done", "next_steps": "", "results": "", "questions": []}

        mock_check_in = MagicMock()
        mock_check_in.id = "ci1"
        mock_check_in.goal_id = "g1"

        with patch("job_star.checkin.engine.generate_check_in_content", mock_generate), \
             patch("job_star.checkin.engine.create_check_in", new_callable=AsyncMock, return_value=mock_check_in) as mock_create, \
             patch("job_star.proof.WitnessClient") as mock_witness_class:
            mock_witness = MagicMock()
            mock_witness.health = AsyncMock(return_value=False)
            mock_witness_class.return_value = mock_witness

            result = await engine.create_completion_check_in(goal, steps)

        # Verify create_check_in was called with escalation-style results
        call_kwargs = mock_create.call_args.kwargs
        results_text = call_kwargs.get("results", "")
        assert "No verifiable artifacts" in results_text
        # The question should have 3 options including repair
        questions = call_kwargs.get("questions", [])
        assert len(questions) == 1
        assert "Send back for repairs" in questions[0].options
        assert "Accept" in questions[0].options

    async def test_repair_response_appends_step(self):
        """Choosing 'Send back for repairs' should append a repair step."""
        from job_star.checkin import CheckIn, CheckInQuestion
        from job_star.checkin.engine import CheckInEngine
        from job_star.db import StepStatus as SS

        goal = Goal(id="g1", title="test goal", status=GoalStatus.ACTIVE)

        check_in = CheckIn(
            id="ci1", goal_id="g1", type=CheckInType.COMPLETION,
            status=CheckInStatus.RESPONDED,
            questions=[CheckInQuestion(
                id="q1", question="test", type="choice",
                options=["Accept", "Reject", "Send back for repairs"],
            )],
            decisions=[{"question_id": "q1", "answer": "Send back for repairs"}],
            response="needs real proof",
        )

        engine = CheckInEngine()

        with patch("job_star.checkin.engine.get_check_in", AsyncMock(return_value=check_in)), \
             patch("job_star.checkin.engine.get_goal", AsyncMock(return_value=goal)), \
             patch("job_star.checkin.engine.update_goal_status", AsyncMock()) as mock_update, \
             patch("job_star.db.create_step", AsyncMock()) as mock_create_step, \
             patch("job_star.checkin.engine.record_decision", AsyncMock()), \
             patch("job_star.checkin.engine.action_check_in", AsyncMock()), \
             patch("job_star.checkin.engine.audit", AsyncMock()), \
             patch("job_star.checkin.engine.publish_event", AsyncMock()):

            result = await engine.process_response("ci1")

        assert "goal_sent_back_for_repairs" in result["actions"]
        # A repair step should have been created
        mock_create_step.assert_called_once()
        # The goal should have been re-activated
        mock_update.assert_any_call("g1", GoalStatus.ACTIVE)
        # Verify the repair step title
        call_args = mock_create_step.call_args
        title = call_args.kwargs.get("title") or ""
        assert "Repair" in title

    async def test_accept_response_completes_goal(self):
        """Choosing 'Accept' should mark the goal completed."""
        from job_star.checkin import CheckIn, CheckInQuestion
        from job_star.checkin.engine import CheckInEngine

        goal = Goal(id="g1", title="test goal", status=GoalStatus.ACTIVE)

        check_in = CheckIn(
            id="ci1", goal_id="g1", type=CheckInType.COMPLETION,
            status=CheckInStatus.RESPONDED,
            questions=[CheckInQuestion(
                id="q1", question="test", type="choice",
                options=["Accept", "Reject", "Send back for repairs"],
            )],
            decisions=[{"question_id": "q1", "answer": "Accept"}],
            response="looks good",
        )

        engine = CheckInEngine()

        with patch("job_star.checkin.engine.get_check_in", AsyncMock(return_value=check_in)), \
             patch("job_star.checkin.engine.get_goal", AsyncMock(return_value=goal)), \
             patch("job_star.checkin.engine.update_goal_status", AsyncMock()) as mock_update, \
             patch("job_star.checkin.engine.update_goal_progress", AsyncMock()), \
             patch("job_star.checkin.engine.record_decision", AsyncMock()), \
             patch("job_star.checkin.engine.action_check_in", AsyncMock()), \
             patch("job_star.checkin.engine.audit", AsyncMock()), \
             patch("job_star.checkin.engine.publish_event", AsyncMock()):

            result = await engine.process_response("ci1")

        assert "goal_accepted" in result["actions"]
        mock_update.assert_any_call("g1", GoalStatus.COMPLETED)

    async def test_reject_response_rejects_goal(self):
        """Choosing 'Reject' should reject the goal (not complete it)."""
        from job_star.checkin import CheckIn, CheckInQuestion
        from job_star.checkin.engine import CheckInEngine

        goal = Goal(id="g1", title="test goal", status=GoalStatus.ACTIVE)

        check_in = CheckIn(
            id="ci1", goal_id="g1", type=CheckInType.COMPLETION,
            status=CheckInStatus.RESPONDED,
            questions=[CheckInQuestion(
                id="q1", question="test", type="choice",
                options=["Accept", "Reject", "Send back for repairs"],
            )],
            decisions=[{"question_id": "q1", "answer": "Reject"}],
            response="not good enough",
        )

        engine = CheckInEngine()

        with patch("job_star.checkin.engine.get_check_in", AsyncMock(return_value=check_in)), \
             patch("job_star.checkin.engine.get_goal", AsyncMock(return_value=goal)), \
             patch("job_star.checkin.engine.update_goal_status", AsyncMock()) as mock_update, \
             patch("job_star.checkin.engine.record_decision", AsyncMock()), \
             patch("job_star.checkin.engine.action_check_in", AsyncMock()), \
             patch("job_star.checkin.engine.audit", AsyncMock()), \
             patch("job_star.checkin.engine.publish_event", AsyncMock()):

            result = await engine.process_response("ci1")

        assert "goal_rejected" in result["actions"]
        # Goal should NOT have been completed
        for call in mock_update.call_args_list:
            args, kwargs = call
            status = kwargs.get("status") or (args[1] if len(args) > 1 else None)
            assert status != GoalStatus.COMPLETED
