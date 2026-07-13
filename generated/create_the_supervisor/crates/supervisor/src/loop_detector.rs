//! Loop detection: identifies when a task is cycling through the same
//! states or repeating the same actions without making progress.

use crate::progress::{ProgressSnapshot, TaskStatus};
use crate::TaskId;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// What kind of loop was detected.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum LoopType {
    /// The same step is being retried repeatedly.
    StepRetry,
    /// A sequence of steps is cycling (A → B → C → A → B → C).
    StepCycle,
    /// The task state is oscillating (Running → Waiting → Running → Waiting).
    StateOscillation,
    /// No progress for too many consecutive events.
    Stall,
}

/// A detected loop event.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LoopEvent {
    pub task_id: TaskId,
    pub loop_type: LoopType,
    /// Number of repetitions observed.
    pub repetitions: u32,
    /// The step IDs involved in the loop.
    pub involved_steps: Vec<String>,
    /// Human-readable description.
    pub description: String,
    /// Severity (1 = mild, 3 = severe).
    pub severity: u8,
}

/// Configuration for loop detection.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LoopDetectorConfig {
    /// Number of times a single step must repeat before flagging.
    pub step_retry_threshold: u32,
    /// Minimum cycle length to detect (in steps).
    pub min_cycle_length: usize,
    /// Number of times a cycle must repeat before flagging.
    pub cycle_repeat_threshold: u32,
    /// Stall count threshold before flagging.
    pub stall_threshold: u32,
}

impl Default for LoopDetectorConfig {
    fn default() -> Self {
        Self {
            step_retry_threshold: 3,
            min_cycle_length: 2,
            cycle_repeat_threshold: 2,
            stall_threshold: 5,
        }
    }
}

/// Detects loops in task progress.
pub struct LoopDetector {
    config: LoopDetectorConfig,
}

impl LoopDetector {
    pub fn new(config: LoopDetectorConfig) -> Self {
        Self { config }
    }

    pub fn with_defaults() -> Self {
        Self::new(LoopDetectorConfig::default())
    }

    /// Analyze a progress snapshot for loops.
    pub fn detect(&self, task_id: TaskId, snapshot: &ProgressSnapshot) -> Vec<LoopEvent> {
        let mut events = Vec::new();

        // 1. Stall detection.
        if snapshot.stall_count >= self.config.stall_threshold {
            events.push(LoopEvent {
                task_id,
                loop_type: LoopType::Stall,
                repetitions: snapshot.stall_count,
                involved_steps: vec![],
                description: format!(
                    "No forward progress for {} consecutive events",
                    snapshot.stall_count
                ),
                severity: 2,
            });
        }

        // 2. Analyze recent events for step retries and cycles.
        let step_sequence: Vec<&str> = snapshot
            .recent_events
            .iter()
            .map(|e| e.step_id.as_str())
            .collect();

        // Step retry: same step appears N times with errors or in-progress.
        let retry_events = self.detect_step_retries(task_id, &snapshot.recent_events);
        events.extend(retry_events);

        // Step cycle: detect repeating subsequences.
        let cycle_events = self.detect_cycles(task_id, &step_sequence);
        events.extend(cycle_events);

        // 3. State oscillation: check if state is bouncing.
        // This would require state transition history; we approximate using
        // the mix of statuses in recent events.
        let oscillation = self.detect_status_oscillation(task_id, &snapshot.recent_events);
        if let Some(e) = oscillation {
            events.push(e);
        }

        events
    }

    fn detect_step_retries(
        &self,
        task_id: TaskId,
        events: &[crate::progress::ProgressEvent],
    ) -> Vec<LoopEvent> {
        let mut counts: HashMap<&str, u32> = HashMap::new();
        let mut error_counts: HashMap<&str, u32> = HashMap::new();

        for e in events {
            *counts.entry(e.step_id.as_str()).or_insert(0) += 1;
            if e.to_status == TaskStatus::Error {
                *error_counts.entry(e.step_id.as_str()).or_insert(0) += 1;
            }
        }

        let mut result = Vec::new();
        for (step, &count) in &counts {
            if count >= self.config.step_retry_threshold {
                let errors = error_counts.get(step).copied().unwrap_or(0);
                let severity = if errors >= self.config.step_retry_threshold {
                    3
                } else {
                    2
                };
                result.push(LoopEvent {
                    task_id,
                    loop_type: LoopType::StepRetry,
                    repetitions: count,
                    involved_steps: vec![step.to_string()],
                    description: format!(
                        "Step '{}' appeared {} times ({} errors)",
                        step, count, errors
                    ),
                    severity,
                });
            }
        }
        result
    }

    fn detect_cycles(&self, task_id: TaskId, sequence: &[&str]) -> Vec<LoopEvent> {
        let mut events = Vec::new();
        let n = sequence.len();

        // Try cycle lengths from min_cycle_length to n/2.
        for cycle_len in self.config.min_cycle_length..=(n / 2) {
            // Check if the last `cycle_len * repeat_threshold` elements
            // form a repeating pattern of length `cycle_len`.
            let needed = cycle_len * (self.config.cycle_repeat_threshold as usize);
            if n < needed {
                continue;
            }

            let tail = &sequence[n - needed..];
            let pattern = &tail[..cycle_len];

            let mut is_cycle = true;
            for i in 0..needed {
                if tail[i] != pattern[i % cycle_len] {
                    is_cycle = false;
                    break;
                }
            }

            if is_cycle {
                // Avoid duplicate reports for the same cycle.
                let already = events.iter().any(|e: &LoopEvent| {
                    e.loop_type == LoopType::StepCycle
                        && e.involved_steps
                            == pattern.iter().map(|s| s.to_string()).collect::<Vec<_>>()
                });
                if !already {
                    let repetitions = (needed / cycle_len) as u32;
                    events.push(LoopEvent {
                        task_id,
                        loop_type: LoopType::StepCycle,
                        repetitions,
                        involved_steps: pattern.iter().map(|s| s.to_string()).collect(),
                        description: format!(
                            "Detected cycle of length {} repeating {} times: {:?}",
                            cycle_len, repetitions, pattern
                        ),
                        severity: 2,
                    });
                }
            }
        }
        events
    }

    fn detect_status_oscillation(
        &self,
        task_id: TaskId,
        events: &[crate::progress::ProgressEvent],
    ) -> Option<LoopEvent> {
        // Look for alternating InProgress → Error → InProgress → Error pattern.
        let statuses: Vec<TaskStatus> = events.iter().map(|e| e.to_status).collect();
        if statuses.len() < 6 {
            return None;
        }

        let mut alternations = 0;
        for i in (1..statuses.len()).step_by(2) {
            if i + 1 < statuses.len() {
                if statuses[i] == TaskStatus::Error && statuses[i + 1] == TaskStatus::InProgress {
                    alternations += 1;
                }
            }
        }

        if alternations >= 2 {
            Some(LoopEvent {
                task_id,
                loop_type: LoopType::StateOscillation,
                repetitions: alternations as u32,
                involved_steps: vec![],
                description: format!(
                    "Status oscillating between InProgress and Error {} times",
                    alternations
                ),
                severity: 3,
            })
        } else {
            None
        }
    }
}

impl Default for LoopDetector {
    fn default() -> Self {
        Self::with_defaults()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::progress::{ProgressEvent, ProgressSnapshot, ProgressState, TaskStatus};
    use chrono::Utc;
    use std::time::Duration;

    fn make_snapshot(events: Vec<ProgressEvent>, stall: u32) -> ProgressSnapshot {
        ProgressSnapshot {
            task_id: TaskId::new(),
            state: ProgressState::Running,
            completion_fraction: 0.0,
            completed_steps: 0,
            total_steps: 10,
            errored_steps: 0,
            stall_count: stall,
            elapsed: Duration::ZERO,
            time_in_current_state: Duration::ZERO,
            recent_events: events,
        }
    }

    fn event(step: &str, status: TaskStatus) -> ProgressEvent {
        ProgressEvent {
            timestamp: Utc::now(),
            step_id: step.to_string(),
            from_status: TaskStatus::NotStarted,
            to_status: status,
            message: None,
        }
    }

    #[test]
    fn test_stall_detection() {
        let detector = LoopDetector::with_defaults();
        let snap = make_snapshot(vec![], 5);
        let events = detector.detect(snap.task_id, &snap);
        assert!(events.iter().any(|e| e.loop_type == LoopType::Stall));
    }

    #[test]
    fn test_step_retry_detection() {
        let detector = LoopDetector::with_defaults();
        let events = vec![
            event("step-1", TaskStatus::Error),
            event("step-1", TaskStatus::Error),
            event("step-1", TaskStatus::Error),
        ];
        let snap = make_snapshot(events, 0);
        let detected = detector.detect(snap.task_id, &snap);
        assert!(detected
            .iter()
            .any(|e| e.loop_type == LoopType::StepRetry && e.severity == 3));
    }

    #[test]
    fn test_cycle_detection() {
        let detector = LoopDetector::with_defaults();
        // A B A B A B — cycle of length 2, repeating 3 times
        let events = vec![
            event("A", TaskStatus::InProgress),
            event("B", TaskStatus::InProgress),
            event("A", TaskStatus::InProgress),
            event("B", TaskStatus::InProgress),
            event("A", TaskStatus::InProgress),
            event("B", TaskStatus::InProgress),
        ];
        let snap = make_snapshot(events, 0);
        let detected = detector.detect(snap.task_id, &snap);
        assert!(detected.iter().any(|e| e.loop_type == LoopType::StepCycle));
    }

    #[test]
    fn test_no_false_positive() {
        let detector = LoopDetector::with_defaults();
        let events = vec![
            event("A", TaskStatus::Done),
            event("B", TaskStatus::Done),
            event("C", TaskStatus::Done),
        ];
        let snap = make_snapshot(events, 0);
        let detected = detector.detect(snap.task_id, &snap);
        assert!(detected.is_empty());
    }
}
