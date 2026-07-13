"""Data models for parsed intake requests.

IntakeRequest represents the output of the intake parser — the structured
signals extracted from a raw job/intake description that downstream agents
(including the context gatherer) can consume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class IntakeRequest:
    """A parsed intake request with signals for context gathering.

    Attributes:
        title: Short human-readable title of the intake/job.
        description: Longer free-text description of the request.
        keywords: Salient terms extracted from title/description
            (e.g. "auth", "login", "rate limit"). Used for filename and
            content matching.
        mentioned_paths: File or directory paths explicitly referenced in
            the intake text (e.g. "src/auth/login.py").
        error_signatures: Strings that look like error messages or stack
            traces (e.g. "AttributeError: 'NoneType' object has no attribute 'foo'").
        component_hints: High-level component/module names inferred from
            the intake (e.g. "auth", "billing", "api").
        tags: Free-form tags assigned during intake parsing.
        priority: Optional priority label (e.g. "soon", "urgent").
    """

    title: str = ""
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    mentioned_paths: list[str] = field(default_factory=list)
    error_signatures: list[str] = field(default_factory=list)
    component_hints: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    priority: Optional[str] = None

    def all_search_terms(self) -> list[str]:
        """Return a deduplicated list of all terms useful for matching."""
        seen: set[str] = set()
        terms: list[str] = []
        for term in (
            *self.keywords,
            *self.component_hints,
            *self.tags,
        ):
            key = term.strip().lower()
            if key and key not in seen:
                seen.add(key)
                terms.append(key)
        return terms
