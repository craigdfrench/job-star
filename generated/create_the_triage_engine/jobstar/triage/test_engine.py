"""Tests for the Job-Star triage engine."""

import pytest
from job_star.triage.engine import (
    triage,
    IntakeRequest,
    Classification,
    Domain,
    Urgency,
    RequestType,
)


class TestDomainClassification:
    def test_meta_domain(self):
        req = IntakeRequest(raw_text="Update the Job-Star triage system to handle new domains")
        result = triage(req)
        assert result.domain == Domain.META

    def test_code_domain(self):
        req = IntakeRequest(raw_text="Fix the bug in the user authentication endpoint")
        result = triage(req)
        assert result.domain == Domain.CODE

    def test_infra_domain(self):
        req = IntakeRequest(raw_text="Set up Docker deployment with Kubernetes")
        result = triage(req)
        assert result.domain == Domain.INFRA

    def test_docs_domain(self):
        req = IntakeRequest(raw_text="Write documentation for the new API guide")
        result = triage(req)
        assert result.domain == Domain.DOCS

    def test_security_domain(self):
        req = IntakeRequest(raw_text="Fix the authentication vulnerability in permissions")
        result = triage(req)
        assert result.domain == Domain.SECURITY

    def test_explicit_tag_overrides(self):
        req = IntakeRequest(raw_text="[domain:research] Fix the bug in the code")
        result = triage(req)
        assert result.domain == Domain.RESEARCH
        assert result.domain_confidence == 1.0


class TestUrgencyClassification:
    def test_now_urgency(self):
        req = IntakeRequest(raw_text="This is urgent, production is down and blocking everything")
        result = triage(req)
        assert result.urgency == Urgency.NOW

    def test_soon_urgency(self):
        req = IntakeRequest(raw_text="This is important and should be done soon")
        result = triage(req)
        assert result.urgency == Urgency.SOON

    def test_backlog_urgency(self):
        req = IntakeRequest(raw_text="Nice to have, put in backlog, low priority")
        result = triage(req)
        assert result.urgency == Urgency.BACKLOG

    def test_default_urgency(self):
        req = IntakeRequest(raw_text="Create a new function")
        result = triage(req)
        assert result.urgency == Urgency.SOON

    def test_hotfix_escalates_urgency(self):
        req = IntakeRequest(raw_text="Need a hotfix for the login module")
        result = triage(req)
        assert result.type == RequestType.HOTFIX
        assert result.urgency == Urgency.NOW


class TestTypeClassification:
    def test_bug_type(self):
        req = IntakeRequest(raw_text="The application crashes with an error when loading")
        result = triage(req)
        assert result.type == RequestType.BUG

    def test_feature_type(self):
        req = IntakeRequest(raw_text="Add support for new feature: export to PDF")
        result = triage(req)
        assert result.type == RequestType.FEATURE

    def test_question_type(self):
        req = IntakeRequest(raw_text="How do I configure the database connection?")
        result = triage(req)
        assert result.type == RequestType.QUESTION

    def test_refactor_type(self):
        req = IntakeRequest(raw_text="Refactor and simplify the existing code, reduce tech debt")
        result = triage(req)
        assert result.type == RequestType.REFACTOR

    def test_goal_type(self):
        req = IntakeRequest(raw_text="Goal: build out the entire milestone for user management")
        result = triage(req)
        assert result.type == RequestType.GOAL


class TestDuplicateDetection:
    def test_no_duplicates_empty_registry(self):
        req = IntakeRequest(raw_text="Build a new feature")
        result = triage(req, goal_registry=[])
        assert result.is_duplicate is False

    def test_exact_hash_duplicate(self):
        text = "Fix the login bug in the authentication module"
        req = IntakeRequest(raw_text=text)
        registry = [
            {"goal_id": "G-001", "raw_text": text, "text_hash": req.text_hash}
        ]
        result = triage(req, goal_registry=registry)
        assert result.is_duplicate is True
        assert result.duplicate_of == "G-001"
        assert result.duplicate_similarity == 1.0

    def test_similar_text_duplicate(self):
        req = IntakeRequest(
            raw_text="Fix the login bug in the authentication module"
        )
        registry = [
            {
                "goal_id": "G-002",
                "raw_text": "Fix login bug in authentication module",
            }
        ]
        result = triage(req, goal_registry=registry)
        assert result.is_duplicate is True
        assert result.duplicate_of == "G-002"

    def test_not_duplicate_different_text(self):
        req = IntakeRequest(raw_text="Build a new dashboard feature")
        registry = [
            {"goal_id": "G-003", "raw_text": "Fix the database connection timeout issue"}
        ]
        result = triage(req, goal_registry=registry)
        assert result.is_duplicate is False


class TestClassificationOutput:
    def test_to_dict_serialization(self):
        req = IntakeRequest(raw_text="Fix the urgent bug in code")
        result = triage(req)
        d = result.to_dict()
        assert "domain" in d
        assert "urgency" in d
        assert "type" in d
        assert isinstance(d["domain"], str)
        assert isinstance(d["urgency"], str)
        assert isinstance(d["type"], str)

    def test_summary_string(self):
        req = IntakeRequest(raw_text="Fix the urgent bug in code")
        result = triage(req)
        s = result.summary()
        assert "domain=" in s
        assert "urgency=" in s
        assert "type=" in s

    def test_text_hash_stable(self):
        req1 = IntakeRequest(raw_text="Fix the bug")
        req2 = IntakeRequest(raw_text="  Fix   the  bug  ")
        assert req1.text_hash == req2.text_hash
