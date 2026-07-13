"""Job-Star: Intelligent client for gatehouse-ai async jobs.

Job-Star decides what to execute, when, and how — acting as the
intelligent orchestration layer on top of gatehouse-ai's async job
interface.
"""

__version__ = "0.1.0"


// --- DUPLICATE BLOCK ---

"""Job-Star CLI: manual job triggering and inspection.

Commands:
    job-star submit <job-type> [--priority N] [--param KEY=VALUE]...
    job-star status <job-id>
    job-star list [--status S] [--type T] [--limit N]
    job-star cancel <job-id>

Intended for debugging and manual operation during bootstrap.
The decision engine will eventually automate most of this.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from job_star.gatehouse_client import (
    GatehouseClient,
    GatehouseError,
    JobNotFoundError,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def _get_client(base_url: str | None, api_key: str | None) -> GatehouseClient:
    """Construct a GatehouseClient from CLI options / env."""
    import os

    url = base_url or os.environ.get("GATEHOUSE_URL", "http://localhost:8000")
    key = api_key or os.environ.get("GATEHOUSE_API_KEY")
    return GatehouseClient(base_url=url, api_key=key)


def _parse_params(params: tuple[str, ...]) -> dict[str, Any]:
    """Parse repeated --param KEY=VALUE flags into a dict.

    Values are JSON-decoded if valid JSON, otherwise kept as strings.
    """
    result: dict[str, Any] = {}
    for item in params:
        if "=" not in item:
            raise click.BadParameter(
                f"--param expects KEY=VALUE, got: {item!r}"
            )
        key, _, value = item.partition("=")
        try:
            result[key.strip()] = json.loads(value)
        except json.JSONDecodeError:
            result[key.strip()] = value
    return result


def _print_job(job: dict[str, Any]) -> None:
    """Pretty-print a single job dict."""
    click.echo(
        f"  {job['id']}  "
        f"[{click.style(job['status'], bold=True)}]  "
        f"{job['type']}  "
        f"(priority {job['priority']})"
    )
    click.echo(f"    created: {job['created_at']}   updated: {job['updated_at']}")
    if job.get("error"):
        click.echo(f"    error:   {click.style(job['error'], fg='red')}")
    if job.get("result") is not None:
        result_str = json.dumps(job["result"], default=str)
        if len(result_str) > 200:
            result_str = result_str[:197] + "..."
        click.echo(f"    result:  {result_str}")
    if job.get("payload"):
        payload_str = json.dumps(job["payload"], default=str)
        if len(payload_str) > 200:
            payload_str = payload_str[:197] + "..."
        click.echo(f"    payload: {payload_str}")


def _handle_error(fn):
    """Decorator that catches gatehouse errors and exits cleanly."""
    import functools

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except JobNotFoundError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(2)
        except GatehouseError as exc:
            click.echo(f"Error: {exc}", err=True)
            sys.exit(1)

    return wrapper


# ---------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------

@click.group()
@click.option(
    "--url",
    default=None,
    envvar="GATEHOUSE_URL",
    help="gatehouse-ai service URL (default: $GATEHOUSE_URL or http://localhost:8000).",
)
@click.option(
    "--api-key",
    default=None,
    envvar="GATEHOUSE_API_KEY",
    help="Authentication key for gatehouse-ai (default: $GATEHOUSE_API_KEY).",
)
@click.version_option(package_name="job-star")
@click.pass_context
def cli(ctx: click.Context, url: str | None, api_key: str | None) -> None:
    """Job-Star: intelligent client for gatehouse-ai async jobs."""
    ctx.ensure_object(dict)
    ctx.obj["client"] = _get_client(url, api_key)


# ---------------------------------------------------------------------
# submit
# ---------------------------------------------------------------------

@cli.command("submit")
@click.argument("job_type")
@click.option("--priority", type=int, default=0, help="Job priority (higher = more urgent).")
@click.option(
    "--param", "params", multiple=True,
    help="Job input parameter as KEY=VALUE (VALUE is JSON-decoded if valid). Repeatable.",
)
@click.pass_context
@_handle_error
def submit(ctx: click.Context, job_type: str, priority: int, params: tuple[str, ...]) -> None:
    """Submit a new async job of type JOB_TYPE."""
    client: GatehouseClient = ctx.obj["client"]
    payload = _parse_params(params)
    job = client.submit(job_type=job_type, payload=payload, priority=priority)
    click.echo("Submitted job:")
    _print_job(job)


# ---------------------------------------------------------------------
# status
# ---------------------------------------------------------------------

@cli.command("status")
@click.argument("job_id")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
@click.pass_context
@_handle_error
def status(ctx: click.Context, job_id: str, as_json: bool) -> None:
    """Show current status of a job."""
    client: GatehouseClient = ctx.obj["client"]
    job = client.status(job_id)
    if as_json:
        click.echo(json.dumps(job, indent=2, default=str))
    else:
        _print_job(job)


# ---------------------------------------------------------------------
# list
# ---------------------------------------------------------------------

@cli.command("list")
@click.option("--status", "filter_status", default=None, help="Filter by status (queued, running, completed, failed, cancelled).")
@click.option("--type", "filter_type", default=None, help="Filter by job type.")
@click.option("--limit", type=int, default=50, help="Maximum jobs to show.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
@click.pass_context
@_handle_error
def list_jobs(
    ctx: click.Context,
    filter_status: str | None,
    filter_type: str | None,
    limit: int,
    as_json: bool,
) -> None:
    """List jobs, optionally filtered."""
    client: GatehouseClient = ctx.obj["client"]
    jobs = client.list(status=filter_status, job_type=filter_type, limit=limit)
    if as_json:
        click.echo(json.dumps(jobs, indent=2, default=str))
        return
    if not jobs:
        click.echo("No jobs found.")
        return
    click.echo(f"{len(jobs)} job(s):")
    for job in jobs:
        _print_job(job)
        click.echo("")


# ---------------------------------------------------------------------
# cancel
# ---------------------------------------------------------------------

@cli.command("cancel")
@click.argument("job_id")
@click.pass_context
@_handle_error
def cancel(ctx: click.Context, job_id: str) -> None:
    """Cancel a queued or running job."""
    client: GatehouseClient = ctx.obj["client"]
    job = client.cancel(job_id)
    click.echo("Cancelled job:")
    _print_job(job)


def main() -> None:
    """Entry point for the `job-star` console script."""
    cli(obj={})


if __name__ == "__main__":
    main()
