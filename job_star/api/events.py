"""Distributed event bus backed by Postgres.

API nodes publish to the `events` table; SSE consumers poll it.
Workers on any machine can also publish events by calling publish_event.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from job_star.db import publish_event as _publish_event


async def publish(event_type: str, payload: dict) -> None:
    """Persist an event to the Postgres events table."""
    await _publish_event(event_type, payload)


async def sse_generator(since_id: str | None = None, poll_interval: float = 1.0):
    """Async generator for SSE: polls events table and yields SSE-formatted bytes."""
    from job_star.db import get_events_since
    import json

    last_id = since_id
    while True:
        events = await get_events_since(last_id, limit=100)
        for event in events:
            last_id = str(event["id"])
            data = json.dumps({
                "id": last_id,
                "type": event["type"],
                "payload": event["payload"],
                "ts": event["created_at"].isoformat() if event.get("created_at") else None,
            })
            yield f"data: {data}\n\n".encode()
        await asyncio.sleep(poll_interval)
