"""Tests for the keyword-based classifiers."""

import pytest

from job_star.triage.classifiers import (
    classify_domain,
    classify_type,
    classify_urgency,
)
from job_star.triage.models import Domain, IntakeRequest, RequestType, Urgency


class TestClassifyDomain:
    def test_meta(self):
        req = IntakeRequest(
            title="Improve Job-Star triage workflow",
            description="The agent bootstrap needs better triage",
        )
        domain, conf = classify_domain(req)
        assert domain == Domain.META
        assert conf > 0

    def test_backend(self):
        req = IntakeRequest(
            title="Add new API endpoint for users",
            description="Need a server route with database query",
        )
        domain, conf = classify_domain(req)
        assert domain == Domain.BACKEND
        assert conf > 0

    def test_frontend(self):
        req = IntakeRequest(
            title="Fix CSS layout on login page",
            description="The React component renders wrong",
        )
        domain, conf = classify_domain(req)
        assert domain == Domain.FRONTEND
        assert conf > 0

    def test_infra(self):
        req = IntakeRequest(
            title="Update Docker deployment pipeline",
            description="Need to fix the CI/CD kubernetes config",
        )
        domain, conf = classify_domain(req)
        assert domain == Domain.INFRA
        assert conf > 0

    def test_unknown_no_keywords(self):
        req = IntakeRequest(title="Hello world", description="Just a note")
        domain, conf = classify_domain(req)
        assert domain == Domain.UNKNOWN
        assert conf == 0.0


class TestClassifyUrgency:
    def test_now(self):
        req = IntakeRequest(
            title="URGENT: production is down",
            description="This is a blocker, need fix immediately",
        )
        urgency, conf = classify_urgency(req)
        assert urgency == Urgency.NOW
        assert conf > 0

    def test_soon(self):
        req = IntakeRequest(
            title="Need this done soon",
            description="Important for this sprint",
        )
        urgency, conf = classify_urgency(req)
        assert urgency == Urgency.SOON
        assert conf > 0

    def test_later(self):
        req = IntakeRequest(
            title="Can do this later",
            description="Planned for next week",
        )
        urgency, conf = classify_urgency(req)
        assert urgency == Urgency.LATER
        assert conf > 0

    def test_eventually(self):
        req = IntakeRequest(
            title="Nice-to-have for the backlog",
            description="Eventually we should do this",
        )
        urgency, conf = classify_urgency(req)
        assert urgency == Urgency.EVENTUALLY
        assert conf > 0

    def test_default_soon_when_no_signal(self):
        req = IntakeRequest(title="Add a button", description="Make it blue")
        urgency, conf = classify_urgency(req)
        assert urgency == Urgency.SOON
        assert conf == 0.0


class TestClassifyType:
    def test_bug(self):
        req = IntakeRequest(
            title="Login is broken",
            description="Getting an error traceback when I click login",
        )
        rtype, conf = classify_type(req)
        assert rtype == RequestType.BUG
        assert conf > 0

    def test_feature(self):
        req = IntakeRequest(
            title="Add support for dark mode",
            description="Users want to be able to toggle theme",
        )
        rtype, conf = classify_type(req)
        assert rtype == RequestType.FEATURE
        assert conf > 0

    def test_refactor(self):
        req = IntakeRequest(
            title="Refactor the auth module",
            description="Clean up technical debt and simplify the code",
        )
        rtype, conf = classify_type(req)
        assert rtype == RequestType.REFACTOR
        assert conf > 0

    def test_question(self):
        req = IntakeRequest(
            title="How do I configure the database?",
            description="Can someone clarify what this setting does?",
        )
        rtype, conf = classify_type(req)
        assert rtype == RequestType.QUESTION
        assert conf > 0

    def test_chore(self):
        req = IntakeRequest(
            title="Upgrade dependencies",
            description="Bump version and do maintenance on packages",
        )
        rtype, conf = classify_type(req)
        assert rtype == RequestType.CHORE
        assert conf > 0

    def test_unknown_no_keywords(self):
        req = IntakeRequest(title="Thing", description="Stuff")
        rtype, conf = classify_type(req)
        assert rtype == RequestType.UNKNOWN
        assert conf == 0.0
