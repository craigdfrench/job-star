use serde::{Deserialize, Serialize};

pub type StepId = String;

/// A step is a single unit of work within a goal.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Step {
    pub id: StepId,
    pub goal_id: String,
    pub title: String,
    pub description: String,
    pub action: StepAction,
    pub status: StepStatus,
    pub attempts: u32,
    pub max_attempts: u32,
    pub result: Option<StepResult>,
    pub started_at: Option<chrono::DateTime<chrono::Utc>>,
    pub completed_at: Option<chrono::DateTime<chrono::Utc>>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum StepAction {
    Read { path: String },
    Write { path: String, content: String },
    Execute { command: String, args: Vec<String> },
    Delegate { domain: String, sub_goal: String },
    Think { prompt: String },
    Escalate { reason: String, question: String },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum StepStatus {
    Pending,
    Ready,
    InProgress,
    Completed,
    Failed,
    Blocked,
    Escalated,
    Cancelled,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StepResult {
    pub success: bool,
    pub output: String,
    pub error: Option<String>,
    pub tokens_used: u64,
    pub duration_ms: u64,
}

impl Step {
    pub fn new(id: StepId, goal_id: String, title: String, action: StepAction) -> Self {
        Self {
            id,
            goal_id,
            title,
            description: String::new(),
            action,
            status: StepStatus::Pending,
            attempts: 0,
            max_attempts: 3,
            result: None,
            started_at: None,
            completed_at: None,
        }
    }

    pub fn is_terminal(&self) -> bool {
        matches!(
            self.status,
            StepStatus::Completed | StepStatus::Failed | StepStatus::Cancelled
        )
    }

    pub fn duration(&self) -> Option<chrono::Duration> {
        match (self.started_at, self.completed_at) {
            (Some(start), Some(end)) => Some(end - start),
            _ => None,
        }
    }
}
