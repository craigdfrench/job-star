"""
Command-line entry point for the context gatherer.

Usage:
    python -m context_gatherer "intake text" --repo . --logs ./logs
    python -m context_gatherer "fix login bug on staging" --repo ~/code/app --logs /var/log/app --output ctx.json
    echo "deploy failed at midnight" | python -m context_gatherer --repo . --logs ./logs

Gathers related files, git history, and recent errors for an intake request
and prints the result as JSON (or writes it to --output).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from context_gatherer.gatherer import ContextGatherer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="context_gatherer",
        description=(
            "Examine an intake request and gather related files, git history, "
            "and recent errors before triage."
        ),
    )
    parser.add_argument(
        "intake",
        nargs="?",
        default=None,
        help='Intake request text (e.g. "fix login bug on staging"). '
        "If omitted, reads from stdin.",
    )
    parser.add_argument(
        "--repo",
        default=".",
        help="Path to the git repository to inspect (default: current directory).",
    )
    parser.add_argument(
        "--logs",
        default=None,
        help="Path to a directory or file of recent logs/error reports to scan.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Write gathered context as JSON to this path instead of stdout.",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=20,
        help="Maximum number of related files to gather (default: 20).",
    )
    parser.add_argument(
        "--max-commits",
        type=int,
        default=10,
        help="Maximum number of recent git commits to include (default: 10).",
    )
    parser.add_argument(
        "--max-errors",
        type=int,
        default=20,
        help="Maximum number of recent error lines to include (default: 20).",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print the JSON output with indentation.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print progress/summary to stderr.",
    )
    return parser


def _read_intake(arg_intake: Optional[str]) -> str:
    """Return intake text from the argument or stdin."""
    if arg_intake is not None:
        return arg_intake.strip()
    if sys.stdin.isatty():
        sys.stderr.write(
            "No intake text provided. Pass it as an argument or pipe via stdin.\n"
        )
        sys.exit(2)
    return sys.stdin.read().strip()


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    intake_text = _read_intake(args.intake)
    if not intake_text:
        sys.stderr.write("Error: intake text is empty.\n")
        return 2

    repo_path = Path(args.repo).resolve()
    logs_path = Path(args.logs).resolve() if args.logs else None

    if not repo_path.exists():
        sys.stderr.write(f"Error: repo path does not exist: {repo_path}\n")
        return 2
    if logs_path and not logs_path.exists():
        sys.stderr.write(f"Error: logs path does not exist: {logs_path}\n")
        return 2

    if args.verbose:
        sys.stderr.write(f"Intake: {intake_text!r}\n")
        sys.stderr.write(f"Repo:   {repo_path}\n")
        sys.stderr.write(f"Logs:   {logs_path or '(none)'}\n")
        sys.stderr.write("Gathering context...\n")

    gatherer = ContextGatherer(
        max_files=args.max_files,
        max_commits=args.max_commits,
        max_errors=args.max_errors,
    )

    try:
        context = gatherer.gather(
            intake_text=intake_text,
            repo_path=repo_path,
            logs_path=logs_path,
        )
    except Exception as exc:  # noqa: BLE001 - CLI surface, report cleanly
        sys.stderr.write(f"Error during gathering: {exc}\n")
        return 1

    indent = 2 if args.pretty else None
    payload = json.dumps(context, indent=indent, default=str)

    if args.output:
        out_path = Path(args.output).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(payload + "\n", encoding="utf-8")
        if args.verbose:
            sys.stderr.write(f"Wrote context to {out_path}\n")
    else:
        print(payload)

    if args.verbose:
        files = len(context.get("related_files", []))
        commits = len(context.get("git_history", []))
        errors = len(context.get("recent_errors", []))
        sys.stderr.write(
            f"Done. files={files} commits={commits} errors={errors}\n"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
