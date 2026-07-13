//! Integration test: full monitoring pipeline.

use jobstar_supervisor::*;
use std::time::Duration;

#[test]
fn test_full_monitoring_pipeline() {
    let supervisor = Supervisor {
        progress: ProgressMonitor::new(),
        budget: BudgetTracker::new(budget::BudgetConfig {
            max_tokens: Some(1000),
            max_duration: Some(Duration::from_secs(3600)),
            max_cost_cents: Some(1000),
            warning_threshold: 0.8,
        }),
        loops: LoopDetector::with_defaults(),
        blockers: BlockerDetector::with_defaults(),
        escalations: EscalationRouter::with_defaults(),
    };

    let task_id = TaskId::new();
    supervisor.progress.register(
        task_id,
        DomainId::new("meta"),
        GoalId::new("build-supervisor"),
        10,
    );
    supervisor.budget.register(task_id);

    // Simulate normal progress.
    supervisor.progress.transition(task_id, ProgressState::Running);
    supervisor.progress.record(task_id, "step-1", TaskStatus::Done, None);
    supervisor.progress.record(task_id, "step-2", TaskStatus::Done, None);
    supervisor.budget.record_tokens(task_id, 200);

    let report = supervisor.monitor(task_id);
    assert_eq!(report.recommendation, Recommendation::Continue);
    assert!(report.escalations.is_empty());

    // Simulate budget exhaustion.
    supervisor.budget.record_tokens(task_id, 800);
    let report = supervisor.monitor(task_id);
    assert_eq!(report.recommendation, Recommendation::Abort);
    assert!(report.budget_status.is_exhausted());
}

#[test]
fn test_loop_detection_triggers_caution() {
    let supervisor = Supervisor {
        progress: ProgressMonitor::new(),
        budget: BudgetTracker::new(budget::BudgetConfig::unlimited()),
        loops: LoopDetector::with_defaults(),
        blockers: BlockerDetector::with_defaults(),
        escalations: EscalationRouter::with_defaults(),
    };

    let task_id = TaskId::new();
    supervisor.progress.register(
        task_id,
        DomainId::new("meta"),
        GoalId::new("test"),
        10,
    );
    supervisor.budget.register_with_config(task_id, budget::BudgetConfig::unlimited());
    supervisor.progress.transition(task_id, ProgressState::Running);

    // Record repeated errors on the same step.
    for _ in 0..5 {
        supervisor
            .progress
            .record(task_id, "step-1", TaskStatus::Error, None);
    }

    let report = supervisor.monitor(task_id);
    assert!(!report.loop_events.is_empty());
    // Should have at least ProceedWithCaution or PauseForReview.
    assert!(report.recommendation != Recommendation::Continue);
}
