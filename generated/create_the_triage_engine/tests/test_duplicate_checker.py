import os
import tempfile
import unittest

from job_star.triage import (
    DuplicateChecker,
    GoalRegistry,
    GoalRecord,
    compute_source_hash,
    extract_keywords,
)


class TestDuplicateChecker(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.registry = GoalRegistry(self.tmp.name)
        self.checker = DuplicateChecker(self.registry)

        # Seed an existing goal
        rec = GoalRecord(
            goal_id="G-001",
            title="Fix login page crash on Safari",
            domain="frontend",
            urgency="high",
            type="bug",
            status="active",
            source_text="The login page crashes when users on Safari submit the form.",
            source_hash=compute_source_hash(
                "The login page crashes when users on Safari submit the form."
            ),
            keywords=extract_keywords(
                "The login page crashes when users on Safari submit the form."
            ),
        )
        self.registry.register(rec)

    def tearDown(self) -> None:
        os.unlink(self.tmp.name)

    def test_exact_duplicate_rejected(self) -> None:
        report = self.checker.check(
            title="Fix login page crash on Safari",
            source_text="The login page crashes when users on Safari submit the form.",
            domain="frontend",
        )
        self.assertTrue(report.is_duplicate)
        self.assertEqual(report.action, "reject")
        self.assertGreaterEqual(report.confidence, 0.98)

    def test_near_duplicate_merges(self) -> None:
        report = self.checker.check(
            title="Fix login page crash on Safari browser",
            source_text="Login page crashes on Safari when submitting the form.",
            domain="frontend",
        )
        self.assertTrue(report.is_duplicate)
        self.assertIn(report.action, ("merge", "link"))
        self.assertGreaterEqual(report.confidence, 0.55)

    def test_unrelated_request_creates(self) -> None:
        report = self.checker.check(
            title="Add dark mode to settings panel",
            source_text="Users want a dark theme option in the settings menu.",
            domain="frontend",
        )
        self.assertFalse(report.is_duplicate)
        self.assertEqual(report.action, "create")

    def test_empty_registry_creates(self) -> None:
        tmp2 = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp2.close()
        try:
            reg2 = GoalRegistry(tmp2.name)
            checker2 = DuplicateChecker(reg2)
            report = checker2.check(
                title="Brand new request",
                source_text="Something nobody has asked for before.",
            )
            self.assertFalse(report.is_duplicate)
            self.assertEqual(report.action, "create")
        finally:
            os.unlink(tmp2.name)


if __name__ == "__main__":
    unittest.main()
