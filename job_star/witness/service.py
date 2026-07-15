"""Witness HTTP service — runs commands and captures evidence.

Listens on a Unix socket (default: /run/job-star-witness.sock) for independence
from the worker processes. The executor calls POST /observe with a command;
the witness runs it in its own subprocess, captures the output, stores an
evidence record, and returns the GUID.

Endpoints:
  POST /observe   — run a command, capture evidence, return GUID
  GET  /evidence/{guid} — look up an evidence record
  GET  /health    — health check
  GET  /chain     — verify the hash chain is intact
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any

from aiohttp import web

from .store import EvidenceStore, EvidenceRecord, compute_output_hash

DEFAULT_SOCKET_PATH = "/run/job-star-witness.sock"


async def _handle_observe(request: web.Request) -> web.Response:
    """Run a command, capture evidence, return the GUID."""
    store: EvidenceStore = request.app["store"]
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "invalid JSON"}, status=400)

    command = body.get("command")
    if not command:
        return web.json_response({"error": "missing 'command'"}, status=400)
    cwd = body.get("cwd")
    timeout = body.get("timeout", 300)

    # Normalize command to a list for the record, but run via shell if it's a string
    if isinstance(command, str):
        cmd_list = command
        shell = True
    else:
        cmd_list = list(command)
        shell = False

    # Run the command in the witness's own subprocess
    start = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_list if not shell else ["/bin/sh", "-c", command],
            cwd=cwd if cwd and os.path.isdir(cwd) else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            exit_code = proc.returncode
            timed_out = False
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            stdout_bytes, stderr_bytes = b"", b"timeout"
            exit_code = -2
            timed_out = True
    except Exception as e:
        stdout_bytes, stderr_bytes = b"", str(e).encode()
        exit_code = -1

    duration_ms = int((time.time() - start) * 1000)
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    # Build and store the evidence record
    record = EvidenceRecord(
        action="run_command",
        command=cmd_list,
        cwd=cwd or "",
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        output_hash=compute_output_hash(stdout, stderr),
        duration_ms=duration_ms,
    )
    if timed_out:
        record.action = "run_command_timeout"

    try:
        guid = await store.store(record)
    except Exception as e:
        return web.json_response({"error": f"store failed: {e}"}, status=500)

    return web.json_response({
        "guid": guid,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "output_hash": record.output_hash,
    })


async def _handle_evidence(request: web.Request) -> web.Response:
    """Look up an evidence record by GUID."""
    store: EvidenceStore = request.app["store"]
    guid = request.match_info["guid"]
    record = await store.lookup(guid)
    if record is None:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response(record)


async def _handle_health(request: web.Request) -> web.Response:
    return web.json_response({"status": "healthy", "witness_id": request.app["witness_id"]})


async def _handle_chain(request: web.Request) -> web.Response:
    """Verify the hash chain is intact."""
    store: EvidenceStore = request.app["store"]
    ok, msg = await store.verify_chain()
    return web.json_response({"chain_intact": ok, "message": msg})


def create_app(store: EvidenceStore, witness_id: str = "job-star-witness-01") -> web.Application:
    """Create the aiohttp application."""
    app = web.Application()
    app["store"] = store
    app["witness_id"] = witness_id
    app.router.add_post("/observe", _handle_observe)
    app.router.add_get("/evidence/{guid}", _handle_evidence)
    app.router.add_get("/health", _handle_health)
    app.router.add_get("/chain", _handle_chain)
    return app


async def run_service(
    socket_path: str = DEFAULT_SOCKET_PATH,
    dsn: str | None = None,
    witness_id: str = "job-star-witness-01",
) -> None:
    """Run the witness service on a Unix socket."""
    store = EvidenceStore(dsn=dsn, witness_id=witness_id)
    await store.ensure_schema()
    app = create_app(store, witness_id)

    # Ensure socket directory exists
    socket_dir = os.path.dirname(socket_path)
    if socket_dir:
        os.makedirs(socket_dir, exist_ok=True)

    # Remove stale socket
    if os.path.exists(socket_path):
        os.unlink(socket_path)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.UnixSite(runner, socket_path)
    await site.start()

    # Set socket permissions: workers and witness share the jobstar group
    try:
        os.chmod(socket_path, 0o660)
    except OSError:
        pass

    print(f"witness service listening on {socket_path}", flush=True)

    # Run forever
    try:
        await asyncio.Event().wait()
    finally:
        await runner.cleanup()
        await store.close()


def main():
    """CLI entry point for the witness service."""
    import sys
    socket_path = os.environ.get("JOB_STAR_WITNESS_SOCKET", DEFAULT_SOCKET_PATH)
    dsn = os.environ.get("DATABASE_URL")
    witness_id = os.environ.get("JOB_STAR_WITNESS_ID", "job-star-witness-01")
    asyncio.run(run_service(socket_path=socket_path, dsn=dsn, witness_id=witness_id))


if __name__ == "__main__":
    main()
