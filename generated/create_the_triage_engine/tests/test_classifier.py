"""Unit tests for the triage classifier.

Verifies that the Classifier correctly assigns domain, urgency, and type
to free-text intake requests.
"""
import pytest

from job_star.triage.classifier import Classifier, Classification


@pytest.fixture
def classifier():
    return Classifier()


# ---------------------------------------------------------------------------
# Domain classification
# ---------------------------------------------------------------------------

class TestDomainClassification:
    """Verify the classifier routes requests to the correct domain."""

    @pytest.mark.parametrize(
        "text, expected_domain",
        [
            ("The login page crashes on Safari when users click submit.", "web"),
            ("Frontend button is misaligned on mobile viewport.", "web"),
            ("Checkout API returns 500 errors intermittently.", "backend"),
            ("User permissions service needs a refactor to handle roles.", "backend"),
            ("Upgrade PostgreSQL from 14 to 16 in production.", "ops"),
            ("Set up CI pipeline for the mobile app build.", "ops"),
            ("Write onboarding documentation for new hires.", "meta"),
            ("Create a README for the triage engine module.", "meta"),
            ("Security audit of the OAuth authentication flow.", "security"),
            ("Review token storage for potential vulnerabilities.", "security"),
        ],
    )
    def test_domain_assignment(self, classifier, text, expected_domain):
        result = classifier.classify(text)
        assert result.domain == expected_domain, (
            f"Expected domain '{expected_domain}' for text: {text!r}, "
            f"got '{result.domain}'"
        )

    def test_unknown_domain_falls_back_to_meta(self, classifier):
        """Requests with no clear domain signal should default to 'meta'."""
        result = classifier.classify("Something needs to be done about stuff.")
        assert result.domain == "meta"

    def test_domain_is_lowercase_string(self, classifier):
        result = classifier.classify("Fix the database connection pool.")
        assert isinstance(result.domain, str)
        assert result.domain == result.domain.lower()


# ---------------------------------------------------------------------------
# Urgency classification
# ---------------------------------------------------------------------------

class TestUrgencyClassification:
    """Verify urgency is inferred from language cues."""

    @pytest.mark.parametrize(
        "text, expected_urgency",
        [
            ("URGENT: production is down, login page crashes for all users.", "now"),
            ("Critical bug — checkout is broken and we're losing sales.", "now"),
            ("This needs to be fixed immediately.", "now"),
            ("We should add dark mode to the settings page soon.", "soon"),
            ("Plan the PostgreSQL upgrade for next sprint.", "soon"),
            ("Whenever you have time, write onboarding docs.", "whenever"),
            ("Nice to have: refactor the permissions module.", "whenever"),
            ("Low priority — clean up the unused CSS.", "whenever"),
        ],
    )
    def test_urgency_assignment(self, classifier, text, expected_urgency):
        result = classifier.classify(text)
        assert result.urgency == expected_urgency, (
            f"Expected urgency '{expected_urgency}' for text: {text!r}, "
            f"got '{result.urgency}'"
        )

    def test_default_urgency_when_no_signal(self, classifier):
        """Without urgency cues, default to 'soon' (middle of the road)."""
        result = classifier.classify("Add a dark mode toggle to settings.")
        assert result.urgency == "soon"

    def test_urgency_values_are_constrained(self, classifier):
        """Urgency must be one of the three valid levels."""
        valid = {"now", "soon", "whenever"}
        samples = [
            "Fix the crash now!",
            "Add a feature soon.",
            "Clean up whenever.",
            "Do something.",
        ]
        for text in samples:
            result = classifier.classify(text)
            assert result.urgency in valid, (
                f"Invalid urgency '{result.urgency}' for: {text!r}"
            )


# ---------------------------------------------------------------------------
# Type classification
# ---------------------------------------------------------------------------

class TestTypeClassification:
    """Verify the request type (bug, feature, task, refactor, etc.)."""

    @pytest.mark.parametrize(
        "text, expected_type",
        [
            ("The login page crashes on Safari.", "bug"),
            ("Checkout API returns 500 errors.", "bug"),
            ("Users report a blank screen after clicking submit.", "bug"),
            ("Add a dark mode toggle to the settings page.", "feature"),
            ("Support exporting reports as PDF.", "feature"),
            ("Upgrade PostgreSQL from 14 to 16.", "task"),
            ("Write onboarding documentation for new hires.", "task"),
            ("Set up CI pipeline for the mobile app.", "task"),
            ("Refactor the user permissions module.", "refactor"),
            ("Clean up the authentication code, extract role logic.", "refactor"),
            ("Security audit of the OAuth flow.", "task"),
        ],
    )
    def test_type_assignment(self, classifier, text, expected_type):
        result = classifier.classify(text)
        assert result.type == expected_type, (
            f"Expected type '{expected_type}' for text: {text!r}, "
            f"got '{result.type}'"
        )

    def test_type_values_are_constrained(self, classifier):
        valid = {"bug", "feature", "task", "refactor"}
        samples = [
            "Fix the crash.",
            "Add a feature.",
            "Upgrade the database.",
            "Refactor the module.",
            "Do something.",
        ]
        for text in samples:
            result = classifier.classify(text)
            assert result.type in valid, (
                f"Invalid type '{result.type}' for: {text!r}"
            )


# ---------------------------------------------------------------------------
# Combined / structural tests
# ---------------------------------------------------------------------------

class TestClassificationStructure:
    """Verify the Classification object structure and consistency."""

    def test_classification_has_all_fields(self, classifier):
        result = classifier.classify("Fix the urgent login crash on Safari.")
        assert isinstance(result, Classification)
        assert hasattr(result, "domain")
        assert hasattr(result, "urgency")
        assert hasattr(result, "type")

    def test_classification_is_immutable_or_stable(self, classifier):
        """Classifying the same text twice yields the same result."""
        text = "Add dark mode to settings soon."
        r1 = classifier.classify(text)
        r2 = classifier.classify(text)
        assert r1.domain == r2.domain
        assert r1.urgency == r2.urgency
        assert r1.type == r2.type

    def test_empty_input_raises(self, classifier):
        with pytest.raises((ValueError, TypeError)):
            classifier.classify("")

    def test_whitespace_only_input_raises(self, classifier):
        with pytest.raises((ValueError, TypeError)):
            classifier.classify("   \n\t  ")

    def test_very_short_input_handled_gracefully(self, classifier):
        """A single word should not crash; it should produce a classification."""
        result = classifier.classify("bug")
        assert result.domain in {"meta", "web", "backend", "ops", "security"}
        assert result.urgency in {"now", "soon", "whenever"}
        assert result.type in {"bug", "feature", "task", "refactor"}
