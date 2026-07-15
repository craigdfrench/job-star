"""Append-only, hash-chained evidence store.

Each evidence record captures:
  guid, timestamp, action, command, cwd, exit_code, stdout, stderr,
  output_hash, duration_ms, witness_id, prev_hash, record_hash

The hash chain works like a blockchain: each record's `record_hash` is
sha256(record_data + prev_hash). Tampering with any record breaks the chain
at that point. The store is append-only — an INSERT trigger prevents UPDATE
and DELETE.

Storage: a Postgres table `witness_evidence` in the job-star DB, with a
trigger that raises an exception on UPDATE/DELETE. Alternatively, a flat
append-only file for simpler deployments.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

import asyncpg

DEFAULT_DSN = os.environ.get("DATABASE_URL", "postgresql://jobstar:jobstar@localhost:5432/job_star")


@dataclass
class EvidenceRecord:
    """A single evidence record captured by the witness."""
    guid: str = field(default_factory=lambda: f"ev_{uuid4().hex[:16]}")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    action: str = "run_command"
    command: list[str] | str = field(default_factory=list)
    cwd: str = ""
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    output_hash: str = ""  # sha256 of stdout+stderr
    duration_ms: int = 0
    witness_id: str = "job-star-witness-01"
    prev_hash: str = ""  # hash of the previous record (chain)
    record_hash: str = ""  # hash of this record (computed on insert)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def compute_hash(self) -> str:
        """Compute the record hash from its contents + prev_hash."""
        data = {
            "guid": self.guid,
            "timestamp": self.timestamp,
            "action": self.action,
            "command": self.command,
            "cwd": self.cwd,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "output_hash": self.output_hash,
            "duration_ms": self.duration_ms,
            "witness_id": self.witness_id,
            "prev_hash": self.prev_hash,
        }
        return "sha256:" + hashlib.sha256(
            json.dumps(data, sort_keys=True).encode()
        ).hexdigest()


class EvidenceStore:
    """Append-only evidence store backed by Postgres.

    The `witness_evidence` table has a trigger that prevents UPDATE/DELETE,
    making it truly append-only. Records are hash-chained for tamper detection.
    """

    def __init__(self, dsn: str | None = None, witness_id: str = "job-star-witness-01"):
        self.dsn = dsn or DEFAULT_DSN
        self.witness_id = witness_id
        self._pool: Optional[asyncpg.Pool] = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None or getattr(self._pool, "_closed", False):
            self._pool = await asyncpg.create_pool(
                dsn=self.dsn, min_size=1, max_size=3, command_timeout=10,
            )
        return self._pool

    async def ensure_schema(self) -> None:
        """Create the witness_evidence table if it doesn't exist."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute("""
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
                )
            """)
            # Append-only trigger: prevent UPDATE and DELETE
            await conn.execute("""
                CREATE OR REPLACE FUNCTION prevent_witness_modify()
                RETURNS TRIGGER AS $$
                BEGIN
                    RAISE EXCEPTION 'witness_evidence is append-only: % not allowed', TG_OP;
                END;
                $$ LANGUAGE plpgsql
            """)
            await conn.execute("""
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
            """)
            await conn.execute("CREATE INDEX IF NOT EXISTS idx_witness_ts ON witness_evidence (timestamp DESC)")

    async def _get_last_hash(self) -> str:
        """Get the record_hash of the most recent evidence record."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT record_hash FROM witness_evidence ORDER BY timestamp DESC LIMIT 1"
            )
            return row["record_hash"] if row else ""

    async def store(self, record: EvidenceRecord) -> str:
        """Store an evidence record. Sets prev_hash and record_hash, returns guid."""
        record.witness_id = self.witness_id
        record.prev_hash = await self._get_last_hash()
        record.record_hash = record.compute_hash()

        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO witness_evidence
                   (guid, timestamp, action, command, cwd, exit_code,
                    stdout, stderr, output_hash, duration_ms, witness_id,
                    prev_hash, record_hash)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)""",
                record.guid,
                datetime.fromisoformat(record.timestamp.replace("Z", "+00:00"))
                    if "T" in record.timestamp else datetime.now(timezone.utc),
                record.action,
                json.dumps(record.command),
                record.cwd,
                record.exit_code,
                record.stdout[:10000],  # truncate large output
                record.stderr[:10000],
                record.output_hash,
                record.duration_ms,
                record.witness_id,
                record.prev_hash,
                record.record_hash,
            )
        return record.guid

    async def lookup(self, guid: str) -> dict[str, Any] | None:
        """Look up an evidence record by GUID. Returns None if not found."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM witness_evidence WHERE guid = $1", guid,
            )
            if not row:
                return None
            d = dict(row)
            # Parse command JSONB
            cmd = d.get("command")
            if isinstance(cmd, str):
                cmd = json.loads(cmd) if cmd else []
            d["command"] = cmd
            # Convert timestamp to ISO string
            ts = d.get("timestamp")
            if isinstance(ts, datetime):
                d["timestamp"] = ts.isoformat()
            return d

    async def verify_chain(self) -> tuple[bool, str]:
        """Verify the hash chain is intact. Returns (ok, message).

        Recomputes each record's hash and checks it matches, and checks that
        each prev_hash matches the previous record's record_hash.
        """
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM witness_evidence ORDER BY timestamp ASC"
            )
        prev_hash = ""
        for row in rows:
            d = dict(row)
            if d["prev_hash"] != prev_hash:
                return False, f"chain broken at {d['guid']}: prev_hash mismatch"
            # Recompute hash
            cmd = d.get("command")
            if isinstance(cmd, str):
                cmd = json.loads(cmd) if cmd else []
            rec = EvidenceRecord(
                guid=d["guid"],
                timestamp=d["timestamp"].isoformat() if isinstance(d["timestamp"], datetime) else str(d["timestamp"]),
                action=d["action"],
                command=cmd,
                cwd=d["cwd"],
                exit_code=d["exit_code"],
                stdout=d["stdout"],
                stderr=d["stderr"],
                output_hash=d["output_hash"],
                duration_ms=d["duration_ms"],
                witness_id=d["witness_id"],
                prev_hash=d["prev_hash"],
            )
            computed = rec.compute_hash()
            if computed != d["record_hash"]:
                return False, f"chain broken at {d['guid']}: record_hash mismatch (tampered)"
            prev_hash = d["record_hash"]
        return True, f"chain intact ({len(rows)} records)"

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


def compute_output_hash(stdout: str, stderr: str) -> str:
    """Compute a hash of the command output for tamper detection."""
    return "sha256:" + hashlib.sha256((stdout + stderr).encode()).hexdigest()
