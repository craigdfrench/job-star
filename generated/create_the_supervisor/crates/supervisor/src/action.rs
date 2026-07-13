//! Action and capability definitions.

use crate::supervisor::constraint::domain::Domain;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

/// Unique identifier for an action (for tracking).
pub type ActionId = u64;

/// The type of operation being requested.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Capability {
    /// Read data from a resource.
    Read,
    /// Write/modify data in a resource.
    Write,
    /// Execute a program or command.
    Execute,
    /// List contents of a resource.
    List,
    /// Delete a resource.
    Delete,
    /// Spawn a new process or sub-task.
    Spawn,
}

/// A resource path — what the action targets.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ResourcePath {
    /// A file or directory path.
    File(PathBuf),
    /// A URL.
    Url(String),
    /// A process name or PID reference.
    Process(String),
    /// An API endpoint identifier.
    ApiEndpoint(String),
    /// A key in shared memory / state.
    MemoryKey(String),
    /// No specific resource (e.g., a meta-operation).
    None,
}

impl std::fmt::Display for ResourcePath {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ResourcePath::File(p) => write!(f, "file:{}", p.display()),
            ResourcePath::Url(u) => write!(f, "url:{}", u),
            ResourcePath::Process(p) => write!(f, "process:{}", p),
            ResourcePath::ApiEndpoint(e) => write!(f, "api:{}", e),
            ResourcePath::MemoryKey(k) => write!(f, "mem:{}", k),
            ResourcePath::None => write!(f, "none"),
        }
    }
}

/// A proposed action by a worker, to be evaluated by the supervisor.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Action {
    pub id: ActionId,
    pub domain: Domain,
    pub capability: Capability,
    pub resource: ResourcePath,
    /// Human-readable description of intent.
    pub description: String,
    /// Estimated cost in tokens (if known).
    pub estimated_tokens: Option<u64>,
}

impl Action {
    pub fn new(domain: Domain, capability: Capability, resource: ResourcePath) -> Self {
        Self {
            id: 0, // assigned by caller or tracker
            domain,
            capability,
            resource,
            description: String::new(),
            estimated_tokens: None,
        }
    }

    pub fn with_description(mut self, desc: impl Into<String>) -> Self {
        self.description = desc.into();
        self
    }

    pub fn with_tokens(mut self, tokens: u64) -> Self {
        self.estimated_tokens = Some(tokens);
        self
    }

    /// Produce a signature for loop detection (domain + capability + resource).
    pub fn signature(&self) -> String {
        format!("{}|{:?}|{}", self.domain, self.capability, self.resource)
    }
}
