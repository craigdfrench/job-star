//! Job-Star Supervisor: constraint enforcement and progress monitoring.
//!
//! The supervisor is the safety boundary between planning and execution.
//! It enforces read/write/execute permissions per domain and goal,
//! tracks resource budgets, detects loops, and escalates when uncertain.

pub mod capabilities;
pub mod constraints;
pub mod monitor;
pub mod escalation;
pub mod policy;

pub use capabilities::{Capability, CapabilitySet, Domain};
pub use constraints::{ConstraintEngine, ConstraintViolation, ActionContext};
pub use monitor::{ProgressMonitor, ProgressSnapshot, LoopDetector};
pub use escalation::{Escalation, EscalationReason, EscalationLevel};
pub use policy::{SupervisorPolicy, default_policy};

use parking_lot::RwLock;
use std::sync::Arc;

/// The top-level supervisor. Cloneable handle to shared state.
#[derive(Clone)]
pub struct Supervisor {
    inner: Arc<RwLock<SupervisorInner>>,
}

struct SupervisorInner {
    engine: ConstraintEngine,
    monitor: ProgressMonitor,
    policy: SupervisorPolicy,
    /// Pending escalations that a human reviewer must handle.
    pending_escalations: Vec<Escalation>,
    /// Whether the supervisor has been paused (all actions blocked).
    paused: bool,
}

impl Supervisor {
    /// Create a new supervisor with the given policy.
    pub fn new(policy: SupervisorPolicy) -> Self {
        Self {
            inner: Arc::new(RwLock::new(SupervisorInner {
                engine: ConstraintEngine::new(),
                monitor: ProgressMonitor::new(),
                policy,
                pending_escalations: Vec::new(),
                paused: false,
            })),
        }
    }

    /// Register a domain's capabilities. Must be called before actions
    /// referencing that domain can be authorized.
    pub fn register_domain(&self, domain: Domain, caps: CapabilitySet) {
        let mut inner = self.inner.write();
        inner.engine.register_domain(domain, caps);
    }

    /// Register a goal's constraint narrowing. A goal can only *reduce*
    /// the capabilities granted by its domain, never expand them.
    pub fn register_goal(&self, goal_id: &str, domain: &Domain, caps: CapabilitySet) {
        let mut inner = self.inner.write();
        inner.engine.register_goal(goal_id, domain, caps);
    }

    /// Check whether an action is permitted. Returns Ok(()) if allowed,
    /// Err(ConstraintViolation) if blocked.
    pub fn authorize(&self, ctx: &ActionContext) -> Result<(), ConstraintViolation> {
        let inner = self.inner.read();

        if inner.paused {
            return Err(ConstraintViolation::SupervisorPaused);
        }

        inner.engine.check(ctx)
    }

    /// Record that an action was attempted (whether authorized or not).
    /// Updates progress tracking and loop detection.
    pub fn record_action(&self, ctx: &ActionContext, authorized: bool) {
        let mut inner = self.inner.write();
        inner.monitor.record(ctx, authorized);

        // Check for loop detection
        if let Some(loop_info) = inner.monitor.detect_loop() {
            if loop_info.repetitions >= inner.policy.max_action_repetitions {
                inner.pending_escalations.push(Escalation {
                    reason: EscalationReason::LoopDetected(loop_info),
                    level: EscalationLevel::Warning,
                    context: ctx.summary(),
                });
            }
        }

        // Check budget
        if inner.monitor.is_budget_exceeded(&inner.policy) {
            inner.pending_escalations.push(Escalation {
                reason: EscalationReason::BudgetExceeded,
                level: EscalationLevel::Critical,
                context: ctx.summary(),
            });
            inner.paused = true;
        }
    }

    /// Convenience: authorize and record in one call.
    pub fn evaluate(&self, ctx: &ActionContext) -> Result<(), ConstraintViolation> {
        match self.authorize(ctx) {
            Ok(()) => {
                self.record_action(ctx, true);
                Ok(())
            }
            Err(e) => {
                self.record_action(ctx, false);
                Err(e)
            }
        }
    }

    /// Get a snapshot of current progress and budget usage.
    pub fn snapshot(&self) -> ProgressSnapshot {
        let inner = self.inner.read();
        inner.monitor.snapshot(&inner.policy)
    }

    /// Drain pending escalations for human review.
    pub fn drain_escalations(&self) -> Vec<Escalation> {
        let mut inner = self.inner.write();
        std::mem::take(&mut inner.pending_escalations)
    }

    /// Pause the supervisor — all future actions are blocked.
    pub fn pause(&self) {
        self.inner.write().paused = true;
    }

    /// Resume the supervisor after human review.
    pub fn resume(&self) {
        self.inner.write().paused = false;
    }

    /// Check if the supervisor is currently paused.
    pub fn is_paused(&self) -> bool {
        self.inner.read().paused
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use capabilities::{Capability, AccessMode};

    #[test]
    fn test_domain_grants_access() {
        let sup = Supervisor::new(default_policy());

        let domain = Domain::new("system");
        let caps = CapabilitySet::from(vec![
            Capability::file("/etc/hostname", AccessMode::Read),
        ]);
        sup.register_domain(domain.clone(), caps);

        let ctx = ActionContext {
            goal_id: "g1".into(),
            domain: domain.clone(),
            action: "read_file".into(),
            target: "/etc/hostname".into(),
            args: serde_json::Value::Null,
        };

        // Goal must also be registered; goal caps are a subset.
        let goal_caps = CapabilitySet::from(vec![
            Capability::file("/etc/hostname", AccessMode::Read),
        ]);
        sup.register_goal("g1", &domain, goal_caps);

        assert!(sup.authorize(&ctx).is_ok());
    }

    #[test]
    fn test_goal_cannot_expand_domain() {
        let sup = Supervisor::new(default_policy());

        let domain = Domain::new("system");
        let domain_caps = CapabilitySet::from(vec![
            Capability::file("/etc/hostname", AccessMode::Read),
        ]);
        sup.register_domain(domain.clone(), domain_caps);

        // Goal tries to allow write, but domain only allows read.
        let goal_caps = CapabilitySet::from(vec![
            Capability::file("/etc/hostname", AccessMode::Write),
        ]);
        sup.register_goal("g1", &domain, goal_caps);

        let ctx = ActionContext {
            goal_id: "g1".into(),
            domain,
            action: "write_file".into(),
            target: "/etc/hostname".into(),
            args: serde_json::Value::Null,
        };

        assert!(sup.authorize(&ctx).is_err());
    }

    #[test]
    fn test_pause_blocks_all() {
        let sup = Supervisor::new(default_policy());
        sup.pause();

        let ctx = ActionContext {
            goal_id: "g1".into(),
            domain: Domain::new("system"),
            action: "noop".into(),
            target: "".into(),
            args: serde_json::Value::Null,
        };

        assert!(matches!(
            sup.authorize(&ctx),
            Err(ConstraintViolation::SupervisorPaused)
        ));
    }
}


// --- DUPLICATE BLOCK ---

//! Job-Star Supervisor library.
//!
//! The supervision core that enforces constraints, monitors progress,
//! detects anomalies, and escalates when uncertain.

pub mod error;
pub mod supervisor {
    pub mod constraint;
}

pub use error::SupervisorError;


// --- DUPLICATE BLOCK ---

pub mod constraints;
pub mod budget;
pub mod loop_detection;

// Re-export key types.
pub use loop_detection::{
    LoopDetector, LoopDetectorConfig, LoopDetectionResult, LoopKind,
    StateSnapshot, EscalationDecision, should_escalate,
};


// --- DUPLICATE BLOCK ---

//! Job-Star supervision core.
//!
//! Enforces constraints, monitors progress, detects anomalies,
//! and escalates when uncertain.

pub mod budget;
pub mod budget_config;

// Re-export key types.
pub use budget::{
    BudgetConfig, BudgetError, BudgetEvent, BudgetScope, BudgetTracker, ResourceKind,
    ResourceStatus, ScopeStatus,
};
pub use budget_config::defaults_for_domain;


// --- DUPLICATE BLOCK ---

[package]
name = "jobstar-supervisor"
version = "0.1.0"
edition = "2021"
description = "Supervision core for Job-Star: constraint enforcement, progress monitoring, budget tracking"

[dependencies]
serde = { version = "1.0", features = ["derive"] }
serde_json = "1.0"
thiserror = "1.0"

[dev-dependencies]
# Tests use std only, no extra deps needed yet.


// --- DUPLICATE BLOCK ---

//! Job-Star supervisor core.

pub mod constraints;
pub mod progress;
pub mod blocker;

pub use blocker::{Blocker, BlockerConfig, BlockerDetector, BlockerKind, DetectionInput, Severity};
pub use constraints::{Domain, GoalId};
pub use progress::{ProgressDelta, ProgressSnapshot};
