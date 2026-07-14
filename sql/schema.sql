-- Job-Star Seed Schema
-- This is the first thing that exists. Everything else is built around this.
-- This schema IS the goal registry. The foundation of the entire system.

-- Enable UUID generation
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ============================================================================
-- GOALS: The core of the system. Every direction, task, observation, and
-- thought that enters the system becomes a goal (or is attached to one).
-- ============================================================================
CREATE TABLE goals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_id UUID REFERENCES goals(id) ON DELETE SET NULL,

    title TEXT NOT NULL,
    description TEXT,

    -- Classification
    domain TEXT NOT NULL DEFAULT 'coding',
        -- coding, personal, infra, meta (for job-star's own goals)
    status TEXT NOT NULL DEFAULT 'active',
        -- active, paused, completed, blocked, abandoned
    urgency TEXT NOT NULL DEFAULT 'soon',
        -- imperative, soon, idle-opportunistic, timed

    -- Progress tracking
    progress FLOAT NOT NULL DEFAULT 0.0,
    blockers TEXT[] DEFAULT '{}',

    -- Deadline (for timed goals)
    deadline TIMESTAMP,

    -- Where did this come from?
    source TEXT NOT NULL DEFAULT 'intake',
        -- intake, planner, user, system

    -- Expert agent that owns this goal (NULL = generic pool)
    expert TEXT,

    -- Who requested this goal (for multi-user/family instances)
    requested_by TEXT,
        -- e.g. "craig@thefrenches.ca" or "sarah"
        -- e.g. 'gatehouse-ai' — only workers with matching affinity can claim

    -- Vikunja task ID if this goal was synced from a Vikunja task
    vikunja_task_id INTEGER,

    -- Flexible metadata
    metadata JSONB DEFAULT '{}',

    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- GOAL STEPS: A goal is broken into steps. Each step is a unit of work
-- that an AI agent can execute.
-- ============================================================================
CREATE TABLE goal_steps (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,

    title TEXT NOT NULL,
    description TEXT,

    status TEXT NOT NULL DEFAULT 'pending',
        -- pending, in_progress, completed, failed, blocked

    order_index INTEGER NOT NULL,

    -- DAG dependencies: this step can only be claimed when all steps in
    -- depends_on are completed. Empty array = no deps (series via order_index).
    depends_on UUID[] DEFAULT '{}',

    -- What was the result of executing this step?
    result JSONB,

    -- Which model was used?
    model TEXT,

    -- Token/cost tracking
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost FLOAT DEFAULT 0.0,

    attempted_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- AUDIT TRAIL: Every action is logged. This is the security foundation.
-- Nothing in this table can be deleted or modified by any job-star.
-- ============================================================================
CREATE TABLE audit_trail (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id UUID REFERENCES goals(id) ON DELETE SET NULL,
    step_id UUID REFERENCES goal_steps(id) ON DELETE SET NULL,

    event TEXT NOT NULL,
        -- goal_created, goal_updated, goal_completed, goal_abandoned,
        -- step_created, step_started, step_completed, step_failed,
        -- ai_called, constraint_violated, conflict_detected, etc.

    details JSONB DEFAULT '{}',

    -- What AI model was used?
    model TEXT,

    -- Cost tracking
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost FLOAT DEFAULT 0.0,

    timestamp TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- GOAL CONFLICTS: When two goals conflict, it's recorded here.
-- AI-driven detection happens later; for now this supports manual flagging.
-- ============================================================================
CREATE TABLE goal_conflicts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_a_id UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    goal_b_id UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,

    conflict_type TEXT NOT NULL,
        -- duplicate, contradictory, competing_resource, tension

    description TEXT,
    resolution TEXT DEFAULT 'unresolved',
        -- unresolved, auto_merged, user_decided, dismissed

    detected_at TIMESTAMP NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMP
);

-- ============================================================================
-- DECISIONS: Every decision is recorded with its shadow paths
-- (the alternatives that were considered but not chosen).
-- ============================================================================
CREATE TABLE decisions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id UUID REFERENCES goals(id) ON DELETE CASCADE,

    decision TEXT NOT NULL,
    reasoning TEXT,

    -- What other options were considered?
    alternatives_considered JSONB DEFAULT '[]',

    decided_by TEXT NOT NULL,
        -- ai, user

    decided_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- JOB QUEUE: Work requests that are not tied to pre-existing steps.
-- Allows the API to enqueue planning or other goal-level work.
-- ============================================================================
CREATE TABLE job_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id UUID REFERENCES goals(id) ON DELETE CASCADE,

    kind TEXT NOT NULL DEFAULT 'plan',
        -- plan, execute, notify, ask
    status TEXT NOT NULL DEFAULT 'pending',
        -- pending, claimed, completed, failed
    priority INTEGER NOT NULL DEFAULT 0,

    payload JSONB DEFAULT '{}',

    worker_id TEXT,
    claimed_at TIMESTAMP,
    completed_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_job_queue_status_priority ON job_queue(status, priority DESC, created_at);
CREATE INDEX idx_job_queue_goal ON job_queue(goal_id);

-- ============================================================================
-- CHECK-INS: Structured two-way progress dialogue between job-star and user.
-- Created at key lifecycle points (progress, clarification, milestone, completion).
-- AI-generated content, persistent, asynchronous, actionable.
-- ============================================================================
CREATE TABLE check_ins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    step_id UUID REFERENCES goal_steps(id) ON DELETE SET NULL,

    type TEXT NOT NULL DEFAULT 'progress',
        -- progress, clarification, milestone, completion
    status TEXT NOT NULL DEFAULT 'draft',
        -- draft, sent, awaiting_response, responded, actioned, expired

    -- Structured content (AI-generated)
    progress_summary TEXT,
    next_steps TEXT,
    results TEXT,
    questions JSONB DEFAULT '[]',
        -- [{id, question, type, options[], required, answer}]

    -- User response
    response TEXT,
    decisions JSONB DEFAULT '[]',
        -- [{question_id, answer}]
    responded_at TIMESTAMP,

    -- Metadata
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_checkins_goal ON check_ins(goal_id);
CREATE INDEX idx_checkins_status ON check_ins(status);
CREATE INDEX idx_checkins_type ON check_ins(type);

CREATE TRIGGER checkins_updated_at
    BEFORE UPDATE ON check_ins
    FOR EACH ROW
    EXECUTE FUNCTION update_timestamp();

-- ============================================================================
-- SCHEMA MIGRATIONS: Track which DB migrations have been applied.
-- The upgrade tool reads sql/migrations/ and applies pending ones.
-- ============================================================================
CREATE TABLE schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT,
    applied_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ============================================================================
-- WORKER REGISTRY: Blue-green drain management.
-- Workers register on startup, send heartbeats, and check for drain signals.
-- The upgrade tool sets draining=TRUE for old workers to drain them gracefully.
-- ============================================================================
CREATE TABLE worker_registry (
    worker_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL DEFAULT 1,
    draining BOOLEAN NOT NULL DEFAULT FALSE,
    last_heartbeat TIMESTAMP,
    current_step_id UUID,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);

-- ============================================================================
-- EVENTS: Distributed pub/sub for SSE and cross-machine notifications.
-- Consumers read from this table and delete or archive old events.
-- ============================================================================
CREATE TABLE events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_events_created_at ON events(created_at);
CREATE INDEX idx_events_type ON events(type);

-- ============================================================================
-- INDEXES
-- ============================================================================
CREATE INDEX idx_goals_status ON goals(status);
CREATE INDEX idx_goals_urgency ON goals(urgency);
CREATE INDEX idx_goals_domain ON goals(domain);
CREATE INDEX idx_goals_expert ON goals(expert);
CREATE INDEX idx_goals_parent ON goals(parent_id);
CREATE INDEX idx_goals_updated ON goals(updated_at DESC);

-- ============================================================================
-- EXPERTS: Registry of expert agents that own specific topics/codebases.
-- Each expert can be pinned to a specific machine (required_machine) so only
-- workers on that machine can claim its goals.
-- ============================================================================
CREATE TABLE IF NOT EXISTS experts (
    name TEXT PRIMARY KEY,
    description TEXT,
    required_machine TEXT,   -- NULL = any machine can claim
    context_path TEXT,        -- local path for curated context (docs/codebase)
    repo_path TEXT,           -- git repo path for PR-based execution
    test_command TEXT,        -- command to run tests (e.g. 'go test ./...')
    base_branch TEXT,         -- branch to create PRs against (default 'main')
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

INSERT INTO experts (name, description, required_machine, context_path, repo_path, test_command, base_branch) VALUES
('gatehouse-ai', 'Gatehouse-AI developer expert (curated docs + codebase knowledge)', 'DESKTOP-RNK6J72', '/home/craig/gatehouse-ai', '/home/craig/gatehouse-ai', 'go test ./...', 'main'),
('research', 'Recurring research/tickle-file agent (monitors topics during idle time)', NULL, NULL, NULL, NULL, NULL)
ON CONFLICT (name) DO NOTHING;

CREATE INDEX idx_steps_goal ON goal_steps(goal_id);
CREATE INDEX idx_steps_status ON goal_steps(status);
CREATE INDEX idx_steps_order ON goal_steps(goal_id, order_index);

CREATE INDEX idx_audit_goal ON audit_trail(goal_id);
CREATE INDEX idx_audit_timestamp ON audit_trail(timestamp DESC);

CREATE INDEX idx_conflicts_a ON goal_conflicts(goal_a_id);
CREATE INDEX idx_conflicts_b ON goal_conflicts(goal_b_id);
CREATE INDEX idx_conflicts_unresolved ON goal_conflicts(resolution) WHERE resolution = 'unresolved';
-- Prevent duplicate conflict rows (same pair + type, regardless of order)
CREATE UNIQUE INDEX idx_conflicts_unique ON goal_conflicts (
    LEAST(goal_a_id, goal_b_id), GREATEST(goal_a_id, goal_b_id), conflict_type
);

-- ============================================================================
-- HELPER: updated_at trigger
-- ============================================================================
CREATE OR REPLACE FUNCTION update_timestamp()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER goals_updated_at
    BEFORE UPDATE ON goals
    FOR EACH ROW
    EXECUTE FUNCTION update_timestamp();

CREATE TRIGGER steps_updated_at
    BEFORE UPDATE ON goal_steps
    FOR EACH ROW
    EXECUTE FUNCTION update_timestamp();

-- ============================================================================
-- SEED: The first goals. Job-star building job-star.
-- ============================================================================
INSERT INTO goals (title, description, domain, urgency, source, metadata) VALUES
(
    'Build Job-Star: Create the Postgres schema',
    'Create the foundational goal registry schema in Postgres. This is the first component of the job-star system.',
    'meta', 'imperative', 'user',
    '{"component": "goal_registry", "bootstrap": true}'
),
(
    'Build Job-Star: Create the seed CLI',
    'Build a minimal TypeScript CLI that can add goals, list goals, show goal details, and trigger AI to work on a goal.',
    'meta', 'imperative', 'user',
    '{"component": "seed_cli", "bootstrap": true}'
),
(
    'Build Job-Star: Create the triage engine',
    'Build a Python service that classifies incoming intake requests by domain, urgency, and type. Checks for duplicates against the goal registry.',
    'meta', 'soon', 'user',
    '{"component": "triage", "bootstrap": true}'
),
(
    'Build Job-Star: Create the context gatherer',
    'Build a lightweight agent that examines intake requests and gathers related files, git history, and recent errors before triage.',
    'meta', 'soon', 'user',
    '{"component": "context_gatherer", "bootstrap": true}'
),
(
    'Build Job-Star: Create the router',
    'Build a routing service that picks the right AI model based on task complexity, urgency, cost budget, and model availability. Uses LiteLLM.',
    'meta', 'soon', 'user',
    '{"component": "router", "bootstrap": true}'
),
(
    'Build Job-Star: Create the supervisor',
    'Build the supervision core in Rust. Enforces constraints (read/write/execute per domain and goal), monitors progress, detects loops/budget overruns/blockers, escalates when uncertain.',
    'meta', 'soon', 'user',
    '{"component": "supervisor", "bootstrap": true}'
),
(
    'Build Job-Star: Create the idle loop',
    'Build a background process that checks resource availability every N minutes, picks the next step from the idle-opportunistic queue, checks for conflicts, executes under supervision, and updates progress.',
    'meta', 'idle-opportunistic', 'user',
    '{"component": "idle_loop", "bootstrap": true}'
),
(
    'Build Job-Star: Create the follow-up engine',
    'Build a notification service that receives escalations, classifies urgency (interrupt/batch/silent), surfaces through appropriate channels, and respects user flow state.',
    'meta', 'idle-opportunistic', 'user',
    '{"component": "followup_engine", "bootstrap": true}'
),
(
    'Build Job-Star: Create the web intake surface',
    'Build a local web app for rich intake: screenshots, voice recording, file upload, visual context.',
    'meta', 'idle-opportunistic', 'user',
    '{"component": "web_intake", "bootstrap": true}'
),
(
    'Build Job-Star: Create the Telegram integration',
    'Integrate Telegram as an intake channel for zero-friction mobile input (voice, text).',
    'meta', 'idle-opportunistic', 'user',
    '{"component": "telegram_intake", "bootstrap": true}'
),
(
    'Build Job-Star: Create the conflict detection engine',
    'AI-driven conflict detection between goals: duplicates, contradictions, competing resources, and tensions. Cross-domain awareness.',
    'meta', 'idle-opportunistic', 'user',
    '{"component": "conflict_detection", "bootstrap": true}'
),
(
    'Build Job-Star: Integrate with gatehouse-ai async jobs',
    'Connect job-star execution layer to gatehouse-ai''s existing async job interface. Job-star becomes the intelligent client that decides what to execute, when, and how.',
    'meta', 'soon', 'user',
    '{"component": "gatehouse_integration", "bootstrap": true}'
);

-- Log the seed creation in audit trail
INSERT INTO audit_trail (event, details)
VALUES ('system_created', '{"message": "Job-Star seed database initialized. The loop begins."}');