//! Escalation routing: decides when and how to escalate to a human or
//! higher-level supervisor.

use crate::blocker::{Blocker, BlockerReason};
use crate::budget::BudgetStatus;
use crate::loop_detector::{LoopEvent, LoopType};
use crate::progress::ProgressSnapshot;
use crate::TaskId;
use serde::{Deserialize, Serialize};

/// Severity level of an escalation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub enum EscalationLevel {
    /// Log only, no action needed.
    Log,
    /// Notify the operator but continue.
    Notify,
    /// Require human review before continuing.
    Human,
    /// Critical — immediate human intervention required.
    Critical,
}

/// Why an escalation was raised.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum EscalationReason {
    BudgetWarning { fraction: f64 },
    BudgetExhausted,
    LoopDetected { loop_type: LoopType, repetitions: u32 },
    BlockerDetected { reason: String },
    HighErrorRate { errored: u32, total: u32 },
    TaskFailed,
    UncertainProgress { completion: f64, elapsed_secs: u64 },
}

/// An escalation event.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Escalation {
    pub task_id: TaskId,
    pub level: EscalationLevel,
    pub reason: EscalationReason,
    pub message: String,
    pub timestamp: chrono::DateTime<chrono::Utc>,
}

/// Configuration for escalation thresholds.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EscalationConfig {
    /// Error rate (fraction) that triggers escalation.
    pub error_rate_threshold: f64,
    /// If completion is below this fraction after this many seconds, escalate.
    pub slow_progress_threshold: f64,
    pub slow_progress_time_secs: u64,
    /// Severity at which loop events trigger human escalation.
    pub loop_severity_for_human: u8,
}

impl Default for EscalationConfig {
    fn default() -> Self {
        Self {
            error_rate_threshold: 0.3,
            slow_progress_threshold: 0.1,
            slow_progress_time_secs: 600,
            loop_severity_for_human: 3,
        }
    }
}

/// Routes escalations based on monitoring signals.
pub struct EscalationRouter {
    config: EscalationConfig,
}

impl EscalationRouter {
    pub fn new(config: EscalationConfig) -> Self {
        Self { config }
    }

    pub fn with_defaults() -> Self {
        Self::new(EscalationConfig::default())
    }

    /// Evaluate all monitoring signals and produce escalations.
    pub fn evaluate(
        &self,
        task_id: TaskId,
        budget: &BudgetStatus,
        loops: &[LoopEvent],
        blockers: &[Blocker],
        snapshot: &ProgressSnapshot,
    ) -> Vec<Escalation> {
        let mut escalations = Vec::new();
        let now = chrono::Utc::now();

        // Budget escalations.
        if budget.exhausted {
            escalations.push(Escalation {
                task_id,
                level: EscalationLevel::Critical,
                reason: EscalationReason::BudgetExhausted,
                message: format!(
                    "Budget exhausted (fraction: {:.1}%)",
                    budget.max_fraction * 100.0
                ),
                timestamp: now,
            });
        } else if budget.warning {
            escalations.push(Escalation {
                task_id,
                level: EscalationLevel::Notify,
                reason: EscalationReason::BudgetWarning {
                    fraction: budget.max_fraction,
                },
                message: format!(
                    "Budget warning: {:.1}% consumed",
                    budget.max_fraction * 100.0
                ),
                timestamp: now,
            });
        }

        // Loop escalations.
        for le in loops {
            let level = if le.severity >= self.config.loop_severity_for_human {
                EscalationLevel::Human
            } else {
                EscalationLevel::Notify
            };
            escalations.push(Escalation {
                task_id,
                level,
                reason: EscalationReason::LoopDetected {
                    loop_type: le.loop_type.clone(),
                    repetitions: le.repetitions,
                },
                message: le.description.clone(),
                timestamp: now,
            });
        }

        // Blocker escalations.
        for b in blockers {
            if b.resolved {
                continue;
            }
            let level = match &b.reason {
                BlockerReason::AwaitingHuman { .. } => EscalationLevel::Human,
                BlockerReason::ErrorThreshold { errored, .. } => {
                    if *errored >= 10 {
                        EscalationLevel::Critical
                    } else {
                        EscalationLevel::Human
                    }
                }
                _ => EscalationLevel::Notify,
            };
            escalations.push(Escalation {
                task_id,
                level,
                reason: EscalationReason::BlockerDetected {
                    reason: b.description.clone(),
                },
                message: b.description.clone(),
                timestamp: now,
            });
        }

        // High error rate.
        if snapshot.total_steps > 0 {
            let error_rate = snapshot.errored_steps as f64 / snapshot.total_steps as f64;
            if error_rate >= self.config.error_rate_threshold {
                escalations.push(Escalation {
                    task_id,
                    level: EscalationLevel::Human,
                    reason: EscalationReason::HighErrorRate {
                        errored: snapshot.errored_steps,
                        total: snapshot.total_steps,
                    },
                    message: format!(
                        "High error rate: {}/{} steps errored ({:.1}%)",
                        snapshot.errored_steps,
                        snapshot.total_steps,
                        error_rate * 100.0
                    ),
                    timestamp: now,
                });
            }
        }

        // Slow progress.
        let elapsed_secs = snapshot.elapsed.as_secs();
        if elapsed_secs >= self.config.slow_progress_time_secs
            && snapshot.completion_fraction < self.config.slow_progress_threshold
        {
            escalations.push(Escalation {
                task_id,
                level: EscalationLevel::Human,
                reason: EscalationReason::UncertainProgress {
                    completion: snapshot.completion_fraction,
                    elapsed_secs,
                },
                message: format!(
                    "Slow progress: {:.1}% complete after {}s",
                    snapshot.completion_fraction * 100.0,
                    elapsed_secs
                ),
                timestamp: now,
            });
        }

        // Task failed.
        if snapshot.state == crate::progress::ProgressState::Failed {
            escalations.push(Escalation {
                task_id,
                level: EscalationLevel::Critical,
                reason: EscalationReason::TaskFailed,
                message: "Task has entered Failed state".to_string(),
                timestamp: now,
            });
        }

        escalations
    }
}

impl Default for EscalationRouter {
    fn default() -> Self {
        Self::with_defaults()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::budget::BudgetStatus;
    use crate::progress::{ProgressSnapshot, ProgressState};
    use std::time::Duration;

    #[test]
    fn test_budget_exhausted_escalates_critical() {
        let router = EscalationRouter::with_defaults();
        let task_id = TaskId::new();
        let budget = BudgetStatus {
            task_id,
            tokens_used: 1000,
            max_tokens: Some(1000),
            cost_cents: 0,
            max_cost_cents: None,
            elapsed: Duration::ZERO,
            max_duration: None,
            max_fraction: 1.0,
            exhausted: true,
            warning: true,
        };
        let snap = ProgressSnapshot {
            task_id,
            state: ProgressState::Running,
            completion_fraction: 0.5,
            completed_steps: 5,
            total_steps: 10,
            errored_steps: 0,
            stall_count: 0,
            elapsed: Duration::from_secs(10),
            time_in_current_state: Duration::from_secs(10),
            recent_events: vec![],
        };

        let escs = router.evaluate(task_id, &budget, &[], &[], &snap);
        assert!(escs
            .iter()
            .any(|e| e.level == EscalationLevel::Critical
                && matches!(e.reason, EscalationReason::BudgetExhausted)));
    }

    #[test]
    fn test_high_error_rate_escalates() {
        let router = EscalationRouter::with_defaults();
        let task_id = TaskId::new();
        let budget = BudgetStatus {
            task_id,
            tokens_used: 100,
            max_tokens: Some(1000),
            cost_cents: 0,
            max_cost_cents: None,
            elapsed: Duration::ZERO,
            max_duration: None,
            max_fraction: 0.1,
            exhausted: false,
            warning: false,
        };
        let snap = ProgressSnapshot {
            task_id,
            state: ProgressState::Running,
            completion_fraction: 0.0,
            completed_steps: 0,
            total_steps: 10,
            errored_steps: 4, // 40% > 30% threshold
            stall_count: 0,
            elapsed: Duration::from_secs(10),
            time_in_current_state: Duration::from_secs(10),
            recent_events: vec![],
        };

        let escs = router.evaluate(task_id, &budget, &[], &[], &snap);
        assert!(escs
            .iter()
            .any(|e| matches!(e.reason, EscalationReason::HighErrorRate { .. })));
    }
}


// --- DUPLICATE BLOCK ---

//! Escalation mechanism for the Job-Star supervisor.
//!
//! Handles detection, routing, and resolution of situations that exceed
//! the agent's autonomous operating envelope.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;
use std::sync::Arc;
use tokio::sync::RwLock;
use uuid::Uuid;

use crate::constraints::{ConstraintViolation, Domain, Permission};
use crate::monitor::{ProgressSnapshot, ProgressStatus};

// ─── Escalation Severity ──────────────────────────────────────────────

/// Severity level determines routing and response time expectations.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub enum Severity {
    /// Informational — agent wants to note something but can continue.
    Info,
    /// Warning — something is off, agent is attempting recovery but
    /// human should be aware.
    Warning,
    /// Critical — agent cannot proceed safely without intervention.
    /// Work is paused.
    Critical,
    /// Blocker — a hard stop. Constraint violation, budget exhausted,
    /// or unrecoverable error. All work halted.
    Blocker,
}

impl Severity {
    pub fn requires_human(&self) -> bool {
        matches!(self, Severity::Critical | Severity::Blocker)
    }

    pub fn halts_work(&self) -> bool {
        matches!(self, Severity::Critical | Severity::Blocker)
    }

    pub fn label(&self) -> &'static str {
        match self {
            Severity::Info => "INFO",
            Severity::Warning => "WARNING",
            Severity::Critical => "CRITICAL",
            Severity::Blocker => "BLOCKER",
        }
    }
}

// ─── Escalation Triggers ──────────────────────────────────────────────

/// What caused the escalation. Used for routing and pattern analysis.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum EscalationTrigger {
    /// Agent explicitly requested help (uncertainty).
    AgentRequest {
        question: String,
        attempted_actions: Vec<String>,
    },

    /// A constraint was violated.
    ConstraintViolation(ViolationDetail),

    /// Budget (time, tokens, steps) exceeded or near limit.
    BudgetExceeded {
        budget_type: BudgetType,
        consumed: u64,
        limit: u64,
    },

    /// Budget approaching limit (configurable threshold).
    BudgetWarning {
        budget_type: BudgetType,
        consumed: u64,
        limit: u64,
        threshold_pct: u8,
    },

    /// Detected a loop — same action repeated without progress.
    LoopDetected {
        action_signature: String,
        repetitions: u32,
        window_steps: u32,
    },

    /// No progress for N consecutive steps.
    Stagnation {
        steps_without_progress: u32,
        last_progress_step: u32,
    },

    /// Agent reported a blocker it cannot resolve.
    AgentBlocker {
        description: String,
        attempted_resolutions: Vec<String>,
    },

    /// External error (tool failure, API error, filesystem error).
    ExternalError {
        source: String,
        message: String,
        recoverable: bool,
    },

    /// Goal cannot be decomposed or is ambiguous.
    GoalAmbiguity {
        goal_id: Uuid,
        issue: String,
    },

    /// Human-initiated pause or override.
    HumanOverride {
        instruction: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ViolationDetail {
    pub domain: Domain,
    pub permission: Permission,
    pub resource: String,
    pub message: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum BudgetType {
    Steps,
    Tokens,
    WallTime,
    ApiCalls,
}

// ─── Escalation Record ────────────────────────────────────────────────

/// A single escalation event, from creation through resolution.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Escalation {
    pub id: Uuid,
    pub created_at: DateTime<Utc>,
    pub resolved_at: Option<DateTime<Utc>>,

    pub severity: Severity,
    pub trigger: EscalationTrigger,

    /// Current lifecycle state.
    pub status: EscalationStatus,

    /// Where this escalation was routed.
    pub route: EscalationRoute,

    /// Free-form context to help the resolver.
    pub context: EscalationContext,

    /// Resolution if any.
    pub resolution: Option<Resolution>,
}

impl Escalation {
    pub fn new(severity: Severity, trigger: EscalationTrigger) -> Self {
        Self {
            id: Uuid::new_v4(),
            created_at: Utc::now(),
            resolved_at: None,
            severity,
            trigger,
            status: EscalationStatus::Open,
            route: EscalationRoute::default_for(severity),
            context: EscalationContext::default(),
            resolution: None,
        }
    }

    pub fn is_resolved(&self) -> bool {
        matches!(self.status, EscalationStatus::Resolved { .. })
    }

    pub fn is_blocking(&self) -> bool {
        self.severity.halts_work() && !self.is_resolved()
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum EscalationStatus {
    Open,
    /// Routed to a resolver, awaiting response.
    AwaitingResolver { routed_at: DateTime<Utc> },
    /// Resolver acknowledged, working on it.
    InProgress { acknowledged_at: DateTime<Utc> },
    /// Resolved — agent can continue.
    Resolved { resolved_at: DateTime<Utc> },
    /// Cancelled by supervisor (e.g., stale or superseded).
    Cancelled { reason: String, cancelled_at: DateTime<Utc> },
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum EscalationRoute {
    /// Routed to human operator via the review queue.
    Human { channel: HumanChannel },

    /// Routed to a fallback strategy the supervisor can execute.
    Fallback { strategy: FallbackStrategy },

    /// Routed to a different agent instance.
    AlternateAgent { agent_id: String, reason: String },

    /// Logged only, no active routing (for Info severity).
    LogOnly,
}

impl EscalationRoute {
    fn default_for(severity: Severity) -> Self {
        match severity {
            Severity::Info => EscalationRoute::LogOnly,
            Severity::Warning => EscalationRoute::Fallback {
                strategy: FallbackStrategy::RetryWithBackoff,
            },
            Severity::Critical => EscalationRoute::Human {
                channel: HumanChannel::ReviewQueue,
            },
            Severity::Blocker => EscalationRoute::Human {
                channel: HumanChannel::Immediate,
            },
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum HumanChannel {
    /// Added to the normal review queue — human gets to it when available.
    ReviewQueue,
    /// Immediate notification (e.g., interrupt current session).
    Immediate,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum FallbackStrategy {
    /// Retry the last action with exponential backoff.
    RetryWithBackoff,
    /// Skip the current step and move to next.
    SkipStep,
    /// Restart the current sub-goal from scratch.
    RestartSubGoal,
    /// Switch to a simpler approach.
    SimplifyApproach,
    /// Request more context from the environment.
    GatherMoreContext,
}

// ─── Context ──────────────────────────────────────────────────────────

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct EscalationContext {
    /// Current goal being worked on.
    pub goal_id: Option<Uuid>,
    /// Current step number.
    pub step: Option<u32>,
    /// Recent action history (signatures).
    pub recent_actions: Vec<String>,
    /// Progress snapshot at time of escalation.
    pub progress: Option<ProgressSnapshot>,
    /// Additional free-form notes.
    pub notes: Option<String>,
}

// ─── Resolution ───────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Resolution {
    pub resolved_by: Resolver,
    pub resolved_at: DateTime<Utc>,
    pub outcome: ResolutionOutcome,
    pub message: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Resolver {
    Human,
    Supervisor,
    Agent,
    System,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum ResolutionOutcome {
    /// Agent should continue with the provided guidance.
    Continue { guidance: String },
    /// Agent should retry the failed action.
    Retry,
    /// Agent should skip the current step.
    SkipStep,
    /// Agent should abort the current goal.
    AbortGoal { reason: String },
    /// Goal was modified — agent should re-plan.
    ModifyGoal { new_goal_description: String },
    /// Escalation was a false alarm — dismiss.
    Dismissed,
}

// ─── Escalation Manager ───────────────────────────────────────────────

/// Manages the lifecycle of escalations. Thread-safe, shared state.
pub struct EscalationManager {
    /// All escalations, newest first.
    escalations: Arc<RwLock<VecDeque<Escalation>>>,

    /// Configuration for thresholds and routing.
    config: EscalationConfig,

    /// Callback invoked when a human-routed escalation is created.
    /// Used to push to review queue, send notification, etc.
    human_notify: Arc<dyn Fn(&Escalation) + Send + Sync>,

    /// Callback invoked when a fallback strategy should execute.
    fallback_handler: Arc<dyn Fn(FallbackStrategy, &Escalation) + Send + Sync>,
}

impl EscalationManager {
    pub fn new(config: EscalationConfig) -> Self {
        Self::with_callbacks(
            config,
            |_| {},
            |_, _| {},
        )
    }

    pub fn with_callbacks(
        config: EscalationConfig,
        human_notify: impl Fn(&Escalation) + Send + Sync + 'static,
        fallback_handler: impl Fn(FallbackStrategy, &Escalation) + Send + Sync + 'static,
    ) -> Self {
        Self {
            escalations: Arc::new(RwLock::new(VecDeque::new())),
            config,
            human_notify: Arc::new(human_notify),
            fallback_handler: Arc::new(fallback_handler),
        }
    }

    /// Create and route a new escalation. Returns the escalation ID.
    pub async fn escalate(
        &self,
        severity: Severity,
        trigger: EscalationTrigger,
        context: EscalationContext,
    ) -> Uuid {
        let mut escalation = Escalation::new(severity, trigger);
        escalation.context = context;

        // Apply config-based routing overrides
        self.apply_routing_overrides(&mut escalation);

        let id = escalation.id;

        // Route based on destination
        match &escalation.route {
            EscalationRoute::Human { .. } => {
                (self.human_notify)(&escalation);
            }
            EscalationRoute::Fallback { strategy } => {
                (self.fallback_handler)(*strategy, &escalation);
            }
            EscalationRoute::AlternateAgent { .. } => {
                // Alternate agent routing would be handled by the orchestrator
                tracing::info!(
                    escalation_id = %id,
                    "Escalation routed to alternate agent"
                );
            }
            EscalationRoute::LogOnly => {
                tracing::info!(
                    escalation_id = %id,
                    severity = severity.label(),
                    "Escalation logged (info-level)"
                );
            }
        }

        // Store
        {
            let mut escs = self.escalations.write().await;
            escs.push_front(escalation);
            // Trim to max history
            while escs.len() > self.config.max_history {
                escs.pop_back();
            }
        }

        tracing::warn!(
            escalation_id = %id,
            severity = severity.label(),
            "Escalation created"
        );

        id
    }

    /// Resolve an escalation by ID.
    pub async fn resolve(&self, id: Uuid, resolution: Resolution) -> Result<(), EscalationError> {
        let mut escs = self.escalations.write().await;

        let escalation = escs
            .iter_mut()
            .find(|e| e.id == id)
            .ok_or(EscalationError::NotFound { id })?;

        if escalation.is_resolved() {
            return Err(EscalationError::AlreadyResolved { id });
        }

        escalation.status = EscalationStatus::Resolved {
            resolved_at: Utc::now(),
        };
        escalation.resolved_at = Some(Utc::now());
        escalation.resolution = Some(resolution);

        tracing::info!(
            escalation_id = %id,
            "Escalation resolved"
        );

        Ok(())
    }

    /// Acknowledge an escalation (resolver is working on it).
    pub async fn acknowledge(&self, id: Uuid) -> Result<(), EscalationError> {
        let mut escs = self.escalations.write().await;

        let escalation = escs
            .iter_mut()
            .find(|e| e.id == id)
            .ok_or(EscalationError::NotFound { id })?;

        escalation.status = EscalationStatus::InProgress {
            acknowledged_at: Utc::now(),
        };

        Ok(())
    }

    /// Cancel an escalation (supervisor determines it's stale).
    pub async fn cancel(&self, id: Uuid, reason: String) -> Result<(), EscalationError> {
        let mut escs = self.escalations.write().await;

        let escalation = escs
            .iter_mut()
            .find(|e| e.id == id)
            .ok_or(EscalationError::NotFound { id })?;

        escalation.status = EscalationStatus::Cancelled {
            reason,
            cancelled_at: Utc::now(),
        };
        escalation.resolved_at = Some(Utc::now());

        Ok(())
    }

    /// Get all open (unresolved) escalations, sorted by severity (highest first).
    pub async fn open_escalations(&self) -> Vec<Escalation> {
        let escs = self.escalations.read().await;
        escs
            .iter()
            .filter(|e| !e.is_resolved())
            .cloned()
            .collect()
    }

    /// Get blocking escalations (Critical/Blocker, unresolved).
    pub async fn blocking_escalations(&self) -> Vec<Escalation> {
        let escs = self.escalations.read().await;
        escs
            .iter()
            .filter(|e| e.is_blocking())
            .cloned()
            .collect()
    }

    /// Check if work should be paused due to unresolved blocking escalations.
    pub async fn work_should_pause(&self) -> bool {
        !self.blocking_escalations().await.is_empty()
    }

    /// Get a specific escalation by ID.
    pub async fn get(&self, id: Uuid) -> Option<Escalation> {
        let escs = self.escalations.read().await;
        escs.iter().find(|e| e.id == id).cloned()
    }

    /// Get recent escalations (last N).
    pub async fn recent(&self, n: usize) -> Vec<Escalation> {
        let escs = self.escalations.read().await;
        escs.iter().take(n).cloned().collect()
    }

    /// Get escalation statistics for monitoring.
    pub async fn stats(&self) -> EscalationStats {
        let escs = self.escalations.read().await;
        let total = escs.len();
        let open = escs.iter().filter(|e| !e.is_resolved()).count();
        let blocking = escs.iter().filter(|e| e.is_blocking()).count();

        let by_severity = escs
            .iter()
            .fold([0usize; 4], |mut acc, e| {
                acc[e.severity as usize] += 1;
                acc
            });

        EscalationStats {
            total,
            open,
            blocking,
            info: by_severity[0],
            warning: by_severity[1],
            critical: by_severity[2],
            blocker: by_severity[3],
        }
    }

    /// Apply any config-based routing overrides.
    fn apply_routing_overrides(&self, escalation: &mut Escalation) {
        // If budget warning and auto-fallback is enabled, route to fallback
        if let EscalationTrigger::BudgetWarning { .. } = &escalation.trigger {
            if self.config.auto_fallback_for_budget_warnings {
                escalation.route = EscalationRoute::Fallback {
                    strategy: FallbackStrategy::SimplifyApproach,
                };
            }
        }

        // If loop detected and auto-fallback is enabled
        if let EscalationTrigger::LoopDetected { .. } = &escalation.trigger {
            if self.config.auto_fallback_for_loops {
                escalation.route = EscalationRoute::Fallback {
                    strategy: FallbackStrategy::SimplifyApproach,
                };
            }
        }

        // If stagnation, try gathering more context before escalating to human
        if let EscalationTrigger::Stagnation { .. } = &escalation.trigger {
            if self.config.auto_fallback_for_stagnation {
                escalation.route = EscalationRoute::Fallback {
                    strategy: FallbackStrategy::GatherMoreContext,
                };
            }
        }
    }
}

// ─── Config ───────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EscalationConfig {
    /// Maximum escalations to keep in history.
    pub max_history: usize,

    /// Automatically use fallback strategies for budget warnings
    /// before escalating to human.
    pub auto_fallback_for_budget_warnings: bool,

    /// Automatically use fallback strategies for detected loops.
    pub auto_fallback_for_loops: bool,

    /// Automatically use fallback strategies for stagnation.
    pub auto_fallback_for_stagnation: bool,

    /// Step count threshold for stagnation detection.
    pub stagnation_threshold: u32,

    /// Repetition count for loop detection.
    pub loop_repetition_threshold: u32,

    /// Budget warning threshold (percentage of limit).
    pub budget_warning_threshold_pct: u8,
}

impl Default for EscalationConfig {
    fn default() -> Self {
        Self {
            max_history: 500,
            auto_fallback_for_budget_warnings: true,
            auto_fallback_for_loops: true,
            auto_fallback_for_stagnation: true,
            stagnation_threshold: 5,
            loop_repetition_threshold: 3,
            budget_warning_threshold_pct: 80,
        }
    }
}

// ─── Stats ────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EscalationStats {
    pub total: usize,
    pub open: usize,
    pub blocking: usize,
    pub info: usize,
    pub warning: usize,
    pub critical: usize,
    pub blocker: usize,
}

// ─── Error ────────────────────────────────────────────────────────────

#[derive(Debug, thiserror::Error)]
pub enum EscalationError {
    #[error("Escalation {id} not found")]
    NotFound { id: Uuid },

    #[error("Escalation {id} already resolved")]
    AlreadyResolved { id: Uuid },
}

// ─── Detector: Automatic Escalation from Monitoring Data ──────────────

/// The detector analyzes monitoring data and creates escalations
/// when thresholds are crossed. This is called by the supervisor's
/// main loop after each step.
pub struct EscalationDetector {
    config: EscalationConfig,
}

impl EscalationDetector {
    pub fn new(config: EscalationConfig) -> Self {
        Self { config }
    }

    /// Analyze a progress snapshot and return any escalations that should be created.
    pub fn detect(
        &self,
        snapshot: &ProgressSnapshot,
        recent_actions: &[String],
        budgets: &BudgetState,
    ) -> Vec<(Severity, EscalationTrigger)> {
        let mut triggers = Vec::new();

        // Check budgets
        triggers.extend(self.check_budgets(budgets));

        // Check for loops
        if let Some(loop_trigger) = self.check_loops(recent_actions) {
            triggers.push((Severity::Warning, loop_trigger));
        }

        // Check for stagnation
        if let Some(stagnation_trigger) = self.check_stagnation(snapshot) {
            triggers.push((Severity::Warning, stagnation_trigger));
        }

        // Check progress status
        match snapshot.status {
            ProgressStatus::Blocked { ref reason } => {
                triggers.push((
                    Severity::Critical,
                    EscalationTrigger::AgentBlocker {
                        description: reason.clone(),
                        attempted_resolutions: vec![],
                    },
                ));
            }
            ProgressStatus::Failed { ref error } => {
                triggers.push((
                    Severity::Critical,
                    EscalationTrigger::ExternalError {
                        source: "agent".to_string(),
                        message: error.clone(),
                        recoverable: false,
                    },
                ));
            }
            _ => {}
        }

        triggers
    }

    fn check_budgets(&self, budgets: &BudgetState) -> Vec<(Severity, EscalationTrigger)> {
        let mut triggers = Vec::new();

        for budget in &budgets.budgets {
            let pct = if budget.limit > 0 {
                (budget.consumed * 100) / budget.limit
            } else {
                0
            };

            if budget.consumed >= budget.limit {
                triggers.push((
                    Severity::Blocker,
                    EscalationTrigger::BudgetExceeded {
                        budget_type: budget.budget_type,
                        consumed: budget.consumed,
                        limit: budget.limit,
                    },
                ));
            } else if pct >= self.config.budget_warning_threshold_pct as u64 {
                triggers.push((
                    Severity::Warning,
                    EscalationTrigger::BudgetWarning {
                        budget_type: budget.budget_type,
                        consumed: budget.consumed,
                        limit: budget.limit,
                        threshold_pct: self.config.budget_warning_threshold_pct,
                    },
                ));
            }
        }

        triggers
    }

    fn check_loops(&self, recent_actions: &[String]) -> Option<EscalationTrigger> {
        if recent_actions.len() < self.config.loop_repetition_threshold as usize {
            return None;
        }

        // Check if the last N actions are identical
        let threshold = self.config.loop_repetition_threshold as usize;
        let last = &recent_actions[0];
        let repetitions = recent_actions
            .iter()
            .take(threshold)
            .filter(|a| *a == last)
            .count();

        if repetitions as u32 >= self.config.loop_repetition_threshold {
            return Some(EscalationTrigger::LoopDetected {
                action_signature: last.clone(),
                repetitions: repetitions as u32,
                window_steps: recent_actions.len() as u32,
            });
        }

        None
    }

    fn check_stagnation(&self, snapshot: &ProgressSnapshot) -> Option<EscalationTrigger> {
        if snapshot.steps_since_progress >= self.config.stagnation_threshold {
            return Some(EscalationTrigger::Stagnation {
                steps_without_progress: snapshot.steps_since_progress,
                last_progress_step: snapshot
                    .current_step
                    .saturating_sub(snapshot.steps_since_progress),
            });
        }
        None
    }
}

// ─── Budget State (for detection) ─────────────────────────────────────

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct BudgetState {
    pub budgets: Vec<BudgetEntry>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BudgetEntry {
    pub budget_type: BudgetType,
    pub consumed: u64,
    pub limit: u64,
}

impl BudgetEntry {
    pub fn new(budget_type: BudgetType, limit: u64) -> Self {
        Self {
            budget_type,
            consumed: 0,
            limit,
        }
    }

    pub fn remaining(&self) -> u64 {
        self.limit.saturating_sub(self.consumed)
    }

    pub fn is_exhausted(&self) -> bool {
        self.consumed >= self.limit
    }

    pub fn consume(&mut self, amount: u64) {
        self.consumed = self.consumed.saturating_add(amount);
    }
}

// ─── Tests ────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_escalate_and_resolve() {
        let manager = EscalationManager::new(EscalationConfig::default());

        let id = manager
            .escalate(
                Severity::Critical,
                EscalationTrigger::AgentRequest {
                    question: "Which file?".to_string(),
                    attempted_actions: vec![],
                },
                EscalationContext::default(),
            )
            .await;

        let open = manager.open_escalations().await;
        assert_eq!(open.len(), 1);
        assert!(manager.work_should_pause().await);

        manager
            .resolve(
                id,
                Resolution {
                    resolved_by: Resolver::Human,
                    resolved_at: Utc::now(),
                    outcome: ResolutionOutcome::Continue {
                        guidance: "Use config.toml".to_string(),
                    },
                    message: "Pointed agent to config file".to_string(),
                },
            )
            .await
            .unwrap();

        assert!(!manager.work_should_pause().await);
        assert!(manager.open_escalations().await.is_empty());
    }

    #[test]
    fn test_loop_detection() {
        let config = EscalationConfig::default();
        let detector = EscalationDetector::new(config);

        let actions = vec![
            "read_file:foo.txt".to_string(),
            "read_file:foo.txt".to_string(),
            "read_file:foo.txt".to_string(),
        ];

        let trigger = detector.check_loops(&actions);
        assert!(trigger.is_some());
    }

    #[test]
    fn test_no_loop_when_actions_differ() {
        let config = EscalationConfig::default();
        let detector = EscalationDetector::new(config);

        let actions = vec![
            "read_file:a.txt".to_string(),
            "read_file:b.txt".to_string(),
            "read_file:c.txt".to_string(),
        ];

        let trigger = detector.check_loops(&actions);
        assert!(trigger.is_none());
    }

    #[test]
    fn test_budget_exceeded_detection() {
        let config = EscalationConfig::default();
        let detector = EscalationDetector::new(config);

        let mut budgets = BudgetState::default();
        let mut entry = BudgetEntry::new(BudgetType::Steps, 100);
        entry.consume(100);
        budgets.budgets.push(entry);

        let snapshot = ProgressSnapshot {
            current_step: 100,
            steps_since_progress: 0,
            status: ProgressStatus::InProgress,
            completion_pct: 50.0,
        };

        let triggers = detector.detect(&snapshot, &[], &budgets);
        assert!(triggers.iter().any(|(s, _)| *s == Severity::Blocker));
    }

    #[test]
    fn test_budget_warning_detection() {
        let config = EscalationConfig::default();
        let detector = EscalationDetector::new(config);

        let mut budgets = BudgetState::default();
        let mut entry = BudgetEntry::new(BudgetType::Tokens, 1000);
        entry.consume(850); // 85%
        budgets.budgets.push(entry);

        let snapshot = ProgressSnapshot {
            current_step: 50,
            steps_since_progress: 0,
            status: ProgressStatus::InProgress,
            completion_pct: 50.0,
        };

        let triggers = detector.detect(&snapshot, &[], &budgets);
        assert!(triggers.iter().any(|(s, _)| *s == Severity::Warning));
    }

    #[tokio::test]
    async fn test_severity_ordering() {
        assert!(Severity::Blocker > Severity::Critical);
        assert!(Severity::Critical > Severity::Warning);
        assert!(Severity::Warning > Severity::Info);
    }

    #[tokio::test]
    async fn test_cancel_escalation() {
        let manager = EscalationManager::new(EscalationConfig::default());

        let id = manager
            .escalate(
                Severity::Warning,
                EscalationTrigger::LoopDetected {
                    action_signature: "test".to_string(),
                    repetitions: 3,
                    window_steps: 5,
                },
                EscalationContext::default(),
            )
            .await;

        manager
            .cancel(id, "Stale — loop resolved by fallback".to_string())
            .await
            .unwrap();

        let esc = manager.get(id).await.unwrap();
        assert!(esc.is_resolved());
    }
}
