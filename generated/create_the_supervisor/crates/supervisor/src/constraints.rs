use crate::budget::Budget;
use crate::permission::{Capability, CapabilitySet};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

/// A constraint is the rule the supervisor enforces. It can be a capability
/// requirement, a budget, or a path/pattern restriction.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Constraint {
    /// Require a capability to be present (or absent).
    RequireCapability { capability: Capability },
    ForbidCapability { capability: Capability },
    /// Restrict writable/readable paths (domain-specific; interpreted by worker).
    PathAllow { patterns: Vec<String> },
    PathDeny { patterns: Vec<String> },
    /// Restrict which commands may be executed.
    ExecAllow { commands: Vec<String> },
    ExecDeny { commands: Vec<String> },
    /// Resource budget.
    Budget(Budget),
    /// Require human approval before proceeding past this point.
    RequireApproval { reason: String },
    /// Forbid spawning sub-goals beyond a depth.
    MaxDepth { depth: u32 },
}

/// A bundle of constraints attached to a domain or goal.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Constraints {
    pub items: Vec<Constraint>,
    /// Convenience: explicit capability set (derived from items, but cached).
    #[serde(default)]
    pub explicit_capabilities: Option<CapabilitySet>,
}

impl Constraints {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with(mut self, c: Constraint) -> Self {
        self.items.push(c);
        self
    }

    pub fn budget(mut self, b: Budget) -> Self {
        self.items.push(Constraint::Budget(b));
        self
    }

    pub fn require(mut self, cap: Capability) -> Self {
        self.items.push(Constraint::RequireCapability { capability: cap });
        self
    }

    pub fn forbid(mut self, cap: Capability) -> Self {
        self.items.push(Constraint::ForbidCapability { capability: cap });
        self
    }

    /// Extract the effective budget, if any. Last one wins.
    pub fn budget(&self) -> Option<&Budget> {
        self.items.iter().rev().find_map(|c| match c {
            Constraint::Budget(b) => Some(b),
            _ => None,
        })
    }

    /// Compute effective capability set from require/forbid items.
    pub fn effective_capabilities(&self, base: &CapabilitySet) -> CapabilitySet {
        let mut set = self.explicit_capabilities.clone().unwrap_or_else(|| base.clone());
        for c in &self.items {
            match c {
                Constraint::RequireCapability { capability } => {
                    set.insert(*capability);
                }
                Constraint::ForbidCapability { capability } => {
                    set.remove(capability);
                }
                _ => {}
            }
        }
        set
    }

    pub fn max_depth(&self) -> Option<u32> {
        self.items.iter().rev().find_map(|c| match c {
            Constraint::MaxDepth { depth } => Some(*depth),
            _ => None,
        })
    }

    pub fn requires_approval(&self) -> Option<&str> {
        self.items.iter().rev().find_map(|c| match c {
            Constraint::RequireApproval { reason } => Some(reason.as_str()),
            _ => None,
        })
    }
}


// --- DUPLICATE BLOCK ---

//! Constraints: the rules the supervisor enforces per domain and per goal.

use crate::budget::Budget;
use crate::permission::{Capability, CapabilitySet};
use serde::{Deserialize, Serialize};

/// A single constraint. Tagged enum so persisted constraints are self-describing.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Constraint {
    /// Require a capability to be present.
    RequireCapability { capability: Capability },
    /// Forbid a capability even if the domain grants it.
    ForbidCapability { capability: Capability },
    /// Restrict which paths may be read (patterns interpreted by the worker).
    PathAllow { patterns: Vec<String> },
    /// Forbid reading/writing these paths.
    PathDeny { patterns: Vec<String> },
    /// Allow only these executables to be run.
    ExecAllow { commands: Vec<String> },
    /// Forbid these executables.
    ExecDeny { commands: Vec<String> },
    /// Resource budget for the scope this constraint is attached to.
    Budget(Budget),
    /// Require human approval before proceeding.
    RequireApproval { reason: String },
    /// Forbid spawning sub-goals beyond this depth.
    MaxDepth { depth: u32 },
    /// Custom named constraint for domain-specific rules.
    Custom { name: String, payload: serde_json::Value },
}

/// A bundle of constraints attached to a domain or goal. Order matters for
/// "last wins" semantics on budget/depth; require/forbid compose additively.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Constraints {
    #[serde(default)]
    pub items: Vec<Constraint>,
    /// Optional explicit capability set; if present, used as the base before
    /// require/forbid items are applied.
    #[serde(default)]
    pub explicit_capabilities: Option<CapabilitySet>,
}

impl Constraints {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with(mut self, c: Constraint) -> Self {
        self.items.push(c);
        self
    }

    pub fn budget(mut self, b: Budget) -> Self {
        self.items.push(Constraint::Budget(b));
        self
    }

    pub fn require(mut self, cap: Capability) -> Self {
        self.items.push(Constraint::RequireCapability { capability: cap });
        self
    }

    pub fn forbid(mut self, cap: Capability) -> Self {
        self.items.push(Constraint::ForbidCapability { capability: cap });
        self
    }

    pub fn max_depth(mut self, depth: u32) -> Self {
        self.items.push(Constraint::MaxDepth { depth });
        self
    }

    pub fn require_approval(mut self, reason: impl Into<String>) -> Self {
        self.items.push(Constraint::RequireApproval {
            reason: reason.into(),
        });
        self
    }

    /// Effective budget, if any. Last `Budget` item wins.
    pub fn budget(&self) -> Option<&Budget> {
        self.items.iter().rev().find_map(|c| match c {
            Constraint::Budget(b) => Some(b),
            _ => None,
        })
    }

    /// Compute the effective capability set given a base (e.g. the domain's
    /// capabilities). Require adds, Forbid removes.
    pub fn effective_capabilities(&self, base: &CapabilitySet) -> CapabilitySet {
        let mut set = self
            .explicit_capabilities
            .clone()
            .unwrap_or_else(|| base.clone());
        for c in &self.items {
            match c {
                Constraint::RequireCapability { capability } => {
                    set.insert(*capability);
                }
                Constraint::ForbidCapability { capability } => {
                    set.remove(capability);
                }
                _ => {}
            }
        }
        set
    }

    /// Effective max spawn depth, if constrained. Last `MaxDepth` wins.
    pub fn max_depth(&self) -> Option<u32> {
        self.items.iter().rev().find_map(|c| match c {
            Constraint::MaxDepth { depth } => Some(*depth),
            _ => None,
        })
    }

    /// If this scope requires human approval, return the reason.
    pub fn requires_approval(&self) -> Option<&str> {
        self.items.iter().rev().find_map(|c| match c {
            Constraint::RequireApproval { reason } => Some(reason.as_str()),
            _ => None,
        })
    }

    pub fn path_allow(&self) -> impl Iterator<Item = &str> {
        self.items.iter().flat_map(|c| match c {
            Constraint::PathAllow { patterns } => patterns.iter().map(String::as_str).collect::<Vec<_>>().into_iter(),
            _ => Vec::new().into_iter(),
        })
    }

    pub fn path_deny(&self) -> impl Iterator<Item = &str> {
        self.items.iter().flat_map(|c| match c {
            Constraint::PathDeny { patterns } => patterns.iter().map(String::as_str).collect::<Vec<_>>().into_iter(),
            _ => Vec::new().into_iter(),
        })
    }

    pub fn exec_allow(&self) -> impl Iterator<Item = &str> {
        self.items.iter().flat_map(|c| match c {
            Constraint::ExecAllow { commands } => commands.iter().map(String::as_str).collect::<Vec<_>>().into_iter(),
            _ => Vec::new().into_iter(),
        })
    }

    pub fn exec_deny(&self) -> impl Iterator<Item = &str> {
        self.items.iter().flat_map(|c| match c {
            Constraint::ExecDeny { commands } => commands.iter().map(String::as_str).collect::<Vec<_>>().into_iter(),
            _ => Vec::new().into_iter(),
        })
    }
}


// --- DUPLICATE BLOCK ---

//! Constraint definitions and enforcement.
//!
//! Every domain has a set of permissions. A goal may further restrict
//! (but never broaden) the domain's permissions. The [`ConstraintPolicy`]
//! is the authoritative enforcement layer.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::error::{Result, SupervisorError};

/// A logical domain of operation (e.g. "filesystem", "network", "meta").
///
/// Domains are simple string identifiers so they can be extended without
/// recompiling the supervisor.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Domain(pub String);

impl Domain {
    pub fn new(s: impl Into<String>) -> Self {
        Self(s.into())
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// An action that a step wants to perform.
///
/// `Read`, `Write`, and `Execute` are the three coarse-grained categories.
/// Each can carry a free-form `target` string (e.g. a path, URL, command name)
/// so that constraint policies can be as fine-grained as needed.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Action {
    Read { target: String },
    Write { target: String },
    Execute { target: String },
}

impl Action {
    pub fn category(&self) -> &'static str {
        match self {
            Action::Read { .. } => "read",
            Action::Write { .. } => "write",
            Action::Execute { .. } => "execute",
        }
    }

    pub fn target(&self) -> &str {
        match self {
            Action::Read { target } => target,
            Action::Write { target } => target,
            Action::Execute { target } => target,
        }
    }
}

/// Permission for a single action category within a domain.
///
/// - `Allow` — always permitted.
/// - `Deny` — always denied.
/// - `AllowIfTarget(matches)` — permitted only if the action target matches
///   one of the provided glob-like patterns (simple prefix or exact match).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Permission {
    Allow,
    Deny,
    AllowIfTarget { patterns: Vec<String> },
}

impl Permission {
    /// Check whether a given target is permitted under this permission.
    pub fn permits(&self, target: &str) -> bool {
        match self {
            Permission::Allow => true,
            Permission::Deny => false,
            Permission::AllowIfTarget { patterns } => {
                patterns.iter().any(|p| target.starts_with(p) || p == target)
            }
        }
    }
}

/// The set of permissions for a single domain.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ConstraintSet {
    pub read: Permission,
    pub write: Permission,
    pub execute: Permission,
}

impl ConstraintSet {
    /// A fully permissive set (all actions allowed).
    pub fn permissive() -> Self {
        Self {
            read: Permission::Allow,
            write: Permission::Allow,
            execute: Permission::Allow,
        }
    }

    /// A fully restrictive set (all actions denied).
    pub fn restrictive() -> Self {
        Self {
            read: Permission::Deny,
            write: Permission::Deny,
            execute: Permission::Deny,
        }
    }

    /// Check an action against this constraint set.
    pub fn check(&self, action: &Action) -> bool {
        match action {
            Action::Read { target } => self.read.permits(target),
            Action::Write { target } => self.write.permits(target),
            Action::Execute { target } => self.execute.permits(target),
        }
    }
}

/// The full constraint policy: a map from domain to constraint set.
///
/// Policies are additive in the sense that a goal may layer additional
/// restrictions on top of a base domain policy. The [`merge_goal_restrictions`]
/// method produces a combined policy where the goal's permissions always
/// narrow the domain's.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ConstraintPolicy {
    /// Base domain permissions.
    pub domains: HashMap<Domain, ConstraintSet>,
}

impl ConstraintPolicy {
    pub fn new() -> Self {
        Self {
            domains: HashMap::new(),
        }
    }

    /// Register or replace a domain's constraint set.
    pub fn set_domain(&mut self, domain: Domain, set: ConstraintSet) {
        self.domains.insert(domain, set);
    }

    /// Get the constraint set for a domain, falling back to fully restrictive
    /// if the domain is unknown (fail-closed).
    pub fn get(&self, domain: &Domain) -> ConstraintSet {
        self.domains.get(domain).cloned().unwrap_or_else(ConstraintSet::restrictive)
    }

    /// Merge goal-level restrictions on top of the base policy.
    ///
    /// For each action category, the *more restrictive* of (domain, goal)
    /// wins. Concretely:
    /// - If either is `Deny`, the result is `Deny`.
    /// - If both are `AllowIfTarget`, the result is the intersection of patterns.
    /// - If one is `Allow` and the other is `AllowIfTarget`, the result is `AllowIfTarget`.
    /// - If both are `Allow`, the result is `Allow`.
    pub fn merge_goal_restrictions(
        &self,
        goal_restrictions: &HashMap<Domain, ConstraintSet>,
    ) -> ConstraintPolicy {
        let mut merged = self.clone();
        for (domain, goal_set) in goal_restrictions {
            let base = self.get(domain);
            let combined = ConstraintSet {
                read: merge_permission(&base.read, &goal_set.read),
                write: merge_permission(&base.write, &goal_set.write),
                execute: merge_permission(&base.execute, &goal_set.execute),
            };
            merged.domains.insert(domain.clone(), combined);
        }
        merged
    }

    /// Enforce: check whether an action in a domain is permitted.
    pub fn enforce(&self, domain: &Domain, action: &Action) -> Result<()> {
        let set = self.get(domain);
        if set.check(action) {
            Ok(())
        } else {
            Err(SupervisorError::ConstraintViolation {
                message: format!(
                    "action '{}' on target '{}' is not permitted in domain '{}'",
                    action.category(),
                    action.target(),
                    domain.as_str()
                ),
            })
        }
    }
}

/// Merge two permissions, taking the more restrictive result.
fn merge_permission(a: &Permission, b: &Permission) -> Permission {
    use Permission::*;
    match (a, b) {
        (Deny, _) | (_, Deny) => Deny,
        (Allow, Allow) => Allow,
        (Allow, AllowIfTarget { patterns }) |
        (AllowIfTarget { patterns }, Allow) => AllowIfTarget { patterns: patterns.clone() },
        (AllowIfTarget { patterns: pa }, AllowIfTarget { patterns: pb }) => {
            let intersection: Vec<String> = pa
                .iter()
                .filter(|p| pb.iter().any(|q| q == *p))
                .cloned()
                .collect();
            if intersection.is_empty() {
                Deny
            } else {
                AllowIfTarget { patterns: intersection }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_permission_allow() {
        assert!(Permission::Allow.permits("/anything"));
    }

    #[test]
    fn test_permission_deny() {
        assert!(!Permission::Deny.permits("/anything"));
    }

    #[test]
    fn test_permission_allow_if_target() {
        let p = Permission::AllowIfTarget {
            patterns: vec!["/tmp/".to_string(), "/var/log/app".to_string()],
        };
        assert!(p.permits("/tmp/foo.txt"));
        assert!(p.permits("/var/log/app"));
        assert!(!p.permits("/etc/passwd"));
    }

    #[test]
    fn test_enforce_fail_closed() {
        let policy = ConstraintPolicy::new();
        let domain = Domain::new("unknown");
        let action = Action::Read { target: "/x".into() };
        assert!(policy.enforce(&domain, &action).is_err());
    }

    #[test]
    fn test_merge_goal_restrictions_narrows() {
        let mut base = ConstraintPolicy::new();
        base.set_domain(
            Domain::new("fs"),
            ConstraintSet {
                read: Permission::Allow,
                write: Permission::AllowIfTarget { patterns: vec!["/tmp/".into()] },
                execute: Permission::Deny,
            },
        );

        let mut goal = HashMap::new();
        goal.insert(
            Domain::new("fs"),
            ConstraintSet {
                read: Permission::AllowIfTarget { patterns: vec!["/tmp/".into()] },
                write: Permission::Allow,
                execute: Permission::Deny,
            },
        );

        let merged = base.merge_goal_restrictions(&goal);
        let set = merged.get(&Domain::new("fs"));

        // read: Allow ∩ AllowIfTarget(/tmp/) => AllowIfTarget(/tmp/)
        assert!(set.read.permits("/tmp/x"));
        assert!(!set.read.permits("/etc/x"));

        // write: AllowIfTarget(/tmp/) ∩ Allow => AllowIfTarget(/tmp/)
        assert!(set.write.permits("/tmp/x"));
        assert!(!set.write.permits("/etc/x"));

        // execute: Deny ∩ Deny => Deny
        assert!(!set.execute.permits("anything"));
    }
}
