use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GoalStatus {
    Pending,
    Ready,
    InProgress,
    AwaitingApproval,
    Blocked,
    Completed,
    Failed,
    Escalated,
    Cancelled,
}

impl GoalStatus {
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            GoalStatus::Completed | GoalStatus::Failed | GoalStatus::Cancelled
        )
    }

    pub fn is_active(&self) -> bool {
        matches!(
            self,
            GoalStatus::Ready | GoalStatus::InProgress | GoalStatus::AwaitingApproval
        )
    }
}

impl Default for GoalStatus {
    fn default() -> Self {
        GoalStatus::Pending
    }
}


// --- DUPLICATE BLOCK ---

//! Goal lifecycle status.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum GoalStatus {
    /// Created but not yet admitted by the supervisor.
    Pending,
    /// Admitted, waiting for a worker to pick it up.
    Ready,
    /// A worker is actively making progress.
    InProgress,
    /// Paused pending human approval.
    AwaitingApproval,
    /// Cannot proceed; supervisor should diagnose or escalate.
    Blocked,
    /// Successfully completed.
    Completed,
    /// Failed terminally.
    Failed,
    /// Handed off to a human; supervisor has given up autonomous resolution.
    Escalated,
    /// Cancelled by an external command.
    Cancelled,
}

impl GoalStatus {
    pub fn is_terminal(&self) -> bool {
        matches!(
            self,
            GoalStatus::Completed | GoalStatus::Failed | GoalStatus::Cancelled
        )
    }

    pub fn is_active(&self) -> bool {
        matches!(
            self,
            GoalStatus::Ready
                | GoalStatus::InProgress
                | GoalStatus::AwaitingApproval
                | GoalStatus::Blocked
                | GoalStatus::Escalated
        )
    }
}

impl Default for GoalStatus {
    fn default() -> Self {
        GoalStatus::Pending
    }
}
