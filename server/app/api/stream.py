"""The live stream: everything the API records, as it happens, to every open tab.

One process serves one workspace, so the fan-out is in memory — no broker, nothing to run. A tab that
stops reading loses events rather than blocking the API, and a reconnect catches up by asking for
what it missed (`?after=` on the run and session reads), so nothing is lost, only late. A tab that is
*connected* and merely slow — backgrounded, throttled, on battery — used to be the exception: it lost
events with nothing dropped, so nothing reconnected and nothing ever put its screens right. It is now
told, with `resync`.

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
- `routine` — `{scheduleId, projectId, name, fire}`: a routine's fire opened, ended or was skipped, with the
  fire as `GET /schedules/{id}/fires` lists it.
- `review` — a review asked for on demand, as `GET /reviews/{ref}` returns it: when it is asked for, when it
  finishes or fails, and when it is sent to a session or made into a plan.
- `resync` — `{why: "missed", events: <n>}`: this reader was too slow and `n` events went past it.
  What they were is not kept — the queue is bounded so one slow tab cannot hold up the API — so the
  only true thing to say is that what it holds may be wrong and it should load the workspace again.
- `reset` — `{at: <ISO 8601>}`: the workspace was emptied. Emptying is one bulk statement per table,
  so there is no document-by-document account of it to send; a tab that hears this reloads everything
  it holds. Published only after the emptying has committed, so the reload reads the empty workspace.

Asking who is connecting takes a database session, and the stream can stay open for hours. The
session is the request's own, which is closed — and its connection returned to the pool — as soon as
this route has returned the response, before the first event is sent (see `deps.session`). A stream
holds no connection and no transaction while it is open; a dozen tabs used to hold a dozen, which
emptied the pool and made REINDEX wait on every one of them.

**A restricted project does not scroll past.** Every event that names a project is dropped unless the
reader may see that project, by the same rule `GET /projects` lists by (`Person.may_see`). Without
this the whole of per-project rights is decoration: the project is gone from the list and 404s when
asked for by id, while its runs, plans, reviews and activity lines keep arriving in every open tab.

Which projects those are is resolved when the stream opens, and **re-resolved every
`RESOLVE_EVERY` seconds** from a read-only session of its own. A cadence rather than a set frozen at
open, because a stream lives for hours: an admin who restricts a project, or takes somebody off one,
would otherwise keep feeding it to every tab that was already open until each of them was reloaded.
So the honest promise is "within half a minute", not "at once" and not "when you next reload". The
connection is held for the length of one indexed query, not for the length of the stream.

Two things this does not cover, said here rather than left to be discovered: a `run` or `chat` event
carries only its run's or session's ref, so a line of a restricted project's run still reaches a
reader who has the ref — the fences for those are on the routes that hand out the refs. And a
`change` with `op: "drop"` carries an id and a collection and no project, so a deletion is announced
to everyone; what was deleted is not.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from time import monotonic
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.engine import Database
from ..events import Bus
from ..services.identity import Person
from .deps import current_person, database, session, unseen_by

log = logging.getLogger(__name__)

router = APIRouter()

KEEPALIVE = 15          # seconds of quiet before a comment keeps the connection open
#: How often an open stream asks again which projects its reader may see. Short enough that
#: restricting a project reaches the tabs that are already open, long enough that a day of tabs is a
#: few queries a minute rather than a poll.
RESOLVE_EVERY = 30


def bus(request: Request) -> Bus:
    return request.app.state.bus


def publish(bus_or_none: Bus | None, kind: str, data: Any) -> None:
    """Publishing is optional everywhere: a service without a bus simply does not stream."""
    if bus_or_none is not None:
        bus_or_none.publish(kind, data)


def project_of(kind: str, data: Any) -> str | None:
    """Which project an event belongs to, or None when it belongs to the workspace itself.

    `activity`, `routine` and `review` name it outright. A `change` names it on the document, except
    for a project's own document, where the project *is* the document and its id is `id`.
    """
    if not isinstance(data, dict):
        return None
    if kind == "change":
        doc = data.get("doc")
        body = doc if isinstance(doc, dict) else data
        key = "id" if data.get("collection") == "projects" else "projectId"
        found = body.get(key)
    else:
        found = data.get("projectId")
    return found if isinstance(found, str) else None


@dataclass(frozen=True)
class Reader:
    """Who is reading this stream, and which projects it must not carry to them.

    One object, because the generator asks the same question twice: what to hide now, and what to
    hide half a minute from now. A Reader with nothing hidden and nobody to ask about is the whole
    workspace — which is what every reader is until somebody restricts a project.
    """

    hidden: frozenset[str] = frozenset()
    who: Person | None = None
    db: Database | None = None

    async def again(self) -> frozenset[str]:
        """What to hide now, on a read-only session of its own — held for one indexed query, never
        for the length of the stream."""
        if self.who is None or self.db is None:
            return self.hidden
        async with self.db.read() as its_own:
            return await unseen_by(self.who, its_own)


async def reading(who: Person = Depends(current_person), db: Database = Depends(database),
                  open_session: AsyncSession = Depends(session)) -> Reader:
    """The first answer to "which projects", taken on the request's own session — so a database that
    cannot answer it is a status code rather than a stream that opens and quietly shows everything."""
    return Reader(await unseen_by(who, open_session), who, db)


@router.get("/activity/stream")
async def stream(feed: Bus = Depends(bus), reader: Reader = Depends(reading)) -> StreamingResponse:
    """Server-sent events: `activity`, `change`, `run`, `chat`, `routine`, `review`, `resync` and `reset`,
    exactly as the screens expect — minus anything belonging to a project this reader may not see.
    """
    queue = feed.subscribe()

    def sent(kind: str, data: Any) -> str:
        return f"event: {kind}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

    async def events():
        out_of_sight, asked_at = reader.hidden, monotonic()
        try:
            yield "retry: 3000\n\n"
            while True:
                # Before anything else, whether this reader fell behind while it was away. The queue
                # is bounded on purpose — a slow tab must never hold up the API — so the events it
                # was not there for are gone, and the only honest thing left to say is "load it all
                # again". Said every time it happens, and the count reset, so a tab that keeps
                # falling behind keeps being told rather than being told once and left wrong.
                if (missed := feed.missed_by(queue)):
                    yield sent("resync", {"why": "missed", "events": missed})
                if monotonic() - asked_at >= RESOLVE_EVERY:
                    asked_at = monotonic()
                    try:
                        out_of_sight = await reader.again()
                    except Exception:            # noqa: BLE001 — a blip must not end every open tab
                        # The set stands until the next round. It is the one this reader was already
                        # being served, so a database that stutters narrows nothing and widens
                        # nothing; it only delays a change to what is hidden.
                        log.warning("could not re-read which projects this stream may carry; keeping "
                                    "the set it opened with", exc_info=True)
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE)
                    if (pid := project_of(kind, data)) is not None and pid in out_of_sight:
                        continue
                    yield sent(kind, data)
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            feed.unsubscribe(queue)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
