//! Data models mirroring `jobstar/supervisor/models.py`.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::fmt;
use uuid::Uuid;

pub type JobId = String;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum JobStatus {
    Pending,
    Triage,
    Routing,
    Running,
    Succeeded,
    Failed,
    Cancelled,
}

impl fmt::Display for JobStatus {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            JobStatus::Pending => write!(f, "pending"),
            JobStatus::Triage => write!(f, "triage"),
            JobStatus::Routing => write!(f, "routing"),
            JobStatus::Running => write!(f, "running"),
            JobStatus::Succeeded => write!(f, "succeeded"),
            JobStatus::Failed => write!(f, "failed"),
            JobStatus::Cancelled => write!(f, "cancelled"),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobState {
    pub job_id: JobId,
    pub status: JobStatus,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
    pub attempts: u32,
    pub last_error: Option<String>,
}

impl JobState {
    pub fn new(job_id: impl Into<JobId>) -> Self {
        let now = Utc::now();
        Self {
            job_id: job_id.into(),
            status: JobStatus::Pending,
            created_at: now,
            updated_at: now,
            attempts: 0,
            last_error: None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Job {
    pub id: JobId,
    pub payload: serde_json::Value,
    pub state: JobState,
}

impl Job {
    pub fn new(payload: serde_json::Value) -> Self {
        let id = Uuid::new_v4().to_string();
        Self {
            id: id.clone(),
            payload,
            state: JobState::new(id),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SupervisorConfig {
    pub max_concurrent_jobs: usize,
    pub max_retries: u32,
    pub default_timeout_secs: u64,
}

impl Default for SupervisorConfig {
    fn default() -> Self {
        Self {
            max_concurrent_jobs: 16,
            max_retries: 3,
            default_timeout_secs: 300,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskResult {
    pub job_id: JobId,
    pub success: bool,
    pub output: Option<serde_json::Value>,
    pub error: Option<String>,
    pub duration_secs: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskError {
    pub job_id: JobId,
    pub message: String,
    pub recoverable: bool,
}
