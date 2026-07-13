-- Migration 002: Check-in system + worker registry + schema versioning
-- Already applied to the running database.

CREATE TABLE IF NOT EXISTS check_ins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    step_id UUID REFERENCES goal_steps(id) ON DELETE SET NULL,
    type TEXT NOT NULL DEFAULT 'progress',
    status TEXT NOT NULL DEFAULT 'draft',
    progress_summary TEXT,
    next_steps TEXT,
    results TEXT,
    questions JSONB DEFAULT '[]',
    response TEXT,
    decisions JSONB DEFAULT '[]',
    responded_at TIMESTAMP,
    created_by TEXT NOT NULL DEFAULT 'system',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_checkins_goal ON check_ins(goal_id);
CREATE INDEX IF NOT EXISTS idx_checkins_status ON check_ins(status);
CREATE INDEX IF NOT EXISTS idx_checkins_type ON check_ins(type);
CREATE TRIGGER IF NOT EXISTS checkins_updated_at
    BEFORE UPDATE ON check_ins
    FOR EACH ROW
    EXECUTE FUNCTION update_timestamp();

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    checksum TEXT,
    applied_at TIMESTAMP NOT NULL DEFAULT NOW()
);

-- Worker registry for blue-green drain management
CREATE TABLE IF NOT EXISTS worker_registry (
    worker_id TEXT PRIMARY KEY,
    generation INTEGER NOT NULL DEFAULT 1,
    draining BOOLEAN NOT NULL DEFAULT FALSE,
    last_heartbeat TIMESTAMP,
    current_step_id UUID,
    started_at TIMESTAMP NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'
);
