use crate::budget::BudgetUsage;
use crate::domain::DomainId;
use crate::goal::GoalId;
use crate::permission::Capability;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Events emitted during supervised execution. The supervisor consumes these
/// to monitor progress and detect anomalies.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SupervisionEvent {
    StepStarted {
        goal: GoalId,
        domain: DomainId,
        at: DateTime<Utc>,
        description: String,
    },
    StepCompleted {
        goal: GoalId,
        at: DateTime<Utc>,
        usage_delta: BudgetUsage,
        summary: String,
    },
    StepFailed {
        goal: GoalId,
        at: DateTime<Utc>,
        error: String,
    },
    CapabilityUsed {
        goal: GoalId,
        domain: DomainId,
        capability: Capability,
        at: DateTime<Utc>,
        detail: String,
    },
    SubgoalSpawned {
        parent: GoalId,
        child: GoalId,
        at: DateTime<Utc>,
    },
    Blocked {
        goal: GoalId,
        at: DateTime<Utc>,
        reason: String,
    },
    ApprovalRequested {
        goal: GoalId,
        at: DateTime<Utc>,
        reason: String,
    },
    Escalated {
        goal: GoalId,
        at: DateTime<Utc>,
        reason: String,
    },
    GoalCompleted {
        goal: GoalId,
        at: DateTime<Utc>,
    },
    GoalFailed {
        goal: GoalId,
        at: DateTime<Utc>,
        reason: String,
    },
}

impl SupervisionEvent {
    pub fn timestamp(&self) -> DateTime<Utc> {
        match self {
            SupervisionEvent::StepStarted { at, .. }
            | SupervisionEvent::StepCompleted { at, .. }
            | SupervisionEvent::StepFailed { at, .. }
            | SupervisionEvent::CapabilityUsed { at, .. }
            | SupervisionEvent::SubgoalSpawned { at, .. }
            | SupervisionEvent::Blocked { at, .. }
            | SupervisionEvent::ApprovalRequested { at, .. }
            | SupervisionEvent::Escalated { at, .. }
            | SupervisionEvent::GoalCompleted { at, .. }
            | SupervisionEvent::GoalFailed { at, .. } => *at,
        }
    }

    pub fn goal(&self) -> Option<GoalId> {
        match self {
            SupervisionEvent::SubgoalSpawned { parent, .. } => Some(parent.clone()),
            SupervisionEvent::StepStarted { goal, .. }
            | SupervisionEvent::StepCompleted { goal, .. }
            | SupervisionEvent::StepFailed { goal, .. }
            | SupervisionEvent::CapabilityUsed { goal, .. }
            | SupervisionEvent::Blocked { goal, .. }
            | SupervisionEvent::ApprovalRequested { goal, .. }
            | SupervisionEvent::Escalated { goal, .. }
            | SupervisionEvent::GoalCompleted { goal, .. }
            | SupervisionEvent::GoalFailed { goal, .. } => Some(goal.clone()),
        }
    }
}


// --- DUPLICATE BLOCK ---

//! Supervision events: the stream the supervisor consumes to monitor progress.

use crate::budget::BudgetUsage;
use crate::domain::DomainId;
use crate::goal::GoalId;
use crate::permission::Capability;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

/// Events emitted during supervised execution. The supervisor consumes these
/// to update budget usage, detect loops, and decide on escalation.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SupervisionEvent {
    StepStarted {
        goal: GoalId,
        domain: DomainId,
        at: DateTime<Utc>,
        description: String,
    },
    StepCompleted {
        goal: GoalId,
        at: DateTime<Utc>,
        usage_delta: BudgetUsage,
        summary: String,
    },
    StepFailed {
        goal: GoalId,
        at: DateTime<Utc>,
        error: String,
    },
    CapabilityUsed {
        goal: GoalId,
        domain: DomainId,
        capability: Capability,
        at: DateTime<Utc>,
        detail: String,
    },
    SubgoalSpawned {
        parent: GoalId,
        child: GoalId,
        at: DateTime<Utc>,
    },
    Blocked {
        goal: GoalId,
        at: DateTime<Utc>,
        reason: String,
    },
    ApprovalRequested {
        goal: GoalId,
        at: DateTime<Utc>,
        reason: String,
    },
    Escalated {
        goal: GoalId,
        at: DateTime<Utc>,
        reason: String,
    },
    GoalCompleted {
        goal: GoalId,
        at: DateTime<Utc>,
    },
    GoalFailed {
        goal: GoalId,
        at: DateTime<Utc>,
        reason: String,
    },
}

impl SupervisionEvent {
    pub fn timestamp(&self) -> DateTime<Utc> {
        match self {
            SupervisionEvent::StepStarted { at, .. }
            | SupervisionEvent::StepCompleted { at, .. }
            | SupervisionEvent::StepFailed { at, .. }
            | SupervisionEvent::CapabilityUsed { at, .. }
            | SupervisionEvent::SubgoalSpawned { at, .. }
            | SupervisionEvent::Blocked { at, .. }
            | SupervisionEvent::ApprovalRequested { at, .. }
            | SupervisionEvent::Escalated { at, .. }
            | SupervisionEvent::GoalCompleted { at, .. }
            | SupervisionEvent::GoalFailed { at, .. } => *at,
        }
    }

    /// The primary goal this event pertains to (parent for spawn events).
    pub fn goal(&self) -> Option<GoalId> {
        match self {
            SupervisionEvent::SubgoalSpawned { parent, .. } => Some(parent.clone()),
            SupervisionEvent::StepStarted { goal, .. }
            | SupervisionEvent::StepCompleted { goal, .. }
            | SupervisionEvent::StepFailed { goal, .. }
            | SupervisionEvent::CapabilityUsed { goal, .. }
            | SupervisionEvent::Blocked { goal, .. }
            | SupervisionEvent::ApprovalRequested { goal, .. }
            | SupervisionEvent::Escalated { goal, .. }
            | SupervisionEvent::GoalCompleted { goal, .. }
            | SupervisionEvent::GoalFailed { goal, .. } => Some(goal.clone()),
        }
    }
}
