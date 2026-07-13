use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

/// A capability that can be granted to a worker operating within a domain.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Capability {
    Read,
    Write,
    Execute,
    /// Create sub-goals / spawn workers.
    Spawn,
    /// Communicate with the supervisor (request escalation, report progress).
    Signal,
}

/// A set of capabilities. Using BTreeSet for deterministic serialization.
pub type CapabilitySet = BTreeSet<Capability>;

impl Capability {
    pub fn all() -> CapabilitySet {
        [Capability::Read, Capability::Write, Capability::Execute, Capability::Spawn, Capability::Signal]
            .into_iter()
            .collect()
    }

    pub fn read_only() -> CapabilitySet {
        [Capability::Read, Capability::Signal].into_iter().collect()
    }
}
