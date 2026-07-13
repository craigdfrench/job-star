//! Live state of a supervised job.

use std::collections::HashMap;

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};

use crate::{constraints::Domain, JobId, StepId};

/// The kind of budget being tracked.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum BudgetKind {
    /// Wall-clock time in seconds.
    TimeSeconds,
    /// Number of steps.
    Steps,
    /// Number of tokens (for LLM-based steps).
    Tokens,
    /// Number of bytes written.
    Bytes,
}

/// A budget allocation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Budget {
    pub kind: BudgetKind,
    pub limit: u64,
    pub used: u64,
}

impl Budget {
    pub fn new(kind: BudgetKind, limit: u64) -> Self {
        Self { kind, limit, used: 0 }
    }

    pub fn remaining(&self) -> u64 {
        self.limit.saturating_sub(self.used)
    }

    pub fn is_exceeded(&self) -> bool {
        self.used >= self.limit
    }

    /// Consume `amount` from the budget. Returns `Err` if this would exceed the limit.
    pub fn consume(&mut self, amount: u64) -> crate::error::Result<()> {
        let new_used = self.used.saturating_add(amount);
        if new_used > self.limit {
            return Err(crate::error::SupervisorError::BudgetExceeded {
                kind: self.kind,
                used: new_used,
                limit: self.limit,
            });
        }
        self.used = new_used;
        Ok(())
    }
}

/// The outcome of a single step.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StepStatus {
    /// The step completed successfully.
    Success,
    /// The step failed but the job may continue.
    Failed,
    /// The step is blocked and cannot proceed without intervention.
    Blocked { reason: String },
    /// The step was cancelled by the supervisor.
    Cancelled,
}

/// A record of a completed (or attempted) step.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StepRecord {
    pub step_id: StepId,
    pub domain: Domain,
    pub description: String,
    pub status: StepStatus,
    pub started_at: DateTime<Utc>,
    pub ended_at: DateTime<Utc>,
    /// A short hash or signature of the step's key inputs, used for loop detection.
    pub signature: String,
}

/// The overall state of a job under supervision.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobState {
    pub job_id: JobId,
    pub goal: String,
    pub created_at: DateTime<Utc>,
    /// All step records in chronological order.
    pub history: Vec<StepRecord>,
    /// Budgets keyed by kind.
    pub budgets: HashMap<BudgetKind, Budget>,
    /// Whether the job has been halted (and why).
    pub halted: Option<String>,
    /// Count of consecutive failures.
    pub consecutive_failures: u32,
}

impl JobState {
    pub fn new(job_id: JobId, goal: impl Into<String>) -> Self {
        Self {
            job_id,
            goal: goal.into(),
            created_at: Utc::now(),
            history: Vec::new(),
            budgets: HashMap::new(),
            halted: None,
            consecutive_failures: 0,
        }
    }

    pub fn set_budget(&mut self, kind: BudgetKind, limit: u64) {
        self.budgets.insert(kind, Budget::new(kind, limit));
    }

    pub fn budget(&self, kind: BudgetKind) -> Option<&Budget> {
        self.budgets.get(&kind)
    }

    pub fn budget_mut(&mut self, kind: BudgetKind) -> Option<&mut Budget> {
        self.budgets.get_mut(&kind)
    }

    /// Record a completed step and update derived state.
    pub fn record_step(&mut self, record: StepRecord) {
        match &record.status {
            StepStatus::Success => {
                self.consecutive_failures = 0;
            }
            StepStatus::Failed => {
                self.consecutive_failures += 1;
            }
            StepStatus::Blocked { .. } => {
                // Blocked steps don't count as failures but should be noted.
            }
            StepStatus::Cancelled => {}
        }
        self.history.push(record);
    }

    /// Halt the job with a reason.
    pub fn halt(&mut self, reason: impl Into<String>) {
        if self.halted.is_none() {
            self.halted = Some(reason.into());
        }
    }

    pub fn is_halted(&self) -> bool {
        self.halted.is_some()
    }

    /// Total number of steps recorded.
    pub fn step_count(&self) -> usize {
        self.history.len()
    }

    /// Return the last N step signatures (for loop detection).
    pub fn recent_signatures(&self, n: usize) -> Vec<&str> {
        self.history
            .iter()
            .rev()
            .take(n)
            .map(|r| r.signature.as_str())
            .collect()
    }
}
