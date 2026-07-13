use crate::models::{Step, StepId, StepStatus};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Detects anomalies: loops, repeated failures, blockers, stalls.
#[derive(Debug)]
pub struct AnomalyDetector {
    window_size: usize,
    similarity_threshold: f64,
    /// Recent step titles/descriptions per goal for loop detection
    recent_actions: HashMap<String, Vec<String>>,
    /// Failure counts per step
    failure_counts: HashMap<StepId, u32>,
    /// Steps that are blocked and why
    blocked_steps: HashMap<StepId, String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum Anomaly {
    /// The same or very similar action has been repeated multiple times
    LoopDetected {
        goal_id: String,
        repeated_action: String,
        count: usize,
    },
    /// A step has failed multiple times
    RepeatedFailure {
        step_id: StepId,
        failures: u32,
    },
    /// A step is blocked with no path forward
    Blocked {
        step_id: StepId,
        reason: String,
    },
    /// No progress has been made for a while
    Stall {
        goal_id: String,
        idle_seconds: u64,
    },
    /// A step depends on a failed step
    DependencyFailed {
        step_id: StepId,
        failed_dependency: StepId,
    },
}

impl AnomalyDetector {
    pub fn new(window_size: usize, similarity_threshold: f64) -> Self {
        Self {
            window_size,
            similarity_threshold,
            recent_actions: HashMap::new(),
            failure_counts: HashMap::new(),
            blocked_steps: HashMap::new(),
        }
    }

    /// Record a step execution and check for anomalies
    pub fn observe(&mut self, step: &Step) -> Vec<Anomaly> {
        let mut anomalies = Vec::new();
        let goal_id = &step.goal_id;

        // Track recent actions for loop detection
        let action_key = self.action_signature(step);
        let recent = self
            .recent_actions
            .entry(goal_id.clone())
            .or_insert_with(Vec::new);

        recent.push(action_key.clone());
        if recent.len() > self.window_size {
            recent.remove(0);
        }

        // Check for loops — same action signature appearing 3+ times in window
        let count = recent.iter().filter(|a| **a == action_key).count();
        if count >= 3 {
            anomalies.push(Anomaly::LoopDetected {
                goal_id: goal_id.clone(),
                repeated_action: action_key,
                count,
            });
        }

        // Track failures
        if step.status == StepStatus::Failed {
            let failures = self
                .failure_counts
                .entry(step.id.clone())
                .or_insert(0);
            *failures += 1;

            if *failures >= 2 {
                anomalies.push(Anomaly::RepeatedFailure {
                    step_id: step.id.clone(),
                    failures: *failures,
                });
            }
        }

        // Track blocked steps
        if step.status == StepStatus::Blocked {
            let reason = step
                .result
                .as_ref()
                .map(|r| r.error.clone().unwrap_or_else(|| "Unknown block".to_string()))
                .unwrap_or_else(|| "No result".to_string());
            self.blocked_steps.insert(step.id.clone(), reason.clone());
            anomalies.push(Anomaly::Blocked {
                step_id: step.id.clone(),
                reason,
            });
        } else if step.is_terminal() {
            self.blocked_steps.remove(&step.id);
        }

        anomalies
    }

    /// Check if a step's dependencies have failed
    pub fn check_dependencies(
        &self,
        step: &Step,
        all_steps: &HashMap<StepId, Step>,
    ) -> Vec<Anomaly> {
        let mut anomalies = Vec::new();

        for dep_id in &step.action.dependencies() {
            if let Some(dep) = all_steps.get(dep_id) {
                if dep.status == StepStatus::Failed {
                    anomalies.push(Anomaly::DependencyFailed {
                        step_id: step.id.clone(),
                        failed_dependency: dep_id.clone(),
                    });
                }
            }
        }

        anomalies
    }

    /// Clear state for a completed/cancelled goal
    pub fn clear_goal(&mut self, goal_id: &str) {
        self.recent_actions.remove(goal_id);
    }

    fn action_signature(&self, step: &Step) -> String {
        // Create a normalized signature of the action for comparison
        match &step.action {
            StepAction::Read { path } => format!("read:{}", normalize_path(path)),
            StepAction::Write { path, .. } => format!("write:{}", normalize_path(path)),
            StepAction::Execute { command, args } => {
                format!("exec:{}:{}", command, args.join(" "))
            }
            StepAction::Delegate { domain, sub_goal } => {
                format!("delegate:{}:{}", domain, sub_goal)
            }
            StepAction::Think { prompt } => {
                // Use first 50 chars as signature
                format!("think:{}", &prompt.chars().take(50).collect::<String>())
            }
            StepAction::Escalate { reason, .. } => {
                format!("escalate:{}", reason)
            }
        }
    }
}

fn normalize_path(path: &str) -> String {
    // Simple normalization — collapse repeated slashes, remove trailing
    let mut result = String::new();
    let mut last_was_slash = false;
    for c in path.chars() {
        if c == '/' {
            if !last_was_slash {
                result.push(c);
            }
            last_was_slash = true;
        } else {
            result.push(c);
            last_was_slash = false;
        }
    }
    result.trim_end_matches('/').to_string()
}

/// Helper trait to extract dependencies from actions
impl StepAction {
    pub fn dependencies(&self) -> Vec<StepId> {
        // In a full implementation, dependencies would be tracked at the step level
        // This is a placeholder for the delegation case
        vec![]
    }
}
