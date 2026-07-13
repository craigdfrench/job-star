//! Budget tracking for the supervisor.

use crate::error::SupervisorError;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;

/// Units of budget that can be tracked.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum BudgetUnit {
    /// Number of actions/steps executed.
    Steps,
    /// LLM tokens consumed.
    Tokens,
    /// Wall-clock time in seconds.
    TimeSeconds,
    /// Network bytes transferred.
    NetworkBytes,
    /// Custom unit (by index).
    Custom(u8),
}

/// A budget specification — initial limits.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Budget {
    limits: HashMap<BudgetUnit, u64>,
}

impl Budget {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with(mut self, unit: BudgetUnit, limit: u64) -> Self {
        self.limits.insert(unit, limit);
        self
    }

    pub fn limit(&self, unit: BudgetUnit) -> u64 {
        self.limits.get(&unit).copied().unwrap_or(0)
    }

    pub fn units(&self) -> impl Iterator<Item = &BudgetUnit> {
        self.limits.keys()
    }
}

/// Runtime budget state — tracks spending.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BudgetState {
    /// Initial limits.
    limits: HashMap<BudgetUnit, u64>,
    /// Amount spent so far.
    spent: HashMap<BudgetUnit, u64>,
    /// Threshold below which budget is "critical" (fraction of limit).
    critical_threshold: f64,
}

impl BudgetState {
    pub fn from_budget(budget: Budget) -> Self {
        let limits = budget.limits.clone();
        Self {
            limits,
            spent: HashMap::new(),
            critical_threshold: 0.1, // 10% remaining = critical
        }
    }

    pub fn remaining(&self, unit: BudgetUnit) -> u64 {
        let limit = self.limits.get(&unit).copied().unwrap_or(0);
        let spent = self.spent.get(&unit).copied().unwrap_or(0);
        limit.saturating_sub(spent)
    }

    pub fn spent(&self, unit: BudgetUnit) -> u64 {
        self.spent.get(&unit).copied().unwrap_or(0)
    }

    pub fn limit(&self, unit: BudgetUnit) -> u64 {
        self.limits.get(&unit).copied().unwrap_or(0)
    }

    /// Can we afford this cost?
    pub fn can_afford(&self, cost: Cost) -> bool {
        self.remaining(cost.unit) >= cost.amount
    }

    /// Spend budget. Returns error if insufficient.
    pub fn spend(&mut self, cost: Cost) -> Result<(), SupervisorError> {
        let remaining = self.remaining(cost.unit);
        if remaining < cost.amount {
            return Err(SupervisorError::BudgetExhausted {
                unit: cost.unit,
                remaining,
                required: cost.amount,
            });
        }
        let entry = self.spent.entry(cost.unit).or_insert(0);
        *entry += cost.amount;
        Ok(())
    }

    /// Is any tracked budget below the critical threshold?
    pub fn is_critical(&self) -> bool {
        for (unit, limit) in &self.limits {
            let remaining = self.remaining(*unit);
            if (*limit as f64) > 0.0 {
                let fraction = (remaining as f64) / (*limit as f64);
                if fraction <= self.critical_threshold {
                    return true;
                }
            }
        }
        false
    }

    /// Get a summary of all budget states.
    pub fn summary(&self) -> Vec<BudgetSummary> {
        self.limits
            .iter()
            .map(|(unit, limit)| {
                let spent = self.spent(unit);
                BudgetSummary {
                    unit: *unit,
                    limit: *limit,
                    spent,
                    remaining: limit.saturating_sub(spent),
                }
            })
            .collect()
    }
}

/// A cost to be charged against budget.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Cost {
    pub unit: BudgetUnit,
    pub amount: u64,
}

impl Cost {
    pub fn new(unit: BudgetUnit, amount: u64) -> Self {
        Self { unit, amount }
    }
}

/// A snapshot of one budget unit's state.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BudgetSummary {
    pub unit: BudgetUnit,
    pub limit: u64,
    pub spent: u64,
    pub remaining: u64,
}
