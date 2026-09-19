"""The live stream: everything the API records, as it happens, to every open tab.

One process serves one workspace, so the fan-out is in memory — no broker, nothing to run. A tab that
stops reading loses events rather than blocking the API, and a reconnect catches up by asking for
what it missed (`?after=` on the run and session reads), so nothing is lost, only late.

The events, by name:

- `activity` — one line of the activity log, as `GET /activity` lists it.
- `change` — `{op: "put", collection, doc}` or `{op: "drop", collection, id}`: a document a screen
  holds, in exactly the shape its list route returns (see `data.changes`).
- `run` — one log line of an agent run, with its `runRef`.
- `chat` — one message of a session, as `GET /sessions/{ref}` lists it, with its `sessionRef`. While
  an answer is being written, `chat` also carries `{sessionRef, stream: {step, answer?, answerAt?,
  reasoning?, reasoningAt?, ms}}` — the words that just arrived, and where they go in the answer (or its
  reasoning) so far — or `{sessionRef, stream: {step, restart: true, lane}}` when a lane failed halfway
  and the next starts afresh. These are never stored: the finished turn is written once, and arrives
  as an ordinary message that replaces them. A tab that misses one ignores the rest of that step.
- `reset` — `{at: <ISO 8601>}`: the workspace was emptied. Emptying is one bulk statement per table,
  so there is no document-by-document account of it to send; a tab that hears this reloads everything
  it holds. Published only after the emptying has committed, so the reload reads the empty workspace.

Asking who is connecting takes a database session, and the stream can stay open for hours. The
session is the request's own, which is closed — and its connection returned to the pool — as soon as
this route has returned the response, before the first event is sent (see `deps.session`). A stream
holds no connection and no transaction while it is open; a dozen tabs used to hold a dozen, which
emptied the pool and made REINDEX wait on every one of them.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from ..events import Bus
from .deps import current_person

router = APIRouter()

KEEPALIVE = 15          # seconds of quiet before a comment keeps the connection open


def bus(request: Request) -> Bus:
    return request.app.state.bus


def publish(bus_or_none: Bus | None, kind: str, data: Any) -> None:
    """Publishing is optional everywhere: a service without a bus simply does not stream."""
    if bus_or_none is not None:
        bus_or_none.publish(kind, data)


@router.get("/activity/stream", dependencies=[Depends(current_person)])
async def stream(feed: Bus = Depends(bus)) -> StreamingResponse:
    """Server-sent events: `activity`, `change`, `run` and `chat`, exactly as the screens expect."""
    queue = feed.subscribe()

    async def events():
        try:
            yield "retry: 3000\n\n"
            while True:
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE)
                    yield f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            feed.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
