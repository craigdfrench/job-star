//! Progress monitoring and the main [`Supervisor`] entry point.
//!
//! The [`Supervisor`] is the central object that ties together constraints,
//! state, detection, and escalation. Callers feed it [`StepEvent`]s and
//! query it for decisions.

use chrono::Utc;
use serde::{Deserialize, Serialize};
use uuid::Uuid;

use crate::{
    constraints::{Action, ConstraintPolicy, Domain},
    detector::{Anomaly, AnomalyDetector, AnomalyReport},
    escalation::{Escalation, EscalationLevel, EscalationReason},
    error::{Result, SupervisorError},
    state::{JobState, StepRecord, StepStatus},
    JobId, StepId,
};

/// An event representing a step that is about to start or has just finished.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "phase", rename_all = "snake_case")]
pub enum StepEvent {
    /// A step is about to run. The supervisor checks constraints and budget.
    Starting {
        step_id: StepId,
        domain: Domain,
        description: String,
        actions: Vec<Action>,
        /// Estimated cost to charge against budgets (e.g. tokens, seconds).
        estimated_cost: Vec<(crate::state::BudgetKind, u64)>,
        /// Signature of the step's key inputs for loop detection.
        signature: String,
    },
    /// A step has finished.
    Finished {
        step_id: StepId,
        domain: Domain,
        description: String,
        status: StepStatus,
        /// Actual cost charged against budgets.
        actual_cost: Vec<(crate::state::BudgetKind, u64)>,
        /// Signature of the step's key inputs.
        signature: String,
    },
}

/// The outcome of processing a [`StepEvent`].
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StepOutcome {
    /// Whether the step is allowed to proceed.
    pub allowed: bool,
    /// Human-readable explanation.
    pub message: String,
    /// Any anomalies detected during this event.
    pub anomalies: Vec<Anomaly>,
    /// An escalation, if one was triggered.
    pub escalation: Option<Escalation>,
    /// Whether the job has been halted.
    pub halted: bool,
}

impl StepOutcome {
    fn allow(msg: impl Into<String>) -> Self {
        Self {
            allowed: true,
            message: msg.into(),
            anomalies: vec![],
            escalation: None,
            halted: false,
        }
    }

    fn deny(msg: impl Into<String>) -> Self {
        Self {
            allowed: false,
            message: msg.into(),
            anomalies: vec![],
            escalation: None,
            halted: false,
        }
    }
}

/// A snapshot of progress for external reporting.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProgressSnapshot {
    pub job_id: JobId,
    pub goal: String,
    pub steps_total: usize,
    pub steps_success: usize,
    pub steps_failed: usize,
    pub steps_blocked: usize,
    pub consecutive_failures: u32,
    pub budgets: Vec<(crate::state::BudgetKind, u64, u64)>, // (kind, used, limit)
    pub halted: Option<String>,
    pub last_anomalies: Vec<Anomaly>,
}

/// The supervisor. Owns the policy, state, and detector.
pub struct Supervisor {
    pub policy: ConstraintPolicy,
    pub state: JobState,
    pub detector: AnomalyDetector,
    /// Threshold for consecutive failures before auto-escalation.
    pub failure_escalation_threshold: u32,
    /// Whether to halt on any anomaly.
    pub halt_on_anomaly: bool,
}

impl Supervisor {
    /// Create a new supervisor for a job.
    pub fn new(job_id: JobId, goal: impl Into<String>, policy: ConstraintPolicy) -> Self {
        Self {
            policy,
            state: JobState::new(job_id, goal),
            detector: AnomalyDetector::default(),
            failure_escalation_threshold: 3,
            halt_on_anomaly: false,
        }
    }

    /// Set a budget limit.
    pub fn with_budget(mut self, kind: crate::state::BudgetKind, limit: u64) -> Self {
        self.state.set_budget(kind, limit);
        self
    }

    /// Process a step event and return the outcome.
    pub fn handle(&mut self, event: StepEvent) -> Result<StepOutcome> {
        if self.state.is_halted() {
            return Err(SupervisorError::JobHalted {
                state: self.state.halted.clone().unwrap_or_default(),
            });
        }

        match event {
            StepEvent::Starting {
                step_id,
                domain,
                description,
                actions,
                estimated_cost,
                signature,
            } => self.handle_starting(step_id, domain, description, actions, estimated_cost, signature),
            StepEvent::Finished {
                step_id,
                domain,
                description,
                status,
                actual_cost,
                signature,
            } => self.handle_finished(step_id, domain, description, status, actual_cost, signature),
        }
    }

    fn handle_starting(
        &mut self,
        _step_id: StepId,
        domain: Domain,
        _description: String,
        actions: Vec<Action>,
        estimated_cost: Vec<(crate::state::BudgetKind, u64)>,
        _signature: String,
    ) -> Result<StepOutcome> {
        // 1. Enforce constraints for every action.
        for action in &actions {
            if let Err(e) = self.policy.enforce(&domain, action) {
                let outcome = StepOutcome::deny(format!("constraint violation: {e}"));
                return Ok(outcome);
            }
        }

        // 2. Check budgets (without consuming yet — consumption happens on Finished).
        for (kind, amount) in &estimated_cost {
            if let Some(budget) = self.state.budget(*kind) {
                if budget.used + amount > budget.limit {
                    let outcome = StepOutcome::deny(format!(
                        "budget {:?} would be exceeded (used {}, estimated {}, limit {})",
                        kind, budget.used, amount, budget.limit
                    ));
                    return Ok(outcome);
                }
            }
        }

        Ok(StepOutcome::allow("all constraints and budgets satisfied"))
    }

    fn handle_finished(
        &mut self,
        step_id: StepId,
        domain: Domain,
        description: String,
        status: StepStatus,
        actual_cost: Vec<(crate::state::BudgetKind, u64)>,
        signature: String,
    ) -> Result<StepOutcome> {
        let now = Utc::now();

        // 1. Consume budgets.
        for (kind, amount) in &actual_cost {
            if let Some(budget) = self.state.budget_mut(*kind) {
                if let Err(e) = budget.consume(*amount) {
                    // Budget exceeded — record the step then halt.
                    let record = StepRecord {
                        step_id,
                        domain: domain.clone(),
                        description: description.clone(),
                        status: StepStatus::Cancelled,
                        started_at: now,
                        ended_at: now,
                        signature: signature.clone(),
                    };
                    self.state.record_step(record);
                    self.state.halt(format!("budget exceeded: {e}"));
                    return Ok(StepOutcome {
                        allowed: false,
                        message: format!("budget exceeded: {e}"),
                        anomalies: vec![Anomaly::BudgetExceeded {
                            kind: *kind,
                            used: budget.used,
                            limit: budget.limit,
                        }],
                        escalation: Some(Escalation::new(
                            EscalationLevel::Human,
                            EscalationReason::BudgetExceeded { kind: *kind },
                            "Budget exceeded during step execution".to_string(),
                        )),
                        halted: true,
                    });
                }
            }
        }

        // 2. Record the step.
        let record = StepRecord {
            step_id,
            domain: domain.clone(),
            description: description.clone(),
            status: status.clone(),
            started_at: now,
            ended_at: now,
            signature: signature.clone(),
        };
        self.state.record_step(record);

        // 3. Run anomaly detection.
        let report = self.detector.analyze(&self.state);

        // 4. Decide on escalation.
        let mut escalation = report.escalation.clone();

        // Auto-escalate on too many consecutive failures.
        if self.state.consecutive_failures >= self.failure_escalation_threshold
            && escalation.is_none()
        {
            escalation = Some(Escalation::new(
                EscalationLevel::Human,
                EscalationReason::RepeatedFailures {
                    count: self.state.consecutive_failures,
                },
                format!(
                    " {} consecutive failures reached threshold",
                    self.state.consecutive_failures
                ),
            ));
        }

        // Auto-escalate on blocked steps.
        if matches!(status, StepStatus::Blocked { .. }) && escalation.is_none() {
            let reason = match &status {
                StepStatus::Blocked { reason } => reason.clone(),
                _ => String::new(),
            };
            escalation = Some(Escalation::new(
                EscalationLevel::Human,
                EscalationReason::Blocker { description: reason.clone() },
                format!("step blocked: {reason}"),
            ));
        }

        // 5. Halt if configured or if a critical anomaly is detected.
        let mut halted = false;
        if self.halt_on_anomaly && !report.anomalies.is_empty() {
            self.state.halt("anomaly detected (halt_on_anomaly)");
            halted = true;
        }
        for anomaly in &report.anomalies {
            if anomaly.is_critical() {
                self.state.halt(format!("critical anomaly: {anomaly}"));
                halted = true;
                break;
            }
        }

        Ok(StepOutcome {
            allowed: !halted,
            message: if halted {
                "job halted due to anomaly".to_string()
            } else {
                "step recorded".to_string()
            },
            anomalies: report.anomalies,
            escalation,
            halted,
        })
    }

    /// Produce a progress snapshot for external reporting.
    pub fn snapshot(&self) -> ProgressSnapshot {
        let mut steps_success = 0;
        let mut steps_failed = 0;
        let mut steps_blocked = 0;
        for r in &self.state.history {
            match &r.status {
                StepStatus::Success => steps_success += 1,
                StepStatus::Failed => steps_failed += 1,
                StepStatus::Blocked { .. } => steps_blocked += 1,
                StepStatus::Cancelled => {}
            }
        }

        let budgets: Vec<_> = self
            .state
            .budgets
            .iter()
            .map(|(k, b)| (*k, b.used, b.limit))
            .collect();

        ProgressSnapshot {
            job_id: self.state.job_id,
            goal: self.state.goal.clone(),
            steps_total: self.state.history.len(),
            steps_success,
            steps_failed,
            steps_blocked,
            consecutive_failures: self.state.consecutive_failures,
            budgets,
            halted: self.state.halted.clone(),
            last_anomalies: self.detector.last_anomalies.clone(),
        }
    }

    /// Convenience: create a new step ID.
    pub fn new_step_id() -> StepId {
        Uuid::new_v4()
    }
}
