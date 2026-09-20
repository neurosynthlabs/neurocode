"""In-process fan-out of everything the API records, to every open tab.

One process serves one workspace, so no broker is needed. Publishing is safe from worker threads
(onboarding runs in one): each subscriber's queue is fed on its own event loop.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


class Bus:
    def __init__(self) -> None:
        self.queues: dict[asyncio.Queue[tuple[str, Any]], asyncio.AbstractEventLoop] = {}
        #: How many events each reader was too slow to take. A tab that falls behind loses events —
        #: it always has — but losing them in silence is what made a stale screen look like a wrong
        #: one: nothing dropped, so nothing reconnected, so nothing ever put it right. Counted here
        #: and read by the stream, which tells that reader to load the workspace again.
        self.missed: dict[asyncio.Queue[tuple[str, Any]], int] = {}
        self.listeners: list[Callable[[str, Any], None]] = []

    def subscribe(self) -> asyncio.Queue[tuple[str, Any]]:
        q: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=500)
        self.queues[q] = asyncio.get_running_loop()
        return q

    def unsubscribe(self, q: asyncio.Queue[tuple[str, Any]]) -> None:
        self.queues.pop(q, None)
        self.missed.pop(q, None)

    def missed_by(self, q: asyncio.Queue[tuple[str, Any]]) -> int:
        """What this reader has missed since it last asked, and start counting again."""
        return self.missed.pop(q, 0)

    def listen(self, fn: Callable[[str, Any], None]) -> None:
        """A synchronous listener, for tests and for anything that must see every event in order."""
        self.listeners.append(fn)

    def publish(self, kind: str, data: Any) -> None:
        for fn in list(self.listeners):
            fn(kind, data)
        for q, loop in list(self.queues.items()):
            try:
                loop.call_soon_threadsafe(self._offer, q, (kind, data))
            except RuntimeError:  # the loop behind that tab is gone
                self.queues.pop(q, None)

    def _offer(self, q: asyncio.Queue[tuple[str, Any]], item: tuple[str, Any]) -> None:
        try:
            q.put_nowait(item)
        except asyncio.QueueFull:  # a tab that stopped reading loses events; it never blocks the API
            self.missed[q] = self.missed.get(q, 0) + 1
