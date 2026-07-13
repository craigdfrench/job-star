"""Classification rules: keyword patterns and signal definitions.

Each rule maps signal patterns to a classification. The engine scores
all matching signals and picks the highest-confidence result.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from .models import Domain, RequestType, Urgency


class SignalMatcher(Protocol):
    """Protocol for signal matchers."""
    def matches(self, text: str) -> bool:
        ...


@dataclass
class KeywordMatcher:
    """Matches if any keyword appears in the text."""
    keywords: list[str]

    def matches(self, text: str) -> bool:
        return any(kw in text for kw in self.keywords)


@dataclass
class Signal:
    """A single classification signal."""
    name: str
    matcher: SignalMatcher
    domain: Domain | None = None
    urgency: Urgency | None = None
    request_type: RequestType | None = None
    weight: float = 1.0  # How strongly this signal counts


# ---------------------------------------------------------------------------
# Domain signals
# ---------------------------------------------------------------------------

DOMAIN_SIGNALS: list[Signal] = [
    Signal(
        name="domain:meta",
        matcher=KeywordMatcher([
            "job-star", "jobstar", "triage", "intake", "bootstrap",
            "process", "workflow", "planning", "roadmap", "goal registry",
            "supervised", "operator",
        ]),
        domain=Domain.META,
        weight=1.5,
    ),
    Signal(
        name="domain:code",
        matcher=KeywordMatcher([
            "function", "class", "method", "bug", "crash", "error",
            "stack trace", "exception", "unit test", "import", "module",
            "api", "endpoint", "logic", "algorithm", "refactor",
            "implementation", "compile", "syntax",
        ]),
        domain=Domain.CODE,
        weight=1.2,
    ),
    Signal(
        name="domain:docs",
        matcher=KeywordMatcher([
            "documentation", "readme", "guide", "tutorial", "docstring",
            "wiki", "manual", "help text", "inline docs", "api docs",
        ]),
        domain=Domain.DOCS,
        weight=1.3,
    ),
    Signal(
        name="domain:devops",
        matcher=KeywordMatcher([
            "deploy", "deployment", "ci/cd", "pipeline", "docker",
            "kubernetes", "k8s", "terraform", "ansible", "infrastructure",
            "server", "nginx", "cloud", "aws", "gcp", "azure",
            "build", "release", "staging", "production environment",
        ]),
        domain=Domain.DEVOPS,
        weight=1.3,
    ),
    Signal(
        name="domain:research",
        matcher=KeywordMatcher([
            "investigate", "research", "explore", "prototype", "spike",
            "evaluate", "feasibility", "proof of concept", "poc",
            "benchmark", "compare options",
        ]),
        domain=Domain.RESEARCH,
        weight=1.2,
    ),
    Signal(
        name="domain:security",
        matcher=KeywordMatcher([
            "security", "vulnerability", "cve", "exploit", "xss",
            "injection", "auth", "authentication", "authorization",
            "encryption", "ssl", "tls", "pen test", "hardening",
            "secret", "credential leak",
        ]),
        domain=Domain.SECURITY,
        weight=1.8,  # Security signals are strong
    ),
    Signal(
        name="domain:data",
        matcher=KeywordMatcher([
            "database", "schema", "migration", "sql", "query",
            "model", "orm", "seed", "fixture", "data pipeline",
            "etl", "table", "index", "record",
        ]),
        domain=Domain.DATA,
        weight=1.2,
    ),
    Signal(
        name="domain:ui",
        matcher=KeywordMatcher([
            "ui", "ux", "frontend", "css", "html", "react", "vue",
            "component", "layout", "responsive", "accessibility",
            "a11y", "design system", "styling", "button", "form",
        ]),
        domain=Domain.UI,
        weight=1.2,
    ),
]

# ---------------------------------------------------------------------------
# Urgency signals
# ---------------------------------------------------------------------------

URGENCY_SIGNALS: list[Signal] = [
    Signal(
        name="urgency:now:blocker",
        matcher=KeywordMatcher([
            "production down", "outage", "blocker", "blocking",
            "critical", "emergency", "down", "cannot proceed",
            "stopped", "halted", "broken build", "red alert",
        ]),
        urgency=Urgency.NOW,
        weight=2.0,
    ),
    Signal(
        name="urgency:now:security",
        matcher=KeywordMatcher([
            "security vulnerability", "exploit", "data breach",
            "credential leak", "active attack", "urgent security",
        ]),
        urgency=Urgency.NOW,
        weight=2.0,
    ),
    Signal(
        name="urgency:soon",
        matcher=KeywordMatcher([
            "soon", "this week", "next sprint", "should do",
            "needed", "important", "priority", "before release",
            "this cycle", "current iteration",
        ]),
        urgency=Urgency.SOON,
        weight=1.0,
    ),
    Signal(
        name="urgency:later",
        matcher=KeywordMatcher([
            "backlog", "nice to have", "nice-to-have", "eventually",
            "someday", "future", "when time permits", "low priority",
            "wishlist", "idea", "consider", "maybe",
        ]),
        urgency=Urgency.LATER,
        weight=0.8,
    ),
]

# ---------------------------------------------------------------------------
# Type signals
# ---------------------------------------------------------------------------

TYPE_SIGNALS: list[Signal] = [
    Signal(
        name="type:bug",
        matcher=KeywordMatcher([
            "bug", "crash", "error", "broken", "fails", "failure",
            "incorrect", "wrong", "unexpected", "regression",
            "doesn't work", "not working", "issue",
        ]),
        request_type=RequestType.BUG,
        weight=1.3,
    ),
    Signal(
        name="type:feature",
        matcher=KeywordMatcher([
            "feature", "add support", "implement", "new capability",
            "enable", "allow users to", "support for", "add ability",
            "enhancement", "new function",
        ]),
        request_type=RequestType.FEATURE,
        weight=1.2,
    ),
    Signal(
        name="type:refactor",
        matcher=KeywordMatcher([
            "refactor", "restructure", "clean up", "cleanup",
            "simplify", "deduplicate", "consolidate", "reorganize",
            "technical debt", "code smell", "improve design",
        ]),
        request_type=RequestType.REFACTOR,
        weight=1.2,
    ),
    Signal(
        name="type:question",
        matcher=KeywordMatcher([
            "how do i", "how to", "what is", "why does", "question",
            "help understanding", "clarify", "explain", "confused",
            "is it possible", "can we",
        ]),
        request_type=RequestType.QUESTION,
        weight=1.0,
    ),
    Signal(
        name="type:docs",
        matcher=KeywordMatcher([
            "document", "documentation", "readme", "write docs",
            "update docs", "add docstring", "guide", "tutorial",
        ]),
        request_type=RequestType.DOCS,
        weight=1.3,
    ),
    Signal(
        name="type:chore",
        matcher=KeywordMatcher([
            "chore", "dependency", "upgrade", "update version",
            "bump", "maintenance", "cleanup task", "housekeeping",
            "license", "formatting", "linting",
        ]),
        request_type=RequestType.CHORE,
        weight=0.9,
    ),
    Signal(
        name="type:security",
        matcher=KeywordMatcher([
            "security", "vulnerability", "hardening", "patch",
            "cve", "sanitize", "validate input", "secure",
        ]),
        request_type=RequestType.SECURITY,
        weight=1.8,
    ),
]

ALL_SIGNALS: list[Signal] = DOMAIN_SIGNALS + URGENCY_SIGNALS + TYPE_SIGNALS


// --- DUPLICATE BLOCK ---

class MyRegistry:
    def search(self, text, domain=None, limit=10) -> list[RegistryGoal]:
        # Query your database
        ...
    def get(self, goal_id) -> RegistryGoal | None:
        ...


// --- DUPLICATE BLOCK ---

"""Classification rules: keyword patterns and signal definitions.

Each rule maps signal patterns to a classification. The engine scores
all matching signals and picks the highest-confidence result.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Protocol

from .models import Domain, RequestType, Urgency


class SignalMatcher(Protocol):
    """Protocol for signal matchers."""
    def matches(self, text: str) -> bool:
        ...


@dataclass
class KeywordMatcher:
    """Matches if any keyword appears in the text."""
    keywords: list[str]

    def matches(self, text: str) -> bool:
        return any(kw in text for kw in self.keywords)


@dataclass
class Signal:
    """A single classification signal."""
    name: str
    matcher: SignalMatcher
    domain: Domain | None = None
    urgency: Urgency | None = None
    request_type: RequestType | None = None
    weight: float = 1.0  # How strongly this signal counts


# ---------------------------------------------------------------------------
# Domain signals
# ---------------------------------------------------------------------------

DOMAIN_SIGNALS: list[Signal] = [
    Signal(
        name="domain:meta",
        matcher=KeywordMatcher([
            "job-star", "jobstar", "triage", "intake", "bootstrap",
            "process", "workflow", "planning", "roadmap", "goal registry",
            "supervised", "operator",
        ]),
        domain=Domain.META,
        weight=1.5,
    ),
    Signal(
        name="domain:code",
        matcher=KeywordMatcher([
            "function", "class", "method", "bug", "crash", "error",
            "stack trace", "exception", "unit test", "import", "module",
            "api", "endpoint", "logic", "algorithm", "refactor",
            "implementation", "compile", "syntax",
        ]),
        domain=Domain.CODE,
        weight=1.2,
    ),
    Signal(
        name="domain:docs",
        matcher=KeywordMatcher([
            "documentation", "readme", "guide", "tutorial", "docstring",
            "wiki", "manual", "help text", "inline docs", "api docs",
        ]),
        domain=Domain.DOCS,
        weight=1.3,
    ),
    Signal(
        name="domain:devops",
        matcher=KeywordMatcher([
            "deploy", "deployment", "ci/cd", "pipeline", "docker",
            "kubernetes", "k8s", "terraform", "ansible", "infrastructure",
            "server", "nginx", "cloud", "aws", "gcp", "azure",
            "build", "release", "staging", "production environment",
        ]),
        domain=Domain.DEVOPS,
        weight=1.3,
    ),
    Signal(
        name="domain:research",
        matcher=KeywordMatcher([
            "investigate", "research", "explore", "prototype", "spike",
            "evaluate", "feasibility", "proof of concept", "poc",
            "benchmark", "compare options",
        ]),
        domain=Domain.RESEARCH,
        weight=1.2,
    ),
    Signal(
        name="domain:security",
        matcher=KeywordMatcher([
            "security", "vulnerability", "cve", "exploit", "xss",
            "injection", "auth", "authentication", "authorization",
            "encryption", "ssl", "tls", "pen test", "hardening",
            "secret", "credential leak",
        ]),
        domain=Domain.SECURITY,
        weight=1.8,  # Security signals are strong
    ),
    Signal(
        name="domain:data",
        matcher=KeywordMatcher([
            "database", "schema", "migration", "sql", "query",
            "model", "orm", "seed", "fixture", "data pipeline",
            "etl", "table", "index", "record",
        ]),
        domain=Domain.DATA,
        weight=1.2,
    ),
    Signal(
        name="domain:ui",
        matcher=KeywordMatcher([
            "ui", "ux", "frontend", "css", "html", "react", "vue",
            "component", "layout", "responsive", "accessibility",
            "a11y", "design system", "styling", "button", "form",
        ]),
        domain=Domain.UI,
        weight=1.2,
    ),
]

# ---------------------------------------------------------------------------
# Urgency signals
# ---------------------------------------------------------------------------

URGENCY_SIGNALS: list[Signal] = [
    Signal(
        name="urgency:now:blocker",
        matcher=KeywordMatcher([
            "production down", "outage", "blocker", "blocking",
            "critical", "emergency", "down", "cannot proceed",
            "stopped", "halted", "broken build", "red alert",
        ]),
        urgency=Urgency.NOW,
        weight=2.0,
    ),
    Signal(
        name="urgency:now:security",
        matcher=KeywordMatcher([
            "security vulnerability", "exploit", "data breach",
            "credential leak", "active attack", "urgent security",
        ]),
        urgency=Urgency.NOW,
        weight=2.0,
    ),
    Signal(
        name="urgency:soon",
        matcher=KeywordMatcher([
            "soon", "this week", "next sprint", "should do",
            "needed", "important", "priority", "before release",
            "this cycle", "current iteration",
        ]),
        urgency=Urgency.SOON,
        weight=1.0,
    ),
    Signal(
        name="urgency:later",
        matcher=KeywordMatcher([
            "backlog", "nice to have", "nice-to-have", "eventually",
            "someday", "future", "when time permits", "low priority",
            "wishlist", "idea", "consider", "maybe",
        ]),
        urgency=Urgency.LATER,
        weight=0.8,
    ),
]

# ---------------------------------------------------------------------------
# Type signals
# ---------------------------------------------------------------------------

TYPE_SIGNALS: list[Signal] = [
    Signal(
        name="type:bug",
        matcher=KeywordMatcher([
            "bug", "crash", "error", "broken", "fails", "failure",
            "incorrect", "wrong", "unexpected", "regression",
            "doesn't work", "not working", "issue",
        ]),
        request_type=RequestType.BUG,
        weight=1.3,
    ),
    Signal(
        name="type:feature",
        matcher=KeywordMatcher([
            "feature", "add support", "implement", "new capability",
            "enable", "allow users to", "support for", "add ability",
            "enhancement", "new function",
        ]),
        request_type=RequestType.FEATURE,
        weight=1.2,
    ),
    Signal(
        name="type:refactor",
        matcher=KeywordMatcher([
            "refactor", "restructure", "clean up", "cleanup",
            "simplify", "deduplicate", "consolidate", "reorganize",
            "technical debt", "code smell", "improve design",
        ]),
        request_type=RequestType.REFACTOR,
        weight=1.2,
    ),
    Signal(
        name="type:question",
        matcher=KeywordMatcher([
            "how do i", "how to", "what is", "why does", "question",
            "help understanding", "clarify", "explain", "confused",
            "is it possible", "can we",
        ]),
        request_type=RequestType.QUESTION,
        weight=1.0,
    ),
    Signal(
        name="type:docs",
        matcher=KeywordMatcher([
            "document", "documentation", "readme", "write docs",
            "update docs", "add docstring", "guide", "tutorial",
        ]),
        request_type=RequestType.DOCS,
        weight=1.3,
    ),
    Signal(
        name="type:chore",
        matcher=KeywordMatcher([
            "chore", "dependency", "upgrade", "update version",
            "bump", "maintenance", "cleanup task", "housekeeping",
            "license", "formatting", "linting",
        ]),
        request_type=RequestType.CHORE,
        weight=0.9,
    ),
    Signal(
        name="type:security",
        matcher=KeywordMatcher([
            "security", "vulnerability", "hardening", "patch",
            "cve", "sanitize", "validate input", "secure",
        ]),
        request_type=RequestType.SECURITY,
        weight=1.8,
    ),
]

ALL_SIGNALS: list[Signal] = DOMAIN_SIGNALS + URGENCY_SIGNALS + TYPE_SIGNALS


// --- DUPLICATE BLOCK ---

class MyRegistry:
    def search(self, text, domain=None, limit=10) -> list[RegistryGoal]:
        # Query your database
        ...
    def get(self, goal_id) -> RegistryGoal | None:
        ...
