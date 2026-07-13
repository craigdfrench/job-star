use crate::constraints::Constraints;
use crate::domain::DomainId;
use crate::status::GoalStatus;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use uuid::Uuid;

#[derive(Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize)]
pub struct GoalId(Uuid);

impl GoalId {
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }
    pub fn as_uuid(&self) -> &Uuid {
        &self.0
    }
}

impl Default for GoalId {
    fn default() -> Self {
        Self::new()
    }
}

/// A unit of work the supervisor oversees.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Goal {
    pub id: GoalId,
    /// Short title.
    pub title: String,
    /// Detailed description of intent.
    pub description: String,
    /// The domain this goal operates within.
    pub domain: DomainId,
    /// Constraints specific to this goal (merged with domain constraints).
    pub constraints: Constraints,
    pub status: GoalStatus,
    /// Parent goal, if this is a sub-goal.
    pub parent: Option<GoalId>,
    /// Depth in the goal tree (root = 0).
    pub depth: u32,
    pub created_at: DateTime<Utc>,
    #[serde(default)]
    pub updated_at: DateTime<Utc>,
    /// Free-form metadata.
    #[serde(default)]
    pub metadata: BTreeMap<String, String>,
    /// Success criteria: a list of checkable assertions.
    #[serde(default)]
    pub success_criteria: Vec<String>,
}

impl Goal {
    pub fn new(domain: DomainId, title: impl Into<String>) -> Self {
        let now = Utc::now();
        Self {
            id: GoalId::new(),
            title: title.into(),
            description: String::new(),
            domain,
            constraints: Constraints::default(),
            status: GoalStatus::Pending,
            parent: None,
            depth: 0,
            created_at: now,
            updated_at: now,
            metadata: BTreeMap::new(),
            success_criteria: Vec::new(),
        }
    }

    pub fn with_description(mut self, d: impl Into<String>) -> Self {
        self.description = d.into();
        self
    }

    pub fn with_constraints(mut self, c: Constraints) -> Self {
        self.constraints = c;
        self
    }

    pub fn with_success_criteria(mut self, criteria: Vec<String>) -> Self {
        self.success_criteria = criteria;
        self
    }

    pub fn touch(&mut self) {
        self.updated_at = Utc::now();
    }
}


// --- DUPLICATE BLOCK ---

//! Goals: units of work the supervisor oversees.

use crate::constraints::Constraints;
use crate::domain::DomainId;
use crate::status::GoalStatus;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use uuid::Uuid;

/// Stable identifier for a goal.
#[derive(
    Debug, Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Serialize, Deserialize,
)]
pub struct GoalId(Uuid);

impl GoalId {
    pub fn new() -> Self {
        Self(Uuid::new_v4())
    }
    pub fn as_uuid(&self) -> &Uuid {
        &self.0
    }
}

impl Default for GoalId {
    fn default() -> Self {
        Self::new()
    }
}

impl std::fmt::Display for GoalId {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// A unit of work the supervisor oversees. Goals form a tree via `parent`
/// and `depth`; the supervisor uses depth to enforce `MaxDepth` constraints.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Goal {
    pub id: GoalId,
    /// Short title.
    pub title: String,
    /// Detailed description of intent.
    #[serde(default)]
    pub description: String,
    /// The domain this goal operates within.
    pub domain: DomainId,
    /// Constraints specific to this goal (merged with domain constraints).
    #[serde(default)]
    pub constraints: Constraints,
    #[serde(default)]
    pub status: GoalStatus,
    /// Parent goal, if this is a sub-goal.
    #[serde(default)]
    pub parent: Option<GoalId>,
    /// Depth in the goal tree (root = 0).
    #[serde(default)]
    pub depth: u32,
    pub created_at: DateTime<Utc>,
    #[serde(default = "Utc::now")]
    pub updated_at: DateTime<Utc>,
    /// Free-form metadata.
    #[serde(default)]
    pub metadata: BTreeMap<String, String>,
    /// Checkable success criteria. The supervisor treats completion as
    /// "all criteria asserted satisfied by worker AND no constraint violations".
    #[serde(default)]
    pub success_criteria: Vec<String>,
}

impl Goal {
    pub fn new(domain: DomainId, title: impl Into<String>) -> Self {
        let now = Utc::now();
        Self {
            id: GoalId::new(),
            title: title.into(),
            description: String::new(),
            domain,
            constraints: Constraints::default(),
            status: GoalStatus::Pending,
            parent: None,
            depth: 0,
            created_at: now,
            updated_at: now,
            metadata: BTreeMap::new(),
            success_criteria: Vec::new(),
        }
    }

    pub fn with_description(mut self, d: impl Into<String>) -> Self {
        self.description = d.into();
        self
    }

    pub fn with_constraints(mut self, c: Constraints) -> Self {
        self.constraints = c;
        self
    }

    pub fn with_parent(mut self, parent: GoalId, depth: u32) -> Self {
        self.parent = Some(parent);
        self.depth = depth;
        self
    }

    pub fn with_success_criteria(mut self, criteria: Vec<String>) -> Self {
        self.success_criteria = criteria;
        self
    }

    pub fn with_metadata(mut self, k: impl Into<String>, v: impl Into<String>) -> Self {
        self.metadata.insert(k.into(), v.into());
        self
    }

    pub fn touch(&mut self) {
        self.updated_at = Utc::now();
    }

    pub fn set_status(&mut self, status: GoalStatus) {
        self.status = status;
        self.touch();
    }
}
