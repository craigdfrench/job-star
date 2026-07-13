"""Configuration loader for the triage engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import re

import yaml


@dataclass
class DomainRule:
    keywords: dict[str, float]  # keyword -> weight
    weight: float = 1.0


@dataclass
class UrgencyRule:
    keywords: dict[str, float]
    regex: list[tuple[re.Pattern, float]] = field(default_factory=list)
    weight: float = 1.0


@dataclass
class TypeRule:
    keywords: dict[str, float]
    regex: list[tuple[re.Pattern, float]] = field(default_factory=list)
    weight: float = 1.0


@dataclass
class DuplicateConfig:
    similarity_threshold: float = 0.6
    high_confidence_threshold: float = 0.8
    min_title_length: int = 5
    stopwords: set[str] = field(default_factory=set)


@dataclass
class TaggingConfig:
    max_tags: int = 5
    min_keyword_length: int = 4
    tag_map: dict[str, str] = field(default_factory=dict)


@dataclass
class TriageConfig:
    domains: dict[str, DomainRule]
    urgency: dict[str, UrgencyRule]
    types: dict[str, TypeRule]
    duplicate: DuplicateConfig
    tagging: TaggingConfig

    @classmethod
    def from_yaml(cls, path: str | Path) -> "TriageConfig":
        path = Path(path)
        with path.open("r") as f:
            raw: dict[str, Any] = yaml.safe_load(f)
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TriageConfig":
        domains: dict[str, DomainRule] = {}
        for name, rule in (raw.get("domains") or {}).items():
            kw_list = rule.get("keywords", [])
            weight = rule.get("weight", 1.0)
            # If keywords is a list, give each weight 1.0; if dict, use values
            if isinstance(kw_list, list):
                keywords = {kw.lower(): 1.0 for kw in kw_list}
            elif isinstance(kw_list, dict):
                keywords = {k.lower(): float(v) for k, v in kw_list.items()}
            else:
                keywords = {}
            domains[name] = DomainRule(keywords=keywords, weight=weight)

        def _parse_scored_rule(rule: dict) -> tuple[dict, list, float]:
            kw_list = rule.get("keywords", [])
            if isinstance(kw_list, list):
                keywords = {kw.lower(): 1.0 for kw in kw_list}
            elif isinstance(kw_list, dict):
                keywords = {k.lower(): float(v) for k, v in kw_list.items()}
            else:
                keywords = {}
            regexes = []
            for rx in rule.get("regex", []) or []:
                pattern = rx["pattern"]
                w = float(rx.get("weight", 1.0))
                regexes.append((re.compile(pattern, re.IGNORECASE), w))
            weight = float(rule.get("weight", 1.0))
            return keywords, regexes, weight

        urgency: dict[str, UrgencyRule] = {}
        for name, rule in (raw.get("urgency") or {}).items():
            kw, rx, w = _parse_scored_rule(rule)
            urgency[name] = UrgencyRule(keywords=kw, regex=rx, weight=w)

        types: dict[str, TypeRule] = {}
        for name, rule in (raw.get("types") or {}).items():
            kw, rx, w = _parse_scored_rule(rule)
            types[name] = TypeRule(keywords=kw, regex=rx, weight=w)

        dup_raw = raw.get("duplicate", {}) or {}
        duplicate = DuplicateConfig(
            similarity_threshold=float(
                dup_raw.get("similarity_threshold", 0.6)
            ),
            high_confidence_threshold=float(
                dup_raw.get("high_confidence_threshold", 0.8)
            ),
            min_title_length=int(dup_raw.get("min_title_length", 5)),
            stopwords=set(w.lower() for w in dup_raw.get("stopwords", [])),
        )

        tag_raw = raw.get("tagging", {}) or {}
        tagging = TaggingConfig(
            max_tags=int(tag_raw.get("max_tags", 5)),
            min_keyword_length=int(tag_raw.get("min_keyword_length", 4)),
            tag_map={k.lower(): v for k, v in
                     (tag_raw.get("tag_map", {}) or {}).items()},
        )

        return cls(
            domains=domains,
            urgency=urgency,
            types=types,
            duplicate=duplicate,
            tagging=tagging,
        )
