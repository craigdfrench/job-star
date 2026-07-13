use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SupervisorConfig {
    pub check_interval_ms: u64,
    pub max_concurrent_workers: usize,
    pub escalation_timeout_seconds: u64,
    pub loop_detection_window: usize,
    pub loop_similarity_threshold: f64,
    pub log_level: String,
}

impl Default for SupervisorConfig {
    fn default() -> Self {
        Self {
            check_interval_ms: 1000,
            max_concurrent_workers: 4,
            escalation_timeout_seconds: 300,
            loop_detection_window: 10,
            loop_similarity_threshold: 0.85,
            log_level: "info".to_string(),
        }
    }
}
