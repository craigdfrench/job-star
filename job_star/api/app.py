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
    return {"status": "ok", "service": "job-star"}


@app.get("/")
async def root():
    return {
        "service": "job-star",
        "docs": "/docs",
        "health": "/health",
        "api_prefix": "/api/v1",
    }
