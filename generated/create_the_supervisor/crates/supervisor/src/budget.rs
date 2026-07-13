//! Budget tracking for the supervision core.

use serde::{Deserialize, Serialize};

/// Tracks resource budget consumption.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BudgetState {
    /// Total budget units available.
    pub total: u64,

    /// Budget units consumed so far.
    pub consumed: u64,

    /// Threshold below which budget is considered "low" (e.g., 20% of total).
    pub low_threshold: u64,
}

impl BudgetState {
    pub fn new(total: u64) -> Self {
        Self {
            total,
            consumed: 0,
            low_threshold: total / 5, // 20%
        }
    }

    pub fn with_remaining(remaining: u64, total: u64) -> Self {
        Self {
            total,
            consumed: total.saturating_sub(remaining),
            low_threshold: total / 5,
        }
    }

    pub fn remaining(&self) -> u64 {
        self.total.saturating_sub(self.consumed)
    }

    pub fn is_low(&self) -> bool {
        self.remaining() <= self.low_threshold
    }

    pub fn consume(&mut self, amount: u64) {
        self.consumed = self.consumed.saturating_add(amount);
    }

    pub fn remaining_summary(&self) -> String {
        format!("{}/{} units", self.remaining(), self.total)
    }
}


// --- DUPLICATE BLOCK ---

//! Budget tracking for the Job-Star supervision core.
//!
//! Tracks resource consumption across multiple dimensions (time, tokens,
//! iterations, API calls, file writes) scoped hierarchically: global,
//! per-domain, and per-goal. Detects overruns and emits warnings at
//! configurable thresholds.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

// ---------------------------------------------------------------------------
// Resource Types
// ---------------------------------------------------------------------------

/// Identifies a kind of resource being budgeted.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
pub enum ResourceKind {
    /// Wall-clock time, measured from goal start.
    Time,
    /// Total tokens consumed (prompt + completion).
    Tokens,
    /// Number of agent iterations / steps executed.
    Iterations,
    /// Number of external API calls made.
    ApiCalls,
    /// Number of file write operations.
    FileWrites,
    /// Number of shell / execute operations.
    Executions,
}

impl ResourceKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            ResourceKind::Time => "time",
            ResourceKind::Tokens => "tokens",
            ResourceKind::Iterations => "iterations",
            ResourceKind::ApiCalls => "api_calls",
            ResourceKind::FileWrites => "file_writes",
            ResourceKind::Executions => "executions",
        }
    }
}

/// A single resource budget: a limit and current consumption.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ResourceBudget {
    /// Maximum allowed consumption. `None` means unlimited.
    pub limit: Option<f64>,
    /// Current consumption.
    pub consumed: f64,
    /// Unit label for human-readable display (e.g., "ms", "tok", "calls").
    pub unit: &'static str,
}

impl ResourceBudget {
    pub fn new(limit: Option<f64>, unit: &'static str) -> Self {
        Self {
            limit,
            consumed: 0.0,
            unit,
        }
    }

    pub fn unlimited(unit: &'static str) -> Self {
        Self::new(None, unit)
    }

    /// Returns remaining budget, or `f64::INFINITY` if unlimited.
    pub fn remaining(&self) -> f64 {
        match self.limit {
            Some(lim) => (lim - self.consumed).max(0.0),
            None => f64::INFINITY,
        }
    }

    /// Fraction consumed: `consumed / limit`, or 0.0 if unlimited.
    pub fn fraction_consumed(&self) -> f64 {
        match self.limit {
            Some(lim) if lim > 0.0 => (self.consumed / lim).clamp(0.0, 1.0),
            _ => 0.0,
        }
    }

    /// Returns `true` if the budget is fully consumed.
    pub fn is_exhausted(&self) -> bool {
        match self.limit {
            Some(lim) => self.consumed >= lim,
            None => false,
        }
    }

    /// Check if consuming `amount` more would exceed the budget.
    pub fn would_exceed(&self, amount: f64) -> bool {
        match self.limit {
            Some(lim) => self.consumed + amount > lim,
            None => false,
        }
    }

    /// Consume `amount` from this budget. Returns `Err` if it would exceed.
    pub fn consume(&mut self, amount: f64) -> Result<(), BudgetError> {
        if amount < 0.0 {
            return Err(BudgetError::NegativeConsumption {
                kind: "unknown",
                amount,
            });
        }
        if self.would_exceed(amount) {
            return Err(BudgetError::Exceeded {
                kind: "unknown",
                limit: self.limit.unwrap(),
                consumed: self.consumed,
                attempted: amount,
            });
        }
        self.consumed += amount;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Budget Scope
// ---------------------------------------------------------------------------

/// A named scope for budgets: "global", a domain name, or a goal ID.
#[derive(Debug, Clone, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
pub enum BudgetScope {
    Global,
    Domain(String),
    Goal(String),
}

impl BudgetScope {
    pub fn as_str(&self) -> String {
        match self {
            BudgetScope::Global => "global".to_string(),
            BudgetScope::Domain(d) => format!("domain:{}", d),
            BudgetScope::Goal(g) => format!("goal:{}", g),
        }
    }
}

/// A collection of resource budgets for a single scope.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ScopeBudget {
    pub scope: BudgetScope,
    pub resources: HashMap<ResourceKind, ResourceBudget>,
    /// When this scope's time budget started ticking (if it has one).
    #[serde(skip)]
    pub start_time: Option<Instant>,
}

impl ScopeBudget {
    pub fn new(scope: BudgetScope) -> Self {
        Self {
            scope,
            resources: HashMap::new(),
            start_time: None,
        }
    }

    /// Set a limit for a specific resource.
    pub fn set_limit(&mut self, kind: ResourceKind, limit: f64, unit: &'static str) {
        self.resources.insert(kind, ResourceBudget::new(Some(limit), unit));
    }

    /// Mark a resource as unlimited.
    pub fn set_unlimited(&mut self, kind: ResourceKind, unit: &'static str) {
        self.resources.insert(kind, ResourceBudget::unlimited(unit));
    }

    /// Get a resource budget, if it exists.
    pub fn get(&self, kind: ResourceKind) -> Option<&ResourceBudget> {
        self.resources.get(&kind)
    }

    /// Get a mutable resource budget, if it exists.
    pub fn get_mut(&mut self, kind: ResourceKind) -> Option<&mut ResourceBudget> {
        self.resources.get_mut(&kind)
    }

    /// Consume `amount` of `kind`. Returns error if exceeded.
    pub fn consume(&mut self, kind: ResourceKind, amount: f64) -> Result<(), BudgetError> {
        let scope_str = self.scope.as_str();
        let unit;
        {
            let entry = self
                .resources
                .entry(kind)
                .or_insert_with(|| ResourceBudget::unlimited(default_unit(kind)));
            unit = entry.unit;
            let limit = entry.limit;
            let consumed = entry.consumed;
            if entry.would_exceed(amount) {
                return Err(BudgetError::Exceeded {
                    kind: kind.as_str(),
                    limit: limit.unwrap(),
                    consumed,
                    attempted: amount,
                });
            }
            entry.consumed += amount;
        }
        let _ = unit; // suppress unused warning
        Ok(())
    }

    /// Update time-based consumption if a time budget exists.
    pub fn tick_time(&mut self) {
        if let Some(start) = self.start_time {
            let elapsed_ms = start.elapsed().as_millis() as f64;
            if let Some(b) = self.resources.get_mut(&ResourceKind::Time) {
                b.consumed = elapsed_ms;
            }
        }
    }

    /// Check all resources for exhaustion.
    pub fn exhausted_resources(&self) -> Vec<ResourceKind> {
        self.resources
            .iter()
            .filter(|(_, b)| b.is_exhausted())
            .map(|(k, _)| *k)
            .collect()
    }

    /// Check which resources are above a warning threshold.
    pub fn resources_above_threshold(&self, threshold: f64) -> Vec<(ResourceKind, f64)> {
        self.resources
            .iter()
            .filter(|(_, b)| b.fraction_consumed() >= threshold)
            .map(|(k, b)| (*k, b.fraction_consumed()))
            .collect()
    }
}

fn default_unit(kind: ResourceKind) -> &'static str {
    match kind {
        ResourceKind::Time => "ms",
        ResourceKind::Tokens => "tok",
        ResourceKind::Iterations => "steps",
        ResourceKind::ApiCalls => "calls",
        ResourceKind::FileWrites => "writes",
        ResourceKind::Executions => "execs",
    }
}

// ---------------------------------------------------------------------------
// Budget Tracker
// ---------------------------------------------------------------------------

/// Configuration for a budget scope.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct BudgetConfig {
    pub limits: HashMap<ResourceKind, f64>,
}

impl BudgetConfig {
    pub fn new() -> Self {
        Self {
            limits: HashMap::new(),
        }
    }

    pub fn with(mut self, kind: ResourceKind, limit: f64) -> Self {
        self.limits.insert(kind, limit);
        self
    }
}

impl Default for BudgetConfig {
    fn default() -> Self {
        Self::new()
    }
}

/// Warning threshold (fraction of budget). When consumption exceeds this,
/// a warning event is emitted.
pub const DEFAULT_WARNING_THRESHOLD: f64 = 0.80;

/// The central budget tracker. Thread-safe via an internal mutex.
#[derive(Debug, Clone)]
pub struct BudgetTracker {
    inner: Arc<Mutex<BudgetTrackerInner>>,
}

#[derive(Debug)]
struct BudgetTrackerInner {
    scopes: HashMap<BudgetScope, ScopeBudget>,
    warning_threshold: f64,
    /// Scopes that have already emitted a warning, to avoid spam.
    warned: HashMap<(BudgetScope, ResourceKind), bool>,
}

impl BudgetTracker {
    /// Create a new empty tracker.
    pub fn new() -> Self {
        Self {
            inner: Arc::new(Mutex::new(BudgetTrackerInner {
                scopes: HashMap::new(),
                warning_threshold: DEFAULT_WARNING_THRESHOLD,
                warned: HashMap::new(),
            })),
        }
    }

    /// Set the warning threshold (0.0–1.0).
    pub fn set_warning_threshold(&self, threshold: f64) {
        let mut inner = self.inner.lock().unwrap();
        inner.warning_threshold = threshold.clamp(0.0, 1.0);
    }

    /// Register a scope with given config. If the scope already exists,
    /// its limits are updated (consumption is preserved).
    pub fn register_scope(&self, scope: BudgetScope, config: &BudgetConfig) {
        let mut inner = self.inner.lock().unwrap();
        let sb = inner
            .scopes
            .entry(scope.clone())
            .or_insert_with(|| ScopeBudget::new(scope.clone()));

        for (kind, limit) in &config.limits {
            sb.set_limit(*kind, *limit, default_unit(*kind));
        }

        // If there's a time budget, record start time.
        if config.limits.contains_key(&ResourceKind::Time) && sb.start_time.is_none() {
            sb.start_time = Some(Instant::now());
        }
    }

    /// Convenience: register the global scope.
    pub fn register_global(&self, config: &BudgetConfig) {
        self.register_scope(BudgetScope::Global, config);
    }

    /// Convenience: register a domain scope.
    pub fn register_domain(&self, domain: &str, config: &BudgetConfig) {
        self.register_scope(BudgetScope::Domain(domain.to_string()), config);
    }

    /// Convenience: register a goal scope.
    pub fn register_goal(&self, goal_id: &str, config: &BudgetConfig) {
        self.register_scope(BudgetScope::Goal(goal_id.to_string()), config);
    }

    /// Consume `amount` of `kind` in `scope`.
    /// Also propagates to the global scope.
    pub fn consume(
        &self,
        scope: &BudgetScope,
        kind: ResourceKind,
        amount: f64,
    ) -> Result<Vec<BudgetEvent>, BudgetError> {
        let mut inner = self.inner.lock().unwrap();
        let mut events = Vec::new();

        // Update time consumption first.
        if let Some(sb) = inner.scopes.get_mut(scope) {
            sb.tick_time();
        }
        if let Some(sb) = inner.scopes.get_mut(&BudgetScope::Global) {
            sb.tick_time();
        }

        // Consume in the specific scope.
        if let Some(sb) = inner.scopes.get_mut(scope) {
            sb.consume(kind, amount)?;
        }

        // Propagate to global (unless we ARE the global scope).
        if *scope != BudgetScope::Global {
            if let Some(sb) = inner.scopes.get_mut(&BudgetScope::Global) {
                // For time, we don't double-count; time is wall-clock.
                if kind != ResourceKind::Time {
                    sb.consume(kind, amount)?;
                }
            }
        }

        // Check for warnings and exhaustion.
        let threshold = inner.warning_threshold;
        let scopes_to_check = if *scope == BudgetScope::Global {
            vec![BudgetScope::Global]
        } else {
            vec![scope.clone(), BudgetScope::Global]
        };

        for s in &scopes_to_check {
            if let Some(sb) = inner.scopes.get(s) {
                // Exhaustion events.
                for ex_kind in sb.exhausted_resources() {
                    events.push(BudgetEvent::Exhausted {
                        scope: s.clone(),
                        kind: ex_kind,
                        limit: sb.get(ex_kind).and_then(|b| b.limit).unwrap_or(0.0),
                        consumed: sb.get(ex_kind).map(|b| b.consumed).unwrap_or(0.0),
                    });
                }

                // Warning events (only once per scope+kind).
                for (w_kind, frac) in sb.resources_above_threshold(threshold) {
                    let key = (s.clone(), w_kind);
                    if !inner.warned.get(&key).copied().unwrap_or(false) {
                        inner.warned.insert(key, true);
                        events.push(BudgetEvent::Warning {
                            scope: s.clone(),
                            kind: w_kind,
                            fraction: frac,
                            remaining: sb.get(w_kind).map(|b| b.remaining()).unwrap_or(0.0),
                        });
                    }
                }
            }
        }

        Ok(events)
    }

    /// Convenience: consume tokens.
    pub fn consume_tokens(
        &self,
        scope: &BudgetScope,
        tokens: u64,
    ) -> Result<Vec<BudgetEvent>, BudgetError> {
        self.consume(scope, ResourceKind::Tokens, tokens as f64)
    }

    /// Convenience: consume one iteration.
    pub fn consume_iteration(
        &self,
        scope: &BudgetScope,
    ) -> Result<Vec<BudgetEvent>, BudgetError> {
        self.consume(scope, ResourceKind::Iterations, 1.0)
    }

    /// Convenience: consume one API call.
    pub fn consume_api_call(
        &self,
        scope: &BudgetScope,
    ) -> Result<Vec<BudgetEvent>, BudgetError> {
        self.consume(scope, ResourceKind::ApiCalls, 1.0)
    }

    /// Convenience: consume one file write.
    pub fn consume_file_write(
        &self,
        scope: &BudgetScope,
    ) -> Result<Vec<BudgetEvent>, BudgetError> {
        self.consume(scope, ResourceKind::FileWrites, 1.0)
    }

    /// Convenience: consume one execution.
    pub fn consume_execution(
        &self,
        scope: &BudgetScope,
    ) -> Result<Vec<BudgetEvent>, BudgetError> {
        self.consume(scope, ResourceKind::Executions, 1.0)
    }

    /// Get a snapshot of a scope's budget status.
    pub fn status(&self, scope: &BudgetScope) -> Option<ScopeStatus> {
        let mut inner = self.inner.lock().unwrap();
        if let Some(sb) = inner.scopes.get_mut(scope) {
            sb.tick_time();
            let resources: HashMap<ResourceKind, ResourceStatus> = sb
                .resources
                .iter()
                .map(|(k, b)| {
                    (
                        *k,
                        ResourceStatus {
                            limit: b.limit,
                            consumed: b.consumed,
                            remaining: b.remaining(),
                            fraction: b.fraction_consumed(),
                            exhausted: b.is_exhausted(),
                            unit: b.unit,
                        },
                    )
                })
                .collect();
            Some(ScopeStatus {
                scope: scope.clone(),
                resources,
            })
        } else {
            None
        }
    }

    /// Get status for all scopes.
    pub fn all_status(&self) -> Vec<ScopeStatus> {
        let mut inner = self.inner.lock().unwrap();
        let scopes: Vec<BudgetScope> = inner.scopes.keys().cloned().collect();
        drop(inner);
        scopes
            .iter()
            .filter_map(|s| self.status(s))
            .collect()
    }

    /// Check if a scope has any exhausted resources.
    pub fn is_scope_blocked(&self, scope: &BudgetScope) -> bool {
        let mut inner = self.inner.lock().unwrap();
        if let Some(sb) = inner.scopes.get_mut(scope) {
            sb.tick_time();
            !sb.exhausted_resources().is_empty()
        } else {
            false
        }
    }

    /// Check if the global budget is blocked.
    pub fn is_global_blocked(&self) -> bool {
        self.is_scope_blocked(&BudgetScope::Global)
    }

    /// Reset a scope's consumption (e.g., when retrying a goal).
    pub fn reset_scope(&self, scope: &BudgetScope) {
        let mut inner = self.inner.lock().unwrap();
        if let Some(sb) = inner.scopes.get_mut(scope) {
            for b in sb.resources.values_mut() {
                b.consumed = 0.0;
            }
            if sb.resources.contains_key(&ResourceKind::Time) {
                sb.start_time = Some(Instant::now());
            }
        }
        // Clear warnings for this scope.
        let keys_to_clear: Vec<(BudgetScope, ResourceKind)> = inner
            .warned
            .keys()
            .filter(|(s, _)| s == scope)
            .cloned()
            .collect();
        for k in keys_to_clear {
            inner.warned.remove(&k);
        }
    }

    /// Remove a scope entirely (e.g., when a goal completes).
    pub fn remove_scope(&self, scope: &BudgetScope) {
        let mut inner = self.inner.lock().unwrap();
        inner.scopes.remove(scope);
        let keys_to_remove: Vec<(BudgetScope, ResourceKind)> = inner
            .warned
            .keys()
            .filter(|(s, _)| s == scope)
            .cloned()
            .collect();
        for k in keys_to_remove {
            inner.warned.remove(&k);
        }
    }
}

impl Default for BudgetTracker {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Status & Events
// ---------------------------------------------------------------------------

/// Snapshot of a single resource's status.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ResourceStatus {
    pub limit: Option<f64>,
    pub consumed: f64,
    pub remaining: f64,
    pub fraction: f64,
    pub exhausted: bool,
    pub unit: &'static str,
}

/// Snapshot of a scope's budget status.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ScopeStatus {
    pub scope: BudgetScope,
    pub resources: HashMap<ResourceKind, ResourceStatus>,
}

impl ScopeStatus {
    /// Human-readable summary.
    pub fn summary(&self) -> String {
        let mut lines = vec![format!("[{}]", self.scope.as_str())];
        let mut kinds: Vec<_> = self.resources.keys().collect();
        kinds.sort_by_key(|k| k.as_str());
        for kind in kinds {
            let r = &self.resources[kind];
            match r.limit {
                Some(lim) => {
                    let pct = (r.fraction * 100.0) as u32;
                    let marker = if r.exhausted { " ❌" } else if r.fraction >= 0.8 { " ⚠" } else { "" };
                    lines.push(format!(
                        "  {}: {:.0}/{:.0} {} ({}%){}",
                        kind.as_str(), r.consumed, lim, r.unit, pct, marker
                    ));
                }
                None => {
                    lines.push(format!(
                        "  {}: {:.0} {} (unlimited)",
                        kind.as_str(), r.consumed, r.unit
                    ));
                }
            }
        }
        lines.join("\n")
    }
}

/// Events emitted by the budget tracker.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub enum BudgetEvent {
    /// A resource budget has been fully consumed.
    Exhausted {
        scope: BudgetScope,
        kind: ResourceKind,
        limit: f64,
        consumed: f64,
    },
    /// A resource budget has crossed the warning threshold.
    Warning {
        scope: BudgetScope,
        kind: ResourceKind,
        fraction: f64,
        remaining: f64,
    },
}

impl BudgetEvent {
    pub fn scope(&self) -> &BudgetScope {
        match self {
            BudgetEvent::Exhausted { scope, .. } => scope,
            BudgetEvent::Warning { scope, .. } => scope,
        }
    }

    pub fn kind(&self) -> ResourceKind {
        match self {
            BudgetEvent::Exhausted { kind, .. } => *kind,
            BudgetEvent::Warning { kind, .. } => *kind,
        }
    }

    pub fn is_critical(&self) -> bool {
        matches!(self, BudgetEvent::Exhausted { .. })
    }
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, thiserror::Error, serde::Serialize, serde::Deserialize)]
pub enum BudgetError {
    #[error("budget exceeded for {kind}: limit={limit}, consumed={consumed}, attempted={attempted}")]
    Exceeded {
        kind: &'static str,
        limit: f64,
        consumed: f64,
        attempted: f64,
    },
    #[error("negative consumption for {kind}: {amount}")]
    NegativeConsumption { kind: &'static str, amount: f64 },
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_resource_budget_basic() {
        let mut b = ResourceBudget::new(Some(100.0), "tok");
        assert!(!b.is_exhausted());
        assert_eq!(b.remaining(), 100.0);

        b.consume(40.0).unwrap();
        assert_eq!(b.consumed, 40.0);
        assert!(!b.is_exhausted());

        b.consume(60.0).unwrap();
        assert!(b.is_exhausted());
        assert_eq!(b.remaining(), 0.0);

        assert!(b.consume(1.0).is_err());
    }

    #[test]
    fn test_unlimited_budget() {
        let mut b = ResourceBudget::unlimited("tok");
        assert!(!b.is_exhausted());
        b.consume(1e18).unwrap();
        assert!(!b.is_exhausted());
    }

    #[test]
    fn test_would_exceed() {
        let b = ResourceBudget::new(Some(50.0), "tok");
        assert!(!b.would_exceed(49.0));
        assert!(!b.would_exceed(50.0));
        assert!(b.would_exceed(51.0));
    }

    #[test]
    fn test_scope_budget_consume() {
        let mut sb = ScopeBudget::new(BudgetScope::Domain("test".to_string()));
        sb.set_limit(ResourceKind::Tokens, 1000.0, "tok");
        sb.set_limit(ResourceKind::Iterations, 10.0, "steps");

        sb.consume(ResourceKind::Tokens, 300.0).unwrap();
        assert_eq!(sb.get(ResourceKind::Tokens).unwrap().consumed, 300.0);

        sb.consume(ResourceKind::Iterations, 10.0).unwrap();
        assert!(sb.get(ResourceKind::Iterations).unwrap().is_exhausted());

        // Over the limit
        let err = sb.consume(ResourceKind::Tokens, 800.0).unwrap_err();
        assert!(matches!(err, BudgetError::Exceeded { .. }));
    }

    #[test]
    fn test_tracker_global_and_domain() {
        let tracker = BudgetTracker::new();

        let global_cfg = BudgetConfig::new()
            .with(ResourceKind::Tokens, 10000.0)
            .with(ResourceKind::Iterations, 100.0);

        let domain_cfg = BudgetConfig::new()
            .with(ResourceKind::Tokens, 2000.0)
            .with(ResourceKind::Iterations, 20.0);

        tracker.register_global(&global_cfg);
        tracker.register_domain("coding", &domain_cfg);

        let scope = BudgetScope::Domain("coding".to_string());

        // Consume 500 tokens in coding domain.
        let events = tracker.consume_tokens(&scope, 500).unwrap();
        assert!(events.is_empty()); // No warnings yet.

        // Check both scopes updated.
        let domain_status = tracker.status(&scope).unwrap();
        assert_eq!(
            domain_status.resources[&ResourceKind::Tokens].consumed,
            500.0
        );

        let global_status = tracker.status(&BudgetScope::Global).unwrap();
        assert_eq!(
            global_status.resources[&ResourceKind::Tokens].consumed,
            500.0
        );
    }

    #[test]
    fn test_warning_threshold() {
        let tracker = BudgetTracker::new();
        tracker.set_warning_threshold(0.5);

        let cfg = BudgetConfig::new().with(ResourceKind::Tokens, 100.0);
        tracker.register_goal("g1", &cfg);

        let scope = BudgetScope::Goal("g1".to_string());

        // Consume 40% — no warning.
        let events = tracker.consume_tokens(&scope, 40).unwrap();
        assert!(events.is_empty());

        // Consume 20% more (total 60%) — should warn.
        let events = tracker.consume_tokens(&scope, 20).unwrap();
        assert!(events.iter().any(|e| matches!(e, BudgetEvent::Warning { .. })));

        // Consume again — no duplicate warning.
        let events = tracker.consume_tokens(&scope, 5).unwrap();
        assert!(!events.iter().any(|e| matches!(e, BudgetEvent::Warning { .. })));
    }

    #[test]
    fn test_exhaustion_event() {
        let tracker = BudgetTracker::new();
        let cfg = BudgetConfig::new().with(ResourceKind::Iterations, 3.0);
        tracker.register_goal("g2", &cfg);

        let scope = BudgetScope::Goal("g2".to_string());

        tracker.consume_iteration(&scope).unwrap();
        tracker.consume_iteration(&scope).unwrap();
        let events = tracker.consume_iteration(&scope).unwrap();

        assert!(events.iter().any(|e| {
            matches!(e, BudgetEvent::Exhausted { kind, .. } if *kind == ResourceKind::Iterations)
        }));
    }

    #[test]
    fn test_is_scope_blocked() {
        let tracker = BudgetTracker::new();
        let cfg = BudgetConfig::new().with(ResourceKind::Tokens, 10.0);
        tracker.register_goal("g3", &cfg);

        let scope = BudgetScope::Goal("g3".to_string());
        assert!(!tracker.is_scope_blocked(&scope));

        tracker.consume_tokens(&scope, 10).unwrap();
        assert!(tracker.is_scope_blocked(&scope));
    }

    #[test]
    fn test_reset_scope() {
        let tracker = BudgetTracker::new();
        let cfg = BudgetConfig::new().with(ResourceKind::Tokens, 100.0);
        tracker.register_goal("g4", &cfg);

        let scope = BudgetScope::Goal("g4".to_string());
        tracker.consume_tokens(&scope, 80).unwrap();

        tracker.reset_scope(&scope);
        let status = tracker.status(&scope).unwrap();
        assert_eq!(status.resources[&ResourceKind::Tokens].consumed, 0.0);
    }

    #[test]
    fn test_remove_scope() {
        let tracker = BudgetTracker::new();
        let cfg = BudgetConfig::new().with(ResourceKind::Tokens, 100.0);
        tracker.register_goal("g5", &cfg);

        let scope = BudgetScope::Goal("g5".to_string());
        assert!(tracker.status(&scope).is_some());

        tracker.remove_scope(&scope);
        assert!(tracker.status(&scope).is_none());
    }

    #[test]
    fn test_status_summary() {
        let tracker = BudgetTracker::new();
        let cfg = BudgetConfig::new()
            .with(ResourceKind::Tokens, 1000.0)
            .with(ResourceKind::Iterations, 50.0);
        tracker.register_domain("test", &cfg);

        let scope = BudgetScope::Domain("test".to_string());
        tracker.consume_tokens(&scope, 850).unwrap();

        let status = tracker.status(&scope).unwrap();
        let summary = status.summary();
        assert!(summary.contains("domain:test"));
        assert!(summary.contains("⚠")); // 85% should trigger warning marker
    }

    #[test]
    fn test_time_budget() {
        let tracker = BudgetTracker::new();
        let cfg = BudgetConfig::new().with(ResourceKind::Time, 10000.0); // 10 seconds
        tracker.register_goal("timed", &cfg);

        let scope = BudgetScope::Goal("timed".to_string());
        std::thread::sleep(Duration::from_millis(50));

        let status = tracker.status(&scope).unwrap();
        let time_status = &status.resources[&ResourceKind::Time];
        assert!(time_status.consumed > 0.0);
        assert!(time_status.consumed < 10000.0);
    }

    #[test]
    fn test_negative_consumption_rejected() {
        let mut sb = ScopeBudget::new(BudgetScope::Global);
        sb.set_limit(ResourceKind::Tokens, 100.0, "tok");
        let err = sb.consume(ResourceKind::Tokens, -10.0).unwrap_err();
        assert!(matches!(err, BudgetError::NegativeConsumption { .. }));
    }
}
