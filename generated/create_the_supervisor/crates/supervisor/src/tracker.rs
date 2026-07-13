//! Progress tracking and loop detection.

use crate::supervisor::constraint::action::Action;
use std::collections::VecDeque;

/// A signature for loop detection — identifies repeated actions.
pub type LoopSignature = String;

/// Detected loop information.
#[derive(Debug, Clone)]
pub struct LoopDetector {
    pub signature: LoopSignature,
    pub repetition_count: usize,
}

/// Tracks action history for progress monitoring and loop detection.
#[derive(Debug, Clone)]
pub struct ProgressTracker {
    /// Sliding window of recent action signatures.
    window: VecDeque<String>,
    /// Maximum window size.
    window_size: usize,
    /// Count of consecutive steps without successful progress.
    stall_count: usize,
    /// Total actions recorded.
    total_actions: u64,
    /// Total successful actions.
    successful_actions: u64,
}

impl ProgressTracker {
    pub fn new(window_size: usize) -> Self {
        Self {
            window: VecDeque::with_capacity(window_size),
            window_size,
            stall_count: 0,
            total_actions: 0,
            successful_actions: 0,
        }
    }

    /// Record an executed action and its outcome.
    pub fn record(&mut self, action: &Action, success: bool) {
        let sig = action.signature();
        self.window.push_back(sig);
        if self.window.len() > self.window_size {
            self.window.pop_front();
        }

        self.total_actions += 1;
        if success {
            self.successful_actions += 1;
            self.stall_count = 0;
        } else {
            self.stall_count += 1;
        }
    }

    /// Check if the proposed action would create a loop.
    /// Returns loop info if the action signature has appeared `>= max_repetitions` times
    /// in the current window.
    pub fn detect_loop(&self, action: &Action) -> Option<LoopDetector> {
        let sig = action.signature();
        let count = self.window.iter().filter(|s| **s == sig).count();

        if count > 0 {
            Some(LoopDetector {
                signature: sig,
                repetition_count: count + 1, // +1 for the proposed action
            })
        } else {
            None
        }
    }

    /// Get the current stall count (consecutive failures).
    pub fn stall_count(&self) -> usize {
        self.stall_count
    }

    /// Get total actions recorded.
    pub fn total_actions(&self) -> u64 {
        self.total_actions
    }

    /// Get successful actions count.
    pub fn successful_actions(&self) -> u64 {
        self.successful_actions
    }

    /// Success rate (0.0 to 1.0).
    pub fn success_rate(&self) -> f64 {
        if self.total_actions == 0 {
            return 1.0;
        }
        self.successful_actions as f64 / self.total_actions as f64
    }

    /// Get the recent action signatures (for debugging/reporting).
    pub fn recent_signatures(&self) -> Vec<&String> {
        self.window.iter().collect()
    }
}
