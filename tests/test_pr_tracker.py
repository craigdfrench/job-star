"""Tests for the PR tracker update helper."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import update_pr_tracker


class TestPrTracker(unittest.TestCase):
    def test_append_record_writes_markdown(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as f:
            tracker_path = Path(f.name)
        try:
            with mock.patch.object(update_pr_tracker, "TRACKER_FILE", tracker_path):
                update_pr_tracker.append_cleanup_record(
                    [12, 15], 24, notes="test run"
                )
            content = tracker_path.read_text()
            self.assertIn("Replacement PR: #24", content)
            self.assertIn("#12", content)
            self.assertIn("#15", content)
            self.assertIn("test run", content)
        finally:
            tracker_path.unlink(missing_ok=True)

    def test_append_record_empty_list(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as f:
            tracker_path = Path(f.name)
        try:
            with mock.patch.object(update_pr_tracker, "TRACKER_FILE", tracker_path):
                update_pr_tracker.append_cleanup_record([], 24)
            content = tracker_path.read_text()
            self.assertIn("No PRs closed in this run.", content)
        finally:
            tracker_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()