-- Migration 004: Witness evidence store
--
-- Creates the witness_evidence table for the independent witness service.
-- The witness captures ephemeral/stateful command executions (migrations,
-- backfills, deployments) as append-only, hash-chained evidence records.
--
-- The append-only trigger prevents UPDATE and DELETE, making the store
-- tamper-resistant. The hash chain (prev_hash + record_hash on each row)
-- makes retroactive tampering detectable.
--
-- See: job_star/witness/store.py, job_star/witness/service.py

CREATE TABLE IF NOT EXISTS witness_evidence (
    guid         TEXT PRIMARY KEY,
    timestamp    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    action       TEXT NOT NULL DEFAULT 'run_command',
    command      JSONB NOT NULL DEFAULT '[]',
    cwd          TEXT NOT NULL DEFAULT '',
    exit_code    INTEGER NOT NULL DEFAULT -1,
    stdout       TEXT NOT NULL DEFAULT '',
    stderr       TEXT NOT NULL DEFAULT '',
    output_hash  TEXT NOT NULL DEFAULT '',
    duration_ms  INTEGER NOT NULL DEFAULT 0,
    witness_id   TEXT NOT NULL DEFAULT 'job-star-witness-01',
    prev_hash    TEXT NOT NULL DEFAULT '',
    record_hash  TEXT NOT NULL DEFAULT ''
);

-- Append-only enforcement: block UPDATE and DELETE
CREATE OR REPLACE FUNCTION prevent_witness_modify()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'witness_evidence is append-only: % not allowed', TG_OP;
END;
$$ LANGUAGE plpgsql;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'witness_evidence_append_only'
    ) THEN
        CREATE TRIGGER witness_evidence_append_only
        BEFORE UPDATE OR DELETE ON witness_evidence
        FOR EACH ROW EXECUTE FUNCTION prevent_witness_modify();
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_witness_ts ON witness_evidence (timestamp DESC);
