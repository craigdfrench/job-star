"""
Structured logging and observability for the Job-Star router.

Captures routing decisions in a structured, queryable format so they can be
replayed, audited, and mined for routing-rule improvements.
"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Context var so nested calls can share a correlation id.
correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")

# Default sink location for decision logs (JSON Lines).
DEFAULT_DECISION_LOG_PATH = Path("logs/router_decisions.jsonl")


@dataclass
class ModelCandidate:
    """A model that was considered during routing."""
    model: str
    provider: str
    complexity_score: float
    urgency_score: float
    cost_per_1k_tokens: float
    availability: float          # 0..1 confidence the model is reachable
    eligible: bool               # passed all hard filters
    disqualify_reasons: list[str] = field(default_factory=list)
    final_score: float = 0.0     # weighted composite used for ranking


@dataclass
class RoutingDecision:
    """A complete record of one routing event."""
    correlation_id: str
    timestamp: str
    task_id: str
    task_description: str
    domain: str
    urgency: str                 # "now" | "soon" | "whenever"
    complexity: str              # "trivial" | "simple" | "moderate" | "complex" | "unknown"
    cost_budget_usd: float
    input_request: dict[str, Any]
    models_considered: list[dict[str, Any]]
    selected_model: Optional[str]
    selected_provider: Optional[str]
    rationale: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: float
    routing_time_ms: float
    status: str                  # "success" | "no_model" | "error"
    error: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DecisionLogger:
    """
    Emits routing decisions to both the standard logging pipeline and a
    dedicated JSONL file for offline analysis.
    """

    def __init__(
        self,
        logger_name: str = "job_star.router",
        decision_log_path: Optional[Path] = None,
        level: int = logging.INFO,
    ) -> None:
        self.logger = logging.getLogger(logger_name)
        self.logger.setLevel(level)
        self._configure_handlers()
        self.decision_log_path = decision_log_path or DEFAULT_DECISION_LOG_PATH
        self.decision_log_path.parent.mkdir(parents=True, exist_ok=True)

    def _configure_handlers(self) -> None:
        if self.logger.handlers:
            return  # already configured
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
            )
        )
        self.logger.addHandler(handler)
        self.logger.propagate = False

    @staticmethod
    def new_correlation_id() -> str:
        return uuid.uuid4().hex

    def log_decision(self, decision: RoutingDecision) -> None:
        """Persist a routing decision to JSONL and emit a summary log line."""
        record = decision.to_dict()

        # Append-only JSONL sink for analysis pipelines.
        try:
            with self.decision_log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, default=str) + "\n")
        except OSError as exc:
            self.logger.warning("Failed to write decision log: %s", exc)

        # Human-readable summary on stdout.
        self.logger.info(
            "routing_decision id=%s task=%s domain=%s urgency=%s complexity=%s "
            "selected=%s status=%s cost~$%.6f time=%.2fms rationale=%s",
            decision.correlation_id,
            decision.task_id,
            decision.domain,
            decision.urgency,
            decision.complexity,
            decision.selected_model,
            decision.status,
            decision.estimated_cost_usd,
            decision.routing_time_ms,
            decision.rationale,
        )

    def log_error(self, message: str, **context: Any) -> None:
        self.logger.error("%s | context=%s", message, json.dumps(context, default=str))


# Module-level singleton for convenience.
_default_logger: Optional[DecisionLogger] = None


def get_decision_logger() -> DecisionLogger:
    global _default_logger
    if _default_logger is None:
        _default_logger = DecisionLogger()
    return _default_logger


def set_decision_logger(logger: DecisionLogger) -> None:
    global _default_logger
    _default_logger = logger


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def timer_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000.0, 3)
