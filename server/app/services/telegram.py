"""Telegram: the gates that wait on a person, sent to their phone and answered from there.

A workspace admin gives the bot's token (made with @BotFather). Each person links their own chat by
sending the bot a one-time code made in Settings while signed in, which proves the chat is theirs. From
then on a gate that person may decide — a project's first test run, a command or an edit a tool rule asks
about, a step walked one at a time — arrives as a message with its answers as buttons, and a button goes
through the same service, the same project fence and the same activity log as the web screen's.

Two gates never get an answer button: a run's signature, because signing belongs in front of the diff,
and an agent's question, because it wants words. Those arrive with a link to open instead.

One loop per API process long-polls Telegram, so it works on a laptop with no public address exactly as
on the server. Only one process may poll a bot at a time, so a Postgres advisory lock picks which; the
others wait. Nothing here stores a message's text: what a button decides is read fresh from the database
when it is pressed, and a gate somebody already answered says so rather than answering twice.
"""
from __future__ import annotations

import asyncio
import logging
import re
import secrets as tokens
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.base import utcnow
from ..data.engine import Database
from ..events import Bus
from ..models import Approval, Project, Setting
from ..repositories import AuditRepository
from ..schemas.work import gate_kind
from ..secrets import Secrets
from .errors import Denied, Refused
from .gates import ApprovalService
from .identity import IdentityService, Person

log = logging.getLogger("neurocode.telegram")

BASE = "https://api.telegram.org"
TOKEN_KEY = "telegram_bot_token"
CONFIG = "channels.telegram"                   # {username, botId, at, by}
LINK = "channels.telegram.link."               # + user id → {chatId, username, at}
CODE = "channels.telegram.code."               # + code → {userId, expires}
OFFSET = "channels.telegram.offset"            # {offset}: the next update to ask for
CODE_LIFE = timedelta(minutes=10)
POLL_SECONDS = 25                              # Telegram holds a long poll open this long
IDLE_SECONDS = 15                              # how often a loop with no token looks again
LOCK = 0x4E43_7467                             # "NCtg": the advisory lock that makes one poller
DECIDE = "approvals:decide"
#: The answers a button may give, per kind of gate: (callback word, label, decision, scope).
BUTTONS: dict[str, tuple[tuple[str, str, str, str | None], ...]] = {
    "tests": (("a", "Allow", "approve", None), ("d", "Deny", "deny", None)),
    "step": (("a", "Run step", "approve", None), ("d", "Stop here", "deny", None)),
    "other": (("a", "Approve", "approve", None), ("d", "Deny", "deny", None)),
    "command": (("o", "Allow once", "approve", "once"), ("r", "Allow for run", "approve", "run"),
                ("d", "Deny", "deny", None)),
    "edit": (("o", "Allow once", "approve", "once"), ("r", "Allow for run", "approve", "run"),
             ("d", "Deny", "deny", None)),
}
#: Gates that are answered in the app, never from a chat button.
IN_APP = {"signature": "Sign it where you can read the diff.", "question": "Answer it in words, in the app."}
CALLBACK = re.compile(r"^g:([A-Z]+-\d+):([a-z])$")
CODE_SHAPE = re.compile(r"^[A-Z0-9]{8}$")


class BotError(Exception):
    """Telegram said no: a bad token, a chat that blocked the bot, a malformed call."""


class Bot:
    """The few Bot API calls this needs, over HTTPS. `transport` lets a test answer instead of Telegram."""

    def __init__(self, token: str, *, base: str = BASE, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.url = f"{base.rstrip('/')}/bot{token}"
        self.client = httpx.AsyncClient(timeout=POLL_SECONDS + 10, transport=transport)

    async def call(self, method: str, **params: Any) -> Any:
        try:
            r = await self.client.post(f"{self.url}/{method}", json=params)
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise BotError(f"Telegram did not answer: {type(e).__name__}") from e
        if not body.get("ok"):
            raise BotError(str(body.get("description") or f"HTTP {r.status_code}"))
        return body.get("result")

    async def close(self) -> None:
        await self.client.aclose()


async def _get(session: AsyncSession, key: str) -> dict[str, Any] | None:
    row = await session.get(Setting, key)
    return row.value if row is not None else None


async def _put(session: AsyncSession, key: str, value: dict[str, Any] | None) -> None:
    row = await session.get(Setting, key)
    if value is None:
        if row is not None:
            await session.delete(row)
    elif row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value
    await session.flush()


class ChannelService:
    """The bot's configuration and who is linked to it. The token lives in the keys file, never in a row."""

    def __init__(self, session: AsyncSession, secrets: Secrets, *, base: str = BASE,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.session, self.secrets, self.base, self.transport = session, secrets, base, transport

    async def status(self, who: Person) -> dict[str, Any]:
        config = await _get(self.session, CONFIG) if self.secrets.get(TOKEN_KEY) else None
        mine = await _get(self.session, LINK + who.id)
        linked = 0
        if config and who.can("workspace:admin"):
            linked = len((await self.session.execute(
                select(Setting.key).where(Setting.key.like(f"{LINK}%")))).scalars().all())
        return {"telegram": {
            "configured": bool(config), "bot": f"@{config['username']}" if config else None,
            "linked": bool(mine), "chat": (mine or {}).get("username") or None,
            **({"linkedPeople": linked} if config and who.can("workspace:admin") else {}),
        }}

    async def configure(self, token: str, who: Person, ip: str = "") -> dict[str, Any]:
        """Check a token with Telegram and keep it; an empty one switches the channel off and unlinks everyone."""
        who.must("workspace:admin", "set up the Telegram channel")
        token = token.strip()
        audit = AuditRepository(self.session)
        if not token:
            self.secrets.set(TOKEN_KEY, None)
            await _put(self.session, CONFIG, None)
            for key in (await self.session.execute(
                    select(Setting.key).where(Setting.key.like("channels.telegram.%")))).scalars().all():
                await _put(self.session, key, None)
            await audit.record(action="channels.telegram", user_id=who.id, target="Telegram",
                               detail={"token": "removed"}, ip=ip)
            return await self.status(who)
        if not re.fullmatch(r"\d{5,12}:[A-Za-z0-9_-]{30,50}", token):
            raise Refused("That is not a bot token. @BotFather gives one shaped like 123456789:AA…", status=422)
        bot = Bot(token, base=self.base, transport=self.transport)
        try:
            me = await bot.call("getMe")
        except BotError as e:
            raise Refused(f"Telegram refused that token: {e}", status=422) from e
        finally:
            await bot.close()
        before = await _get(self.session, CONFIG)
        if before is not None and before.get("botId") != me.get("id"):
            # Another bot: its update ids are its own, and nobody's chat has started it yet. The old bot's
            # offset would silently drop the new one's first messages, and its links would point nowhere.
            for key in (await self.session.execute(
                    select(Setting.key).where(Setting.key.like("channels.telegram.%")))).scalars().all():
                if key != CONFIG:
                    await _put(self.session, key, None)
        self.secrets.set(TOKEN_KEY, token)
        await _put(self.session, CONFIG, {"username": me.get("username", ""), "botId": me.get("id"),
                                          "at": utcnow().isoformat(), "by": who.name})
        await audit.record(action="channels.telegram", user_id=who.id, target=f"@{me.get('username', '')}",
                           detail={"token": "set"}, ip=ip)
        return await self.status(who)

    async def link_code(self, who: Person) -> dict[str, Any]:
        """A code this person sends the bot to link their chat. One at a time, for ten minutes."""
        config = await _get(self.session, CONFIG)
        if not config or not self.secrets.get(TOKEN_KEY):
            raise Refused("Telegram is not set up here yet. A workspace admin adds the bot's token first.")
        for key in (await self.session.execute(select(Setting).where(Setting.key.like(f"{CODE}%")))).scalars().all():
            if key.value.get("userId") == who.id:
                await self.session.delete(key)
        code = "".join(tokens.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
        expires = utcnow() + CODE_LIFE
        await _put(self.session, CODE + code, {"userId": who.id, "expires": expires.isoformat()})
        return {"code": code, "url": f"https://t.me/{config['username']}?start={code}",
                "expiresAt": expires.isoformat()}

    async def unlink(self, who: Person) -> dict[str, Any]:
        await _put(self.session, LINK + who.id, None)
        return await self.status(who)

    async def claim(self, code: str, chat_id: int, username: str) -> str | None:
        """The person a code was made for, now linked to this chat — or None for a wrong or stale code."""
        code = code.strip().upper()
        if not CODE_SHAPE.match(code):
            return None
        held = await _get(self.session, CODE + code)
        await _put(self.session, CODE + code, None)             # used once, whatever happens next
        if not held or held.get("expires", "") < utcnow().isoformat():
            return None
        user_id = str(held["userId"])
        # A chat is one person's. A code someone else made does not move a chat already linked to another
        # person — a tapped link would otherwise hand them the chat — so it is unlinked first, in Settings.
        for row in (await self.session.execute(select(Setting).where(Setting.key.like(f"{LINK}%")))).scalars().all():
            if row.value.get("chatId") == chat_id and row.key != LINK + user_id:
                return None
        await _put(self.session, LINK + user_id, {"chatId": chat_id, "username": username, "at": utcnow().isoformat()})
        return user_id

    async def person_for(self, chat_id: int) -> Person | None:
        """The active person this chat is linked to. A disabled account's chat is nobody's."""
        for row in (await self.session.execute(select(Setting).where(Setting.key.like(f"{LINK}%")))).scalars().all():
            if row.value.get("chatId") == chat_id:
                who = await IdentityService(self.session).person(row.key[len(LINK):])
                return who if who is not None and who.status == "active" else None
        return None

    async def linked(self) -> list[tuple[str, int]]:
        rows = (await self.session.execute(select(Setting).where(Setting.key.like(f"{LINK}%")))).scalars().all()
        return [(row.key[len(LINK):], int(row.value["chatId"])) for row in rows if "chatId" in row.value]


async def may_decide(session: AsyncSession, who: Person, approval: Approval) -> bool:
    """The web route's fence, said once for the chat: the right to decide, and the project in sight."""
    if who.status != "active" or not who.can(DECIDE):
        return False
    if approval.project_id is None:
        return True
    project = await session.get(Project, approval.project_id)
    return (project is not None and who.may_see(project.id, project.restricted)
            and DECIDE in who.in_project(project.id, project.restricted))


def message_for(approval: Approval, project: str, public_url: str) -> tuple[str, dict[str, Any] | None]:
    """The words and the buttons for one gate. Plain text: nothing a model wrote is read as markup."""
    kind = gate_kind(approval.tool)
    lines = [f"Needs you · {approval.ref}{f' · {project}' if project else ''}", approval.title]
    what = approval.payload.strip() or approval.tool
    if what and what != approval.title:
        lines.append(what[:600])
    who = " · ".join(x for x in (approval.agent, approval.run_ref or "", f"{approval.risk.lower()} risk") if x)
    lines.append(who)
    if kind in IN_APP:
        lines.append(IN_APP[kind])
    rows: list[list[dict[str, str]]] = []
    if kind not in IN_APP:
        rows.append([{"text": label, "callback_data": f"g:{approval.ref}:{word}"}
                     for word, label, _, _ in BUTTONS.get(kind, BUTTONS["other"])])
    if public_url.startswith("https://"):
        target = f"/runs?ref={approval.run_ref}" if approval.run_ref else "/permissions"
        rows.append([{"text": "Open", "url": f"{public_url.rstrip('/')}{target}"}])
    return "\n".join(lines), ({"inline_keyboard": rows} if rows else None)


@dataclass
class Sent:
    chat_id: int
    message_id: int


class Relay:
    """The loop the app's lifespan starts: polls Telegram for what people press, and sends each gate that
    appears to the linked people who may decide it."""

    def __init__(self, db: Database, gateway: Gateway, secrets: Secrets, bus: Bus, *, public_url: str = "",
                 base: str = BASE, transport: httpx.AsyncBaseTransport | None = None,
                 resume: Callable[..., Any] | None = None) -> None:
        self.db, self.gateway, self.secrets, self.public_url = db, gateway, secrets, public_url
        self.base, self.transport = base, transport
        self.resume = resume
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=500)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.sent: dict[str, list[Sent]] = {}
        self.jobs: set[asyncio.Task[Any]] = set()
        self._bot: Bot | None = None
        self._token = ""
        self._lock_conn: Any = None
        bus.listen(self._heard)

    # ── hearing the workspace ──
    def _heard(self, kind: str, data: Any) -> None:
        """Called by the bus, possibly from a worker thread: note a gate that is waiting or was answered."""
        if kind != "change" or not isinstance(data, dict) or data.get("collection") != "approvals":
            return
        doc = data.get("doc") or {}
        ref = doc.get("ref")
        if not ref or self.loop is None or not self._token:
            return
        try:
            self.loop.call_soon_threadsafe(self._enqueue, str(ref))
        except RuntimeError:                         # the loop behind it is gone
            pass

    def _enqueue(self, ref: str) -> None:
        try:
            self.queue.put_nowait(ref)
        except asyncio.QueueFull:                    # a burst beyond reason: the inbox still has it
            log.warning("telegram: %s was not sent, the queue is full", ref)

    def bot(self) -> Bot | None:
        token = self.secrets.get(TOKEN_KEY) or ""
        if token != self._token:
            if self._bot is not None:
                self.jobs.add(asyncio.create_task(self._bot.close()))
            self._bot = Bot(token, base=self.base, transport=self.transport) if token else None
            self._token = token
        return self._bot

    # ── sending ──
    async def announce(self, ref: str) -> int:
        """Send a waiting gate to everyone linked who may decide it, once; tidy the messages of one that
        was answered. Returns how many chats it wrote to."""
        bot = self.bot()
        if bot is None:
            return 0
        async with self.db.session() as s:
            approval = (await s.execute(select(Approval).where(Approval.ref == ref))).scalar_one_or_none()
            if approval is None:
                return 0
            if approval.status != "pending":
                return await self._settle(bot, approval, s)
            if ref in self.sent:
                return 0
            project = await s.get(Project, approval.project_id) if approval.project_id else None
            words, buttons = message_for(approval, project.name if project else "", self.public_url)
            channel = ChannelService(s, self.secrets)
            targets: list[int] = []
            for user_id, chat_id in await channel.linked():
                who = await IdentityService(s).person(user_id)
                if who is not None and await may_decide(s, who, approval):
                    targets.append(chat_id)
        self.sent[ref] = []
        for chat_id in targets:
            try:
                msg = await bot.call("sendMessage", chat_id=chat_id, text=words,
                                     **({"reply_markup": buttons} if buttons else {}))
                self.sent[ref].append(Sent(chat_id, int(msg["message_id"])))
            except BotError as e:
                log.warning("telegram: could not send %s to a linked chat: %s", ref, e)
        return len(self.sent[ref])

    async def _settle(self, bot: Bot, approval: Approval, s: AsyncSession) -> int:
        messages = self.sent.pop(approval.ref, [])
        if not messages:
            return 0
        by = ""
        if approval.decided_by:
            decided = await IdentityService(s).person(approval.decided_by)
            by = f" by {decided.name}" if decided else ""
        note = f"{approval.ref} was {approval.status}{by}."
        for m in messages:
            try:
                await bot.call("editMessageReplyMarkup", chat_id=m.chat_id, message_id=m.message_id,
                               reply_markup={"inline_keyboard": []})
                await bot.call("sendMessage", chat_id=m.chat_id, text=note,
                               reply_parameters={"message_id": m.message_id})
            except BotError:
                pass
        return len(messages)

    # ── what people send ──
    async def handle(self, update: dict[str, Any]) -> None:
        bot = self.bot()
        if bot is None:
            return
        if "callback_query" in update:
            await self._pressed(bot, update["callback_query"])
            return
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        if chat.get("type") != "private" or "text" not in message:
            return                                  # groups are not linked: a gate is one person's
        chat_id, words = int(chat["id"]), str(message["text"]).strip()
        username = (message.get("from") or {}).get("username") or ""
        reply: str
        async with self.db.session() as s:
            channel = ChannelService(s, self.secrets)
            if words.startswith("/start"):
                code = words[len("/start"):].strip()
                user_id = await channel.claim(code, chat_id, username) if code else None
                if user_id:
                    who = await IdentityService(s).person(user_id)
                    reply = (f"Linked to NeuroCode as {who.name if who else 'you'}. "
                             "Gates you may decide will arrive here.")
                    await AuditRepository(s).record(action="channels.telegram.link", user_id=user_id,
                                                    target=f"@{username}" if username else str(chat_id))
                elif code and (linked := await channel.person_for(chat_id)) is not None:
                    reply = (f"This chat is linked to {linked.name}. Unlink it in NeuroCode → Settings → "
                             "Notifications first.")
                else:
                    reply = ("Send the code from NeuroCode → Settings → Notifications. "
                             "A code works once, for ten minutes.")
            else:
                who = await channel.person_for(chat_id)
                if who is None:
                    reply = "This chat is not linked. Make a code in NeuroCode → Settings → Notifications."
                elif words.startswith("/inbox"):
                    reply = await self._inbox(s, who)
                else:
                    reply = "Gates that need you arrive here with their answers. /inbox lists what is waiting."
        await self._say(bot, chat_id, reply)

    async def _inbox(self, s: AsyncSession, who: Person) -> str:
        waiting = (await s.execute(select(Approval).where(Approval.status == "pending")
                                   .order_by(Approval.created_at.desc()).limit(500))).scalars().all()
        mine = [a for a in waiting if await may_decide(s, who, a)]
        if not mine:
            return "Nothing is waiting on you."
        return "Waiting on you:\n" + "\n".join(f"· {a.ref} — {a.title}"[:200] for a in mine[:10])

    async def _pressed(self, bot: Bot, press: dict[str, Any]) -> None:
        chat_id = int(((press.get("message") or {}).get("chat") or {}).get("id") or 0)
        found = CALLBACK.match(str(press.get("data") or ""))
        answer = "That button is not one NeuroCode sent."
        if found and chat_id:
            answer = await self.decide(chat_id, found.group(1), found.group(2))
        try:
            await bot.call("answerCallbackQuery", callback_query_id=press.get("id"), text=answer[:190])
        except BotError:
            pass

    async def decide(self, chat_id: int, ref: str, word: str) -> str:
        """A button pressed: decided exactly as the web decides it, by the person the chat is linked to."""
        resume_with: tuple[str, int, bool] | None = None
        async with self.db.session() as s:
            who = await ChannelService(s, self.secrets).person_for(chat_id)
            if who is None:
                return "This chat is not linked to anyone any more."
            approval = (await s.execute(select(Approval).where(Approval.ref == ref))).scalar_one_or_none()
            if approval is None or not await may_decide(s, who, approval):
                return "Your role cannot decide that gate."
            kind = gate_kind(approval.tool)
            if kind in IN_APP:
                return IN_APP[kind]
            choice = next((b for b in BUTTONS.get(kind, BUTTONS["other"]) if b[0] == word), None)
            if choice is None:
                return "That answer does not fit this gate."
            _, label, decision, scope = choice
            try:
                answered = await ApprovalService(s).decide(ref, decision, by_id=who.id, by_name=who.name,
                                                           scope=scope, who=who)
            except (Refused, Denied) as e:
                return str(e)
            await AuditRepository(s).record(action="approval.telegram", user_id=who.id, target=ref,
                                            detail={"decision": decision, "scope": scope})
            if answered.run_ref:
                resume_with = (answered.run_ref, answered.step or 0, decision == "approve")
        # The decision is committed before the run is told to carry on, as the web route's hand_off does.
        if resume_with is not None:
            self._spawn(self._resume(*resume_with))
        return f"{label}: {ref}."

    async def _resume(self, run_ref: str, step: int, approved: bool) -> None:
        from .runs import resume                   # the runtime imports a lot; only a decision needs it
        await (self.resume or resume)(self.db, self.gateway, run_ref, step, approved)

    def _spawn(self, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self.jobs.add(task)
        task.add_done_callback(self.jobs.discard)

    async def _say(self, bot: Bot, chat_id: int, words: str) -> None:
        try:
            await bot.call("sendMessage", chat_id=chat_id, text=words)
        except BotError as e:
            log.warning("telegram: could not answer a chat: %s", e)

    # ── the loops ──
    async def _lead(self) -> bool:
        """Whether this process is the one that polls. The lock lives on a connection of its own, held for as
        long as the process is up; a second process asks each round and waits."""
        if self._lock_conn is not None:
            try:                                     # a lock is only held while its connection lives
                await self._lock_conn.execute(text("SELECT 1"))
                return True
            except Exception:                        # noqa: BLE001 — the server restarted or killed it
                self._lock_conn = None
        conn = await self.db.engine.connect()
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        got = (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK})).scalar()
        if got:
            self._lock_conn = conn
            return True
        await conn.close()
        return False

    async def poll(self) -> None:
        bot = self.bot()
        if bot is None:
            return
        async with self.db.session() as s:
            offset = int((await _get(s, OFFSET) or {}).get("offset", 0))
        updates = await bot.call("getUpdates", offset=offset, timeout=POLL_SECONDS,
                                 allowed_updates=["message", "callback_query"])
        for update in updates or []:
            try:
                await self.handle(update)
            except Exception:                        # noqa: BLE001 — one bad update must not stop the rest
                log.warning("telegram: an update could not be handled", exc_info=True)
            offset = int(update["update_id"]) + 1
            async with self.db.session() as s:
                await _put(s, OFFSET, {"offset": offset})

    async def _polling(self) -> None:
        quiet = False
        while True:
            try:
                if self.bot() is None or not await self._lead():
                    await asyncio.sleep(IDLE_SECONDS)
                    continue
                await self.poll()
                quiet = False
            except asyncio.CancelledError:
                raise
            except Exception as e:                   # noqa: BLE001 — Telegram or the database away: wait, retry
                if not quiet:
                    log.warning("telegram: polling paused: %s", e)
                quiet = True
                await asyncio.sleep(IDLE_SECONDS)

    async def _sending(self) -> None:
        while True:
            ref = await self.queue.get()
            try:
                await self.announce(ref)
            except Exception:                        # noqa: BLE001
                log.warning("telegram: could not announce %s", ref, exc_info=True)

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.bot()
        await asyncio.gather(self._polling(), self._sending())

    async def stop(self) -> None:
        for task in list(self.jobs):
            task.cancel()
        await asyncio.gather(*self.jobs, return_exceptions=True)
        if self._bot is not None:
            await self._bot.close()
        if self._lock_conn is not None:
            await self._lock_conn.close()
