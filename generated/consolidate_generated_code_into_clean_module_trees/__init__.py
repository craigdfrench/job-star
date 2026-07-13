"""Context gatherer: collects job-relevant context from sources."""
from __future__ import annotations

from jobstar.context_gatherer.gatherer import ContextGatherer
from jobstar.context_gatherer.models import ContextBundle, ContextItem

__all__ = ["ContextGatherer", "ContextBundle", "ContextItem"]


// --- DUPLICATE BLOCK ---

[workspace]
resolver = "2"
members = ["supervisor"]

[workspace.package]
version = "0.1.0"
edition = "2021"
license = "MIT"

[workspace.dependencies]
serde = { version = "1", features = ["derive"] }
serde_json = "1"
tokio = { version = "1", features = ["full"] }
thiserror = "1"
tracing = "0.1"
