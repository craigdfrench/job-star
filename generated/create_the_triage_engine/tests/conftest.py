"""Shared pytest configuration and fixtures for Job-Star triage tests."""
import sys
from pathlib import Path

# Ensure the job_star package is importable when running tests from repo root.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Make the fixtures directory available as a path fixture.
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def pytest_configure(config):
    """Ensure the fixtures directory exists."""
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
