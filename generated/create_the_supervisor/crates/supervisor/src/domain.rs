use crate::constraints::Constraints;
use crate::permission::{Capability, CapabilitySet};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use uuid::Uuid;

/// Identifier for a domain.
#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct DomainId(Uuid);

impl DomainId {
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }
    pub fn as_uuid(&self) -> &Uuid {
        &self.0
    }
}

impl Default for DomainId {
    fn default() -> Self {
        Self::new()
    }
}

/// A logical scope of operation. Domains partition the world a worker may touch
/// and carry the constraints that apply within them.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Domain {
    pub id: DomainId,
    /// Human-readable name, e.g. "filesystem", "network", "meta".
    pub name: String,
    /// Free-form description of what this domain covers.
    pub description: String,
    /// Capabilities granted within this domain by default.
    pub capabilities: CapabilitySet,
    /// Constraints scoped to this domain (may be empty = inherit parent).
    pub constraints: Constraints,
    /// Optional parent domain; child inherits unless overridden.
    pub parent: Option<DomainId>,
}

impl Domain {
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            id: DomainId::new(),
            name: name.into(),
            description: String::new(),
            capabilities: Capability::read_only(),
            constraints: Constraints::default(),
            parent: None,
        }
    }

    pub fn with_capabilities(mut self, caps: CapabilitySet) -> Self {
        self.capabilities = caps;
        self
    }

    pub fn with_description(mut self, desc: impl Into<String>) -> Self {
        self.description = desc.into();
        self
    }

    pub fn with_constraints(mut self, c: Constraints) -> Self {
        self.constraints = c;
        self
    }

    /// Effective capabilities, optionally merged with a parent.
    pub fn effective_capabilities(&self, parent_caps: Option<&CapabilitySet>) -> CapabilitySet {
        match parent_caps {
            Some(p) => self.capabilities.intersection(p).cloned().collect(),
            None => self.capabilities.clone(),
        }
    }
}

/// Registry of known domains.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct DomainRegistry {
    pub domains: BTreeMap<DomainId, Domain>,
}

impl DomainRegistry {
    pub fn register(&mut self, domain: Domain) -> &Domain {
        let id = domain.id.clone();
        self.domains.insert(id.clone(), domain);
        self.domains.get(&id).unwrap()
    }

    pub fn get(&self, id: &DomainId) -> Option<&Domain> {
        self.domains.get(id)
    }

    /// Resolve the full chain from a domain up to its root.
    pub fn ancestry(&self, id: &DomainId) -> Vec<DomainId> {
        let mut chain = vec![id.clone()];
        let mut cursor = id.clone();
        while let Some(d) = self.domains.get(&cursor) {
            match &d.parent {
                Some(p) => {
                    chain.push(p.clone());
                    cursor = p.clone();
                }
                None => break,
            }
        }
        chain
    }
}


// --- DUPLICATE BLOCK ---

//! Domains: logical scopes of operation that carry their own constraints.

use crate::constraints::Constraints;
use crate::permission::{Capability, CapabilitySet};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use uuid::Uuid;

/// Stable identifier for a domain.
#[derive(
    Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize,
)]
pub struct DomainId(Uuid);

impl DomainId {
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }
    pub fn as_uuid(&self) -> &Uuid {
        &self.0
    }
}

impl Default for DomainId {
    fn default() -> Self {
        Self::new()
    }
}

impl std::fmt::Display for DomainId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// A logical scope of operation. Domains partition the world a worker may touch
/// and carry the constraints that apply within them. Domains may nest: a child
/// domain inherits its parent's capabilities (intersected) unless it overrides.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Domain {
    pub id: DomainId,
    /// Human-readable name, e.g. "filesystem", "network", "meta".
    pub name: String,
    /// Free-form description of what this domain covers.
    #[serde(default)]
    pub description: String,
    /// Capabilities granted within this domain by default.
    #[serde(default = "Capability::read_only")]
    pub capabilities: CapabilitySet,
    /// Constraints scoped to this domain (may be empty = inherit parent).
    #[serde(default)]
    pub constraints: Constraints,
    /// Optional parent domain; child inherits unless overridden.
    #[serde(default)]
    pub parent: Option<DomainId>,
}

impl Domain {
    pub fn new(name: impl Into<String>) -> Self {
        Self {
            id: DomainId::new(),
            name: name.into(),
            description: String::new(),
            capabilities: Capability::read_only(),
            constraints: Constraints::default(),
            parent: None,
        }
    }

    pub fn with_capabilities(mut self, caps: CapabilitySet) -> Self {
        self.capabilities = caps;
        self
    }

    pub fn with_description(mut self, desc: impl Into<String>) -> Self {
        self.description = desc.into();
        self
    }

    pub fn with_constraints(mut self, c: Constraints) -> Self {
        self.constraints = c;
        self
    }

    pub fn with_parent(mut self, parent: DomainId) -> Self {
        self.parent = Some(parent);
        self
    }

    /// Effective capabilities given an optional parent set. A child can only
    /// hold capabilities its parent also holds (intersection). This prevents
    /// privilege escalation via domain nesting.
    pub fn effective_capabilities(&self, parent_caps: Option<&CapabilitySet>) -> CapabilitySet {
        match parent_caps {
            Some(p) => self.capabilities.intersection(p).cloned().collect(),
            None => self.capabilities.clone(),
        }
    }
}

/// Registry of known domains. The supervisor consults this to resolve
/// constraints and capabilities for any goal.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct DomainRegistry {
    pub domains: BTreeMap<DomainId, Domain>,
}

impl DomainRegistry {
    pub fn register(&mut self, domain: Domain) -> &Domain {
        let id = domain.id.clone();
        self.domains.insert(id.clone(), domain);
        self.domains.get(&id).expect("just inserted")
    }

    pub fn get(&self, id: &DomainId) -> Option<&Domain> {
        self.domains.get(id)
    }

    pub fn get_mut(&mut self, id: &DomainId) -> Option<&mut Domain> {
        self.domains.get_mut(id)
    }

    /// Resolve the chain from a domain up to its root (inclusive).
    /// Cycles are defensively truncated at 64 hops.
    pub fn ancestry(&self, id: &DomainId) -> Vec<DomainId> {
        let mut chain = vec![id.clone()];
        let mut cursor = id.clone();
        for _ in 0..64 {
            match self.domains.get(&cursor) {
                Some(d) if d.parent.is_some() => {
                    let p = d.parent.clone().unwrap();
                    if chain.contains(&p) {
                        break; // cycle guard
                    }
                    chain.push(p.clone());
                    cursor = p;
                }
                _ => break,
            }
        }
        chain
    }

    /// Compute the effective capabilities for a domain by walking its ancestry
    /// and intersecting at each level.
    pub fn effective_capabilities(&self, id: &DomainId) -> Option<CapabilitySet> {
        let chain = self.ancestry(id);
        let mut caps: Option<CapabilitySet> = None;
        for did in chain {
            if let Some(d) = self.domains.get(&did) {
                caps = Some(match caps {
                    None => d.capabilities.clone(),
                    Some(parent) => d.effective_capabilities(Some(&parent)),
                });
            }
        }
        caps
    }
}
