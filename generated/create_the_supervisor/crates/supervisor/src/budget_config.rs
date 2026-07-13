//! Default budget configurations for common domain scenarios.

use crate::budget::{BudgetConfig, ResourceKind};

/// Conservative defaults for the global budget.
pub fn global_defaults() -> BudgetConfig {
    BudgetConfig::new()
        .with(ResourceKind::Tokens, 500_000.0)
        .with(ResourceKind::Iterations, 200.0)
        .with(ResourceKind::ApiCalls, 100.0)
        .with(ResourceKind::FileWrites, 50.0)
        .with(ResourceKind::Executions, 30.0)
        .with(ResourceKind::Time, 1_800_000.0) // 30 minutes in ms
}

/// Budget for coding-domain goals.
pub fn coding_defaults() -> BudgetConfig {
    BudgetConfig::new()
        .with(ResourceKind::Tokens, 100_000.0)
        .with(ResourceKind::Iterations, 40.0)
        .with(ResourceKind::FileWrites, 20.0)
        .with(ResourceKind::Executions, 15.0)
        .with(ResourceKind::Time, 600_000.0) // 10 minutes
}

/// Budget for research-domain goals.
pub fn research_defaults() -> BudgetConfig {
    BudgetConfig::new()
        .with(ResourceKind::Tokens, 80_000.0)
        .with(ResourceKind::Iterations, 30.0)
        .with(ResourceKind::ApiCalls, 20.0)
        .with(ResourceKind::Time, 600_000.0) // 10 minutes
}

/// Budget for meta-domain goals (building Job-Star itself).
pub fn meta_defaults() -> BudgetConfig {
    BudgetConfig::new()
        .with(ResourceKind::Tokens, 150_000.0)
        .with(ResourceKind::Iterations, 50.0)
        .with(ResourceKind::FileWrites, 30.0)
        .with(ResourceKind::Executions, 20.0)
        .with(ResourceKind::Time, 900_000.0) // 15 minutes
}

/// Budget for writing-domain goals.
pub fn writing_defaults() -> BudgetConfig {
    BudgetConfig::new()
        .with(ResourceKind::Tokens, 60_000.0)
        .with(ResourceKind::Iterations, 25.0)
        .with(ResourceKind::FileWrites, 10.0)
        .with(ResourceKind::Time, 300_000.0) // 5 minutes
}

/// Get defaults for a domain by name.
pub fn defaults_for_domain(domain: &str) -> BudgetConfig {
    match domain {
        "coding" => coding_defaults(),
        "research" => research_defaults(),
        "meta" => meta_defaults(),
        "writing" => writing_defaults(),
        _ => coding_defaults(), // safe default
    }
}
