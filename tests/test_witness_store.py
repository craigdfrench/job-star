"""Tests for the witness evidence store — hash chaining and append-only.

Uses a real Postgres connection (same DB as job_star) to test the schema,
hash chain, and append-only trigger. Skipped if the DB is not available.
"""

from __future__ import annotations

import asyncio
import json
import os
import pytest

from job_star.witness.store import EvidenceStore, EvidenceRecord, compute_output_hash


async def _can_connect(dsn: str) -> bool:
    """Check if we can connect to the DB."""
    import asyncpg
    try:
        conn = await asyncpg.connect(dsn=dsn)
        await conn.close()
        return True
    except Exception:
        return False


# Use the job-star DB for witness tests (the witness_evidence table lives there)
TEST_DSN = os.environ.get("DATABASE_URL", "postgresql://jobstar:jobstar@localhost:5432/job_star")

# Check DB availability at import time without deprecated get_event_loop
_db_available = asyncio.new_event_loop().run_until_complete(_can_connect(TEST_DSN))

pytestmark = pytest.mark.skipif(
    not _db_available,
    reason="job_star DB not available",
)


@pytest.fixture
async def store():
    """Create an EvidenceStore and ensure schema, cleanup after."""
    s = EvidenceStore(dsn=TEST_DSN, witness_id="test-witness")
    await s.ensure_schema()
    yield s
    # Cleanup: delete test records (DELETE is blocked by trigger, so we
    # need to temporarily disable the trigger or use TRUNCATE)
    pool = await s._get_pool()
    async with pool.acquire() as conn:
        # TRUNCATE bypasses the trigger
        await conn.execute("TRUNCATE witness_evidence CASCADE")
    await s.close()


class TestEvidenceRecord:
    """Tests for the EvidenceRecord dataclass and hash computation."""

    def test_compute_hash_is_deterministic(self):
        """The same record data should produce the same hash."""
        ts = "2026-07-15T20:00:00+00:00"
        rec1 = EvidenceRecord(guid="ev_test1", timestamp=ts, exit_code=0, stdout="hello", prev_hash="sha256:abc")
        rec2 = EvidenceRecord(guid="ev_test1", timestamp=ts, exit_code=0, stdout="hello", prev_hash="sha256:abc")
        assert rec1.compute_hash() == rec2.compute_hash()

    def test_compute_hash_changes_on_content_change(self):
        """Different content should produce a different hash."""
        rec1 = EvidenceRecord(guid="ev_test1", exit_code=0, stdout="hello")
        rec2 = EvidenceRecord(guid="ev_test1", exit_code=0, stdout="world")
        assert rec1.compute_hash() != rec2.compute_hash()

    def test_compute_hash_includes_prev_hash(self):
        """The hash should change when prev_hash changes (chain linkage)."""
        rec1 = EvidenceRecord(guid="ev_test1", prev_hash="sha256:aaa")
        rec2 = EvidenceRecord(guid="ev_test1", prev_hash="sha256:bbb")
        assert rec1.compute_hash() != rec2.compute_hash()

    def test_compute_hash_format(self):
        """The hash should be a sha256-prefixed hex string."""
        rec = EvidenceRecord(guid="ev_test1")
        h = rec.compute_hash()
        assert h.startswith("sha256:")
        assert len(h) == 7 + 64  # "sha256:" + 64 hex chars


class TestEvidenceStore:
    """Tests for the EvidenceStore DB operations."""

    async def test_store_and_lookup(self, store):
        """A stored record should be retrievable by GUID."""
        record = EvidenceRecord(
            action="run_command",
            command=["echo", "hello"],
            cwd="/tmp",
            exit_code=0,
            stdout="hello\n",
            stderr="",
            output_hash=compute_output_hash("hello\n", ""),
        )
        guid = await store.store(record)
        assert guid.startswith("ev_")

        retrieved = await store.lookup(guid)
        assert retrieved is not None
        assert retrieved["guid"] == guid
        assert retrieved["exit_code"] == 0
        assert retrieved["stdout"] == "hello\n"

    async def test_lookup_nonexistent(self, store):
        """Looking up a non-existent GUID should return None."""
        result = await store.lookup("ev_nonexistent_12345")
        assert result is None

    async def test_hash_chain_links_records(self, store):
        """The second record's prev_hash should match the first's record_hash."""
        rec1 = EvidenceRecord(guid="ev_chain1", exit_code=0, stdout="first")
        guid1 = await store.store(rec1)

        rec2 = EvidenceRecord(guid="ev_chain2", exit_code=0, stdout="second")
        guid2 = await store.store(rec2)

        r1 = await store.lookup(guid1)
        r2 = await store.lookup(guid2)

        # The second record's prev_hash should equal the first's record_hash
        assert r2["prev_hash"] == r1["record_hash"]
        assert r1["prev_hash"] == ""  # first record has no predecessor

    async def test_verify_chain_intact(self, store):
        """The chain verification should pass for untampered records."""
        for i in range(3):
            rec = EvidenceRecord(
                guid=f"ev_chain_test_{i}",
                exit_code=0,
                stdout=f"output_{i}",
            )
            await store.store(rec)

        ok, msg = await store.verify_chain()
        assert ok is True
        assert "intact" in msg

    async def test_append_only_blocks_update(self, store):
        """The append-only trigger should block UPDATE."""
        rec = EvidenceRecord(guid="ev_append_test", exit_code=0, stdout="original")
        guid = await store.store(rec)

        pool = await store._get_pool()
        with pytest.raises(Exception, match="append-only"):
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE witness_evidence SET stdout = 'tampered' WHERE guid = $1",
                    guid,
                )

    async def test_append_only_blocks_delete(self, store):
        """The append-only trigger should block DELETE."""
        rec = EvidenceRecord(guid="ev_delete_test", exit_code=0, stdout="temp")
        guid = await store.store(rec)

        pool = await store._get_pool()
        with pytest.raises(Exception, match="append-only"):
            async with pool.acquire() as conn:
                await conn.execute("DELETE FROM witness_evidence WHERE guid = $1", guid)

    async def test_output_hash_stored(self, store):
        """The output_hash should be stored and retrievable."""
        stdout = "command output\n"
        stderr = ""
        ohash = compute_output_hash(stdout, stderr)
        rec = EvidenceRecord(
            guid="ev_hash_test",
            exit_code=0,
            stdout=stdout,
            stderr=stderr,
            output_hash=ohash,
        )
        guid = await store.store(rec)
        retrieved = await store.lookup(guid)
        assert retrieved["output_hash"] == ohash
