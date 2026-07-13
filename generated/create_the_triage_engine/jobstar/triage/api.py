"""
FastAPI service for the Job-Star triage engine.
Exposes REST endpoints for classifying and triaging intake requests.
"""

from __future__ import annotations

import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from .engine import TriageEngine
from .models import (
    GoalRegistryEntry,
    IntakeRequest,
    TriageResponse,
    TriageResult,
)
from .registry import (
    DuplicateChecker,
    GoalRegistryBackend,
    HttpRegistry,
    InMemoryRegistry,
)

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(backend: Optional[GoalRegistryBackend] = None) -> FastAPI:
    """
    Create the FastAPI application.
    If no backend is provided, uses an in-memory registry (dev mode)
    or an HTTP backend if JOBSTAR_REGISTRY_URL is set.
    """
    if backend is None:
        registry_url = os.environ.get("JOBSTAR_REGISTRY_URL")
        if registry_url:
            api_key = os.environ.get("JOBSTAR_REGISTRY_API_KEY")
            backend = HttpRegistry(base_url=registry_url, api_key=api_key)
        else:
            backend = InMemoryRegistry()

    duplicate_checker = DuplicateChecker(backend=backend)
    engine = TriageEngine(duplicate_checker=duplicate_checker)

    app = FastAPI(
        title="Job-Star Triage Engine",
        description="Classifies intake requests by domain, urgency, and type. "
                    "Checks for duplicates against the goal registry.",
        version="0.1.0",
    )

    # Store engine and backend in app state for access in routes
    app.state.engine = engine
    app.state.backend = backend

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    @app.get("/health")
    async def health():
        return {"status": "ok", "service": "triage", "version": "0.1.0"}

    @app.post("/triage", response_model=TriageResponse)
    async def triage_request(request: IntakeRequest):
        """Triage a single intake request: classify + duplicate check."""
        try:
            result = await engine.triage(request)
            return TriageResponse(success=True, result=result)
        except Exception as e:
            return TriageResponse(success=False, error=str(e))

    @app.post("/triage/batch", response_model=list[TriageResponse])
    async def triage_batch(requests: list[IntakeRequest]):
        """Triage multiple intake requests at once."""
        results = []
        for req in requests:
            try:
                result = await engine.triage(req)
                results.append(TriageResponse(success=True, result=result))
            except Exception as e:
                results.append(TriageResponse(success=False, error=str(e)))
        return results

    @app.post("/classify", response_model=TriageResponse)
    async def classify_only(request: IntakeRequest):
        """Classify a request without duplicate checking (faster)."""
        from .classifier import classify as do_classify
        from .models import ClassificationResult
        try:
            classification = do_classify(request)
            result = TriageResult(
                request_id=request.id,
                classification=classification,
                duplicates=[],
                is_duplicate=False,
                recommended_action="Classification only (no duplicate check).",
            )
            return TriageResponse(success=True, result=result)
        except Exception as e:
            return TriageResponse(success=False, error=str(e))

    # --- Goal registry management (useful for testing/dev) ---

    @app.get("/goals", response_model=dict)
    async def list_goals(status: Optional[str] = Query(default="open")):
        """List goals in the registry."""
        goals = await backend.list_goals(status=status)
        return {
            "goals": [g.model_dump(mode="json") for g in goals],
            "count": len(goals),
        }

    @app.post("/goals", response_model=dict)
    async def add_goal(entry: GoalRegistryEntry):
        """Add a goal to the registry."""
        created = await backend.add_goal(entry)
        return created.model_dump(mode="json")

    @app.get("/goals/{goal_id}")
    async def get_goal(goal_id: str):
        """Get a single goal by ID."""
        from uuid import UUID
        try:
            gid = UUID(goal_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid UUID")
        goal = await backend.get_goal(gid)
        if goal is None:
            raise HTTPException(status_code=404, detail="Goal not found")
        return goal.model_dump(mode="json")

    return app


# Default app instance for `uvicorn jobstar.triage.api:app`
app = create_app()
