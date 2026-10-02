"""MISSION MEW — Server-Sent Events stream for the event bus.

GET /api/events/stream -> text/event-stream. Replays every published event
(reminder_due, protocol, ...) and emits a ``: heartbeat`` comment every 15s
so proxies and clients can detect a live connection.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from app.services.event_bus import subscribe, unsubscribe

router = APIRouter()

_HEARTBEAT_SECONDS = 15


@router.get("/api/events/stream")
async def stream_events():
    async def gen():
        queue = subscribe()
        try:
            # Immediate heartbeat: proves liveness and flushes headers, so a
            # short `curl --max-time 8` already sees ": heartbeat".
            yield ": heartbeat\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=_HEARTBEAT_SECONDS
                    )
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                name = str(event.get("type") or "message")
                yield f"event: {name}\ndata: {json.dumps(event)}\n\n"
        finally:
            unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream")
