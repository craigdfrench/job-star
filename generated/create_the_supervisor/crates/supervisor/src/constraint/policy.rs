//! Constraint policy — the rules the engine enforces.

use crate::supervisor::constraint::action::{Action, Capability, ResourcePath};
use crate::supervisor::constraint::budget::{BudgetUnit, Cost};
use crate::supervisor::constraint::decision::DenialReason;
use crate::supervisor::constraint::domain::Domain;
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

/// The full set of constraints the supervisor enforces.
#[derive(Debug, Clone)]
pub struct ConstraintPolicy {
    /// Domains that are allowed at all.
    pub allowed_domains: HashSet<Domain>,
    /// Capabilities allowed per domain.
    pub allowed_capabilities: HashMap<Domain, HashSet<Capability>>,
    /// Allowed path prefixes per domain (for filesystem).
    pub allowed_paths: HashMap<Domain, Vec<PathBuf>>,
    /// Allowed URL patterns per domain (for network/api).
    pub allowed_url_patterns: HashMap<Domain, Vec<String>>,
    /// Capabilities that always require human escalation.
    pub escalation_capabilities: HashSet<(Domain, Capability)>,
    /// Maximum repetitions of the same action before escalation.
    pub max_repetitions: usize,
    /// Number of consecutive failed/stalled steps before blocker.
    pub stall_threshold: usize,
    /// Size of the sliding window for loop detection.
    pub loop_window_size: usize,
    /// Cost per action per unit (default costs).
    pub default_costs: HashMap<BudgetUnit, u64>,
}

impl ConstraintPolicy {
    pub fn is_domain_allowed(&self, domain: Domain) -> bool {
        self.allowed_domains.contains(&domain)
    }

    pub fn is_capability_allowed(&self, domain: Domain, capability: Capability) -> bool {
        self.allowed_capabilities
            .get(&domain)
            .map(|caps| caps.contains(&capability))
            .unwrap_or(false)
    }

    /// Check if the action's resource is within allowed scope.
    /// Returns `Some(denial)` if out of scope, `None` if OK.
    pub fn check_resource_scope(&self, action: &Action) -> Option<DenialReason> {
        match (&action.domain, &action.resource) {
            (Domain::Filesystem, ResourcePath::File(path)) => {
                let allowed = self.allowed_paths.get(&Domain::Filesystem);
                if let Some(prefixes) = allowed {
                    if prefixes.iter().any(|p| path.starts_with(p)) {
                        return None; // OK
                    }
                }
                Some(DenialReason::ResourceOutOfScope {
                    domain: action.domain,
                    resource: path.display().to_string(),
                    allowed_prefixes: allowed
                        .map(|p| p.iter().map(|x| x.display().to_string()).collect())
                        .unwrap_or_default(),
                })
            }
            (Domain::Network | Domain::Api, ResourcePath::Url(url)) => {
                let allowed = self.allowed_url_patterns.get(&action.domain);
                if let Some(patterns) = allowed {
                    if patterns.iter().any(|pat| url_matches_pattern(url, pat)) {
                        return None; // OK
                    }
                }
                Some(DenialReason::ResourceOutOfScope {
                    domain: action.domain,
                    resource: url.clone(),
                    allowed_prefixes: allowed.cloned().unwrap_or_default(),
                })
            }
            // For domains without path restrictions, allow.
            _ => None,
        }
    }

    /// Does this action require escalation regardless of other checks?
    pub fn requires_escalation(&self, action: &Action) -> bool {
        // Meta domain always requires escalation (modifying the system itself).
        if action.domain == Domain::Meta && action.capability != Capability::Read {
            return true;
        }
        // Explicit escalation capabilities.
        if self.escalation_capabilities.contains(&(action.domain, action.capability)) {
            return true;
        }
        // Delete operations always escalate.
        if action.capability == Capability::Delete {
            return true;
        }
        false
    }

    /// Estimate the cost of an action.
    pub fn estimate_cost(&self, action: &Action) -> Cost {
        // Every action costs at least 1 step.
        let steps = self
            .default_costs
            .get(&BudgetUnit::Steps)
            .copied()
            .unwrap_or(1);

        // If the action declares token usage, use that.
        if let Some(tokens) = action.estimated_tokens {
            // Return the higher-cost unit; for simplicity, we charge steps here
            // and let the executor charge tokens separately.
            let _ = tokens;
        }

        Cost::new(BudgetUnit::Steps, steps)
    }
}

/// Check if a URL matches a pattern (prefix match or wildcard).
fn url_matches_pattern(url: &str, pattern: &str) -> bool {
    if pattern == "*" {
        return true;
    }
    // Support simple prefix patterns like "https://api.example.com/*"
    if let Some(prefix) = pattern.strip_suffix("/*") {
        return url.starts_with(prefix);
    }
    url.starts_with(pattern)
}

/// Builder for [`ConstraintPolicy`].
pub struct PolicyBuilder {
    policy: ConstraintPolicy,
}

impl PolicyBuilder {
    pub fn new() -> Self {
        Self {
            policy: ConstraintPolicy {
                allowed_domains: HashSet::new(),
                allowed_capabilities: HashMap::new(),
                allowed_paths: HashMap::new(),
                allowed_url_patterns: HashMap::new(),
                escalation_capabilities: HashSet::new(),
                max_repetitions: 5,
                stall_threshold: 10,
                loop_window_size: 20,
                default_costs: {
                    let mut m = HashMap::new();
                    m.insert(BudgetUnit::Steps, 1);
                    m
                },
            },
        }
    }

    pub fn allow_domain(mut self, domain: Domain) -> Self {
        self.policy.allowed_domains.insert(domain);
        self
    }

    pub fn allow_capability(mut self, domain: Domain, cap: Capability) -> Self {
        self.policy
            .allowed_capabilities
            .entry(domain)
            .or_default()
            .insert(cap);
        self
    }

    pub fn allow_path(mut self, domain: Domain, path: PathBuf) -> Self {
        self.policy
            .allowed_paths
            .entry(domain)
            .or_default()
            .push(path);
        self
    }

    pub fn allow_url_pattern(mut self, domain: Domain, pattern: impl Into<String>) -> Self {
        self.policy
            .allowed_url_patterns
            .entry(domain)
            .or_default()
            .push(pattern.into());
        self
    }

    pub fn escalate_capability(mut self, domain: Domain, cap: Capability) -> Self {
        self.policy.escalation_capabilities.insert((domain, cap));
        self
    }

    pub fn max_repetitions(mut self, n: usize) -> Self {
        self.policy.max_repetitions = n;
        self
    }

    pub fn stall_threshold(mut self, n: usize) -> Self {
        self.policy.stall_threshold = n;
        self
    }

    pub fn loop_window_size(mut self, n: usize) -> Self {
        self.policy.loop_window_size = n;
        self
    }

    pub fn default_step_cost(mut self, cost: u64) -> Self {
        self.policy.default_costs.insert(BudgetUnit::Steps, cost);
        self
    }

    pub fn build(self) -> ConstraintPolicy {
        self.policy
    }
}

impl Default for PolicyBuilder {
    fn default() -> Self {
        Self::new()
    }
}
