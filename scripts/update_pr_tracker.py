"""Update local PR tracking file with cleanup results.

This is a small helper that records which PRs were closed as superseded
so we have a durable, queryable record in the repo itself.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


TRACKER_FILE = Path(__file__).resolve().parent.parent / "docs" / "pr_cleanup_log.md"


def append_cleanup_record(prs_closed, replacement_pr, notes=""):
    """Append a cleanup record to the tracker markdown file.

    Args:
        prs_closed: list of PR numbers (ints) that were closed.
        replacement_pr: the PR number that supersedes them.
        notes: optional extra notes string.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "",
        f"## Cleanup Run — {timestamp}",
        f"Replacement PR: #{replacement_pr}",
        "",
    ]
    if prs_closed:
        lines.append("| PR # | Status |")
        lines.append("|------|--------|")
        for pr in prs_closed:
            lines.append(f"| #{pr} | closed (superseded by #{replacement_pr}) |")
    else:
        lines.append("No PRs closed in this run.")
    if notes:
        lines.append("")
        lines.append(f"Notes: {notes}")
    lines.append("")
    with TRACKER_FILE.open("a") as f:
        f.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Record superseded PR cleanup results."
    )
    parser.add_argument(
        "--replacement", type=int, required=True, help="Replacement PR number"
    )
    parser.add_argument(
        "--closed",
        nargs="*",
        type=int,
        default=[],
        help="PR numbers that were closed",
    )
    parser.add_argument("--notes", default="", help="Optional notes")
    args = parser.parse_args()
    append_cleanup_record(args.closed, args.replacement, args.notes)
    print(f"Recorded cleanup: {len(args.closed)} PRs closed, replaced by #{args.replacement}")
    return 0


if __name__ == "__main__":
    sys.exit(main())