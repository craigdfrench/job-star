"""End-to-end integration tests for job-star.

Tests the full pipeline: intake → triage → conflict check → goal registry
→ router → supervisor → execution → follow-up.
"""

import asyncio
import pytest
import os
import json

# Set test environment
os.environ.setdefault("DATABASE_URL", "postgresql://jobstar:jobstar@localhost:5432/job_star")
os.environ.setdefault("GATEHOUSE_API_URL", "http://gatehouse-ai.craigdfrench.com/v1")

from job_star.models import Domain, Goal, GoalStatus, IntakeRequest, Urgency
from job_star.triage import triage as run_triage
from job_star.router import route
from job_star.supervisor import Supervisor, SupervisionDecision
from job_star.conflict import detect_conflicts
from job_star.followup import FollowUpEngine, FollowUpLevel
from job_star.orchestrator import Orchestrator
from job_star.gatehouse import GatewayMonitor
from job_star.db import close_pool, list_goals


@pytest.fixture
async def db_pool():
    """Manage the DB pool lifecycle for tests and clean up test goals."""
    yield
    await close_pool()


@pytest.fixture
async def clean_db(db_pool):
    """Delete test goals before and after each test that touches the DB.

    This fixes the stale-test-goal issue (Vikunja #696) where
    test_orchestrator_add_goal left a completed goal in the DB, causing
    subsequent runs to find 2 goals with the same title.
    """
    import asyncpg
    dsn = os.environ.get("DATABASE_URL", "postgresql://jobstar:jobstar@localhost:5432/job_star")
    conn = await asyncpg.connect(dsn=dsn)
    await conn.execute("DELETE FROM goals WHERE title LIKE 'Test:%' OR title LIKE 'Unique test goal%' OR source = 'test'")
    await conn.close()
    yield
    conn = await asyncpg.connect(dsn=dsn)
    await conn.execute("DELETE FROM goals WHERE title LIKE 'Test:%' OR title LIKE 'Unique test goal%' OR source = 'test'")
    await conn.close()


# ============================================================================
# Triage tests
# ============================================================================

@pytest.mark.asyncio
async def test_triage_classifies_coding():
    """Triage should classify a coding request correctly."""
    request = IntakeRequest(
        title="Fix the socket timeout bug",
        description="The HTTP client times out after 30 seconds",
    )
    result = await run_triage(request, check_duplicates=False)
    assert result.domain == Domain.CODING
    assert result.confidence > 0


@pytest.mark.asyncio
async def test_triage_classifies_meta():
    """Triage should classify a meta (job-star) request correctly."""
    request = IntakeRequest(
        title="Build job-star: Create the router",
        description="Build a routing service for job-star",
    )
    result = await run_triage(request, check_duplicates=False)
    assert result.domain == Domain.META


@pytest.mark.asyncio
async def test_triage_classifies_personal():
    """Triage should classify a personal request correctly."""
    request = IntakeRequest(
        title="Extract Maddy's images from Google Photos",
        description="Process photos from Google Takeout",
    )
    result = await run_triage(request, check_duplicates=False)
    assert result.domain == Domain.PERSONAL


@pytest.mark.asyncio
async def test_triage_detects_urgency():
    """Triage should detect imperative urgency from keywords."""
    request = IntakeRequest(
        title="URGENT: production is down, fix immediately",
        description="Critical bug causing crash",
    )
    result = await run_triage(request, check_duplicates=False)
    assert result.urgency == Urgency.IMPERATIVE


# ============================================================================
# Router tests
# ============================================================================

@pytest.mark.asyncio
async def test_router_imperative_uses_capable_model():
    """Imperative tasks should use the most capable model."""
    decision = await route(urgency=Urgency.IMPERATIVE, request_type="bug", description="complex bug fix")
    assert decision.model is not None
    assert "complex" in decision.complexity or "moderate" in decision.complexity


@pytest.mark.asyncio
async def test_router_idle_prefers_free():
    """Idle tasks should prefer free models."""
    decision = await route(urgency=Urgency.IDLE_OPPORTUNISTIC, request_type="chore", description="update deps")
    # Free models have no cost
    assert decision.estimated_cost == 0.0


@pytest.mark.asyncio
async def test_router_model_override():
    """Model override should bypass routing logic."""
    decision = await route(urgency=Urgency.IMPERATIVE, model_override="custom-model")
    assert decision.model == "custom-model"


@pytest.mark.asyncio
async def test_router_avoids_unavailable_model():
    """Router should avoid a model in quota hold and pick a fallback."""
    monitor = GatewayMonitor()
    monitor._gateway_models = {
        "ollama/glm-5.2": {"id": "ollama/glm-5.2", "capabilities": {"text": True, "code": True}, "pricing": {}},
        "ollama/gemini-3-flash-preview": {"id": "ollama/gemini-3-flash-preview", "capabilities": {"text": True, "vision": True}, "pricing": {}},
    }
    # Put the preferred model in quota hold
    monitor.record_failure("ollama/glm-5.2", "quota exceeded")
    # It should pick a fallback that is available
    decision = await route(urgency=Urgency.IMPERATIVE, request_type="bug", gateway_monitor=monitor)
    assert decision.model != "ollama/glm-5.2"


@pytest.mark.asyncio
async def test_gateway_monitor_picks_fallback_for_vision():
    """Gateway monitor should pick a vision-capable fallback."""
    monitor = GatewayMonitor()
    monitor._gateway_models = {
        "ollama/glm-5.2": {"id": "ollama/glm-5.2", "capabilities": {"text": True, "code": True}, "pricing": {}},
        "ollama/gemini-3-flash-preview": {"id": "ollama/gemini-3-flash-preview", "capabilities": {"text": True, "vision": True}, "pricing": {}},
    }
    monitor.record_failure("ollama/gemini-3-flash-preview", "model not found")
    fallback = monitor.pick_fallback("ollama/gemini-3-flash-preview", required_capability="vision")
    assert fallback is None  # no other vision model available


def test_x_gatehouse_parsing_zero_rated():
    """GatewayMonitor should parse x_gatehouse and treat zero-rated as FREE."""
    monitor = GatewayMonitor()
    xg = {
        "cost_class": "included_quota",
        "routing_advice": "harvest",
        "reason": "$0-rated - doesn't consume dollar quota, harvest free retail value",
        "retail_value_this_request": 0.00014205,
        "quota_windows": [
            {"pool_id": "windsurf_daily", "dimension": "quota_units", "window": "daily",
             "limit": 100, "used": 6, "remaining": 94, "remaining_pct": 94,
             "resets_at": "2026-07-12T08:00:00Z", "hours_until_reset": 14.5},
            {"pool_id": "windsurf_weekly", "dimension": "dollars", "window": "weekly",
             "limit": 100, "used": 92, "remaining": 8, "remaining_pct": 8,
             "resets_at": "2026-07-12T08:00:00Z", "hours_until_reset": 14.5},
        ],
    }
    monitor.record_success("kimi-k2-7", tokens=100, x_gatehouse=xg)

    # kimi-k2-7 is zero-rated (included_quota) → tier_for should be FREE
    from job_star.gatehouse.monitor import _cost_class_to_tier, ModelTier
    assert _cost_class_to_tier("included_quota") == ModelTier.FREE
    assert monitor.tier_for("kimi-k2-7") == ModelTier.FREE
    assert not monitor.is_expensive("kimi-k2-7")

    # Quota status should be available
    qs = monitor.quota_status("kimi-k2-7")
    assert qs is not None
    assert qs["cost_class"] == "included_quota"
    assert qs["routing_advice"] == "harvest"
    assert len(qs["quota_windows"]) == 2


def test_x_gatehouse_exhausted_quota_enters_hold():
    """When a quota window is at 0%, the model should be unavailable."""
    monitor = GatewayMonitor()
    xg = {
        "cost_class": "included_quota",
        "routing_advice": "switch",
        "quota_windows": [
            {"pool_id": "windsurf_weekly", "dimension": "dollars", "window": "weekly",
             "limit": 100, "used": 100, "remaining": 0, "remaining_pct": 0,
             "resets_at": "2099-01-01T00:00:00Z", "hours_until_reset": 999},
        ],
    }
    monitor.record_success("kimi-k2-7", tokens=10, x_gatehouse=xg)
    # Quota exhausted → unavailable
    assert not monitor.is_available("kimi-k2-7")
    assert monitor.time_until_available("kimi-k2-7") > 0


def test_pr_executor_parses_file_blocks():
    """PRExecutor should parse file blocks and delete directives from AI output."""
    from job_star.executors.pr_executor import parse_file_blocks, parse_delete_blocks
    output = '''
## File: internal/foo.go
```go
package foo

func Bar() bool { return true }
```

## File: internal/foo_test.go
```go
package foo

import "testing"

func TestBar(t *testing.T) { if !Bar() { t.Fatal() } }
```

## Delete: internal/old.go
'''
    changes = parse_file_blocks(output)
    deletes = parse_delete_blocks(output)
    assert len(changes) == 2
    assert changes[0].path == "internal/foo.go"
    assert "func Bar" in changes[0].content
    assert changes[1].path == "internal/foo_test.go"
    assert len(deletes) == 1
    assert deletes[0].path == "internal/old.go"
    assert deletes[0].action == "delete"


# ============================================================================
# Step DAG (depends_on) tests
# ============================================================================

@pytest.mark.asyncio
async def test_step_dag_blocks_unmet_dependency(clean_db):
    """A step with unmet depends_on should not be claimable."""
    from job_star.db import create_goal, create_step, claim_next_step, update_step_status, StepStatus
    goal = await create_goal(title="Test: DAG dependency", description="Test depends_on blocking", source="test")
    step1 = await create_step(goal.id, title="Step 1: setup", order_index=1)
    step2 = await create_step(goal.id, title="Step 2: build", order_index=2, depends_on=[step1.id])

    # Step 2 should NOT be claimable (step 1 not completed)
    claimed = await claim_next_step(goal.id)
    assert claimed is not None
    assert claimed.id == step1.id  # step 1 (no deps) is claimed first

    # No more claimable steps (step 2 is blocked by step 1)
    claimed2 = await claim_next_step(goal.id)
    assert claimed2 is None

    # Complete step 1
    await update_step_status(step1.id, StepStatus.COMPLETED, result={"ok": True})

    # Now step 2 is claimable
    claimed3 = await claim_next_step(goal.id)
    assert claimed3 is not None
    assert claimed3.id == step2.id


@pytest.mark.asyncio
async def test_step_dag_parallel_steps_no_deps(clean_db):
    """Steps with no dependencies can be claimed in parallel (any order)."""
    from job_star.db import create_goal, create_step, claim_next_step
    goal = await create_goal(title="Test: DAG parallel", description="Test parallel steps", source="test")
    step_a = await create_step(goal.id, title="Step A", order_index=1)
    step_b = await create_step(goal.id, title="Step B", order_index=2)

    # Both should be claimable (no deps)
    claimed1 = await claim_next_step(goal.id)
    claimed2 = await claim_next_step(goal.id)
    assert claimed1 is not None
    assert claimed2 is not None
    assert {claimed1.id, claimed2.id} == {step_a.id, step_b.id}


@pytest.mark.asyncio
async def test_router_refuses_expensive_fallback():
    """Router should not silently fall back to an expensive model."""
    monitor = GatewayMonitor()
    monitor._gateway_models = {
        "ollama/glm-5.2": {"id": "ollama/glm-5.2", "capabilities": {"text": True, "code": True}, "pricing": {}},
        "claude-5-fable-high": {"id": "claude-5-fable-high", "capabilities": {"text": True, "code": True}, "pricing": {}},
    }
    # Make the free model unavailable
    monitor.record_failure("ollama/glm-5.2", "quota exceeded")
    decision = await route(urgency=Urgency.IMPERATIVE, request_type="bug", gateway_monitor=monitor)
    assert decision.model != "claude-5-fable-high"


@pytest.mark.asyncio
async def test_router_allows_expensive_when_requested():
    """Router should allow an expensive model when explicitly requested."""
    decision = await route(urgency=Urgency.IMPERATIVE, model_override="claude-5-fable-high", allow_expensive=True)
    assert decision.model == "claude-5-fable-high"


@pytest.mark.asyncio
async def test_router_blocks_expensive_override_by_default():
    """Router should not allow expensive model override unless allow_expensive=True."""
    monitor = GatewayMonitor()
    decision = await route(urgency=Urgency.IMPERATIVE, model_override="claude-5-fable-high", gateway_monitor=monitor)
    assert decision.model != "claude-5-fable-high"


# ============================================================================
# Supervisor tests
# ============================================================================

@pytest.mark.asyncio
async def test_supervisor_approves_normal_execution(db_pool):
    """Supervisor should approve normal execution."""
    sup = Supervisor()
    goal = Goal(title="Test", domain=Domain.CODING, urgency=Urgency.SOON)
    from job_star.models import Step
    step = Step(title="Do something", goal_id="test-id")
    result = await sup.check_before_execute(goal, step)
    assert result.decision == SupervisionDecision.APPROVE


@pytest.mark.asyncio
async def test_supervisor_blocks_on_budget(db_pool):
    """Supervisor should block when budget is exceeded."""
    sup = Supervisor(max_tokens_per_goal=100)
    goal = Goal(title="Test", id="test-budget", domain=Domain.CODING, urgency=Urgency.SOON)
    from job_star.models import Step
    step = Step(title="Do something", id="step-1", goal_id="test-budget")
    # Exhaust budget in-memory (DB won't have this test goal)
    sup.budget.record_usage("test-budget", 200, 0.01)
    result = await sup.check_before_execute(goal, step)
    assert result.decision == SupervisionDecision.PAUSE_GOAL


def test_supervisor_detects_path_inconsistency():
    """Supervisor should detect inconsistent file paths."""
    sup = Supervisor()
    goal = Goal(title="Test", domain=Domain.CODING, urgency=Urgency.SOON)
    from job_star.models import Step
    step = Step(title="Do something", id="step-1", goal_id="test-id")

    prev_outputs = ["Created file `job_star/triage/engine.py`"]
    proposed = "Created file `new_module/something.py`"

    result = sup.check_after_execute(goal, step, proposed, prev_outputs)
    assert result.decision == SupervisionDecision.REQUIRE_ESCALATION
    assert len(result.violations) > 0


# ============================================================================
# Conflict detection tests
# ============================================================================

def test_conflict_detects_duplicates():
    """Conflict detector should find duplicate goals."""
    from job_star.conflict.detector import _detect_duplicate
    goal_a = Goal(title="Fix the socket timeout bug", description="HTTP client timeout")
    goal_b = Goal(title="Fix the socket timeout bug", description="HTTP client timeout issue")
    result = _detect_duplicate(goal_a, goal_b)
    assert result is not None


def test_conflict_detects_contradiction():
    """Conflict detector should find contradictory goals."""
    from job_star.conflict.detector import _detect_contradiction
    goal_a = Goal(title="Add logging to the service")
    goal_b = Goal(title="Remove logging from the service")
    result = _detect_contradiction(goal_a, goal_b)
    assert result is not None


# ============================================================================
# Follow-up tests
# ============================================================================

@pytest.mark.asyncio
async def test_followup_classifies_interrupt():
    """Follow-up engine should classify critical events as interrupt."""
    engine = FollowUpEngine()
    goal = Goal(title="Test", domain=Domain.CODING, urgency=Urgency.IMPERATIVE)
    level = engine.classify(goal, "step_failed", "Execution failed")
    assert level == FollowUpLevel.INTERRUPT


@pytest.mark.asyncio
async def test_followup_classifies_silent():
    """Follow-up engine should classify idle events as silent."""
    engine = FollowUpEngine()
    goal = Goal(title="Test", domain=Domain.CODING, urgency=Urgency.IDLE_OPPORTUNISTIC)
    level = engine.classify(goal, "step_completed", "Done")
    assert level == FollowUpLevel.SILENT


# ============================================================================
# Orchestrator / integration tests
# ============================================================================

@pytest.mark.asyncio
async def test_orchestrator_add_goal(clean_db):
    """Orchestrator should add a goal through the full pipeline."""
    orch = Orchestrator()
    goal, triage = await orch.add_goal(
        title="Test: integration test goal",
        description="This is a test goal for integration testing",
        source="test",
    )
    assert goal is not None
    assert triage.domain is not None
    assert triage.urgency is not None

    # Verify it's in the database
    goals = await list_goals()
    test_goals = [g for g in goals if g.title == "Test: integration test goal"]
    assert len(test_goals) == 1

    # Clean up — mark as completed
    from job_star.db import update_goal_status
    await update_goal_status(goal.id, GoalStatus.COMPLETED)


@pytest.mark.asyncio
async def test_orchestrator_duplicate_detection(clean_db):
    """Orchestrator should detect duplicate goals."""
    orch = Orchestrator()

    # Add a goal
    goal1, _ = await orch.add_goal(
        title="Unique test goal for duplicate detection",
        description="Testing duplicate detection in the triage engine",
        source="test",
    )
    assert goal1 is not None

    # Try to add the same goal again
    goal2, triage2 = await orch.add_goal(
        title="Unique test goal for duplicate detection",
        description="Testing duplicate detection in the triage engine",
        source="test",
    )
    assert goal2 is None  # Should not create a duplicate
    assert triage2.is_duplicate

    # Clean up
    from job_star.db import update_goal_status
    await update_goal_status(goal1.id, GoalStatus.COMPLETED)


@pytest.mark.asyncio
async def test_orchestrator_status(clean_db):
    """Orchestrator should return system status."""
    orch = Orchestrator()
    status = await orch.status()
    assert "total_goals" in status
    assert "active" in status
    assert "completed" in status
    assert "gateway_healthy" in status