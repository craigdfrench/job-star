[package]
name = "job-star-supervisor"
version = "0.1.0"
edition = "2021"
description = "Supervision core for Job-Star: constraint enforcement, progress monitoring, loop/blocker detection, escalation"

[dependencies]
serde = { version = "1", features = ["derive"] }
serde_json = "1"
uuid = { version = "1", features = ["v4", "serde"] }
chrono = { version = "0.4", features = ["serde"] }
thiserror = "1"
