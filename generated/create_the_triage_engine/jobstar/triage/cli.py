"""
CLI interface for the Job-Star triage engine.
Allows triaging requests from the command line without running the API server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from uuid import uuid4

from .engine import TriageEngine
from .models import (
    Domain,
    GoalRegistryEntry,
    IntakeRequest,
    RequestType,
    Urgency,
)
from .registry import DuplicateChecker, InMemoryRegistry


def _parse_enum(enum_cls, value: str | None):
    """Parse a string into an enum value, or None."""
    if value is None:
        return None
    try:
        return enum_cls(value.lower())
    except ValueError:
        print(f"Warning: '{value}' is not a valid {enum_cls.__name__}, ignoring.", file=sys.stderr)
        return None


async def _run_triage(args: argparse.Namespace) -> int:
    """Execute triage based on CLI args."""
    # Build the request
    request = IntakeRequest(
        id=uuid4(),
        title=args.title,
        description=args.description or "",
        tags=args.tags.split(",") if args.tags else [],
        source=args.source or "cli",
        submitter=args.submitter,
        hint_domain=_parse_enum(Domain, args.hint_domain),
        hint_urgency=_parse_enum(Urgency, args.hint_urgency),
        hint_type=_parse_enum(RequestType, args.hint_type),
    )

    # Set up backend
    registry = InMemoryRegistry()

    # Optionally seed with existing goals from a file
    if args.registry_file:
        with open(args.registry_file) as f:
            data = json.load(f)
        for g in data.get("goals", []):
            entry = GoalRegistryEntry(**g)
            registry.goals[entry.id] = entry

    checker = DuplicateChecker(backend=registry)
    engine = TriageEngine(duplicate_checker=checker)

    result = await engine.triage(request)

    # Output
    output = result.model_dump(mode="json", indent=2)
    print(output)
    return 0


async def _run_classify(args: argparse.Namespace) -> int:
    """Execute classification only (no duplicate check)."""
    from .classifier import classify

    request = IntakeRequest(
        id=uuid4(),
        title=args.title,
        description=args.description or "",
        tags=args.tags.split(",") if args.tags else [],
        hint_domain=_parse_enum(Domain, args.hint_domain),
        hint_urgency=_parse_enum(Urgency, args.hint_urgency),
        hint_type=_parse_enum(RequestType, args.hint_type),
    )

    classification = classify(request)
    print(classification.model_dump_json(indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobstar-triage",
        description="Job-Star Triage Engine — classify and deduplicate intake requests.",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- triage ---
    triage_parser = subparsers.add_parser("triage", help="Full triage: classify + duplicate check")
    triage_parser.add_argument("--title", required=True, help="Request title")
    triage_parser.add_argument("--description", "-d", default="", help="Request description")
    triage_parser.add_argument("--tags", default="", help="Comma-separated tags")
    triage_parser.add_argument("--source", default="cli", help="Request source")
    triage_parser.add_argument("--submitter", default=None, help="Who submitted this")
    triage_parser.add_argument("--hint-domain", default=None, help="Domain hint")
    triage_parser.add_argument("--hint-urgency", default=None, help="Urgency hint")
    triage_parser.add_argument("--hint-type", default=None, help="Type hint")
    triage_parser.add_argument(
        "--registry-file", default=None,
        help="JSON file with existing goals for duplicate checking",
    )
    triage_parser.set_defaults(func=_run_triage)

    # --- classify ---
    classify_parser = subparsers.add_parser("classify", help="Classify only (no duplicate check)")
    classify_parser.add_argument("--title", required=True, help="Request title")
    classify_parser.add_argument("--description", "-d", default="", help="Request description")
    classify_parser.add_argument("--tags", default="", help="Comma-separated tags")
    classify_parser.add_argument("--hint-domain", default=None, help="Domain hint")
    classify_parser.add_argument("--hint-urgency", default=None, help="Urgency hint")
    classify_parser.add_argument("--hint-type", default=None, help="Type hint")
    classify_parser.set_defaults(func=_run_classify)

    # --- serve ---
    serve_parser = subparsers.add_parser("serve", help="Start the HTTP API server")
    serve_parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    serve_parser.add_argument("--port", type=int, default=8100, help="Bind port")
    serve_parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    serve_parser.set_defaults(func=None)  # handled specially

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return 1

    if args.command == "serve":
        import uvicorn
        uvicorn.run(
            "jobstar.triage.api:app",
            host=args.host,
            port=args.port,
            reload=args.reload,
        )
        return 0

    return asyncio.run(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
