//! Integration tests for the Job-Star supervisor core.

use job_star_supervisor::*;
use constraints::*;
use state::BudgetKind;
use uuid::Uuid;

fn make_supervisor() -> Supervisor {
    let job_id = Uuid::new_v4();
    let mut policy = ConstraintPolicy::new();
    policy.set_domain(
        Domain::new("fs"),
        ConstraintSet {
            read: Permission::Allow,
            write: Permission::AllowIfTarget { patterns: vec!["/tmp/".into()] },
            execute: Permission::Deny,
        },
    );
    policy.set_domain(
        Domain::new("meta"),
        ConstraintSet::permissive(),
    );

    Supervisor::new(job_id, "test goal", policy)
        .with_budget(BudgetKind::Steps, 10)
        .with_budget(BudgetKind::Tokens, 1000)
}

fn make_starting_event(domain: &str, actions: Vec<Action>, signature: &str) -> StepEvent {
    StepEvent::Starting {
        step_id: Supervisor::new_step_id(),
        domain: Domain::new(domain),
        description: "test step".into(),
        actions,
        estimated_cost: vec![(BudgetKind::Steps, 1)],
        signature: signature.into(),
    }
}

fn make_finished_event(domain: &str, status: StepStatus, signature: &str) -> StepEvent {
    StepEvent::Finished {
        step_id: Supervisor::new_step_id(),
        domain: Domain::new(domain),
        description: "test step".into(),
        status,
        actual_cost: vec![(BudgetKind::Steps, 1)],
        signature: signature.into(),
    }
}

// =========================================================================
// Constraint enforcement tests
// =========================================================================

#[test]
fn test_allow_permitted_read() {
    let mut sup = make_supervisor();
    let event = make_starting_event(
        "fs",
        vec![Action::Read { target: "/etc/passwd".into() }],
        "sig1",
    );
    let outcome = sup.handle(event).unwrap();
    assert!(outcome.allowed);
}

#[test]
fn test_deny_execute_in_fs_domain() {
    let mut sup = make_supervisor();
    let event = make_starting_event(
        "fs",
        vec![Action::Execute { target: "rm -rf /".into() }],
        "sig1",
    );
    let outcome = sup.handle(event).unwrap();
    assert!(!outcome.allowed);
    assert!(outcome.message.contains("constraint violation"));
}

#[test]
fn test_write_only_in_tmp() {
    let mut sup = make_supervisor();
    // Write to /tmp/ — allowed
    let event = make_starting_event(
        "fs",
        vec![Action::Write { target: "/tmp/out.txt".into() }],
        "sig1",
    );
    let outcome = sup.handle(event).unwrap();
    assert!(outcome.allowed);

    // Write to /etc/ — denied
    let event = make_starting_event(
        "fs",
        vec![Action::Write { target: "/etc/passwd".into() }],
        "sig2",
    );
    let outcome = sup.handle(event).unwrap();
    assert!(!outcome.allowed);
}

#[test]
fn test_unknown_domain_fails_closed() {
    let mut sup = make_supervisor();
    let event = make_starting_event(
        "network",
        vec![Action::Read { target: "http://example.com".into() }],
        "sig1",
    );
    let outcome = sup.handle(event).unwrap();
    assert!(!outcome.allowed);
}

#[test]
fn test_goal_restrictions_narrow_permissions() {
    let job_id = Uuid::new_v4();
    let mut policy = ConstraintPolicy::new();
    policy.set_domain(
        Domain::new("fs"),
        ConstraintSet {
            read: Permission::Allow,
            write: Permission::Allow,
            execute: Permission::Allow,
        },
    );

    // Goal restricts fs to read-only.
    let mut goal_restrictions = std::collections::HashMap::new();
    goal_restrictions.insert(
        Domain::new("fs"),
        ConstraintSet {
            read: Permission::Allow,
            write: Permission::Deny,
            execute: Permission::Deny,
        },
    );

    let merged = policy.merge_goal_restrictions(&goal_restrictions);
    let mut sup = Supervisor::new(job_id, "restricted goal", merged);

    // Read should be allowed.
    let event = make_starting_event("fs", vec![Action::Read { target: "/x".into() }], "sig1");
    assert!(sup.handle(event).unwrap().allowed);

    // Write should be denied.
    let event = make_starting_event("fs", vec![Action::Write { target: "/x".into() }], "sig2");
    assert!(!sup.handle(event).unwrap().allowed);
}

// =========================================================================
// Budget enforcement tests
// =========================================================================

#[test]
fn test_budget_exceeded_halts_job() {
    let job_id = Uuid::new_v4();
    let policy = ConstraintPolicy::new();
    let mut sup = Supervisor::new(job_id, "budget test", policy)
        .with_budget(BudgetKind::Steps, 3);

    // Run 3 successful steps.
    for i in 0..3 {
        let event = make_finished_event("meta", StepStatus::Success, &format!("sig{i}"));
        let outcome = sup.handle(event).unwrap();
        assert!(!outcome.halted, "should not halt on step {i}");
    }

    // 4th step should exceed budget and halt.
    let event = make_finished_event("meta", StepStatus::Success, "sig4");
    let outcome = sup.handle(event).unwrap();
    assert!(outcome.halted);
    assert!(outcome.message.contains("budget"));
    assert!(outcome.escalation.is_some());
}

#[test]
fn test_budget_warning_at_threshold() {
    let job_id = Uuid::new_v4();
    let policy = ConstraintPolicy::new();
    let mut sup = Supervisor::new(job_id, "budget warning test", policy)
        .with_budget(BudgetKind::Tokens, 100);

    // Use 80 tokens — should trigger a warning at 80%.
    let event = StepEvent::Finished {
        step_id: Supervisor::new_step_id(),
        domain: Domain::new("meta"),
        description: "big step".into(),
        status: StepStatus::Success,
        actual_cost: vec![(BudgetKind::Tokens, 80)],
        signature: "sig1".into(),
    };
    let outcome = sup.handle(event).unwrap();
    assert!(outcome.anomalies.iter().any(|a| matches!(
        a,
        Anomaly::BudgetWarning { kind: BudgetKind::Tokens, .. }
    )));
}

#[test]
fn test_starting_event_denied_when_budget_insufficient() {
    let job_id = Uuid::new_v4();
    let policy = ConstraintPolicy::new();
    let mut sup = Supervisor::new(job_id, "budget deny test", policy)
        .with_budget(BudgetKind::Tokens, 100);

    // First, consume 90 tokens.
    let event = StepEvent::Finished {
        step_id: Supervisor::new_step_id(),
        domain: Domain::new("meta"),
        description: "step1".into(),
        status: StepStatus::Success,
        actual_cost: vec![(BudgetKind::Tokens, 90)],
        signature: "sig1".into(),
    };
    sup.handle(event).unwrap();

    // Now try to start a step that estimates 50 tokens — should be denied.
    let event = StepEvent::Starting {
        step_id: Supervisor::new_step_id(),
        domain: Domain::new("meta"),
        description: "step2".into(),
        actions: vec![],
        estimated_cost: vec![(BudgetKind::Tokens, 50)],
        signature: "sig2".into(),
    };
    let outcome = sup.handle(event).unwrap();
    assert!(!outcome.allowed);
    assert!(outcome.message.contains("budget"));
}

// =========================================================================
// Loop detection tests
// =========================================================================

#[test]
fn test_loop_detected() {
    let job_id = Uuid::new_v4();
    let policy = ConstraintPolicy::new();
    let mut sup = Supervisor::new(job_id, "loop test", policy)
        .with_budget(BudgetKind::Steps, 20);

    // Run the same signature 3 times.
    for _ in 0..3 {
        let event = make_finished_event("meta", StepStatus::Success, "repeated_sig");
        let outcome = sup.handle(event).unwrap();
        if outcome.halted {
            break;
        }
    }

    // The 3rd step should have detected the loop.
    let snapshot = sup.snapshot();
    assert!(
        snapshot
            .last_anomalies
            .iter()
            .any(|a| matches!(a, Anomaly::LoopDetected { signature, .. } if signature == "repeated_sig")),
        "expected loop detection anomaly, got: {:?}",
        snapshot.last_anomalies
    );
}

#[test]
fn test_no_loop_when_signatures_differ() {
    let job_id = Uuid::new_v4();
    let policy = ConstraintPolicy::new();
    let mut sup = Supervisor::new(job_id, "no loop test", policy)
        .with_budget(BudgetKind::Steps, 20);

    for i in 0..5 {
        let event = make_finished_event("meta", StepStatus::Success, &format!("unique_sig_{i}"));
        let outcome = sup.handle(event).unwrap();
        assert!(!outcome.halted);
    }

    let snapshot = sup.snapshot();
    assert!(
        !snapshot
            .last_anomalies
            .iter()
            .any(|a| matches!(a, Anomaly::LoopDetected { .. })),
        "should not detect a loop with unique signatures"
    );
}

// =========================================================================
// Blocker detection tests
// =========================================================================

#[test]
fn test_blocker_triggers_escalation() {
    let job_id = Uuid::new_v4();
    let policy = ConstraintPolicy::new();
    let mut sup = Supervisor::new(job_id, "blocker test", policy)
        .with_budget(BudgetKind::Steps, 10);

    let event = make_finished_event(
        "meta",
        StepStatus::Blocked {
            reason: "missing dependency: libfoo".into(),
        },
        "sig1",
    );
    let outcome = sup.handle(event).unwrap();

    assert!(outcome.escalation.is_some());
    let esc = outcome.escalation.unwrap();
    assert!(matches!(esc.level, escalation::EscalationLevel::Human));
    assert!(matches!(
        esc.reason,
        escalation::EscalationReason::Blocker { .. }
    ));
}

// =========================================================================
// Consecutive failure escalation tests
// =========================================================================

#[test]
fn test_consecutive_failures_escalate() {
    let job_id = Uuid::new_v4();
    let policy = ConstraintPolicy::new();
    let mut sup = Supervisor::new(job_id, "failure test", policy)
        .with_budget(BudgetKind::Steps, 20);
    sup.failure_escalation_threshold = 3;

    // Two failures — no escalation yet.
    for i in 0..2 {
        let event = make_finished_event("meta", StepStatus::Failed, &format!("fail_{i}"));
        let outcome = sup.handle(event).unwrap();
        assert!(outcome.escalation.is_none(), "should not escalate on failure {i}");
    }

    // Third failure — should escalate.
    let event = make_finished_event("meta", StepStatus::Failed, "fail_2");
    let outcome = sup.handle(event).unwrap();
    assert!(outcome.escalation.is_some());
    let esc = outcome.escalation.unwrap();
    assert!(matches!(
        esc.reason,
        escalation::EscalationReason::RepeatedFailures { count: 3 }
    ));
}

#[test]
fn test_success_resets_failure_count() {
    let job_id = Uuid::new_v4();
    let policy = ConstraintPolicy::new();
    let mut sup = Supervisor::new(job_id, "reset test", policy)
        .with_budget(BudgetKind::Steps, 20);
    sup.failure_escalation_threshold = 3;

    // Two failures.
    for i in 0..2 {
        let event = make_finished_event("meta", StepStatus::Failed, &format!("fail_{i}"));
        sup.handle(event).unwrap();
    }

    // One success — resets counter.
    let event = make_finished_event("meta", StepStatus::Success, "success");
    sup.handle(event).unwrap();

    // Two more failures — should NOT escalate (count is 2, threshold is 3).
    for i in 0..2 {
        let event = make_finished_event("meta", StepStatus::Failed, &format!("fail_again_{i}"));
        let outcome = sup.handle(event).unwrap();
        assert!(outcome.escalation.is_none(), "should not escalate after reset");
    }
}

// =========================================================================
// Halt behavior tests
// =========================================================================

#[test]
fn test_halted_job_rejects_events() {
    let job_id = Uuid::new_v4();
    let policy = ConstraintPolicy::new();
    let mut sup = Supervisor::new(job_id, "halt test", policy)
        .with_budget(BudgetKind::Steps, 1);

    // One step to exhaust budget.
    let event = make_finished_event("meta", StepStatus::Success, "sig1");
    sup.handle(event).unwrap();

    // Next step exceeds budget and halts.
    let event = make_finished_event("meta", StepStatus::Success, "sig2");
    sup.handle(event).unwrap();

    // Now the job is halted — further events should error.
    let event = make_finished_event("meta", StepStatus::Success, "sig3");
    let result = sup.handle(event);
    assert!(result.is_err());
    assert!(matches!(
        result.unwrap_err(),
        SupervisorError::JobHalted { .. }
    ));
}

#[test]
fn test_halt_on_anomaly_config() {
    let job_id = Uuid::new_v4();
    let policy = ConstraintPolicy::new();
    let mut sup = Supervisor::new(job_id, "halt on anomaly test", policy)
        .with_budget(BudgetKind::Steps, 20);
    sup.halt_on_anomaly = true;

    // Create a loop (3 identical signatures).
    let mut halted = false;
    for _ in 0..3 {
        let event = make_finished_event("meta", StepStatus::Success, "same_sig");
        let outcome = sup.handle(event).unwrap();
        if outcome.halted {
            halted = true;
            break;
        }
    }
    assert!(halted, "should halt when halt_on_anomaly is set and a loop is detected");
}

// =========================================================================
// Progress snapshot tests
// =========================================================================

#[test]
fn test_progress_snapshot() {
    let job_id = Uuid::new_v4();
    let policy = ConstraintPolicy::new();
    let mut sup = Supervisor::new(job_id, "snapshot test", policy)
        .with_budget(BudgetKind::Steps, 100);

    // 3 successes, 1 failure, 1 blocked.
    let events = vec![
        make_finished_event("meta", StepStatus::Success, "s1"),
        make_finished_event("meta", StepStatus::Success, "s2"),
        make_finished_event("meta", StepStatus::Success, "s3"),
        make_finished_event("meta", StepStatus::Failed, "s4"),
        make_finished_event("meta", StepStatus::Blocked { reason: "stuck".into() }, "s5"),
    ];

    for event in events {
        sup.handle(event).unwrap();
    }

    let snap = sup.snapshot();
    assert_eq!(snap.steps_total, 5);
    assert_eq!(snap.steps_success, 3);
    assert_eq!(snap.steps_failed, 1);
    assert_eq!(snap.steps_blocked, 1);
    assert_eq!(snap.consecutive_failures, 0); // blocked doesn't count as failure
    assert!(snap.halted.is_none());
}

// =========================================================================
// Multiple actions in a single step
// =========================================================================

#[test]
fn test_multiple_actions_all_must_pass() {
    let mut sup = make_supervisor();
    // One allowed, one denied — step should be denied.
    let event = make_starting_event(
        "fs",
        vec![
            Action::Read { target: "/etc/passwd".into() },       // allowed
            Action::Execute { target: "rm -rf /".into() },       // denied
        ],
        "sig1",
    );
    let outcome = sup.handle(event).unwrap();
    assert!(!outcome.allowed);
}

#[test]
fn test_multiple_actions_all_allowed() {
    let mut sup = make_supervisor();
    let event = make_starting_event(
        "fs",
        vec![
            Action::Read { target: "/etc/passwd".into() },
            Action::Write { target: "/tmp/out.txt".into() },
        ],
        "sig1",
    );
    let outcome = sup.handle(event).unwrap();
    assert!(outcome.allowed);
}

// =========================================================================
// Serialization tests
// =========================================================================

#[test]
fn test_job_state_serializes() {
    let job_id = Uuid::new_v4();
    let policy = ConstraintPolicy::new();
    let mut sup = Supervisor::new(job_id, "serde test", policy)
        .with_budget(BudgetKind::Steps, 10);

    let event = make_finished_event("meta", StepStatus::Success, "sig1");
    sup.handle(event).unwrap();

    let state_json = serde_json::to_string(&sup.state).unwrap();
    let deserialized: JobState = serde_json::from_str(&state_json).unwrap();
    assert_eq!(deserialized.job_id, job_id);
    assert_eq!(deserialized.history.len(), 1);
}

#[test]
fn test_constraint_policy_serializes() {
    let mut policy = ConstraintPolicy::new();
    policy.set_domain(Domain::new("fs"), ConstraintSet::permissive());

    let json = serde_json::to_string(&policy).unwrap();
    let deserialized: ConstraintPolicy = serde_json::from_str(&json).unwrap();
    assert!(deserialized.get(&Domain::new("fs")).check(&Action::Read { target: "/x".into() }));
}
