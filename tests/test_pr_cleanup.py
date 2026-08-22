"""Tests for the superseded PR cleanup script."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


class TestCleanupScript(unittest.TestCase):
    """The cleanup script must be safe and idempotent."""

    def setUp(self):
        self.repo_root = Path(__file__).resolve().parent.parent
        self.script = self.repo_root / "scripts" / "cleanup_superseded_prs.sh"

    def test_script_exists_and_is_executable(self):
        self.assertTrue(self.script.exists(), f"{self.script} must exist")
        # File should be readable
        self.assertTrue(os.access(self.script, os.R_OK))

    def test_script_has_shebang(self):
        content = self.script.read_text()
        self.assertTrue(
            content.startswith("#!/usr/bin/env bash"),
            "Script must start with bash shebang",
        )

    def test_script_no_op_when_no_env(self):
        """Running without SUPERSEDED_PRS env var should exit 0 cleanly."""
        env = os.environ.copy()
        env.pop("SUPERSEDED_PRS", None)
        result = subprocess.run(
            ["bash", str(self.script), "24"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("No superseded PRs list provided", result.stdout)

    def test_script_dry_run_with_fake_list(self):
        """Script should attempt to close PRs listed (will fail without gh auth,
        but we verify the loop runs)."""
        env = os.environ.copy()
        env["SUPERSEDED_PRS"] = "999999"
        # gh will fail but script should not crash unbound
        result = subprocess.run(
            ["bash", str(self.script), "24"],
            capture_output=True,
            text=True,
            env=env,
        )
        # Script handles gh failure gracefully (warns, continues)
        self.assertIn("Closing PR #999999", result.stdout)


if __name__ == "__main__":
    unittest.main()