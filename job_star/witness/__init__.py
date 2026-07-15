"""Witness — independent evidence-capture service.

The witness is a separate process that runs commands on behalf of the executor
and captures evidence in an append-only, hash-chained store. It exists to
provide proof-of-work for ephemeral/stateful operations that can't be
re-verified after the fact (migrations, deployments, backfills, builds).

Independence:
  - Runs as its own systemd unit (job-star-witness.service) on a Unix socket.
  - The executor calls it over the wire — it can't write to the evidence store.
  - The store is append-only (DB trigger prevents UPDATE/DELETE).
  - Records are hash-chained: each record includes the hash of the previous
    record, making retroactive tampering detectable.

Components:
  - store.py: append-only evidence store with hash chaining.
  - service.py: HTTP service (POST /observe, GET /evidence/{guid}, GET /health).
"""

from .store import EvidenceStore, EvidenceRecord
from .service import create_app, run_service

__all__ = ["EvidenceStore", "EvidenceRecord", "create_app", "run_service"]
