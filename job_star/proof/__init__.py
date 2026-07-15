"""Proof-of-work package — independent verification of declared artifacts.

Separation of duties:
  - The implementor (executor) declares Artifacts (what it claims it did).
  - The verifier independently re-checks each claim against ground truth.
  - The witness (job_star.witness) captures ephemeral evidence that can't be
    re-verified after the fact (migrations, deployments, backfills).

See verifier.py for the verification logic and witness_client.py for the
witness HTTP client.
"""

from .verifier import verify_artifacts, verify_artifact, VerificationResult
from .witness_client import WitnessClient, WitnessError

__all__ = [
    "verify_artifacts",
    "verify_artifact",
    "VerificationResult",
    "WitnessClient",
    "WitnessError",
]
