use super::{
    budget::{BudgetExceeded, BudgetTracker},
    constraints::{ConstraintChecker, ConstraintViolation},
    detector::{Anomaly, AnomalyDetector},
    escalation::{Escalation, EscalationHandler, EscalationLevel},
    monitor::Monitor,
};
use crate::{
    config::SupervisorConfig,
    models::*,
};
use async_trait::async_trait;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;

/// The result of a supervision check on a proposed action
#[derive(Debug, Clone)]
pub enum SupervisionDecision {
    /// The action is approved and can proceed
    Approve,
    /// The action is denied — violation explains why
    Deny { violation: ConstraintViolation },
    /// The action requires human approval before proceeding
    RequireEscalation { escalation: Escalation },
    /// The goal should be paused due to budget or anomaly
    PauseGoal { goal_id: String, reason: String },
    /// The goal should be stopped
    StopGoal { goal_id: String, reason: String },
}

/// Trait for step executors — the actual worker that performs actions
#[async_trait]
pub trait StepExecutor: Send + Sync {
    async fn execute(&self, step: &Step, domain: &Domain) -> Result<StepResult, String>;
}

/// The Supervisor is the central authority that orchestrates everything.
/// It enforces constraints, monitors progress, detects anomalies, and escalates.
pub struct Supervisor {
    config: SupervisorConfig,
    monitor: Monitor,
    constraints: ConstraintChecker,
    budget: BudgetTracker,
    detector: AnomalyDetector,
    escalation_handler: Arc<dyn EscalationHandler>,
    executor: Arc<dyn StepExecutor>,
    /// Active escalations waiting for resolution
    pending_escalations: Arc<Mutex<HashMap<String, Escalation>>>,
}

impl Supervisor {
    pub fn new(
        config: SupervisorConfig,
        escalation_handler: Arc<dyn EscalationHandler>,
        executor: Arc<dyn StepExecutor>,
    ) -> Self {
        Self {
            config,
            monitor: Monitor::new(),
            constraints: ConstraintChecker::new(),
            budget: BudgetTracker::new(),
            detector: AnomalyDetector::new(10, 0.85),
            escalation_handler,
            executor,
            pending_escalations: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    /// Register a domain with the supervisor
    pub fn register_domain(&mut self, domain: Domain) {
        tracing::info!(domain_id = %domain.id, name = %domain.name, "Registered domain");
        self.constraints.register_domain(domain);
    }

    /// Submit a new goal for supervision
    pub fn submit_goal(&mut self, goal: Goal) -> Result<String, String> {
        let goal_id = goal.id.clone();

        // Validate that the goal's domain exists
        if self.constraints.get_domain(&goal.domain.id).is_none() {
            return Err(format!(
                "Goal '{}' references unknown domain '{}'",
                goal_id, goal.domain.id
            ));
        }

        // Register with budget tracker
        self.budget.register_goal(&goal);

        // Register with monitor
        self.monitor.register_goal(goal.clone());

        // Add all steps to the monitor
        for step_ref in &goal.steps {
            // In a real implementation, steps would be pre-created
            // Here we just track the IDs
        }

        tracing::info!(goal_id = %goal_id, title = %goal.title, "Goal submitted");
        Ok(goal_id)
    }

    /// Add a step to a goal
    pub fn add_step(&mut self, step: Step) -> Result<(), String> {
        // Verify the goal exists
        if self.monitor.get_goal(&step.goal_id).is_none() {
            return Err(format!(
                "Cannot add step to unknown goal '{}'",
                step.goal_id
            ));
        }

        // Get the goal's domain
        let goal = self.monitor.get_goal(&step.goal_id).unwrap();
        let domain = &goal.domain;

        // Pre-check constraints before adding
        if let Err(violation) = self.constraints.check_action(&domain.id, &step.action) {
            tracing::warn!(
                step_id = %step.id,
                violation = %violation,
                "Step rejected by constraint checker"
            );
            return Err(violation.to_string());
        }

        self.monitor.add_step(step);
        Ok(())
    }

    /// The main supervision check — called before executing any step.
    /// Returns a decision on whether to proceed, deny, escalate, or pause.
    pub async fn check_before_execute(
        &mut self,
        goal_id: &str,
        step_id: &str,
    ) -> SupervisionDecision {
        // 1. Get the step and goal
        let step = match self.monitor.get_step(step_id) {
            Some(s) => s,
            None => {
                return SupervisionDecision::StopGoal {
                    goal_id: goal_id.to_string(),
                    reason: format!("Step {} not found", step_id),
                };
            }
        };

        let goal = match self.monitor.get_goal(goal_id) {
            Some(g) => g,
            None => {
                return SupervisionDecision::StopGoal {
                    goal_id: goal_id.to_string(),
                    reason: "Goal not found".to_string(),
                };
            }
        };

        // 2. Check if goal is already in a terminal state
        match goal.status {
            GoalStatus::Completed | GoalStatus::Failed | GoalStatus::Cancelled => {
                return SupervisionDecision::StopGoal {
                    goal_id: goal_id.to_string(),
                    reason: format!("Goal is in terminal state: {:?}", goal.status),
                };
            }
            GoalStatus::Escalated => {
                return SupervisionDecision::PauseGoal {
                    goal_id: goal_id.to_string(),
                    reason: "Goal is escalated, waiting for human input".to_string(),
                };
            }
            _ => {}
        }

        // 3. Check max attempts
        if step.attempts >= step.max_attempts {
            let escalation = Escalation::new(
                EscalationLevel::Warning,
                goal_id.to_string(),
                Some(step_id.to_string()),
                format!("Step {} exceeded max attempts ({})", step_id, step.max_attempts),
                "This step has been attempted too many times. Should I skip it, modify the approach, or abort the goal?".to_string(),
            );
            return SupervisionDecision::RequireEscalation { escalation };
        }

        // 4. Check constraints
        if let Err(violation) = self.constraints.check_action(&goal.domain.id, &step.action) {
            let escalation =
                Escalation::from_constraint_violation(&violation, goal_id);
            return SupervisionDecision::Deny { violation };
        }

        // 5. Check budget
        if let Err(exceeded) = self.budget.check(goal_id) {
            let escalation = Escalation::from_budget_exceeded(&exceeded);
            return SupervisionDecision::StopGoal {
                goal_id: goal_id.to_string(),
                reason: format!("Budget exceeded: {:?}", exceeded),
            };
        }

        // 6. Check for anomalies from recent history
        let anomalies = self.detector.observe(&step);
        for anomaly in &anomalies {
            let escalation = Escalation::from_anomaly(anomaly, goal_id);
            match &anomaly {
                Anomaly::LoopDetected { .. } | Anomaly::RepeatedFailure { .. } => {
                    return SupervisionDecision::RequireEscalation { escalation };
                }
                Anomaly::Blocked { .. } => {
                    return SupervisionDecision::RequireEscalation { escalation };
                }
                _ => {
                    // Log but continue
                    tracing::warn!(anomaly = ?anomaly, "Anomaly detected, continuing");
                }
            }
        }

        // 7. All checks passed
        SupervisionDecision::Approve
    }

    /// Execute a step under supervision. This is the main execution path.
    pub async fn execute_step(
        &mut self,
        goal_id: &str,
        step_id: &str,
    ) -> Result<StepResult, SupervisionError> {
        // Pre-execution check
        let decision = self.check_before_execute(goal_id, step_id).await;

        match decision {
            SupervisionDecision::Approve => {
                // Proceed with execution
            }
            SupervisionDecision::Deny { violation } => {
                return Err(SupervisionError::ConstraintViolation(violation));
            }
            SupervisionDecision::RequireEscalation { escalation } => {
                self.handle_escalation(escalation).await?;
                return Err(SupervisionError::Escalated);
            }
            SupervisionDecision::PauseGoal { goal_id, reason } => {
                self.monitor.update_goal_status(&goal_id, GoalStatus::Blocked);
                return Err(SupervisionError::GoalPaused(reason));
            }
            SupervisionDecision::StopGoal { goal_id, reason } => {
                self.monitor.update_goal_status(&goal_id, GoalStatus::Failed);
                return Err(SupervisionError::GoalStopped(reason));
            }
        }

        // Get step and domain for execution
        let step = self.monitor.get_step(step_id).ok_or_else(|| {
            SupervisionError::InternalError(format!("Step {} disappeared", step_id))
        })?;

        let goal = self.monitor.get_goal(goal_id).ok_or_else(|| {
            SupervisionError::InternalError(format!("Goal {} disappeared", goal_id))
        })?;

        // Mark step as in progress
        let mut step = step;
        step.status = StepStatus::InProgress;
        step.attempts += 1;
        step.started_at = Some(chrono::Utc::now());
        self.monitor.update_step(step.clone());

        // Execute via the executor
        let domain = goal.domain.clone();
        let result = self.executor.execute(&step, &domain).await;

        match result {
            Ok(step_result) => {
                // Record budget consumption
                self.budget
                    .record_step(goal_id, step_result.tokens_used);

                // Track file writes and process spawns
                match &step.action {
                    StepAction::Write { .. } => self.budget.record_file_write(goal_id),
                    StepAction::Execute { .. } => self.budget.record_process_spawn(goal_id),
                    _ => {}
                }

                // Update step
                step.status = if step_result.success {
                    StepStatus::Completed
                } else {
                    StepStatus::Failed
                };
                step.result = Some(step_result.clone());
                step.completed_at = Some(chrono::Utc::now());
                self.monitor.update_step(step.clone());

                // Post-execution anomaly check
                let anomalies = self.detector.observe(&step);
                for anomaly in anomalies {
                    tracing::warn!(anomaly = ?anomaly, "Post-execution anomaly detected");
                    let escalation = Escalation::from_anomaly(&anomaly, goal_id);
                    let _ = self.handle_escalation(escalation).await;
                }

                // Check if goal is complete
                if !self.monitor.goal_has_pending_work(goal_id) {
                    let all_steps = self.monitor.get_goal_steps(goal_id);
                    let all_completed = all_steps
                        .iter()
                        .all(|s| s.status == StepStatus::Completed);

                    if all_completed && !all_steps.is_empty() {
                        self.monitor
                            .update_goal_status(goal_id, GoalStatus::Completed);
                        tracing::info!(goal_id = %goal_id, "Goal completed!");
                    } else if all_steps
                        .iter()
                        .any(|s| s.status == StepStatus::Failed)
                    {
                        // Check if any remaining steps can still proceed
                        let has_recoverable = all_steps.iter().any(|s| {
                            s.status == StepStatus::Pending
                                || s.status == StepStatus::Ready
                        });
                        if !has_recoverable {
                            self.monitor
                                .update_goal_status(goal_id, GoalStatus::Failed);
                            tracing::warn!(goal_id = %goal_id, "Goal failed");
                        }
                    }
                }

                Ok(step_result)
            }
            Err(e) => {
                step.status = StepStatus::Failed;
                step.result = Some(StepResult {
                    success: false,
                    output: String::new(),
                    error: Some(e.clone()),
                    tokens_used: 0,
                    duration_ms: 0,
                });
                step.completed_at = Some(chrono::Utc::now());
                self.monitor.update_step(step.clone());

                tracing::error!(
                    step_id = %step_id,
                    error = %e,
                    "Step execution failed"
                );

                Err(SupervisionError::ExecutionFailed(e))
            }
        }
    }

    /// Handle an escalation by sending it to the escalation handler
    async fn handle_escalation(
        &self,
        escalation: Escalation,
    ) -> Result<(), SupervisionError> {
        let esc_id = escalation.id.clone();
        let goal_id = escalation.goal_id.clone();

        // Store pending escalation
        self.pending_escalations
            .lock()
            .await
            .insert(esc_id.clone(), escalation.clone());

        // Mark goal as escalated
        self.monitor
            .update_goal_status(&goal_id, GoalStatus::Escalated);

        // Send to handler
        match self.escalation_handler.handle(&escalation).await {
            Ok(response) => {
                tracing::info!(
                    escalation_id = %esc_id,
                    response = %response,
                    "Escalation handled"
                );
                Ok(())
            }
            Err(e) => {
                tracing::error!(
                    escalation_id = %esc_id,
                    error = %e,
                    "Escalation handler failed"
                );
                Err(SupervisionError::EscalationFailed(e.to_string()))
            }
        }
    }

    /// Resolve a pending escalation and resume the goal
    pub async fn resolve_escalation(
        &self,
        escalation_id: &str,
        resolution: String,
    ) -> Result<(), SupervisionError> {
        let mut pending = self.pending_escalations.lock().await;

        if let Some(escalation) = pending.get_mut(escalation_id) {
            escalation.resolved = true;
            escalation.resolution = Some(resolution.clone());

            let goal_id = escalation.goal_id.clone();
            drop(pending); // Release lock before updating monitor

            // Resume the goal
            self.monitor
                .update_goal_status(&goal_id, GoalStatus::InProgress);
            tracing::info!(
                escalation_id = %escalation_id,
                goal_id = %goal_id,
                resolution = %resolution,
                "Escalation resolved, goal resumed"
            );
            Ok(())
        } else {
            Err(SupervisionError::InternalError(format!(
                "Escalation {} not found",
                escalation_id
            )))
        }
    }

    /// Get a snapshot of the current system state
    pub fn snapshot(&self) -> super::monitor::SystemSnapshot {
        self.monitor.snapshot()
    }

    /// Get the number of pending escalations
    pub async fn pending_escalation_count(&self) -> usize {
        self.pending_escalations.lock().await.len()
    }

    /// Get all pending escalations
    pub async fn get_pending_escalations(&self) -> Vec<Escalation> {
        self.pending_escalations
            .lock()
            .await
            .values()
            .filter(|e| !e.resolved)
            .cloned()
            .collect()
    }

    /// Get budget consumption for a goal
    pub fn get_budget_consumption(
        &self,
        goal_id: &str,
    ) -> Option<&super::budget::BudgetConsumption> {
        self.budget.consumption(goal_id)
    }

    /// Cancel a goal
    pub fn cancel_goal(&mut self, goal_id: &str, reason: &str) {
        self.monitor
            .update_goal_status(goal_id, GoalStatus::Cancelled);
        tracing::info!(goal_id = %goal_id, reason = %reason, "Goal cancelled");
    }
}

/// Errors that can occur during supervision
#[derive(Debug, thiserror::Error)]
pub enum SupervisionError {
    #[error("constraint violation: {0}")]
    ConstraintViolation(#[from] ConstraintViolation),
    #[error("execution failed: {0}")]
    ExecutionFailed(String),
    #[error("escalation required and handled")]
    Escalated,
    #[error("escalation handler failed: {0}")]
    EscalationFailed(String),
    #[error("goal paused: {0}")]
    GoalPaused(String),
    #[error("goal stopped: {0}")]
    GoalStopped(String),
    #[error("internal error: {0}")]
    InternalError(String),
}
