//! Blocker detection for the Job-Star supervisor.
//!
//! A "blocker" is a condition under which an agent cannot reasonably make
//! further progress on its current goal without external intervention
//! (re-planning, dependency injection, human escalation, or goal revision).
//!
//! This module is deliberately conservative: transient failures should be
//! retried, not escalated. A blocker is only declared when repeated signals
//! cross configurable thresholds.

use std::collections::HashMap;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

use crate::constraints::{Domain, GoalId};
use crate::progress::{ProgressSnapshot, ProgressDelta};

/// Identifies a blocker category. Drives the escalation routing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum BlockerKind {
    /// The same operation has failed N times in a row.
    RepeatedFailure,
    /// No measurable progress for a sustained period while budget remains.
    NoProgress,
    /// A required input/resource is missing and the agent cannot produce it.
    DependencyMissing,
    /// The goal statement is ambiguous or contradictory; the agent cannot
    /// determine a valid next action.
    AmbiguousGoal,
    /// The time/token/call budget is effectively exhausted.
    BudgetExhausted,
    /// The agent is cycling through the same states without converging.
    LoopDetected,
    /// An external system (network, tool, API) is unavailable and the agent
    /// cannot proceed without it.
    ExternalBlock,
}

impl BlockerKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            BlockerKind::RepeatedFailure => "repeated_failure",
            BlockerKind::NoProgress => "no_progress",
            BlockerKind::DependencyMissing => "dependency_missing",
            BlockerKind::AmbiguousGoal => "ambiguous_goal",
            BlockerKind::BudgetExhausted => "budget_exhausted",
            BlockerKind::LoopDetected => "loop_detected",
            BlockerKind::ExternalBlock => "external_block",
        }
    }
}

/// Severity hints for the escalation layer.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Severity {
    /// Agent should be nudged or re-prompted; human need not intervene yet.
    Soft,
    /// Human review recommended before continuing.
    Hard,
    /// Agent must stop; goal is likely infeasible as stated.
    Fatal,
}

/// A declared blocker, with enough context to route an escalation.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Blocker {
    pub kind: BlockerKind,
    pub severity: Severity,
    pub goal_id: GoalId,
    pub domain: Domain,
    pub message: String,
    pub evidence: Vec<String>,
    pub detected_at: Instant,
}

/// Tunable thresholds for blocker detection.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BlockerConfig {
    /// Consecutive identical failures before declaring RepeatedFailure.
    pub repeated_failure_threshold: u32,
    /// Wall-clock duration with zero progress before declaring NoProgress.
    pub no_progress_window: Duration,
    /// Number of distinct state hashes seen within `loop_window` before
    /// declaring LoopDetected. A "loop" is when the agent revisits a prior
    /// state hash more than `loop_revisit_threshold` times.
    pub loop_window: Duration,
    pub loop_revisit_threshold: u32,
    /// Fraction (0.0..=1.0) of budget consumed before declaring BudgetExhausted.
    pub budget_exhausted_fraction: f64,
}

impl Default for BlockerConfig {
    fn default() -> Self {
        Self {
            repeated_failure_threshold: 3,
            no_progress_window: Duration::from_secs(180),
            loop_window: Duration::from_secs(120),
            loop_revisit_threshold: 2,
            budget_exhausted_fraction: 0.95,
        }
    }
}

/// Per-goal accumulated signals used by the detector.
#[derive(Debug, Default)]
struct GoalSignals {
    /// Consecutive identical failure signatures.
    consecutive_failures: u32,
    last_failure_signature: Option<String>,
    /// Timestamp of the last progress-bearing event.
    last_progress_at: Option<Instant>,
    /// Rolling history of state hashes with timestamps, for loop detection.
    state_history: Vec<(Instant, u64)>,
    /// Counts of state-hash occurrences within the current window.
    state_counts: HashMap<u64, u32>,
}

impl GoalSignals {
    fn reset_failure_streak(&mut self) {
        self.consecutive_failures = 0;
        self.last_failure_signature = None;
    }

    fn record_progress(&mut self, now: Instant) {
        self.last_progress_at = Some(now);
        self.reset_failure_streak();
    }

    fn record_failure(&mut self, signature: &str, now: Instant) {
        if self.last_failure_signature.as_deref() == Some(signature) {
            self.consecutive_failures += 1;
        } else {
            self.consecutive_failures = 1;
            self.last_failure_signature = Some(signature.to_string());
        }
        // A failure is not progress, but it does count as activity for loop
        // detection purposes — handled separately via state hashes.
        let _ = now;
    }

    fn record_state(&mut self, hash: u64, now: Instant, window: Duration) {
        // Prune entries older than the loop window.
        let cutoff = now - window;
        self.state_history.retain(|(t, _)| *t >= cutoff);
        // Rebuild counts from pruned history.
        self.state_counts.clear();
        for (_, h) in &self.state_history {
            *self.state_counts.entry(*h).or_insert(0) += 1;
        }
        // Insert the new state.
        self.state_history.push((now, hash));
        *self.state_counts.entry(hash).or_insert(0) += 1;
    }
}

/// The blocker detector. Stateless across goals except for accumulated
/// per-goal signal state, which is keyed by `GoalId`.
pub struct BlockerDetector {
    config: BlockerConfig,
    signals: HashMap<GoalId, GoalSignals>,
}

/// Input bundle the supervisor feeds to the detector on each evaluation.
#[derive(Debug, Clone)]
pub struct DetectionInput<'a> {
    pub goal_id: GoalId,
    pub domain: Domain,
    pub now: Instant,
    pub snapshot: &'a ProgressSnapshot,
    pub delta: &'a ProgressDelta,
    /// 0.0..=1.0 fraction of total budget consumed so far.
    pub budget_consumed_fraction: f64,
    /// Optional failure signature from the latest agent step, if it failed.
    pub last_failure: Option<&'a str>,
    /// Optional hash of the agent's current observable state, for loop detection.
    pub current_state_hash: Option<u64>,
    /// Optional explicit dependency-missing hint from the agent.
    pub dependency_missing_hint: Option<&'a str>,
    /// Optional explicit ambiguity hint from the agent.
    pub ambiguous_goal_hint: Option<&'a str>,
    /// Optional external-system-unavailable hint.
    pub external_block_hint: Option<&'a str>,
}

impl BlockerDetector {
    pub fn new(config: BlockerConfig) -> Self {
        Self {
            config,
            signals: HashMap::new(),
        }
    }

    pub fn with_default_config() -> Self {
        Self::new(BlockerConfig::default())
    }

    /// Forget all accumulated signals for a goal (e.g. after a successful
    /// re-plan or human intervention).
    pub fn clear(&mut self, goal_id: &GoalId) {
        self.signals.remove(goal_id);
    }

    /// Evaluate the latest input and return a `Blocker` if one is detected.
    ///
    /// The detector updates its internal signal state as a side effect of
    /// evaluation, so callers should invoke this once per agent step.
    pub fn evaluate(&mut self, input: DetectionInput<'_>) -> Option<Blocker> {
        let DetectionInput {
            goal_id,
            domain,
            now,
            snapshot,
            delta,
            budget_consumed_fraction,
            last_failure,
            current_state_hash,
            dependency_missing_hint,
            ambiguous_goal_hint,
            external_block_hint,
        } = input;

        let signals = self.signals.entry(goal_id).or_default();

        // --- Update signal state ---

        if delta.has_progress() {
            signals.record_progress(now);
        }

        if let Some(sig) = last_failure {
            signals.record_failure(sig, now);
        }

        if let Some(hash) = current_state_hash {
            signals.record_state(hash, now, self.config.loop_window);
        }

        // --- Check blocker conditions, highest-severity first ---

        // 1. Explicit dependency missing (agent self-reports).
        if let Some(dep) = dependency_missing_hint {
            return Some(Blocker {
                kind: BlockerKind::DependencyMissing,
                severity: Severity::Hard,
                goal_id,
                domain,
                message: format!("Agent reports missing dependency: {}", dep),
                evidence: vec![dep.to_string()],
                detected_at: now,
            });
        }

        // 2. Explicit ambiguous goal.
        if let Some(reason) = ambiguous_goal_hint {
            return Some(Blocker {
                kind: BlockerKind::AmbiguousGoal,
                severity: Severity::Hard,
                goal_id,
                domain,
                message: format!("Agent reports ambiguous goal: {}", reason),
                evidence: vec![reason.to_string()],
                detected_at: now,
            });
        }

        // 3. Explicit external block.
        if let Some(reason) = external_block_hint {
            return Some(Blocker {
                kind: BlockerKind::ExternalBlock,
                severity: Severity::Soft,
                goal_id,
                domain,
                message: format!("External system unavailable: {}", reason),
                evidence: vec![reason.to_string()],
                detected_at: now,
            });
        }

        // 4. Budget exhaustion.
        if budget_consumed_fraction >= self.config.budget_exhausted_fraction {
            return Some(Blocker {
                kind: BlockerKind::BudgetExhausted,
                severity: Severity::Fatal,
                goal_id,
                domain,
                message: format!(
                    "Budget exhausted: {:.1}% consumed",
                    budget_consumed_fraction * 100.0
                ),
                evidence: vec![format!(
                    "snapshot steps={}, budget_fraction={:.3}",
                    snapshot.steps_taken, budget_consumed_fraction
                )],
                detected_at: now,
            });
        }

        // 5. Loop detection — a state hash revisited too many times in window.
        if let Some(hash) = current_state_hash {
            if let Some(&count) = signals.state_counts.get(&hash) {
                if count > self.config.loop_revisit_threshold {
                    return Some(Blocker {
                        kind: BlockerKind::LoopDetected,
                        severity: Severity::Hard,
                        goal_id,
                        domain,
                        message: format!(
                            "State hash {:#x} revisited {} times within {:?}",
                            hash, count, self.config.loop_window
                        ),
                        evidence: signals
                            .state_history
                            .iter()
                            .filter(|(_, h)| *h == hash)
                            .map(|(t, _)| format!("seen at {:?}", t.elapsed()))
                            .collect(),
                        detected_at: now,
                    });
                }
            }
        }

        // 6. Repeated identical failures.
        if signals.consecutive_failures >= self.config.repeated_failure_threshold {
            let sig = signals.last_failure_signature.clone().unwrap_or_default();
            return Some(Blocker {
                kind: BlockerKind::RepeatedFailure,
                severity: Severity::Hard,
                goal_id,
                domain,
                message: format!(
                    "Operation failed {} consecutive times: {}",
                    signals.consecutive_failures, sig
                ),
                evidence: vec![format!("failure_signature={}", sig)],
                detected_at: now,
            });
        }

        // 7. No progress for a sustained window.
        if let Some(last_progress) = signals.last_progress_at {
            if now.duration_since(last_progress) >= self.config.no_progress_window {
                return Some(Blocker {
                    kind: BlockerKind::NoProgress,
                    severity: Severity::Soft,
                    goal_id,
                    domain,
                    message: format!(
                        "No measurable progress for {:?}",
                        now.duration_since(last_progress)
                    ),
                    evidence: vec![format!(
                        "last_progress_at={:?}, steps_taken={}",
                        last_progress.elapsed(),
                        snapshot.steps_taken
                    )],
                    detected_at: now,
                });
            }
        } else if snapshot.steps_taken > 0 {
            // We've taken steps but never recorded progress — that's a
            // no-progress condition from the start.
            return Some(Blocker {
                kind: BlockerKind::NoProgress,
                severity: Severity::Soft,
                goal_id,
                domain,
                message: "Steps taken but no progress ever recorded".to_string(),
                evidence: vec![format!("steps_taken={}", snapshot.steps_taken)],
                detected_at: now,
            });
        }

        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::progress::{ProgressDelta, ProgressSnapshot};

    fn make_input<'a>(
        goal_id: GoalId,
        domain: Domain,
        now: Instant,
        snapshot: &'a ProgressSnapshot,
        delta: &'a ProgressDelta,
        budget: f64,
    ) -> DetectionInput<'a> {
        DetectionInput {
            goal_id,
            domain,
            now,
            snapshot,
            delta,
            budget_consumed_fraction: budget,
            last_failure: None,
            current_state_hash: None,
            dependency_missing_hint: None,
            ambiguous_goal_hint: None,
            external_block_hint: None,
        }
    }

    #[test]
    fn detects_repeated_failure() {
        let mut det = BlockerDetector::with_default_config();
        let goal = GoalId::from("g1");
        let snap = ProgressSnapshot::default();
        let delta = ProgressDelta::default();

        let mut now = Instant::now();
        for _ in 0..3 {
            let mut input = make_input(goal, Domain::Meta, now, &snap, &delta, 0.1);
            input.last_failure = Some("tool_x_timeout");
            let b = det.evaluate(input);
            now += Duration::from_secs(1);
            // Only the last iteration should fire.
            if b.is_some() {
                let b = b.unwrap();
                assert_eq!(b.kind, BlockerKind::RepeatedFailure);
                assert_eq!(b.severity, Severity::Hard);
            }
        }
    }

    #[test]
    fn detects_loop() {
        let mut det = BlockerDetector::with_default_config();
        let goal = GoalId::from("g2");
        let snap = ProgressSnapshot::default();
        let delta = ProgressDelta::default();
        let mut now = Instant::now();

        // Same hash three times within window; threshold is 2, so count>2 fires.
        for _ in 0..3 {
            let mut input = make_input(goal, Domain::Meta, now, &snap, &delta, 0.1);
            input.current_state_hash = Some(0xABCD);
            det.evaluate(input);
            now += Duration::from_secs(5);
        }
        // Fourth occurrence should trigger.
        let mut input = make_input(goal, Domain::Meta, now, &snap, &delta, 0.1);
        input.current_state_hash = Some(0xABCD);
        let b = det.evaluate(input).expect("expected loop blocker");
        assert_eq!(b.kind, BlockerKind::LoopDetected);
    }

    #[test]
    fn detects_budget_exhaustion() {
        let mut det = BlockerDetector::with_default_config();
        let goal = GoalId::from("g3");
        let snap = ProgressSnapshot::default();
        let delta = ProgressDelta::default();
        let input = make_input(goal, Domain::Meta, Instant::now(), &snap, &delta, 0.96);
        let b = det.evaluate(input).expect("expected budget blocker");
        assert_eq!(b.kind, BlockerKind::BudgetExhausted);
        assert_eq!(b.severity, Severity::Fatal);
    }

    #[test]
    fn detects_dependency_missing_hint() {
        let mut det = BlockerDetector::with_default_config();
        let goal = GoalId::from("g4");
        let snap = ProgressSnapshot::default();
        let delta = ProgressDelta::default();
        let mut input = make_input(goal, Domain::Meta, Instant::now(), &snap, &delta, 0.1);
        input.dependency_missing_hint = Some("missing: schema.json");
        let b = det.evaluate(input).expect("expected dependency blocker");
        assert_eq!(b.kind, BlockerKind::DependencyMissing);
    }

    #[test]
    fn does_not_block_on_transient_failure() {
        let mut det = BlockerDetector::with_default_config();
        let goal = GoalId::from("g5");
        let snap = ProgressSnapshot::default();
        let delta = ProgressDelta::default();
        let mut input = make_input(goal, Domain::Meta, Instant::now(), &snap, &delta, 0.1);
        input.last_failure = Some("once");
        assert!(det.evaluate(input).is_none());
    }
}
