"""Unit tests for the duplicate detector.

Verifies that DuplicateDetector catches obvious and near-duplicate requests
against the goal registry, and passes genuinely new requests through.
"""
import json
from pathlib import Path

import pytest

from job_star.triage.duplicate import DuplicateDetector, DuplicateResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def registry_data():
    """Load the sample goal registry as a list of goal dicts."""
    with open(FIXTURES_DIR / "sample_registry.json") as f:
        data = json.load(f)
    return data["goals"]


@pytest.fixture
def detector():
    return DuplicateDetector()


# ---------------------------------------------------------------------------
# Obvious duplicates — near-identical text
# ---------------------------------------------------------------------------

class TestObviousDuplicates:
    """Requests that are clearly re-submissions of existing goals."""

    def test_exact_title_match(self, detector, registry_data):
        """An exact title match should be flagged as a duplicate."""
        request = {
            "title": "Fix login page crash on Safari",
            "summary": "Users on Safari see a blank page after login.",
        }
        result = detector.check(request, registry_data)
        assert result.is_duplicate is True
        assert result.matched_goal_id == "G-0001"
        assert result.confidence >= 0.8

    def test_paraphrased_title_same_core_issue(self, detector, registry_data):
        """A reworded title describing the same problem should be caught."""
        request = {
            "title": "Login screen breaks in Safari browser",
            "summary": "After clicking login on Safari, the page goes blank. React hydration error.",
        }
        result = detector.check(request, registry_data)
        assert result.is_duplicate is True
        assert result.matched_goal_id == "G-0001"

    def test_same_summary_different_words(self, detector, registry_data):
        """Same underlying issue described differently should be caught."""
        request = {
            "title": "Checkout endpoint throwing server errors sometimes",
            "summary": "About 2% of checkout API calls fail with HTTP 500, linked to payment gateway timeouts.",
        }
        result = detector.check(request, registry_data)
        assert result.is_duplicate is True
        assert result.matched_goal_id == "G-0003"

    def test_duplicate_with_extra_context(self, detector, registry_data):
        """A duplicate with additional context bolted on should still match."""
        request = {
            "title": "Add dark mode toggle to settings page",
            "summary": "Users want dark mode. Store preference in localStorage and apply a theme class. "
                       "Also consider syncing with OS preference.",
        }
        result = detector.check(request, registry_data)
        assert result.is_duplicate is True
        assert result.matched_goal_id == "G-0002"


# ---------------------------------------------------------------------------
# Near-duplicates — overlapping tags / domain / type
# ---------------------------------------------------------------------------

class TestNearDuplicates:
    """Requests that overlap heavily but aren't verbatim should be caught
    when the signal is strong enough."""

    def test_overlapping_tags_and_domain(self, detector, registry_data):
        """Same domain, same type, heavily overlapping tags → duplicate."""
        request = {
            "title": "Review OAuth token storage for security issues",
            "summary": "Audit the auth flow for vulnerabilities in token handling.",
            "tags": ["security", "auth", "audit"],
            "domain": "security",
            "type": "task",
        }
        result = detector.check(request, registry_data)
        assert result.is_duplicate is True
        assert result.matched_goal_id == "G-0008"

    def test_same_action_different_object_not_duplicate(self, detector, registry_data):
        """'Upgrade PostgreSQL' exists; 'Upgrade Redis' should NOT be a duplicate."""
        request = {
            "title": "Upgrade Redis from 6 to 7",
            "summary": "Plan and execute Redis major version upgrade with backup and rollback.",
            "tags": ["database", "infrastructure", "upgrade"],
            "domain": "ops",
            "type": "task",
        }
        result = detector.check(request, registry_data)
        # Same structure but different subject — should not be flagged.
        assert result.is_duplicate is False


# ---------------------------------------------------------------------------
# Distinct requests — should pass through
# ---------------------------------------------------------------------------

class TestDistinctRequests:
    """Genuinely new requests must not be flagged as duplicates."""

    @pytest.mark.parametrize(
        "title, summary",
        [
            (
                "Implement real-time collaboration in the document editor",
                "Multiple users should be able to edit a document simultaneously with cursor presence.",
            ),
            (
                "Migrate monolith to microservices for the billing module",
                "Extract billing into its own service with an independent database.",
            ),
            (
                "Add support for SSO via SAML 2.0",
                "Enterprise customers need SAML-based single sign-on integration.",
            ),
            (
                "Create a public status page for the API",
                "Show uptime and incident history at status.example.com.",
            ),
        ],
    )
    def test_distinct_request_passes(self, detector, registry_data, title, summary):
        request = {"title": title, "summary": summary}
        result = detector.check(request, registry_data)
        assert result.is_duplicate is False
        assert result.matched_goal_id is None

    def test_same_domain_different_problem(self, detector, registry_data):
        """A new backend bug that isn't the checkout 500 should pass."""
        request = {
            "title": "User avatar uploads fail for images over 5MB",
            "summary": "Large image uploads return a 413 error. Need to handle resizing or chunked uploads.",
            "domain": "backend",
            "type": "bug",
        }
        result = detector.check(request, registry_data)
        assert result.is_duplicate is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestDuplicateEdgeCases:
    """Boundary conditions and robustness."""

    def test_empty_registry_no_duplicate(self, detector):
        """With no existing goals, nothing can be a duplicate."""
        request = {"title": "Fix login crash", "summary": "Safari blank page."}
        result = detector.check(request, [])
        assert result.is_duplicate is False
        assert result.matched_goal_id is None

    def test_empty_request_raises(self, detector, registry_data):
        with pytest.raises((ValueError, TypeError)):
            detector.check({}, registry_data)

    def test_request_missing_title_raises(self, detector, registry_data):
        with pytest.raises((ValueError, KeyError, TypeError)):
            detector.check({"summary": "Some summary."}, registry_data)

    def test_result_has_confidence_score(self, detector, registry_data):
        """The DuplicateResult should always carry a confidence float."""
        request = {"title": "Fix login page crash on Safari", "summary": "Blank page."}
        result = detector.check(request, registry_data)
        assert isinstance(result.confidence, float)
        assert 0.0 <= result.confidence <= 1.0

    def test_non_duplicate_has_zero_or_low_confidence(self, detector, registry_data):
        request = {
            "title": "Implement real-time collaboration in the document editor",
            "summary": "Multi-user simultaneous editing with cursor presence.",
        }
        result = detector.check(request, registry_data)
        assert result.confidence < 0.5

    def test_threshold_respected(self, detector, registry_data):
        """A borderline match below the threshold should not be flagged."""
        # Vague request that touches 'auth' but is really about something else.
        request = {
            "title": "Add biometric authentication to the mobile app",
            "summary": "Support Face ID and fingerprint login on iOS and Android.",
            "tags": ["mobile", "auth", "biometric"],
            "domain": "web",
            "type": "feature",
        }
        result = detector.check(request, registry_data)
        # This is a new feature, not a duplicate of the Safari login bug or
        # the permissions refactor.
        assert result.is_duplicate is False
