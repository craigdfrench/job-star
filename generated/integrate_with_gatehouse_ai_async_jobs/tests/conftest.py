"""
Shared test fixtures for Job-Star integration tests.

Provides a MockGatehouseServer that simulates gatehouse-ai's async job API,
along with fixtures for client, manager, and orchestrator construction.
"""
import asyncio
import json
import time
import uuid
from typing import Any, Callable, Dict, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Mock Gatehouse Server
# ---------------------------------------------------------------------------


class MockGatehouseServer:
    """
    In-memory simulation of gatehouse-ai's async job API.

    Simulates the lifecycle: PENDING -> RUNNING -> COMPLETED (or FAILED),
    with configurable delays and failure injection.

    Endpoints simulated:
      POST   /jobs                 -> submit job, returns {job_id, status}
      GET    /jobs/{job_id}        -> get job status
      GET    /jobs/{job_id}/result -> get job result (if completed)
      POST   /jobs/{job_id}/cancel -> cancel job
      GET    /jobs                 -> list jobs (optional filter)
    """

    def __init__(self):
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self._transition_delays: Dict[str, float] = {}
        self._fail_job_ids: set = set()
        self._result_generators: Dict[str, Callable] = {}
        self._call_log: list = []
        self._running = True
        self._lock = asyncio.Lock()

    # --- Submission ---

    async def submit_job(
        self,
        payload: Dict[str, Any],
        *,
        job_type: str = "default",
        priority: int = 0,
        tags: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Simulate POST /jobs"""
        self._call_log.append(("submit", payload))
        job_id = f"gh-{uuid.uuid4().hex[:12]}"
        now = time.time()
        self.jobs[job_id] = {
            "job_id": job_id,
            "status": "PENDING",
            "job_type": job_type,
            "priority": priority,
            "tags": tags or [],
            "payload": payload,
            "result": None,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "submitted_by": "job-star",
        }
        # Schedule async transition to RUNNING then terminal state
        asyncio.create_task(self._advance_lifecycle(job_id))
        return {"job_id": job_id, "status": "PENDING"}

    # --- Status ---

    async def get_status(self, job_id: str) -> Dict[str, Any]:
        """Simulate GET /jobs/{job_id}"""
        self._call_log.append(("status", job_id))
        if job_id not in self.jobs:
            return {"error": "not_found", "job_id": job_id}
        job = self.jobs[job_id]
        return {
            "job_id": job["job_id"],
            "status": job["status"],
            "job_type": job["job_type"],
            "updated_at": job["updated_at"],
        }

    # --- Results ---

    async def get_result(self, job_id: str) -> Dict[str, Any]:
        """Simulate GET /jobs/{job_id}/result"""
        self._call_log.append(("result", job_id))
        if job_id not in self.jobs:
            return {"error": "not_found", "job_id": job_id}
        job = self.jobs[job_id]
        if job["status"] != "COMPLETED":
            return {"error": "not_ready", "job_id": job_id, "status": job["status"]}
        return {"job_id": job_id, "result": job["result"]}

    # --- Cancel ---

    async def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """Simulate POST /jobs/{job_id}/cancel"""
        self._call_log.append(("cancel", job_id))
        if job_id not in self.jobs:
            return {"error": "not_found", "job_id": job_id}
        job = self.jobs[job_id]
        if job["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
            return {"job_id": job_id, "status": job["status"], "cancelled": False}
        job["status"] = "CANCELLED"
        job["updated_at"] = time.time()
        return {"job_id": job_id, "status": "CANCELLED", "cancelled": True}

    # --- List ---

    async def list_jobs(
        self, *, status: Optional[str] = None, limit: int = 100
    ) -> Dict[str, Any]:
        """Simulate GET /jobs"""
        self._call_log.append(("list", status))
        jobs = list(self.jobs.values())
        if status:
            jobs = [j for j in jobs if j["status"] == status]
        jobs = jobs[:limit]
        return {
            "jobs": [
                {"job_id": j["job_id"], "status": j["status"], "job_type": j["job_type"]}
                for j in jobs
            ],
            "count": len(jobs),
        }

    # --- Lifecycle simulation ---

    async def _advance_lifecycle(self, job_id: str):
        """Advance a job through PENDING -> RUNNING -> terminal."""
        delay = self._transition_delays.get(job_id, 0.05)
        await asyncio.sleep(delay)

        if job_id not in self.jobs:
            return
        job = self.jobs[job_id]
        if job["status"] == "CANCELLED":
            return

        job["status"] = "RUNNING"
        job["updated_at"] = time.time()

        await asyncio.sleep(delay)

        if job_id not in self.jobs:
            return
        job = self.jobs[job_id]
        if job["status"] == "CANCELLED":
            return

        if job_id in self._fail_job_ids:
            job["status"] = "FAILED"
            job["error"] = "Simulated failure for testing"
        else:
            job["status"] = "COMPLETED"
            gen = self._result_generators.get(job_id)
            if gen:
                job["result"] = gen(job)
            else:
                job["result"] = {"output": f"completed:{job['job_type']}"}
        job["updated_at"] = time.time()

    # --- Test configuration helpers ---

    def set_transition_delay(self, job_id: str, seconds: float):
        """Configure how long a job stays in each state."""
        self._transition_delays[job_id] = seconds

    def force_fail(self, job_id: str):
        """Mark a job to fail when it reaches terminal state."""
        self._fail_job_ids.add(job_id)

    def set_result_generator(self, job_id: str, generator: Callable):
        """Set a custom result generator for a job."""
        self._result_generators[job_id] = generator

    @property
    def call_log(self) -> list:
        return list(self._call_log)

    def reset(self):
        self.jobs.clear()
        self._transition_delays.clear()
        self._fail_job_ids.clear()
        self._result_generators.clear()
        self._call_log.clear()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_gatehouse():
    """Fresh mock gatehouse server for each test."""
    server = MockGatehouseServer()
    return server


@pytest.fixture
def mock_gatehouse_transport(mock_gatehouse):
    """
    A transport layer object that routes GatehouseClient calls to the mock server.
    This simulates the HTTP transport without real network calls.
    """
    transport = MagicMock()
    transport.submit_job = AsyncMock(side_effect=mock_gatehouse.submit_job)
    transport.get_status = AsyncMock(side_effect=mock_gatehouse.get_status)
    transport.get_result = AsyncMock(side_effect=mock_gatehouse.get_result)
    transport.cancel_job = AsyncMock(side_effect=mock_gatehouse.cancel_job)
    transport.list_jobs = AsyncMock(side_effect=mock_gatehouse.list_jobs)
    transport._server = mock_gatehouse
    return transport


@pytest.fixture
def gatehouse_client(mock_gatehouse_transport):
    """
    Construct a GatehouseClient wired to the mock transport.

    Requires job_star.gatehouse.GatehouseClient to accept a transport kwarg.
    """
    from job_star.gatehouse import GatehouseClient

    client = GatehouseClient(
        transport=mock_gatehouse_transport,
        base_url="http://mock-gatehouse.local",
        timeout=30.0,
        retry_config={
            "max_retries": 3,
            "backoff_base": 0.01,
            "backoff_max": 0.5,
            "retryable_statuses": {"FAILED"},
        },
    )
    return client


@pytest.fixture
def job_manager(gatehouse_client, tmp_path):
    """
    Construct a JobManager with a temp persistence dir.

    Requires job_star.manager.JobManager to accept client and storage_path.
    """
    from job_star.manager import JobManager

    manager = JobManager(
        client=gatehouse_client,
        storage_path=str(tmp_path / "jobs.json"),
        poll_interval=0.02,
        max_concurrent=10,
    )
    return manager


@pytest.fixture
def orchestrator(job_manager):
    """
    Construct an Orchestrator wired to the JobManager.

    Requires job_star.orchestrator.Orchestrator to accept a job_manager.
    """
    from job_star.orchestrator import Orchestrator

    orch = Orchestrator(
        job_manager=job_manager,
        tick_interval=0.05,
        max_ticks=None,
    )
    return orch


# ---------------------------------------------------------------------------
# Async test support
# ---------------------------------------------------------------------------

@pytest.fixture
def event_loop():
    """Create a fresh event loop for each test."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
