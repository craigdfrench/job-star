"""Thin client wrapping gatehouse-ai's async job interface.

This isolates gatehouse-ai API details from the rest of job-star.
If gatehouse-ai's interface changes, update this file only.

The client is synchronous from the caller's perspective — it handles
whatever async mechanism gatehouse-ai uses internally and returns
plain dicts. This keeps the CLI and decision engine simple.

Job dict shape (normalized):
    {
        "id": str,
        "type": str,
        "status": str,        # "queued" | "running" | "completed" | "failed" | "cancelled"
        "created_at": str,    # ISO 8601
        "updated_at": str,    # ISO 8601
        "result": Any | None,
        "error": str | None,
        "priority": int,
        "payload": dict,      # job-specific input
    }

NOTE: This is a bootstrap stub. The actual gatehouse-ai integration
points are marked with `# GATEHOUSE:` comments. Replace the stub
implementations with real calls once the gatehouse-ai client library
is wired in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


class GatehouseError(Exception):
    """Raised when a gatehouse-ai operation fails."""


class JobNotFoundError(GatehouseError):
    """Raised when a job ID does not exist."""


@dataclass
class GatehouseClient:
    """Client for gatehouse-ai's async job interface.

    Args:
        base_url: gatehouse-ai service URL.
        api_key: Optional authentication key.
        timeout: Request timeout in seconds.
    """

    base_url: str = "http://localhost:8000"
    api_key: str | None = None
    timeout: float = 30.0
    # GATEHOUSE: The real client (httpx.AsyncClient, grpc stub, etc.)
    # gets initialized here once the integration is wired in.
    _stub_store: dict[str, dict[str, Any]] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _normalize(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Normalize a gatehouse-ai job record to job-star's canonical shape."""
        return {
            "id": raw["id"],
            "type": raw.get("type", "unknown"),
            "status": raw.get("status", "unknown"),
            "created_at": raw.get("created_at", self._now()),
            "updated_at": raw.get("updated_at", self._now()),
            "result": raw.get("result"),
            "error": raw.get("error"),
            "priority": raw.get("priority", 0),
            "payload": raw.get("payload", {}),
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(
        self,
        job_type: str,
        payload: dict[str, Any] | None = None,
        priority: int = 0,
    ) -> dict[str, Any]:
        """Submit a new async job to gatehouse-ai.

        Args:
            job_type: The kind of job (e.g. "index-repo", "run-tests").
            payload: Job-specific input parameters.
            priority: Higher = more urgent.

        Returns:
            Normalized job dict with the assigned ID.
        """
        payload = payload or {}
        # GATEHOUSE: Replace with real submission call, e.g.:
        #   response = await self._http.post(f"{self.base_url}/jobs", json={...})
        job_id = str(uuid.uuid4())
        now = self._now()
        raw = {
            "id": job_id,
            "type": job_type,
            "status": "queued",
            "created_at": now,
            "updated_at": now,
            "result": None,
            "error": None,
            "priority": priority,
            "payload": payload,
        }
        self._stub_store[job_id] = raw
        return self._normalize(raw)

    def status(self, job_id: str) -> dict[str, Any]:
        """Fetch the current state of a job.

        Raises:
            JobNotFoundError: If the ID doesn't exist.
        """
        # GATEHOUSE: Replace with real fetch, e.g.:
        #   response = await self._http.get(f"{self.base_url}/jobs/{job_id}")
        raw = self._stub_store.get(job_id)
        if raw is None:
            raise JobNotFoundError(f"No job with id={job_id}")
        return self._normalize(raw)

    def list(
        self,
        status: str | None = None,
        job_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List jobs, optionally filtered by status and/or type.

        Args:
            status: Filter by status (e.g. "running"). None = all.
            job_type: Filter by job type. None = all.
            limit: Maximum number of jobs to return.

        Returns:
            List of normalized job dicts, most recently updated first.
        """
        # GATEHOUSE: Replace with real list call with query params.
        jobs = list(self._stub_store.values())
        if status is not None:
            jobs = [j for j in jobs if j["status"] == status]
        if job_type is not None:
            jobs = [j for j in jobs if j["type"] == job_type]
        jobs.sort(key=lambda j: j["updated_at"], reverse=True)
        return [self._normalize(j) for j in jobs[:limit]]

    def cancel(self, job_id: str) -> dict[str, Any]:
        """Cancel a queued or running job.

        Raises:
            JobNotFoundError: If the ID doesn't exist.
            GatehouseError: If the job is already in a terminal state.
        """
        # GATEHOUSE: Replace with real cancel call.
        raw = self._stub_store.get(job_id)
        if raw is None:
            raise JobNotFoundError(f"No job with id={job_id}")
        if raw["status"] in ("completed", "failed", "cancelled"):
            raise GatehouseError(
                f"Job {job_id} is in terminal state '{raw['status']}' — cannot cancel."
            )
        raw["status"] = "cancelled"
        raw["updated_at"] = self._now()
        return self._normalize(raw)
