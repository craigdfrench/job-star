//! Capability model: what a domain or goal is allowed to do.

use serde::{Deserialize, Serialize};
use std::collections::HashSet;
use std::path::{Path, PathBuf};

/// A logical domain of operation (e.g., "system", "network", "meta").
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Domain(String);

impl Domain {
    pub fn new(name: &str) -> Self {
        Self(name.to_string())
    }

    pub fn name(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Display for Domain {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// Access mode for a capability.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum AccessMode {
    Read,
    Write,
    Execute,
    /// Read + Write
    ReadWrite,
    /// Read + Write + Execute
    Full,
}

impl AccessMode {
    /// Check if this mode includes the given permission.
    pub fn includes(&self, other: AccessMode) -> bool {
        match (self, other) {
            (AccessMode::Full, _) => true,
            (AccessMode::ReadWrite, AccessMode::Read) => true,
            (AccessMode::ReadWrite, AccessMode::Write) => true,
            (AccessMode::ReadWrite, AccessMode::ReadWrite) => true,
            (a, b) if a == b => true,
            _ => false,
        }
    }
}

/// A single capability: permission to perform an action on a target.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Capability {
    /// Filesystem path access.
    File { path: PathPattern, mode: AccessMode },
    /// Network endpoint access.
    Network { host: String, port: Option<u16> },
    /// Command execution (shell or binary).
    Execute { command: StringPattern },
    /// Arbitrary key-value capability (for extensibility).
    Custom { key: String, value: String },
}

impl Capability {
    pub fn file(path: impl Into<String>, mode: AccessMode) -> Self {
        Capability::File {
            path: PathPattern::new(path),
            mode,
        }
    }

    pub fn network(host: impl Into<String>, port: Option<u16>) -> Self {
        Capability::Network {
            host: host.into(),
            port,
        }
    }

    pub fn execute(command: impl Into<String>) -> Self {
        Capability::Execute {
            command: StringPattern::new(command),
        }
    }

    /// Does this capability grant the requested access?
    pub fn grants(&self, requested: &Capability) -> bool {
        match (self, requested) {
            (
                Capability::File { path: allowed, mode: allowed_mode },
                Capability::File { path: req, mode: req_mode },
            ) => allowed.matches_path(req) && allowed_mode.includes(*req_mode),
            (
                Capability::Network { host: ah, port: ap },
                Capability::Network { host: rh, port: rp },
            ) => ah == rh && (ap.is_none() || ap == rp),
            (
                Capability::Execute { command: allowed },
                Capability::Execute { command: req },
            ) => allowed.matches(req),
            (Capability::Custom { key: ak, value: av }, Capability::Custom { key: rk, value: rv }) => {
                ak == rk && av == rv
            }
            _ => false,
        }
    }
}

/// A set of capabilities. Used for both domain-level and goal-level grants.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CapabilitySet {
    caps: Vec<Capability>,
}

impl CapabilitySet {
    pub fn from(caps: Vec<Capability>) -> Self {
        Self { caps }
    }

    pub fn empty() -> Self {
        Self::default()
    }

    /// Check if this set grants the requested capability.
    pub fn grants(&self, requested: &Capability) -> bool {
        self.caps.iter().any(|c| c.grants(requested))
    }

    /// Intersect two capability sets. The result grants only what *both*
    /// sets grant. This is used when combining domain + goal constraints.
    pub fn intersect(&self, other: &CapabilitySet) -> CapabilitySet {
        let result: Vec<Capability> = self
            .caps
            .iter()
            .filter(|c| other.caps.iter().any(|o| o.grants(c)))
            .cloned()
            .collect();
        CapabilitySet::from(result)
    }

    pub fn is_empty(&self) -> bool {
        self.caps.is_empty()
    }

    pub fn iter(&self) -> impl Iterator<Item = &Capability> {
        self.caps.iter()
    }
}

impl From<Vec<Capability>> for CapabilitySet {
    fn from(caps: Vec<Capability>) -> Self {
        Self::from(caps)
    }
}

/// A path pattern that supports prefix matching with `**` wildcards.
/// Example: `/etc/**` matches `/etc/hostname`, `/etc/systemd/foo.conf`
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct PathPattern(String);

impl PathPattern {
    pub fn new(s: impl Into<String>) -> Self {
        Self(s.into())
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// Check if this pattern matches a given path.
    pub fn matches_path(&self, path: &PathPattern) -> bool {
        self.matches_str(path.as_str())
    }

    fn matches_str(&self, target: &str) -> bool {
        let pattern = &self.0;

        // Normalize: remove trailing slashes for comparison
        let pattern = pattern.trim_end_matches('/');
        let target = target.trim_end_matches('/');

        if pattern == "**" || pattern == target {
            return true;
        }

        if pattern.ends_with("/**") {
            let prefix = &pattern[..pattern.len() - 3];
            return target == prefix || target.starts_with(&format!("{}/", prefix));
        }

        if pattern.ends_with("/*") {
            let prefix = &pattern[..pattern.len() - 2];
            if let Some(rest) = target.strip_prefix(&format!("{}/", prefix)) {
                // Only match direct children (no further slashes)
                return !rest.contains('/');
            }
            return false;
        }

        false
    }
}

/// A string pattern supporting prefix matching with `*`.
#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct StringPattern(String);

impl StringPattern {
    pub fn new(s: impl Into<String>) -> Self {
        Self(s.into())
    }

    pub fn as_str(&self) -> &str {
        &self.0
    }

    pub fn matches(&self, target: &StringPattern) -> bool {
        let pattern = &self.0;
        let target = target.as_str();

        if pattern == "*" {
            return true;
        }

        if pattern.ends_with('*') {
            let prefix = &pattern[..pattern.len() - 1];
            return target.starts_with(prefix);
        }

        pattern == target
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_path_pattern_glob() {
        let p = PathPattern::new("/etc/**");
        assert!(p.matches_path(&PathPattern::new("/etc/hostname")));
        assert!(p.matches_path(&PathPattern::new("/etc/systemd/foo.conf")));
        assert!(!p.matches_path(&PathPattern::new("/var/log")));

        let p = PathPattern::new("/tmp/*");
        assert!(p.matches_path(&PathPattern::new("/tmp/foo")));
        assert!(!p.matches_path(&PathPattern::new("/tmp/foo/bar")));
    }

    #[test]
    fn test_access_mode_includes() {
        assert!(AccessMode::ReadWrite.includes(AccessMode::Read));
        assert!(AccessMode::ReadWrite.includes(AccessMode::Write));
        assert!(!AccessMode::Read.includes(AccessMode::Write));
        assert!(AccessMode::Full.includes(AccessMode::Execute));
    }

    #[test]
    fn test_capability_intersect() {
        let a = CapabilitySet::from(vec![
            Capability::file("/etc/**", AccessMode::ReadWrite),
            Capability::file("/var/log/**", AccessMode::Read),
        ]);
        let b = CapabilitySet::from(vec![
            Capability::file("/etc/**", AccessMode::Read),
        ]);

        let result = a.intersect(&b);

        // Should grant read on /etc/** but not write
        assert!(result.grants(&Capability::file("/etc/hostname", AccessMode::Read)));
        assert!(!result.grants(&Capability::file("/etc/hostname", AccessMode::Write)));
        // /var/log was not in b, so not in intersection
        assert!(!result.grants(&Capability::file("/var/log/syslog", AccessMode::Read)));
    }
}
