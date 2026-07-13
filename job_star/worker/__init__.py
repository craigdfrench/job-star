"""Job-Star worker service.

Use `python -m job_star.worker` to run the worker.
"""
from __future__ import annotations

from ..worker_core import Worker, main

__all__ = ["Worker", "main"]
