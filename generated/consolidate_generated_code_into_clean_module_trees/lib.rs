//! Native supervisor crate for Job-Star.
//!
//! Mirrors the Python `jobstar.supervisor` package. The public API is
//! re-exported here so consumers can write `use jobstar_supervisor::*`.

pub mod models;
pub mod orchestrator;

pub use models::{
    JobId, JobState, JobStatus, Job, SupervisorConfig, TaskResult, TaskError,
};
pub use orchestrator::{Orchestrator, OrchestratorHandle};

pub use crate::error::{Result, SupervisorError};

mod error {
    use thiserror::Error;

    pub type Result<T> = std::result::Result<T, SupervisorError>;

    #[derive(Debug, Error)]
    pub enum SupervisorError {
        #[error("job not found: {0}")]
        JobNotFound(String),
        #[error("invalid state transition: {from:?} -> {to:?}")]
        InvalidTransition { from: String, to: String },
        #[error("job already exists: {0}")]
        DuplicateJob(String),
        #[error("worker channel closed")]
        ChannelClosed,
        #[error("io error: {0}")]
        Io(#[from] std::io::Error),
        #[error("serde error: {0}")]
    Serde(#[from] serde_json::Error),
    }
}
