use crate::permission::Capability;
use thiserror::Error;

#[derive(Debug, Clone, Error, Serialize, Deserialize)]
pub enum SupervisionError {
    #[error("capability {capability:?} not granted in this domain")]
    MissingCapability { capability: Capability },
    #[error("budget exhausted: {what}")]
    BudgetExhausted { what: String },
    #[error("constraint violation: {detail}")]
    ConstraintViolation { detail: String },
    #[error("loop detected: action repeated {count} times")]
    LoopDetected { repeated_action: String, count: u32 },
    #[error("goal is blocked: {detail}")]
    Blocked { detail: String },
    #[error("unknown domain: {domain_name}")]
    UnknownDomain { domain_name: String },
    #[error("goal not found")]
    GoalNotFound,
    #[error("escalation required: {detail}")]
    EscalationRequired { detail: String },
}


// --- DUPLICATE BLOCK ---

//! Errors the supervisor can surface when enforcing constraints.

use crate::permission::Capability;
use thiserror::Error;

#[derive(Debug, Clone, Error)]
pub enum SupervisionError {
    #[error("capability {capability:?} not granted in this domain")]
    MissingCapability { capability: Capability },

    #[error("budget exhausted: {what}")]
    BudgetExhausted { what: String },

    #[error("constraint violation: {detail}")]
    ConstraintViolation { detail: String },

    #[error("loop detected: action {repeated_action} repeated {count} times")]
    LoopDetlected { repeated_action: String, count: u32 },

    #[error("goal is blocked: {detail}")]
    Blocked { detail: String },

    #[error("unknown domain: {domain_name}")]
    UnknownDomain { domain_name: String },

    #[error("goal not found")]
    GoalNotFound,

    #[error("max spawn depth exceeded: {depth}")]
    MaxDepthExceeded { depth: u32 },

    #[error("escalation required: {detail}")]
    EscalationRequired { detail: String },
}


// --- DUPLICATE BLOCK ---

//! Error types for the supervisor core.

use thiserror::Error;

/// All errors produced by the supervisor core.
#[derive(Debug, Error)]
pub enum SupervisorError {
    /// A step attempted an action that the constraint policy forbids.
    #[error("constraint violation: {message}")]
    ConstraintViolation { message: String },

    /// The budget for a resource has been exhausted.
    #[error("budget exceeded: {kind:?} — used {used}, limit {limit}")]
    BudgetExceeded {
        kind: crate::BudgetKind,
        used: u64,
        limit: u64,
    },

    /// The supervisor detected a loop and halted the job.
    #[error("loop detected: {description}")]
    LoopDetected { description: String },

    /// The supervisor detected a blocker that cannot be auto-resolved.
    #[error("blocker detected: {description}")]
    BlockerDetected { description: String },

    /// The job is in a terminal state and cannot accept new events.
    #[error("job is {state}")]
    JobHalted { state: String },

    /// A serialization/deserialization error.
    #[error("serde error: {0}")]
    Serde(#[from] serde_json::Error),

    /// Catch-all for unexpected internal errors.
    #[error("internal error: {0}")]
    Internal(String),
}

/// Convenience type alias.
pub type Result<T> = std::result::Result<T, SupervisorError>;
