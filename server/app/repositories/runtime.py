"""Agent runs and sessions, read back.

Two things are deliberate here. Children are fetched **by query**, never through a relationship: a
serialiser that touches an unloaded relationship inside async code raises, and a run's children are
read on every list. And logs are paged from an id you already hold, which is what lets a screen
reconnect after a drop and ask only for what it missed.
"""
from __future__ import annotations

from sqlalchemy import ColumnElement, Integer, cast, func, select

from ..models import Chat, ChatMessage, Run, RunLog
from .base import Page, Repository


class RunRepository(Repository[Run]):
    model = Run

    async def by_ref(self, ref: str) -> Run | None:
        return await self.one(Run.ref == ref)

    async def newest(self, project_id: str | None = None, *, limit: int | None = None,
                     offset: int = 0) -> Page[Run]:
        where: list[ColumnElement[bool]] = [Run.project_id == project_id] if project_id else []
        return await self.page(*where, order_by=Run.created_at.desc(), limit=limit, offset=offset)

    async def children_of(self, run_ids: list[str]) -> dict[str, list[Run]]:
        """parent id → its agent runs. One statement for a whole list of runs, not one per run."""
        if not run_ids:
            return {}
        rows = (await self.session.execute(
            select(Run).where(Run.parent_id.in_(run_ids)).order_by(Run.created_at))).scalars().unique()
        out: dict[str, list[Run]] = {}
        for child in rows:
            out.setdefault(child.parent_id or "", []).append(child)
        return out

    async def waiting(self) -> list[Run]:
        return await self.list(Run.status == "waiting", order_by=Run.created_at)

    async def next_ref(self, prefix: str = "RUN-") -> str:
        return await super().next_ref(Run.ref, prefix)


class RunLogRepository(Repository[RunLog]):
    model = RunLog

    async def after(self, run_id: str, last_id: int = 0, *, limit: int = 1000) -> list[RunLog]:
        stmt = (select(RunLog).where(RunLog.run_id == run_id, RunLog.id > last_id)
                .order_by(RunLog.id).limit(min(limit, 2000)))
        return list((await self.session.execute(stmt)).scalars())

    async def write(self, run_id: str, *, level: str, line: str, step: int | None = None) -> RunLog:
        """One line of a run's output, written and announced together.

        The screens key output by the run's *reference*, which is looked up here rather than passed in
        by fifteen call sites — one of which would eventually forget, and that line would arrive with
        nowhere to go. The run is already in this session almost every time, so it costs nothing.
        """
        written = await self.add(RunLog(run_id=run_id, level=level, line=line[:2000], step=step))
        feed = self.session.info.get("bus")
        if feed is not None:
            run = await self.session.get(Run, run_id)
            if run is not None:
                from ..schemas.runtime import run_log_json
                feed.publish("run", {"runRef": run.ref, **run_log_json(written)})
        return written


class ChatRepository(Repository[Chat]):
    model = Chat

    async def by_ref(self, ref: str) -> Chat | None:
        return await self.one(Chat.ref == ref)

    async def newest(self, project_id: str | None = None, *, limit: int | None = None,
                     offset: int = 0) -> Page[Chat]:
        where: list[ColumnElement[bool]] = [Chat.project_id == project_id] if project_id else []
        return await self.page(*where, order_by=Chat.last_at.desc(), limit=limit, offset=offset)

    async def messages(self, chat_id: str, after: int = 0, *, limit: int = 500) -> list[ChatMessage]:
        stmt = (select(ChatMessage).where(ChatMessage.chat_id == chat_id, ChatMessage.id > after)
                .order_by(ChatMessage.id).limit(min(limit, 1000)))
        return list((await self.session.execute(stmt)).scalars())

    async def say(self, chat_id: str, *, role: str, body: str, **extra: object) -> ChatMessage:
        """One turn, written the moment it happens — that is the whole promise of a session — and
        announced in the same breath, so an open tab sees it arrive rather than on the next reload."""
        said = await self.add(ChatMessage(chat_id=chat_id, role=role, body=body, **extra))
        feed = self.session.info.get("bus")
        if feed is not None:
            chat = await self.session.get(Chat, chat_id)
            if chat is not None:
                from ..schemas.runtime import chat_message_json
                feed.publish("chat", {"sessionRef": chat.ref, **chat_message_json(said)})
        return said

    async def next_ref(self, prefix: str = "CHAT-") -> str:
        return await super().next_ref(Chat.ref, prefix)
