"""Tests for the Job-Star FastAPI API service."""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

import job_star.db as db

os.environ.setdefault("DATABASE_URL", "postgresql://jobstar:jobstar@localhost:5432/job_star")

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(loop_scope="function")
async def client():
    """Yield an HTTP client for the FastAPI app and clean up test goals."""
    from job_star.api.app import app
    import asyncpg

    os.environ["JOB_STAR_API_PASSWORD"] = "testpass"
    os.environ["JOB_STAR_API_TOKEN"] = "testtoken"

    # Reset the global pool so each test gets a fresh pool bound to the current loop
    await db.close_pool()
    db._pool = None

    dsn = os.environ.get("DATABASE_URL", "postgresql://jobstar:jobstar@localhost:5432/job_star")
    conn = await asyncpg.connect(dsn=dsn)
    await conn.execute("DELETE FROM goals WHERE title LIKE 'API Test:%' OR source = 'api_test'")
    await conn.close()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await db.close_pool()
    db._pool = None


async def test_health_unauthenticated(client):
    """/health is public."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_intake_requires_auth(client):
    """POST /intake requires authentication."""
    resp = await client.post("/api/v1/intake", json={"title": "API Test: no auth"})
    assert resp.status_code == 401


async def test_intake_basic_auth(client):
    """POST /intake creates a goal with Basic auth."""
    payload = {
        "title": "API Test: intake goal",
        "description": "Created by API test",
        "domain": "coding",
        "urgency": "soon",
        "source": "api_test",
    }
    resp = await client.post("/api/v1/intake", json=payload, auth=("agent", "testpass"))
    assert resp.status_code == 201
    data = resp.json()
    assert data["title"] == payload["title"]
    assert data["domain"] == "coding"
    assert data["urgency"] == "soon"
    assert data["status"] == "active"
    assert "id" in data

    # Verify it appears in the list
    resp = await client.get("/api/v1/goals", auth=("agent", "testpass"))
    assert resp.status_code == 200
    goals = resp.json()["goals"]
    assert any(g["title"] == payload["title"] for g in goals)


async def test_intake_bearer_token(client):
    """POST /intake accepts Bearer token auth."""
    payload = {
        "title": "API Test: bearer goal",
        "source": "api_test",
    }
    headers = {"Authorization": "Bearer testtoken"}
    resp = await client.post("/api/v1/intake", json=payload, headers=headers)
    assert resp.status_code == 201
    assert resp.json()["title"] == payload["title"]


async def test_get_goal(client):
    """GET /goals/{id} returns goal details."""
    create = await client.post(
        "/api/v1/intake",
        json={"title": "API Test: get goal", "source": "api_test"},
        auth=("agent", "testpass"),
    )
    assert create.status_code == 201
    goal_id = create.json()["id"]

    resp = await client.get(f"/api/v1/goals/{goal_id}", auth=("agent", "testpass"))
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == goal_id
    assert "steps" in data
    assert "conflicts" in data


async def test_get_goal_not_found(client):
    """GET /goals/{id} returns 404 for unknown goal."""
    resp = await client.get("/api/v1/goals/00000000-0000-0000-0000-000000000000", auth=("agent", "testpass"))
    assert resp.status_code == 404


async def test_complete_goal(client):
    """POST /goals/{id}/complete marks goal complete."""
    create = await client.post(
        "/api/v1/intake",
        json={"title": "API Test: complete goal", "source": "api_test"},
        auth=("agent", "testpass"),
    )
    goal_id = create.json()["id"]

    resp = await client.post(f"/api/v1/goals/{goal_id}/complete", json={}, auth=("agent", "testpass"))
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    get = await client.get(f"/api/v1/goals/{goal_id}", auth=("agent", "testpass"))
    assert get.json()["status"] == "completed"
    assert get.json()["progress"] == 1.0


async def test_ask_answer_flow(client):
    """POST /ask and POST /answer/{id} flow."""
    resp = await client.post(
        "/api/v1/ask",
        json={"question": "API Test: what is the answer?"},
        auth=("agent", "testpass"),
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "pending"
    qid = data["question_id"]

    resp = await client.post(
        f"/api/v1/answer/{qid}",
        json={"answer": "42"},
        auth=("agent", "testpass"),
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "answered"


async def test_list_goals_filter(client):
    """GET /goals filters by status."""
    await client.post(
        "/api/v1/intake",
        json={"title": "API Test: filter active", "source": "api_test"},
        auth=("agent", "testpass"),
    )
    resp = await client.get("/api/v1/goals?status=active", auth=("agent", "testpass"))
    assert resp.status_code == 200
    assert any(g["title"] == "API Test: filter active" for g in resp.json()["goals"])
