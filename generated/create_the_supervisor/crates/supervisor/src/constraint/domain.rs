//! Domains that the supervisor can control access to.

use serde::{Deserialize, Serialize};
use std::fmt;

/// A domain of resources/actions the supervisor governs.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Domain {
    /// Local filesystem access.
    Filesystem,
    /// Network requests (HTTP, TCP, etc.).
    Network,
    /// Spawning and managing child processes.
    Process,
    /// In-memory data structures / shared state.
    Memory,
    /// External API calls (distinct from raw network — includes auth).
    Api,
    /// Meta-operations: modifying the supervisor itself, policy, goals.
    Meta,
}

impl fmt::Display for Domain {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Domain::Filesystem => write!(f, "filesystem"),
            Domain::Network => write!(f, "network"),
            Domain::Process => write!(f, "process"),
            Domain::Memory => write!(f, "memory"),
            Domain::Api => write!(f, "api"),
            Domain::Meta => write!(f, "meta"),
        }
    }
}

impl std::str::FromStr for Domain {
    type Err = String;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "filesystem" | "fs" => Ok(Domain::Filesystem),
            "network" | "net" => Ok(Domain::Network),
            "process" | "proc" => Ok(Domain::Process),
            "memory" | "mem" => Ok(Domain::Memory),
            "api" => Ok(Domain::Api),
            "meta" => Ok(Domain::Meta),
            other => Err(format!("unknown domain: {}", other)),
        }
    }
}
