//! Enforcement decisions returned by the constraint engine.

use crate::supervisor::constraint::action::Capability;
use crate::supervisor::constraint::domain::Domain;
use crate::supervisor::constraint::action::ResourcePath;
use serde::{Deserialize, Serialize};

/// The outcome of evaluating an action against constraints.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Decision {
    /// The action is permitted. Proceed.
    Allow,
    /// The action is forbidden. Do not execute.
    Deny(DenialReason),
    /// The action is borderline. Request human review before proceeding.
    Escalate(EscalationReason),
}

/// Why an action was denied.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum DenialReason {
    /// The domain is not in the allowed set.
    DomainForbidden(Domain),
    /// The capability is not permitted for this domain.
    CapabilityForbidden {
        domain: Domain,
        capability: Capability,
    },
    /// The resource is outside the allowed scope.
    ResourceOutOfScope {
        domain: Domain,
        resource: String,
        allowed_prefixes: Vec<String>,
    },
    /// Not enough budget remaining.
    BudgetExhausted {
        unit: crate::supervisor::constraint::budget::BudgetUnit,
        remaining: u64,
        required: u64,
    },
    /// Action violates a hard safety rule.
    SafetyViolation(String),
}

/// Why an action needs escalation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum EscalationReason {
    /// The same action has been repeated too many times.
    LoopDetected {
        signature: String,
        count: usize,
    },
    /// The action touches a boundary that requires human confirmation.
    BoundaryAction {
        domain: Domain,
        capability: Capability,
        resource: ResourcePath,
    },
    /// The worker reported uncertainty about this action.
    WorkerUncertain {
        action_description: String,
        confidence: f32,
    },
    /// Budget is low and this action is expensive.
    BudgetWarning {
        remaining: u64,
        cost: u64,
    },
}

impl Decision {
    pub fn is_allow(&self) -> bool {
        matches!(self, Decision::Allow)
    }

    pub fn is_deny(&self) -> bool {
        matches!(self, Decision::Deny(_))
    }

    pub fn is_escalate(&self) -> bool {
        matches!(self, Decision::Escalate(_))
    }
}

impl std::fmt::Display for Decision {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Decision::Allow => write!(f, "ALLOW"),
            Decision::Deny(reason) => write!(f, "DENY: {}", reason),
            Decision::Escalate(reason) => write!(f, "ESCALATE: {}", reason),
        }
    }
}

impl std::fmt::Display for DenialReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DenialReason::DomainForbidden(d) => write!(f, "domain {} not allowed", d),
            DenialReason::CapabilityForbidden { domain, capability } => {
                write!(f, "capability {:?} not allowed for domain {}", capability, domain)
            }
            DenialReason::ResourceOutOfScope { domain, resource, allowed_prefixes } => {
                write!(
                    f,
                    "resource {} out of scope for domain {} (allowed: {:?})",
                    resource, domain, allowed_prefixes
                )
            }
            DenialReason::BudgetExhausted { unit, remaining, required } => {
                write!(f, "budget {:?} exhausted ({} remaining, {} required)", unit, remaining, required)
            }
            DenialReason::SafetyViolation(msg) => write!(f, "safety violation: {}", msg),
        }
    }
}

impl std::fmt::Display for EscalationReason {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            EscalationReason::LoopDetected { signature, count } => {
                write!(f, "loop detected: {} repeated {} times", signature, count)
            }
            EscalationReason::BoundaryAction { domain, capability, resource } => {
                write!(f, "boundary action: {:?} {} on {}", capability, domain, resource)
            }
            EscalationReason::WorkerUncertain { action_description, confidence } => {
                write!(f, "worker uncertain (confidence {:.2}): {}", confidence, action_description)
            }
            EscalationReason::BudgetWarning { remaining, cost } => {
                write!(f, "budget warning: {} remaining, action costs {}", remaining, cost)
            }
        }
    }
}
