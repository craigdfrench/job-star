//! Constraint enforcement system for Job-Star supervisor.
//!
//! Enforces read/write/execute permissions per domain and goal,
//! monitors progress, detects loops/budget overruns/blockers,
//! and escalates when uncertain.

pub mod action;
pub mod budget;
pub mod decision;
pub mod domain;
pub mod policy;
pub mod tracker;

pub use action::{Action, ActionId, Capability, ResourcePath};
pub use budget::{Budget, BudgetState, BudgetUnit};
pub use decision::{Decision, DenialReason, EscalationReason};
pub use domain::Domain;
pub use policy::{ConstraintPolicy, PolicyBuilder};
pub use tracker::{ProgressTracker, LoopDetector, LoopSignature};

use crate::error::SupervisorError;

/// The constraint enforcement engine.
///
/// Holds the active policy, budget state, and progress tracker.
/// All actions proposed by workers must pass through `evaluate()`
/// before execution.
pub struct ConstraintEngine {
    policy: ConstraintPolicy,
    budget: BudgetState,
    tracker: ProgressTracker,
}

impl ConstraintEngine {
    /// Create a new engine from a policy and initial budget.
    pub fn new(policy: ConstraintPolicy, budget: Budget) -> Self {
        let budget_state = BudgetState::from_budget(budget);
        let tracker = ProgressTracker::new(policy.loop_window_size);
        Self {
            policy,
            budget: budget_state,
            tracker,
        }
    }

    /// Evaluate a proposed action against all constraints.
    ///
    /// Returns a [`Decision`]:
    /// - `Allow` if the action satisfies all constraints
    /// - `Deny` if it violates a hard constraint
    /// - `Escalate` if it is borderline or requires human judgment
    pub fn evaluate(&mut self, action: &Action) -> Decision {
        // 1. Check if domain is permitted at all
        if !self.policy.is_domain_allowed(action.domain) {
            return Decision::Deny(DenialReason::DomainForbidden(action.domain));
        }

        // 2. Check capability permission for this domain
        if !self.policy.is_capability_allowed(action.domain, action.capability) {
            return Decision::Deny(DenialReason::CapabilityForbidden {
                domain: action.domain,
                capability: action.capability,
            });
        }

        // 3. Check resource path scope
        if let Some(denial) = self.policy.check_resource_scope(action) {
            return Decision::Deny(denial);
        }

        // 4. Check budget
        let cost = self.policy.estimate_cost(action);
        if !self.budget.can_afford(cost) {
            return Decision::Deny(DenialReason::BudgetExhausted {
                unit: cost.unit,
                remaining: self.budget.remaining(cost.unit),
                required: cost.amount,
            });
        }

        // 5. Check for loop / repetition
        if let Some(loop_sig) = self.tracker.detect_loop(action) {
            if loop_sig.repetition_count >= self.policy.max_repetitions {
                return Decision::Escalate(EscalationReason::LoopDetected {
                    signature: loop_sig.signature,
                    count: loop_sig.repetition_count,
                });
            }
        }

        // 6. Check uncertainty — does this action touch a restricted boundary?
        if self.policy.requires_escalation(action) {
            return Decision::Escalate(EscalationReason::BoundaryAction {
                domain: action.domain,
                capability: action.capability,
                resource: action.resource.clone(),
            });
        }

        // All checks passed
        Decision::Allow
    }

    /// Record that an action was executed. Updates budget and tracker.
    ///
    /// Called by the executor after the action completes (or fails).
    pub fn record_execution(&mut self, action: &Action, success: bool) -> Result<(), SupervisorError> {
        let cost = self.policy.estimate_cost(action);
        self.budget.spend(cost)?;
        self.tracker.record(action, success);
        Ok(())
    }

    /// Check if the current goal is blocked (no progress for N steps).
    pub fn check_blocker(&self) -> Option<BlockerStatus> {
        let stall_count = self.tracker.stall_count();
        if stall_count >= self.policy.stall_threshold {
            return Some(BlockerStatus::Stalled {
                steps_without_progress: stall_count,
            });
        }

        if self.budget.is_critical() {
            return Some(BlockerStatus::BudgetCritical);
        }

        None
    }

    /// Get a snapshot of current budget state (for reporting).
    pub fn budget_snapshot(&self) -> &BudgetState {
        &self.budget
    }

    /// Get a snapshot of progress tracking (for reporting).
    pub fn progress_snapshot(&self) -> &ProgressTracker {
        &self.tracker
    }

    /// Update the policy at runtime (e.g., after human approval widens scope).
    pub fn update_policy(&mut self, policy: ConstraintPolicy) {
        self.policy = policy;
    }
}

/// Status when the supervisor detects a blocker.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BlockerStatus {
    /// No successful actions for `steps_without_progress` attempts.
    Stalled { steps_without_progress: usize },
    /// Budget is below critical threshold.
    BudgetCritical,
}

#[cfg(test)]
mod tests {
    use super::*;
    use action::ResourcePath;
    use domain::Domain;
    use std::path::PathBuf;

    fn test_policy() -> ConstraintPolicy {
        PolicyBuilder::new()
            .allow_domain(Domain::Filesystem)
            .allow_capability(Domain::Filesystem, Capability::Read)
            .allow_capability(Domain::Filesystem, Capability::Write)
            .allow_path(Domain::Filesystem, PathBuf::from("/workspace"))
            .max_repetitions(3)
            .stall_threshold(5)
            .build()
    }

    fn test_budget() -> Budget {
        Budget::new()
            .with(BudgetUnit::Steps, 100)
            .with(BudgetUnit::Tokens, 10_000)
    }

    #[test]
    fn allows_permitted_read() {
        let mut engine = ConstraintEngine::new(test_policy(), test_budget());
        let action = Action::new(
            Domain::Filesystem,
            Capability::Read,
            ResourcePath::File(PathBuf::from("/workspace/src/main.rs")),
        );
        assert_eq!(engine.evaluate(&action), Decision::Allow);
    }

    #[test]
    fn denies_unpermitted_domain() {
        let mut engine = ConstraintEngine::new(test_policy(), test_budget());
        let action = Action::new(
            Domain::Network,
            Capability::Read,
            ResourcePath::Url("https://example.com".to_string()),
        );
        assert!(matches!(
            engine.evaluate(&action),
            Decision::Deny(DenialReason::DomainForbidden(Domain::Network))
        ));
    }

    #[test]
    fn denies_path_outside_scope() {
        let mut engine = ConstraintEngine::new(test_policy(), test_budget());
        let action = Action::new(
            Domain::Filesystem,
            Capability::Read,
            ResourcePath::File(PathBuf::from("/etc/passwd")),
        );
        assert!(matches!(
            engine.evaluate(&action),
            Decision::Deny(DenialReason::ResourceOutOfScope { .. })
        ));
    }

    #[test]
    fn escalates_on_loop() {
        let mut engine = ConstraintEngine::new(test_policy(), test_budget());
        let action = Action::new(
            Domain::Filesystem,
            Capability::Write,
            ResourcePath::File(PathBuf::from("/workspace/output.txt")),
        );

        // Execute 4 times (max_repetitions is 3)
        for _ in 0..4 {
            let decision = engine.evaluate(&action);
            if matches!(decision, Decision::Escalate(_)) {
                return; // test passes
            }
            assert_eq!(decision, Decision::Allow);
            engine.record_execution(&action, true).unwrap();
        }
        panic!("Expected escalation on loop, but none occurred");
    }

    #[test]
    fn denies_when_budget_exhausted() {
        let budget = Budget::new().with(BudgetUnit::Steps, 2);
        let mut engine = ConstraintEngine::new(test_policy(), budget);
        let action = Action::new(
            Domain::Filesystem,
            Capability::Read,
            ResourcePath::File(PathBuf::from("/workspace/file.rs")),
        );

        assert_eq!(engine.evaluate(&action), Decision::Allow);
        engine.record_execution(&action, true).unwrap();
        assert_eq!(engine.evaluate(&action), Decision::Allow);
        engine.record_execution(&action, true).unwrap();

        let decision = engine.evaluate(&action);
        assert!(matches!(
            decision,
            Decision::Deny(DenialReason::BudgetExhausted { .. })
        ));
    }
}
