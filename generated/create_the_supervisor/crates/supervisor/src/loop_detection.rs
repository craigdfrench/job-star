//! Loop detection for the Job-Star supervision core.
//!
//! Detects repetitive, unproductive agent behavior using multiple strategies:
//! - Exact state repetition (hash-based)
//! - Action repetition counting
//! - Sequence cycle detection (via string-matching on action history)
//! - Output stagnation (similarity-based)
//!
//! When a loop is detected, the supervisor can escalate, force a different
//! strategy, or terminate the goal.

use std::collections::HashMap;
use std::hash::{Hash, Hasher};
use std::collections::hash_map::DefaultHasher;

use serde::{Deserialize, Serialize};

use crate::constraints::{Domain, ActionKind};
use crate::budget::BudgetState;

/// A snapshot of the agent's state at a given step, used for loop detection.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateSnapshot {
    /// Monotonic step index.
    pub step: u64,

    /// The current goal ID being worked on.
    pub goal_id: String,

    /// The current step label or identifier within the goal.
    pub step_label: String,

    /// The domain the action was performed in.
    pub domain: Domain,

    /// The action kind attempted.
    pub action_kind: ActionKind,

    /// A short descriptor of the action (e.g. "write_file:src/main.rs").
    pub action_descriptor: String,

    /// A hash of the relevant context (inputs, environment state).
    pub context_hash: u64,

    /// A hash of the output produced (if any).
    pub output_hash: Option<u64>,

    /// Timestamp (epoch millis) when the snapshot was recorded.
    pub timestamp_ms: u64,
}

impl StateSnapshot {
    /// Compute a hash that captures "state identity" — same goal, step, context.
    /// Two snapshots with the same state_identity_hash indicate the agent
    /// is in the same logical state it was in before.
    pub fn state_identity_hash(&self) -> u64 {
        let mut hasher = DefaultHasher::new();
        self.goal_id.hash(&mut hasher);
        self.step_label.hash(&mut hasher);
        self.domain.hash(&mut hasher);
        self.context_hash.hash(&mut hasher);
        hasher.finish()
    }

    /// Compute a hash that captures "action identity" — same action in same context.
    pub fn action_identity_hash(&self) -> u64 {
        let mut hasher = DefaultHasher::new();
        self.goal_id.hash(&mut hasher);
        self.step_label.hash(&mut hasher);
        self.domain.hash(&mut hasher);
        self.action_kind.hash(&mut hasher);
        self.action_descriptor.hash(&mut hasher);
        self.context_hash.hash(&mut hasher);
        hasher.finish()
    }
}

/// Configuration for the loop detector.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LoopDetectorConfig {
    /// Maximum number of times the exact same state identity can be seen
    /// before declaring a loop. Default: 3.
    pub max_state_repetitions: usize,

    /// Maximum number of times the exact same action can be attempted
    /// (same context) before declaring a loop. Default: 3.
    pub max_action_repetitions: usize,

    /// Minimum cycle length to detect in the action sequence.
    /// E.g., if this is 2, we look for patterns like A→B→A→B.
    /// Default: 2.
    pub min_cycle_length: usize,

    /// Maximum cycle length to detect. Default: 10.
    pub max_cycle_length: usize,

    /// Minimum number of full cycle repetitions required before declaring
    /// a sequence loop. Default: 2 (i.e., the pattern must appear at least
    /// twice in a row).
    pub min_cycle_repetitions: usize,

    /// Maximum number of snapshots to retain in memory. Older snapshots
    /// are evicted. Default: 500.
    pub max_history: usize,

    /// Threshold for output similarity (0.0–1.0). If consecutive outputs
    /// have similarity above this threshold for `max_similar_outputs`
    /// consecutive steps, declare stagnation. Default: 0.92.
    pub output_similarity_threshold: f64,

    /// Number of consecutive similar outputs required to declare stagnation.
    /// Default: 3.
    pub max_similar_outputs: usize,
}

impl Default for LoopDetectorConfig {
    fn default() -> Self {
        Self {
            max_state_repetitions: 3,
            max_action_repetitions: 3,
            min_cycle_length: 2,
            max_cycle_length: 10,
            min_cycle_repetitions: 2,
            max_history: 500,
            output_similarity_threshold: 0.92,
            max_similar_outputs: 3,
        }
    }
}

/// The kind of loop detected.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum LoopKind {
    /// The agent returned to the exact same logical state too many times.
    StateRepetition {
        state_hash: u64,
        count: usize,
        first_seen_step: u64,
        last_seen_step: u64,
    },

    /// The agent attempted the same action (in the same context) too many times.
    ActionRepetition {
        action_hash: u64,
        action_descriptor: String,
        count: usize,
        first_seen_step: u64,
        last_seen_step: u64,
    },

    /// The agent is cycling through a repeating subsequence of actions.
    SequenceCycle {
        cycle_length: usize,
        repetitions: usize,
        cycle_descriptors: Vec<String>,
        start_step: u64,
    },

    /// The agent's outputs are too similar across consecutive steps.
    OutputStagnation {
        consecutive_similar: usize,
        similarity: f64,
        start_step: u64,
    },
}

impl LoopKind {
    pub fn severity(&self) -> Severity {
        match self {
            LoopKind::StateRepetition { count, .. } => {
                if *count >= 5 { Severity::Critical } else { Severity::High }
            }
            LoopKind::ActionRepetition { count, .. } => {
                if *count >= 5 { Severity::Critical } else { Severity::High }
            }
            LoopKind::SequenceCycle { repetitions, .. } => {
                if *repetitions >= 3 { Severity::Critical } else { Severity::Medium }
            }
            LoopKind::OutputStagnation { consecutive_similar, .. } => {
                if *consecutive_similar >= 5 { Severity::High } else { Severity::Medium }
            }
        }
    }

    /// Human-readable description for escalation messages.
    pub fn description(&self) -> String {
        match self {
            LoopKind::StateRepetition { count, first_seen_step, last_seen_step, .. } => {
                format!(
                    "State repetition: same logical state seen {} times (steps {}–{})",
                    count, first_seen_step, last_seen_step
                )
            }
            LoopKind::ActionRepetition { action_descriptor, count, first_seen_step, last_seen_step, .. } => {
                format!(
                    "Action repetition: '{}' attempted {} times (steps {}–{})",
                    action_descriptor, count, first_seen_step, last_seen_step
                )
            }
            LoopKind::SequenceCycle { cycle_length, repetitions, cycle_descriptors, start_step } => {
                format!(
                    "Sequence cycle: pattern [{}] (len {}) repeated {} times starting at step {}",
                    cycle_descriptors.join(" → "),
                    cycle_length,
                    repetitions,
                    start_step
                )
            }
            LoopKind::OutputStagnation { consecutive_similar, similarity, start_step } => {
                format!(
                    "Output stagnation: {} consecutive outputs with {:.2}% similarity starting at step {}",
                    consecutive_similar,
                    similarity * 100.0,
                    start_step
                )
            }
        }
    }
}

/// How severe the loop is, determining escalation behavior.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub enum Severity {
    /// Worth noting but not yet actionable.
    Low,
    /// The supervisor should intervene (e.g., suggest a different approach).
    Medium,
    /// The supervisor should force a strategy change or escalate to human.
    High,
    /// The supervisor should halt the agent immediately.
    Critical,
}

/// The result of a loop detection check.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LoopDetectionResult {
    /// True if any loop pattern was detected.
    pub detected: bool,

    /// The detected loop, if any. If multiple are detected, the most severe
    /// is reported.
    pub loop_kind: Option<LoopKind>,

    /// All loops detected in this check (for diagnostics).
    pub all_detected: Vec<LoopKind>,
}

impl LoopDetectionResult {
    pub fn none() -> Self {
        Self {
            detected: false,
            loop_kind: None,
            all_detected: Vec::new(),
        }
    }
}

/// The loop detector. Maintains history and checks for loop patterns
/// after each step.
pub struct LoopDetector {
    config: LoopDetectorConfig,

    /// All snapshots, in order.
    history: Vec<StateSnapshot>,

    /// Map from state_identity_hash → list of step indices where seen.
    state_index: HashMap<u64, Vec<usize>>,

    /// Map from action_identity_hash → list of step indices where seen.
    action_index: HashMap<u64, Vec<usize>>,

    /// Running count of consecutive similar outputs.
    consecutive_similar: usize,

    /// The similarity of the current stagnation run (if any).
    current_stagnation_similarity: f64,

    /// The step where the current stagnation run started.
    stagnation_start_step: u64,
}

impl LoopDetector {
    pub fn new(config: LoopDetectorConfig) -> Self {
        Self {
            config,
            history: Vec::new(),
            state_index: HashMap::new(),
            action_index: HashMap::new(),
            consecutive_similar: 0,
            current_stagnation_similarity: 0.0,
            stagnation_start_step: 0,
        }
    }

    pub fn with_defaults() -> Self {
        Self::new(LoopDetectorConfig::default())
    }

    /// Record a new state snapshot and run loop detection.
    /// Returns the detection result.
    pub fn record(&mut self, snapshot: StateSnapshot) -> LoopDetectionResult {
        // Evict old history if needed.
        if self.history.len() >= self.config.max_history {
            let evicted = self.history.remove(0);
            let state_hash = evicted.state_identity_hash();
            let action_hash = evicted.action_identity_hash();
            if let Some(steps) = self.state_index.get_mut(&state_hash) {
                steps.retain(|&i| i > 0);
                if steps.is_empty() {
                    self.state_index.remove(&state_hash);
                } else {
                    // Decrement all remaining indices by 1
                    for s in steps.iter_mut() {
                        *s -= 1;
                    }
                }
            }
            if let Some(steps) = self.action_index.get_mut(&action_hash) {
                steps.retain(|&i| i > 0);
                if steps.is_empty() {
                    self.action_index.remove(&action_hash);
                } else {
                    for s in steps.iter_mut() {
                        *s -= 1;
                    }
                }
            }
        }

        let idx = self.history.len();

        // Index by state identity.
        let state_hash = snapshot.state_identity_hash();
        self.state_index
            .entry(state_hash)
            .or_default()
            .push(idx);

        // Index by action identity.
        let action_hash = snapshot.action_identity_hash();
        self.action_index
            .entry(action_hash)
            .or_default()
            .push(idx);

        // Track output similarity.
        self.update_output_similarity(&snapshot);

        // Store the snapshot.
        self.history.push(snapshot);

        // Run all detection strategies.
        self.detect_all()
    }

    /// Update the consecutive-similar-outputs counter.
    fn update_output_similarity(&mut self, snapshot: &StateSnapshot) {
        if let (Some(prev), Some(curr)) = (
            self.history.last(),
            snapshot.output_hash,
        ) {
            if let Some(prev_hash) = prev.output_hash {
                // Simple hash-based similarity: if hashes are equal,
                // similarity is 1.0. Otherwise, we use a normalized
                // hash-distance heuristic.
                let similarity = if prev_hash == curr {
                    1.0
                } else {
                    // Use XOR-based distance normalized to [0, 1].
                    let xor = prev_hash ^ curr;
                    let bits = u64::BITS;
                    let distance = (xor.count_ones() as f64) / (bits as f64);
                    1.0 - distance
                };

                if similarity >= self.config.output_similarity_threshold {
                    if self.consecutive_similar == 0 {
                        self.stagnation_start_step = prev.step;
                        self.current_stagnation_similarity = similarity;
                    }
                    self.consecutive_similar += 1;
                    // Keep the minimum similarity in the run (most conservative).
                    self.current_stagnation_similarity =
                        self.current_stagnation_similarity.min(similarity);
                } else {
                    self.consecutive_similar = 0;
                    self.current_stagnation_similarity = 0.0;
                }
            }
        } else {
            // No output on this step — reset stagnation tracking.
            self.consecutive_similar = 0;
            self.current_stagnation_similarity = 0.0;
        }
    }

    /// Run all detection strategies and return the most severe result.
    fn detect_all(&self) -> LoopDetectionResult {
        let mut all_detected = Vec::new();

        // Strategy 1: State repetition.
        if let Some(loop_kind) = self.detect_state_repetition() {
            all_detected.push(loop_kind);
        }

        // Strategy 2: Action repetition.
        if let Some(loop_kind) = self.detect_action_repetition() {
            all_detected.push(loop_kind);
        }

        // Strategy 3: Sequence cycling.
        if let Some(loop_kind) = self.detect_sequence_cycle() {
            all_detected.push(loop_kind);
        }

        // Strategy 4: Output stagnation.
        if let Some(loop_kind) = self.detect_output_stagnation() {
            all_detected.push(loop_kind);
        }

        if all_detected.is_empty() {
            return LoopDetectionResult::none();
        }

        // Pick the most severe.
        all_detected.sort_by_key(|k| std::cmp::Reverse(k.severity()));
        let most_severe = all_detected[0].clone();

        LoopDetectionResult {
            detected: true,
            loop_kind: Some(most_severe),
            all_detected,
        }
    }

    /// Detect if any state identity has been seen too many times.
    fn detect_state_repetition(&self) -> Option<LoopKind> {
        for (&hash, steps) in &self.state_index {
            if steps.len() >= self.config.max_state_repetitions {
                let first_idx = steps[0];
                let last_idx = *steps.last().unwrap();
                let first = &self.history[first_idx];
                let last = &self.history[last_idx];
                return Some(LoopKind::StateRepetition {
                    state_hash: hash,
                    count: steps.len(),
                    first_seen_step: first.step,
                    last_seen_step: last.step,
                });
            }
        }
        None
    }

    /// Detect if any action has been attempted too many times in the same context.
    fn detect_action_repetition(&self) -> Option<LoopKind> {
        for (&hash, steps) in &self.action_index {
            if steps.len() >= self.config.max_action_repetitions {
                let first_idx = steps[0];
                let last_idx = *steps.last().unwrap();
                let first = &self.history[first_idx];
                let last = &self.history[last_idx];
                return Some(LoopKind::ActionRepetition {
                    action_hash: hash,
                    action_descriptor: first.action_descriptor.clone(),
                    count: steps.len(),
                    first_seen_step: first.step,
                    last_seen_step: last.step,
                });
            }
        }
        None
    }

    /// Detect cycling subsequences in the action descriptor history.
    ///
    /// We look at the tail of the action descriptor sequence and check if
    /// a suffix of length L is repeated R times. This catches patterns like
    /// A→B→C→A→B→C where the cycle [A,B,C] repeats 2 times.
    fn detect_sequence_cycle(&self) -> Option<LoopKind> {
        let descriptors: Vec<&str> = self.history
            .iter()
            .map(|s| s.action_descriptor.as_str())
            .collect();

        let n = descriptors.len();
        if n < self.config.min_cycle_length * self.config.min_cycle_repetitions {
            return None;
        }

        // Try cycle lengths from min to max.
        for cycle_len in self.config.min_cycle_length..=self.config.max_cycle_length.min(n / 2) {
            // Check if the last (cycle_len * R) elements form R repetitions
            // of the same cycle.
            let max_reps = n / cycle_len;
            if max_reps < self.config.min_cycle_repetitions {
                continue;
            }

            // Extract the candidate cycle from the end.
            let cycle_start = n - cycle_len;
            let cycle: Vec<&str> = descriptors[cycle_start..].to_vec();

            // Count how many times it repeats going backwards.
            let mut reps = 1;
            let mut check_start = cycle_start;
            while check_start >= cycle_len {
                let prev_start = check_start - cycle_len;
                let prev_slice = &descriptors[prev_start..check_start];
                if prev_slice == cycle.as_slice() {
                    reps += 1;
                    check_start = prev_start;
                } else {
                    break;
                }
            }

            if reps >= self.config.min_cycle_repetitions {
                let start_idx = n - reps * cycle_len;
                let start_step = self.history[start_idx].step;
                return Some(LoopKind::SequenceCycle {
                    cycle_length: cycle_len,
                    repetitions: reps,
                    cycle_descriptors: cycle.iter().map(|s| s.to_string()).collect(),
                    start_step,
                });
            }
        }

        None
    }

    /// Detect output stagnation.
    fn detect_output_stagnation(&self) -> Option<LoopKind> {
        if self.consecutive_similar >= self.config.max_similar_outputs {
            return Some(LoopKind::OutputStagnation {
                consecutive_similar: self.consecutive_similar,
                similarity: self.current_stagnation_similarity,
                start_step: self.stagnation_start_step,
            });
        }
        None
    }

    /// Get a read-only view of the history.
    pub fn history(&self) -> &[StateSnapshot] {
        &self.history
    }

    /// Clear all history (e.g., when switching to a new goal).
    pub fn reset(&mut self) {
        self.history.clear();
        self.state_index.clear();
        self.action_index.clear();
        self.consecutive_similar = 0;
        self.current_stagnation_similarity = 0.0;
        self.stagnation_start_step = 0;
    }

    /// Get a summary of the detector's state for diagnostics.
    pub fn diagnostics(&self) -> LoopDiagnostics {
        LoopDiagnostics {
            total_snapshots: self.history.len(),
            unique_states: self.state_index.len(),
            unique_actions: self.action_index.len(),
            consecutive_similar_outputs: self.consecutive_similar,
            current_stagnation_similarity: self.current_stagnation_similarity,
        }
    }
}

/// Diagnostic summary of the loop detector's internal state.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LoopDiagnostics {
    pub total_snapshots: usize,
    pub unique_states: usize,
    pub unique_actions: usize,
    pub consecutive_similar_outputs: usize,
    pub current_stagnation_similarity: f64,
}

/// Integration helper: check if a loop detection result should trigger
/// escalation, given the current budget state.
pub fn should_escalate(
    loop_result: &LoopDetectionResult,
    budget: &BudgetState,
) -> EscalationDecision {
    match &loop_result.loop_kind {
        None => EscalationDecision::Continue,
        Some(loop_kind) => {
            let severity = loop_kind.severity();

            match severity {
                Severity::Low => EscalationDecision::Continue,
                Severity::Medium => {
                    // Suggest a strategy change but don't halt.
                    EscalationDecision::Intervene {
                        reason: loop_kind.description(),
                        suggested_action: SuggestedAction::ChangeStrategy,
                    }
                }
                Severity::High => {
                    // If budget is also low, escalate to human.
                    if budget.is_low() {
                        EscalationDecision::EscalateToHuman {
                            reason: format!(
                                "{} — and budget is low ({} remaining)",
                                loop_kind.description(),
                                budget.remaining_summary()
                            ),
                        }
                    } else {
                        EscalationDecision::Intervene {
                            reason: loop_kind.description(),
                            suggested_action: SuggestedAction::ForceDifferentApproach,
                        }
                    }
                }
                Severity::Critical => {
                    EscalationDecision::Halt {
                        reason: loop_kind.description(),
                    }
                }
            }
        }
    }
}

/// What the supervisor should do in response to a loop.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum EscalationDecision {
    /// No action needed.
    Continue,
    /// The supervisor should intervene automatically.
    Intervene {
        reason: String,
        suggested_action: SuggestedAction,
    },
    /// Escalate to a human reviewer.
    EscalateToHuman {
        reason: String,
    },
    /// Halt the agent immediately.
    Halt {
        reason: String,
    },
}

/// What kind of intervention the supervisor should attempt.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum SuggestedAction {
    /// Ask the agent to try a different strategy for the current step.
    ChangeStrategy,
    /// Force the agent to take a substantially different approach
    /// (e.g., switch from editing to reading, or skip to a different sub-goal).
    ForceDifferentApproach,
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_snapshot(
        step: u64,
        goal_id: &str,
        step_label: &str,
        domain: Domain,
        action_kind: ActionKind,
        action_descriptor: &str,
        context_hash: u64,
        output_hash: Option<u64>,
    ) -> StateSnapshot {
        StateSnapshot {
            step,
            goal_id: goal_id.to_string(),
            step_label: step_label.to_string(),
            domain,
            action_kind,
            action_descriptor: action_descriptor.to_string(),
            context_hash,
            output_hash,
            timestamp_ms: step * 1000,
        }
    }

    #[test]
    fn test_state_repetition_detected() {
        let mut detector = LoopDetector::with_defaults();

        // Same state 3 times.
        for i in 0..3 {
            let snap = make_snapshot(
                i,
                "goal-1",
                "step-A",
                Domain::Code,
                ActionKind::Write,
                "write_file:src/main.rs",
                42, // same context hash
                Some(100),
            );
            let result = detector.record(snap);
            if i < 2 {
                assert!(!result.detected, "Should not detect at iteration {}", i);
            } else {
                assert!(result.detected, "Should detect at iteration {}", i);
                assert!(matches!(
                    result.loop_kind,
                    Some(LoopKind::StateRepetition { count: 3, .. })
                ));
            }
        }
    }

    #[test]
    fn test_action_repetition_detected() {
        let mut detector = LoopDetector::with_defaults();

        // Same action 3 times, but different step labels (so state hash differs).
        for i in 0..3 {
            let snap = make_snapshot(
                i,
                "goal-1",
                &format!("step-{}", i), // different step labels
                Domain::Code,
                ActionKind::Write,
                "write_file:src/main.rs",
                99, // same context
                Some(200),
            );
            let result = detector.record(snap);
            if i < 2 {
                assert!(!result.detected, "Should not detect at iteration {}", i);
            } else {
                assert!(result.detected, "Should detect at iteration {}", i);
                match &result.loop_kind {
                    Some(LoopKind::ActionRepetition { count, .. }) => {
                        assert_eq!(*count, 3);
                    }
                    other => panic!("Expected ActionRepetition, got {:?}", other),
                }
            }
        }
    }

    #[test]
    fn test_sequence_cycle_detected() {
        let mut detector = LoopDetector::with_defaults();

        // Pattern: A → B → A → B (cycle length 2, 2 repetitions)
        let actions = ["action_A", "action_B", "action_A", "action_B"];
        for (i, action) in actions.iter().enumerate() {
            let snap = make_snapshot(
                i as u64,
                "goal-1",
                &format!("step-{}", i),
                Domain::Code,
                ActionKind::Execute,
                action,
                i as u64, // different contexts so state/action repetition don't trigger
                Some(i as u64 * 10),
            );
            let result = detector.record(snap);
            if i == 3 {
                // After the 4th action, we should detect the cycle A→B→A→B
                assert!(result.detected, "Should detect cycle at step {}", i);
                let has_cycle = result.all_detected.iter().any(|k| {
                    matches!(k, LoopKind::SequenceCycle { cycle_length: 2, repetitions: 2, .. })
                });
                assert!(has_cycle, "Should have SequenceCycle in results: {:?}", result.all_detected);
            }
        }
    }

    #[test]
    fn test_output_stagnation_detected() {
        let config = LoopDetectorConfig {
            max_similar_outputs: 3,
            output_similarity_threshold: 0.92,
            ..Default::default()
        };
        let mut detector = LoopDetector::new(config);

        // Same output hash 4 times (different actions so other detectors don't fire).
        for i in 0..4 {
            let snap = make_snapshot(
                i as u64,
                "goal-1",
                &format!("step-{}", i),
                Domain::Code,
                ActionKind::Read,
                &format!("read_file:file-{}.txt", i),
                i as u64,
                Some(555), // same output
            );
            let result = detector.record(snap);
            if i >= 3 {
                assert!(result.detected, "Should detect stagnation at step {}", i);
                let has_stagnation = result.all_detected.iter().any(|k| {
                    matches!(k, LoopKind::OutputStagnation { .. })
                });
                assert!(has_stagnation, "Should have OutputStagnation: {:?}", result.all_detected);
            }
        }
    }

    #[test]
    fn test_no_loop_when_progressing() {
        let mut detector = LoopDetector::with_defaults();

        // Each step has different state, action, context, and output.
        for i in 0..10 {
            let snap = make_snapshot(
                i,
                "goal-1",
                &format!("step-{}", i),
                Domain::Code,
                ActionKind::Write,
                &format!("write_file:file-{}.rs", i),
                i as u64 * 7,
                Some(i as u64 * 13),
            );
            let result = detector.record(snap);
            assert!(!result.detected, "Should not detect loop at step {}", i);
        }
    }

    #[test]
    fn test_reset_clears_history() {
        let mut detector = LoopDetector::with_defaults();

        // Create a repetition.
        for i in 0..3 {
            let snap = make_snapshot(
                i,
                "goal-1",
                "step-A",
                Domain::Code,
                ActionKind::Write,
                "write_file:src/main.rs",
                42,
                Some(100),
            );
            detector.record(snap);
        }
        assert!(!detector.history().is_empty());

        detector.reset();
        assert!(detector.history().is_empty());
        assert!(detector.state_index.is_empty());
        assert!(detector.action_index.is_empty());
    }

    #[test]
    fn test_escalation_decision_continue() {
        let result = LoopDetectionResult::none();
        let budget = BudgetState::with_remaining(100, 100);
        let decision = should_escalate(&result, &budget);
        assert!(matches!(decision, EscalationDecision::Continue));
    }

    #[test]
    fn test_escalation_decision_halt_on_critical() {
        let result = LoopDetectionResult {
            detected: true,
            loop_kind: Some(LoopKind::StateRepetition {
                state_hash: 1,
                count: 6,
                first_seen_step: 0,
                last_seen_step: 5,
            }),
            all_detected: vec![],
        };
        let budget = BudgetState::with_remaining(100, 100);
        let decision = should_escalate(&result, &budget);
        assert!(matches!(decision, EscalationDecision::Halt { .. }));
    }
}
