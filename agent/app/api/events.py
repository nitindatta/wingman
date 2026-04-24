"""SSE endpoint — streams run events to the portal debug log panel."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.services.run_events import RunEvent, bus

router = APIRouter()


@router.get("/api/events/stream")
async def stream_events(request: Request) -> StreamingResponse:
    raw_last_id = request.headers.get("Last-Event-ID", "0")
    try:
        after_seq = int(raw_last_id)
    except ValueError:
        after_seq = 0

    async def generator():
        q = await bus.subscribe(after_seq=after_seq)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event: RunEvent = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield "data: {\"type\":\"ping\"}\n\n"
                    continue
                payload = {
                    "type": event.type,
                    "run_id": event.run_id,
                    "label": event.label,
                    "ts": event.ts,
                    "data": event.data,
                }
                yield f"id: {event.seq}\ndata: {json.dumps(payload)}\n\n"
        finally:
            bus.unsubscribe(q)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
