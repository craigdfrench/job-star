use job_star::*;
use job_star::models::*;
use async_trait::async_trait;
use std::sync::Arc;

/// A simple test executor that just returns success
struct TestExecutor;

#[async_trait]
impl StepExecutor for TestExecutor {
    async fn execute(&self, _step: &Step, _domain: &Domain) -> Result<StepResult, String> {
        Ok(StepResult {
            success: true,
            output: "test output".to_string(),
            error: None,
            tokens_used: 100,
            duration_ms: 10,
        })
    }
}

/// An executor that always fails
struct FailingExecutor;

#[async_trait]
impl StepExecutor for FailingExecutor {
    async fn execute(&self, _step: &Step, _domain: &Domain) -> Result<StepResult, String> {
        Err("intentional failure".to_string())
    }
}

fn make_test_domain() -> Domain {
    Domain {
        id: "test-domain".to_string(),
        name: "Test Domain".to_string(),
        description: "Domain for testing".to_string(),
        permissions: PermissionSet {
            read_paths: vec!["/tmp/test/**".to_string()],
            write_paths: vec!["/tmp/test/**".to_string()],
            allowed_executables: vec!["echo".to_string(), "ls".to_string()],
            network_endpoints: vec![],
            can_spawn_processes: true,
            max_cpu_seconds: 30,
            max_memory_mb: 256,
        },
        allowed_delegations: vec![].into_iter().collect(),
        max_workers: 2,
    }
}

fn make_test_goal(domain: Domain) -> Goal {
    Goal {
        id: "test-goal".to_string(),
        title: "Test Goal".to_string(),
        description: "A test goal".to_string(),
        domain: domain.clone(),
        steps: vec![],
        status: GoalStatus::Pending,
        budget: GoalBudget {
            max_total_steps: 10,
            max_wall_time_seconds: 60,
            max_tokens: 10_000,
            max_file_writes: 5,
            max_process_spawns: 5,
        },
        acceptance_criteria: vec!["All steps complete".to_string()],
        involved_domains: vec!["test-domain".to_string()].into_iter().collect(),
    }
}

#[tokio::test]
async fn test_constraint_checking_allows_permitted_action() {
    let config = SupervisorConfig::default();
    let handler = Arc::new(InMemoryEscalationHandler::new());
    let executor = Arc::new(TestExecutor);
    let mut supervisor = Supervisor::new(config, handler, executor);

    let domain = make_test_domain();
    supervisor.register_domain(domain.clone());

    let goal = make_test_goal(domain);
    supervisor.submit_goal(goal).unwrap();

    let step = Step::new(
        "step-1".to_string(),
        "test-goal".to_string(),
        "Read a file".to_string(),
        StepAction::Read {
            path: "/tmp/test/file.txt".to_string(),
        },
    );
    supervisor.add_step(step).unwrap();

    let decision = supervisor.check_before_execute("test-goal", "step-1").await;
    assert!(matches!(decision, SupervisionDecision::Approve));
}

#[tokio::test]
async fn test_constraint_checking_denies_unpermitted_action() {
    let config = SupervisorConfig::default();
    let handler = Arc::new(InMemoryEscalationHandler::new());
    let executor = Arc::new(TestExecutor);
    let mut supervisor = Supervisor::new(config, handler, executor);

    let domain = make_test_domain();
    supervisor.register_domain(domain.clone());

    let goal = make_test_goal(domain);
    supervisor.submit_goal(goal).unwrap();

    let step = Step::new(
        "step-1".to_string(),
        "test-goal".to_string(),
        "Read restricted file".to_string(),
        StepAction::Read {
            path: "/etc/passwd".to_string(),
        },
    );
    let result = supervisor.add_step(step);
    assert!(result.is_err());
}

#[tokio::test]
async fn test_budget_exceeded_stops_goal() {
    let config = SupervisorConfig::default();
    let handler = Arc::new(InMemoryEscalationHandler::new());
    let executor = Arc::new(TestExecutor);
    let mut supervisor = Supervisor::new(config, handler, executor);

    let domain = make_test_domain();
    supervisor.register_domain(domain.clone());

    let mut goal = make_test_goal(domain);
    goal.budget.max_total_steps = 2;
    supervisor.submit_goal(goal).unwrap();

    // Execute two steps to hit the budget
    for i in 0..2 {
        let step = Step::new(
            format!("step-{}", i),
            "test-goal".to_string(),
            "Test step".to_string(),
            StepAction::Read {
                path: "/tmp/test/file.txt".to_string(),
            },
        );
        supervisor.add_step(step).unwrap();
        let _ = supervisor.execute_step("test-goal", &format!("step-{}", i)).await;
    }

    // Third step should be stopped by budget
    let step = Step::new(
        "step-3".to_string(),
        "test-goal".to_string(),
        "Over budget step".to_string(),
        StepAction::Read {
            path: "/tmp/test/file.txt".to_string(),
        },
    );
    supervisor.add_step(step).unwrap();

    let decision = supervisor.check_before_execute("test-goal", "step-3").await;
    assert!(matches!(decision, SupervisionDecision::StopGoal { .. }));
}

#[tokio::test]
async fn test_successful_step_execution() {
    let config = SupervisorConfig::default();
    let handler = Arc::new(InMemoryEscalationHandler::new());
    let executor = Arc::new(TestExecutor);
    let mut supervisor = Supervisor::new(config, handler, executor);

    let domain = make_test_domain();
    supervisor.register_domain(domain.clone());

    let goal = make_test_goal(domain);
    supervisor.submit_goal(goal).unwrap();

    let step = Step::new(
        "step-1".to_string(),
        "test-goal".to_string(),
        "Read a file".to_string(),
        StepAction::Read {
            path: "/tmp/test/file.txt".to_string(),
        },
    );
    supervisor.add_step(step).unwrap();

    let result = supervisor.execute_step("test-goal", "step-1").await;
    assert!(result.is_ok());
    assert!(result.unwrap().success);

    // Verify step is marked completed
    let step = supervisor.monitor.get_step("step-1").unwrap();
    assert_eq!(step.status, StepStatus::Completed);
}

#[tokio::test]
async fn test_failed_step_execution() {
    let config = SupervisorConfig::default();
    let handler = Arc::new(InMemoryEscalationHandler::new());
    let executor = Arc::new(FailingExecutor);
    let mut supervisor = Supervisor::new(config, handler, executor);

    let domain = make_test_domain();
    supervisor.register_domain(domain.clone());

    let goal = make_test_goal(domain);
    supervisor.submit_goal(goal).unwrap();

    let step = Step::new(
        "step-1".to_string(),
        "test-goal".to_string(),
        "Failing step".to_string(),
        StepAction::Execute {
            command: "echo".to_string(),
            args: vec!["hello".to_string()],
        },
    );
    supervisor.add_step(step).unwrap();

    let result = supervisor.execute_step("test-goal", "step-1").await;
    assert!(result.is_err());

    let step = supervisor.monitor.get_step("step-1").unwrap();
    assert_eq!(step.status, StepStatus::Failed);
}

#[tokio::test]
async fn test_system_snapshot() {
    let config = SupervisorConfig::default();
    let handler = Arc::new(InMemoryEscalationHandler::new());
    let executor = Arc::new(TestExecutor);
    let mut supervisor = Supervisor::new(config, handler, executor);

    let domain = make_test_domain();
    supervisor.register_domain(domain.clone());

    let goal = make_test_goal(domain);
    supervisor.submit_goal(goal).unwrap();

    let step = Step::new(
        "step-1".to_string(),
        "test-goal".to_string(),
        "Test step".to_string(),
        StepAction::Read {
            path: "/tmp/test/file.txt".to_string(),
        },
    );
    supervisor.add_step(step).unwrap();

    let _ = supervisor.execute_step("test-goal", "step-1").await;

    let snapshot = supervisor.snapshot();
    assert_eq!(snapshot.goals.len(), 1);
    assert_eq!(snapshot.total_steps, 1);
    assert_eq!(snapshot.completed_steps, 1);
}

#[tokio::test]
async fn test_escalation_flow() {
    let config = SupervisorConfig::default();
    let handler = Arc::new(InMemoryEscalationHandler::new());
    let executor = Arc::new(TestExecutor);
    let mut supervisor = Supervisor::new(config, handler.clone(), executor);

    let domain = make_test_domain();
    supervisor.register_domain(domain.clone());

    let goal = make_test_goal(domain);
    supervisor.submit_goal(goal).unwrap();

    // Create an escalation manually
    let escalation = Escalation::new(
        EscalationLevel::Blocked,
        "test-goal".to_string(),
        None,
        "Test block".to_string(),
        "How should I proceed?".to_string(),
    );
    let esc_id = escalation.id.clone();
    supervisor.handle_escalation(escalation).await.unwrap();

    // Verify goal is escalated
    let goal = supervisor.monitor.get_goal("test-goal").unwrap();
    assert_eq!(goal.status, GoalStatus::Escalated);

    // Verify pending escalation
    assert_eq!(supervisor.pending_escalation_count().await, 1);

    // Resolve it
    supervisor
        .resolve_escalation(&esc_id, "Proceed with alternative".to_string())
        .await
        .unwrap();

    // Verify goal is resumed
    let goal = supervisor.monitor.get_goal("test-goal").unwrap();
    assert_eq!(goal.status, GoalStatus::InProgress);
}

#[test]
fn test_permission_set_matching() {
    let perms = PermissionSet {
        read_paths: vec!["/tmp/test/**".to_string()],
        write_paths: vec!["/tmp/output".to_string()],
        allowed_executables: vec!["echo".to_string(), "python3".to_string()],
        network_endpoints: vec!["api.example.com:443".to_string()],
        can_spawn_processes: true,
        max_cpu_seconds: 30,
        max_memory_mb: 256,
    };

    // Read matching
    assert!(perms.can_read("/tmp/test/file.txt"));
    assert!(perms.can_read("/tmp/test/subdir/file.txt"));
    assert!(!perms.can_read("/etc/passwd"));

    // Write matching (exact path or prefix)
    assert!(perms.can_write("/tmp/output"));
    assert!(perms.can_write("/tmp/output/file.txt"));
    assert!(!perms.can_write("/tmp/test/file.txt"));

    // Execute matching
    assert!(perms.can_execute("echo"));
    assert!(perms.can_execute("/usr/bin/echo"));
    assert!(perms.can_execute("python3"));
    assert!(!perms.can_execute("rm"));

    // Network matching
    assert!(perms.can_reach("api.example.com:443"));
    assert!(!perms.can_reach("evil.com:443"));
}
