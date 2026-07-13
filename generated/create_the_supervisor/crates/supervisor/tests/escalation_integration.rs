//! Integration test: full escalation flow from detection to resolution.

use job_star_supervisor::*;
use chrono::Utc;

#[tokio::test]
async fn full_escalation_lifecycle() {
    let config = EscalationConfig::default();
    let manager = EscalationManager::new(config.clone());
    let detector = EscalationDetector::new(config);

    // Simulate a budget exhaustion scenario
    let mut budgets = BudgetState::default();
    let mut steps = BudgetEntry::new(BudgetType::Steps, 50);
    steps.consume(50);
    budgets.budgets.push(steps);

    let snapshot = monitor::ProgressSnapshot {
        current_step: 50,
        steps_since_progress: 3,
        status: monitor::ProgressStatus::InProgress,
        completion_pct: 60.0,
    };

    // Detect triggers
    let triggers = detector.detect(&snapshot, &[], &budgets);
    assert!(!triggers.is_empty());

    // Escalate all detected triggers
    let mut ids = Vec::new();
    for (severity, trigger) in triggers {
        let id = manager
            .escalate(severity, trigger, EscalationContext {
                step: Some(50),
                ..Default::default()
            })
            .await;
        ids.push(id);
    }

    // Work should be paused
    assert!(manager.work_should_pause().await);

    // Human resolves the blocker
    let blocker_id = *ids
        .first()
        .expect("should have at least one escalation");

    manager
        .resolve(
            blocker_id,
            Resolution {
                resolved_by: Resolver::Human,
                resolved_at: Utc::now(),
                outcome: ResolutionOutcome::Continue {
                    guidance: "Budget increased to 100 steps".to_string(),
                },
                message: "Extended step budget".to_string(),
            },
        )
        .await
        .unwrap();

    // Check stats
    let stats = manager.stats().await;
    assert!(stats.total > 0);
}
