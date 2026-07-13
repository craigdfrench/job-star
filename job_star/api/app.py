"""FastAPI application for the Job-Star API service."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from job_star.db import get_pool, close_pool

from .routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Verify DB connectivity on startup, close pool on shutdown."""
    # startup
    try:
        await get_pool()
    except Exception as exc:
        raise RuntimeError(f"Database connection failed: {exc}") from exc

    # Optionally launch an embedded worker in the API process.
    bg_worker = None
    if os.environ.get("JOB_STAR_API_AUTO_WORKER", "").lower() in ("1", "true", "yes"):
        from job_star.worker_core import Worker
        bg_worker = Worker(interval=float(os.environ.get("JOB_STAR_API_WORKER_INTERVAL", "30")))
        task = asyncio.create_task(bg_worker.run())
    else:
        task = None

    yield

    # shutdown
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await close_pool()


app = FastAPI(
    title="Job-Star API",
    description="Constrained, supervised, goal-oriented AI orchestration API.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("JOB_STAR_API_CORS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")


@app.get("/health")
async def health_check():
    """Comprehensive health check — no auth required (for monitoring/upgrade tool).

    Returns 200 if healthy, 503 if any critical component is down.
    Checks: database, gateway, worker activity, queue depth.
    """
    from job_star.db import get_pool, close_pool
    from job_star.gatehouse import check_health as gateway_health
    from datetime import datetime, timezone
    import asyncio

    checks = {}
    overall_healthy = True

    # ── Database ──────────────────────────────────────
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            total_goals = await conn.fetchval("SELECT count(*) FROM goals")
            pending = await conn.fetchval("SELECT count(*) FROM goal_steps WHERE status='pending'")
            in_progress = await conn.fetchval("SELECT count(*) FROM goal_steps WHERE status='in_progress'")
            # Check for stale in_progress (potential orphans)
            orphans = await conn.fetchval(
                "SELECT count(*) FROM goal_steps WHERE status='in_progress' "
                "AND attempted_at < NOW() - INTERVAL '10 minutes'"
            )
        checks["database"] = {
            "status": "healthy",
            "goals": total_goals,
            "steps_pending": pending,
            "steps_in_progress": in_progress,
            "orphaned_steps": orphans,
        }
        if orphans > 0:
            checks["database"]["warning"] = f"{orphans} orphaned step(s) detected"
    except Exception as e:
        checks["database"] = {"status": "unhealthy", "error": str(e)}
        overall_healthy = False

    # ── Gateway ──────────────────────────────────────
    try:
        healthy = await gateway_health()
        checks["gateway"] = {"status": "healthy" if healthy else "unhealthy"}
        if not healthy:
            overall_healthy = False
    except Exception as e:
        checks["gateway"] = {"status": "unhealthy", "error": str(e)}
        overall_healthy = False

    # ── Workers ──────────────────────────────────────
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            # Workers that claimed steps in the last 5 minutes
            active_workers = await conn.fetch(
                "SELECT details->>'worker' as worker, max(timestamp) as last_seen "
                "FROM audit_trail WHERE event='step_claimed' "
                "AND details->>'worker' IS NOT NULL "
                "AND timestamp > NOW() - INTERVAL '5 minutes' "
                "GROUP BY worker ORDER BY last_seen DESC"
            )
            # Workers registered in worker_registry (if table exists)
            try:
                registered = await conn.fetch(
                    "SELECT worker_id, generation, draining, last_heartbeat "
                    "FROM worker_registry WHERE last_heartbeat > NOW() - INTERVAL '2 minutes' "
                    "ORDER BY last_heartbeat DESC"
                )
                checks["workers"] = {
                    "status": "active" if active_workers or registered else "idle",
                    "active_count": len(active_workers),
                    "registered": [
                        {"worker_id": r["worker_id"], "generation": r["generation"],
                         "draining": r["draining"],
                         "last_heartbeat": r["last_heartbeat"].isoformat() if r["last_heartbeat"] else None}
                        for r in registered
                    ],
                }
            except Exception:
                # worker_registry table might not exist yet
                checks["workers"] = {
                    "status": "active" if active_workers else "idle",
                    "active_count": len(active_workers),
                }
    except Exception as e:
        checks["workers"] = {"status": "unknown", "error": str(e)}

    # ── Schema version ──────────────────────────────
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            try:
                version = await conn.fetchval("SELECT max(version) FROM schema_migrations")
                checks["schema_version"] = version or 0
            except Exception:
                checks["schema_version"] = "not_tracked"
    except Exception:
        pass

    await close_pool()

    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=200 if overall_healthy else 503,
        content={
            "status": "healthy" if overall_healthy else "unhealthy",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "checks": checks,
        },
    )


@app.get("/")
async def root():
    return {
        "service": "job-star",
        "docs": "/docs",
        "health": "/health",
        "api_prefix": "/api/v1",
    }


@app.get("/checkin/{check_in_id}")
async def checkin_page(check_in_id: str):
    """Interactive check-in discussion page - no auth (tailnet boundary)."""
    from pathlib import Path
    from fastapi.responses import HTMLResponse
    html = Path(__file__).parent / "checkin_page.html"
    return HTMLResponse(html.read_text())


@app.get("/checkins")
async def checkins_list_page():
    """List all check-ins - no auth (tailnet boundary)."""
    from pathlib import Path
    from fastapi.responses import HTMLResponse
    html = Path(__file__).parent / "checkins_page.html"
    return HTMLResponse(html.read_text())
