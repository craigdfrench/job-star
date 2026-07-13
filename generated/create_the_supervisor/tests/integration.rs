use jobstar_supervisor::{
    capabilities::{AccessMode, Capability, CapabilitySet, Domain},
    constraints::ActionContext,
    default_policy, Supervisor,
};

#[test]
fn full_authorization_flow() {
    let sup = Supervisor::new(default_policy());

    // Register a "system" domain with read access to /etc
    sup.register_domain(
        Domain::new("system"),
        CapabilitySet::from(vec![
            Capability::file("/etc/**", AccessMode::Read),
            Capability::file("/tmp/**", AccessMode::ReadWrite),
        ]),
    );

    // Register a goal that narrows to only /etc/hostname
    sup.register_goal(
        "check-hostname",
        &Domain::new("system"),
        CapabilitySet::from(vec![
            Capability::file("/etc/hostname", AccessMode::Read),
        ]),
    );

    // Allowed: read /etc/hostname
    let ctx_ok = ActionContext {
        goal_id: "check-hostname".into(),
        domain: Domain::new("system"),
        action: "read_file".into(),
        target: "/etc/hostname".into(),
        args: serde_json::Value::Null,
    };
    assert!(sup.evaluate(&ctx_ok).is_ok());

    // Denied: read /etc/passwd (not in goal caps)
    let ctx_denied = ActionContext {
        goal_id: "check-hostname".into(),
        domain: Domain::new("system"),
        action: "read_file".into(),
        target: "/etc/passwd".into(),
        args: serde_json::Value::Null,
    };
    assert!(sup.evaluate(&ctx_denied).is_err());

    // Denied: write /etc/hostname (domain only allows read on /etc)
    let ctx_write = ActionContext {
        goal_id: "check-hostname".into(),
        domain: Domain::new("system"),
        action: "write_file".into(),
        target: "/etc/hostname".into(),
        args: serde_json::Value::Null,
    };
    assert!(sup.evaluate(&ctx_write).is_err());
}

#[test]
fn loop_detection_triggers_escalation() {
    let mut policy = default_policy();
    policy.max_action_repetitions = 3;
    let sup = Supervisor::new(policy);

    sup.register_domain(
        Domain::new("test"),
        CapabilitySet::from(vec![Capability::file("/tmp/**", AccessMode::Read)]),
    );
    sup.register_goal(
        "g1",
        &Domain::new("test"),
        CapabilitySet::from(vec![Capability::file("/tmp/**", AccessMode::Read)]),
    );

    let ctx = ActionContext {
        goal_id: "g1".into(),
        domain: Domain::new("test"),
        action: "read_file".into(),
        target: "/tmp/foo".into(),
        args: serde_json::Value::Null,
    };

    // Repeat the same action 4 times
    for _ in 0..4 {
        let _ = sup.evaluate(&ctx);
    }

    let escalations = sup.drain_escalations();
    assert!(!escalations.is_empty(), "Should have escalated on loop");
}

#[test]
fn snapshot_reports_progress() {
    let sup = Supervisor::new(default_policy());

    sup.register_domain(
        Domain::new("test"),
        CapabilitySet::from(vec![Capability::file("/tmp/**", AccessMode::Read)]),
    );
    sup.register_goal(
        "g1",
        &Domain::new("test"),
        CapabilitySet::from(vec![Capability::file("/tmp/**", AccessMode::Read)]),
    );

    let ctx = ActionContext {
        goal_id: "g1".into(),
        domain: Domain::new("test"),
        action: "read_file".into(),
        target: "/tmp/foo".into(),
        args: serde_json::Value::Null,
    };

    sup.evaluate(&ctx).unwrap();
    sup.evaluate(&ctx).unwrap();

    let snap = sup.snapshot();
    assert_eq!(snap.total_actions, 2);
    assert_eq!(snap.authorized_actions, 2);
}
