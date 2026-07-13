//! Progress monitoring: tracks task lifecycle, completion, and velocity.

use crate::{DomainId, GoalId, TaskId};
use chrono::{DateTime, Utc};
use dashmap::DashMap;
use parking_lot::RwLock;
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::sync::Arc;
use std::time::Duration;

/// High-level lifecycle state of a supervised task.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum ProgressState {
    /// Created but not yet started.
    Pending,
    /// Actively executing.
    Running,
    /// Paused by supervisor or operator.
    Paused,
    /// Waiting on a dependency or external resource.
    Waiting,
    /// Completed successfully.
    Completed,
    /// Failed and cannot proceed.
    Failed,
    /// Cancelled by operator.
    Cancelled,
}

impl ProgressState {
    pub fn is_terminal(self) -> bool {
        matches!(
            self,
            ProgressState::Completed | ProgressState::Failed | ProgressState::Cancelled
        )
    }

    pub fn is_active(self) -> bool {
        matches!(
            self,
            ProgressState::Running | ProgressState::Waiting
        )
    }
}

/// Granular status of a single step within a task.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TaskStatus {
    NotStarted,
    InProgress,
    Done,
    Skipped,
    Error,
}

/// A single recorded progress event.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProgressEvent {
    pub timestamp: DateTime<Utc>,
    pub step_id: String,
    pub from_status: TaskStatus,
    pub to_status: TaskStatus,
    pub message: Option<String>,
}

/// Ongoing progress record for a task.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TaskProgress {
    pub task_id: TaskId,
    pub domain: DomainId,
    pub goal: GoalId,
    pub state: ProgressState,
    pub total_steps: u32,
    pub completed_steps: u32,
    pub skipped_steps: u32,
    pub errored_steps: u32,
    /// Timestamp when the task entered its current state.
    pub state_entered_at: DateTime<Utc>,
    /// Timestamp when the task was first created.
    pub created_at: DateTime<Utc>,
    /// Timestamp of the last progress update.
    pub updated_at: DateTime<Utc>,
    /// Rolling history of progress events (capped).
    pub events: VecDeque<ProgressEvent>,
    /// Per-step status map.
    pub step_statuses: std::collections::HashMap<String, TaskStatus>,
    /// Number of consecutive steps that produced no forward progress.
    pub stall_count: u32,
}

impl TaskProgress {
    pub fn new(task_id: TaskId, domain: DomainId, goal: GoalId, total_steps: u32) -> Self {
        let now = Utc::now();
        Self {
            task_id,
            domain,
            goal,
            state: ProgressState::Pending,
            total_steps,
            completed_steps: 0,
            skipped_steps: 0,
            errored_steps: 0,
            state_entered_at: now,
            created_at: now,
            updated_at: now,
            events: VecDeque::with_capacity(256),
            step_statuses: std::collections::HashMap::new(),
            stall_count: 0,
        }
    }

    /// Fraction of steps completed (excluding skipped).
    pub fn completion_fraction(&self) -> f64 {
        if self.total_steps == 0 {
            return 1.0;
        }
        let effective = self.total_steps.saturating_sub(self.skipped_steps);
        if effective == 0 {
            return 1.0;
        }
        self.completed_steps as f64 / effective as f64
    }

    /// How long the task has been in its current state.
    pub fn time_in_current_state(&self) -> Duration {
        let dur = Utc::now() - self.state_entered_at;
        dur.to_std().unwrap_or(Duration::ZERO)
    }

    /// Total elapsed time since creation.
    pub fn elapsed(&self) -> Duration {
        let dur = Utc::now() - self.created_at;
        dur.to_std().unwrap_or(Duration::ZERO)
    }

    /// Record a step status change.
    pub fn record_event(&mut self, step_id: &str, to: TaskStatus, message: Option<String>) {
        let from = self
            .step_statuses
            .get(step_id)
            .copied()
            .unwrap_or(TaskStatus::NotStarted);

        // Update counters.
        match (from, to) {
            (_, TaskStatus::Done) => {
                self.completed_steps += 1;
                self.stall_count = 0;
            }
            (_, TaskStatus::Skipped) => {
                self.skipped_steps += 1;
            }
            (_, TaskStatus::Error) => {
                self.errored_steps += 1;
                self.stall_count += 1;
            }
            (TaskStatus::InProgress, TaskStatus::InProgress) => {
                // Same status repeated — potential stall.
                self.stall_count += 1;
            }
            _ => {}
        }

        self.step_statuses.insert(step_id.to_string(), to);
        self.updated_at = Utc::now();

        let event = ProgressEvent {
            timestamp: Utc::now(),
            step_id: step_id.to_string(),
            from_status: from,
            to_status: to,
            message,
        };

        self.events.push_back(event);
        // Cap history.
        if self.events.len() > 256 {
            self.events.pop_front();
        }
    }

    /// Transition the task to a new state.
    pub fn transition_to(&mut self, new_state: ProgressState) {
        if self.state != new_state {
            self.state = new_state;
            self.state_entered_at = Utc::now();
            self.updated_at = Utc::now();
        }
    }
}

/// A point-in-time snapshot used by other monitors.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProgressSnapshot {
    pub task_id: TaskId,
    pub state: ProgressState,
    pub completion_fraction: f64,
    pub completed_steps: u32,
    pub total_steps: u32,
    pub errored_steps: u32,
    pub stall_count: u32,
    pub elapsed: Duration,
    pub time_in_current_state: Duration,
    pub recent_events: Vec<ProgressEvent>,
}

/// Thread-safe progress monitor that tracks all active tasks.
pub struct ProgressMonitor {
    tasks: Arc<DashMap<TaskId, Arc<RwLock<TaskProgress>>>>,
}

impl ProgressMonitor {
    pub fn new() -> Self {
        Self {
            tasks: Arc::new(DashMap::new()),
        }
    }

    /// Register a new task for tracking.
    pub fn register(&self, task_id: TaskId, domain: DomainId, goal: GoalId, total_steps: u32) {
        let progress = TaskProgress::new(task_id, domain, goal, total_steps);
        self.tasks
            .insert(task_id, Arc::new(RwLock::new(progress)));
    }

    /// Record a step status change for a task.
    pub fn record(
        &self,
        task_id: TaskId,
        step_id: &str,
        status: TaskStatus,
        message: Option<String>,
    ) {
        if let Some(entry) = self.tasks.get(&task_id) {
            let mut p = entry.write();
            p.record_event(step_id, status, message);
        }
    }

    /// Transition a task to a new state.
    pub fn transition(&self, task_id: TaskId, state: ProgressState) {
        if let Some(entry) = self.tasks.get(&task_id) {
            let mut p = entry.write();
            p.transition_to(state);
        }
    }

    /// Take a snapshot of a task's current progress.
    pub fn snapshot(&self, task_id: TaskId) -> ProgressSnapshot {
        let entry = match self.tasks.get(&task_id) {
            Some(e) => e,
            None => {
                return ProgressSnapshot {
                    task_id,
                    state: ProgressState::Pending,
                    completion_fraction: 0.0,
                    completed_steps: 0,
                    total_steps: 0,
                    errored_steps: 0,
                    stall_count: 0,
                    elapsed: Duration::ZERO,
                    time_in_current_state: Duration::ZERO,
                    recent_events: vec![],
                }
            }
        };

        let p = entry.read();
        let recent_events = p.events.iter().rev().take(20).cloned().rev().collect();

        ProgressSnapshot {
            task_id,
            state: p.state,
            completion_fraction: p.completion_fraction(),
            completed_steps: p.completed_steps,
            total_steps: p.total_steps,
            errored_steps: p.errored_steps,
            stall_count: p.stall_count,
            elapsed: p.elapsed(),
            time_in_current_state: p.time_in_current_state(),
            recent_events,
        }
    }

    /// Remove a task from tracking (after terminal state confirmed).
    pub fn deregister(&self, task_id: TaskId) {
        self.tasks.remove(&task_id);
    }

    /// List all currently tracked task IDs.
    pub fn tracked_tasks(&self) -> Vec<TaskId> {
        self.tasks.iter().map(|e| *e.key()).collect()
    }
}

impl Default for ProgressMonitor {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_completion_fraction() {
        let mut tp = TaskProgress::new(
            TaskId::new(),
            DomainId::new("meta"),
            GoalId::new("build-supervisor"),
            10,
        );
        assert_eq!(tp.completion_fraction(), 0.0);

        tp.record_event("step-1", TaskStatus::Done, None);
        tp.record_event("step-2", TaskStatus::Done, None);
        assert_eq!(tp.completion_fraction(), 0.2);

        tp.record_event("step-3", TaskStatus::Skipped, None);
        assert_eq!(tp.skipped_steps, 1);
        // effective = 10 - 1 = 9, completed = 2
        assert!((tp.completion_fraction() - (2.0 / 9.0)).abs() < 1e-9);
    }

    #[test]
    fn test_stall_detection() {
        let mut tp = TaskProgress::new(
            TaskId::new(),
            DomainId::new("meta"),
            GoalId::new("test"),
            5,
        );

        // Repeated InProgress without completion increments stall.
        tp.record_event("step-1", TaskStatus::InProgress, None);
        assert_eq!(tp.stall_count, 0); // NotStarted -> InProgress doesn't stall

        tp.record_event("step-1", TaskStatus::InProgress, None);
        assert_eq!(tp.stall_count, 1);

        tp.record_event("step-1", TaskStatus::InProgress, None);
        assert_eq!(tp.stall_count, 2);

        // Completion resets stall.
        tp.record_event("step-1", TaskStatus::Done, None);
        assert_eq!(tp.stall_count, 0);
    }

    #[test]
    fn test_state_transition() {
        let mut tp = TaskProgress::new(
            TaskId::new(),
            DomainId::new("meta"),
            GoalId::new("test"),
            5,
        );
        assert_eq!(tp.state, ProgressState::Pending);

        tp.transition_to(ProgressState::Running);
        assert_eq!(tp.state, ProgressState::Running);

        // Idempotent transition doesn't update timestamp.
        let ts = tp.state_entered_at;
        tp.transition_to(ProgressState::Running);
        assert_eq!(tp.state_entered_at, ts);
    }

    #[test]
    fn test_monitor_snapshot() {
        let monitor = ProgressMonitor::new();
        let id = TaskId::new();
        monitor.register(
            id,
            DomainId::new("meta"),
            GoalId::new("test"),
            4,
        );

        monitor.record(id, "s1", TaskStatus::Done, None);
        monitor.record(id, "s2", TaskStatus::Done, None);

        let snap = monitor.snapshot(id);
        assert_eq!(snap.completed_steps, 2);
        assert_eq!(snap.total_steps, 4);
        assert!((snap.completion_fraction - 0.5).abs() < 1e-9);
        assert_eq!(snap.recent_events.len(), 2);
    }
}
