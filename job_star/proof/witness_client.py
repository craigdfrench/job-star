"""Witness client — calls the independent witness service.

The witness is a separate service (job_star.witness.service) that runs commands
on behalf of the executor and captures evidence in an append-only, hash-chained
store. The executor delegates execution to the witness so it can't lie about
what happened — the witness ran the command, not the executor.

Usage:
    client = WitnessClient(socket_path="/run/job-star-witness.sock")
    guid = await client.run(["node", "enhance.mjs", "--embeddings"], cwd="/path")
    evidence = await client.lookup(guid)  # returns the stored record

The witness service itself is in job_star/witness/service.py. It runs as its
own systemd unit (job-star-witness.service) on a Unix socket for independence.
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import aiohttp

DEFAULT_SOCKET_PATH = "/run/job-star-witness.sock"
DEFAULT_HTTP_URL = os.environ.get("JOB_STAR_WITNESS_URL", "")


class WitnessError(Exception):
    """Raised when the witness service returns an error or is unreachable."""
    pass


class WitnessClient:
    """HTTP client for the witness service.

    Connects via Unix socket (default) or HTTP URL. The witness runs commands
    in its own subprocess, captures evidence, and returns a GUID. The executor
    stores the GUID as an Artifact(kind="witnessed") for the verifier to look up.
    """

    def __init__(
        self,
        socket_path: str | None = None,
        http_url: str | None = None,
        timeout: int = 300,
    ):
        self.socket_path = socket_path or DEFAULT_SOCKET_PATH
        self.http_url = http_url or DEFAULT_HTTP_URL or None
        self.timeout = timeout

    def _connector(self) -> aiohttp.UnixConnector | None:
        """Build a Unix socket connector if using socket mode."""
        if self.http_url:
            return None  # use normal HTTP
        return aiohttp.UnixConnector(path=self.socket_path)

    def _base_url(self) -> str:
        if self.http_url:
            return self.http_url.rstrip("/")
        return "http://localhost"  # dummy host for Unix connector

    async def run(
        self,
        command: list[str] | str,
        cwd: str | None = None,
        timeout: int | None = None,
    ) -> str:
        """Ask the witness to run a command and record evidence.

        Returns the evidence GUID. The executor stores this as an Artifact.
        """
        payload: dict[str, Any] = {
            "command": command,
        }
        if cwd:
            payload["cwd"] = cwd
        if timeout:
            payload["timeout"] = timeout

        try:
            connector = self._connector()
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
            ) as session:
                async with session.post(
                    f"{self._base_url()}/observe",
                    json=payload,
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        raise WitnessError(f"witness /observe returned {resp.status}: {body[:200]}")
                    data = await resp.json()
                    return data.get("guid", "")
        except aiohttp.ClientConnectorError as e:
            raise WitnessError(f"witness unreachable: {e}") from e
        except aiohttp.ClientError as e:
            raise WitnessError(f"witness client error: {e}") from e

    async def lookup(self, guid: str) -> dict[str, Any] | None:
        """Look up evidence by GUID. Returns the evidence record or None."""
        try:
            connector = self._connector()
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as session:
                async with session.get(f"{self._base_url()}/evidence/{guid}") as resp:
                    if resp.status == 404:
                        return None
                    if resp.status != 200:
                        body = await resp.text()
                        raise WitnessError(f"witness /evidence returned {resp.status}: {body[:200]}")
                    return await resp.json()
        except aiohttp.ClientConnectorError as e:
            raise WitnessError(f"witness unreachable: {e}") from e
        except aiohttp.ClientError as e:
            raise WitnessError(f"witness client error: {e}") from e

    async def health(self) -> bool:
        """Check if the witness service is reachable."""
        try:
            connector = self._connector()
            async with aiohttp.ClientSession(
                connector=connector,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as session:
                async with session.get(f"{self._base_url()}/health") as resp:
                    return resp.status == 200
        except Exception:
            return False
