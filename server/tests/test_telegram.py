"""Telegram: linking a chat, sending a waiting gate to whoever may decide it, and a button deciding it.

Everything here commits — the relay opens sessions of its own, as it does in the running app — against
a fake Bot API (an httpx transport) that records every call, and cleans up after itself.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text

from app.data.engine import Database
from app.data.loader import sync_roles
from app.events import Bus
from app.models import ActivityEvent, Approval, Project, Setting
from app.secrets import Secrets
from app.services import telegram
from app.services.identity import IdentityService
from app.services.telegram import ChannelService, Relay

PROJECT = "tg-proj"
HIDDEN = "tg-hidden"
TOKEN = "123456789:" + "A" * 35
OWNER = ("tg-owner@example.com", "Asha", "a long enough password")
VIEWER = ("tg-viewer@example.com", "Vik", "another long passphrase")
ENGINEER = ("tg-eng@example.com", "Eli", "a third long passphrase")


class FakeTelegram:
    """The Bot API, as far as this needs it: every call recorded, and a message id for each send."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.next_id = 100
        self.refuse: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content or b"{}")
        self.calls.append((method, body))
        if method in self.refuse:
            return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})
        result: Any = True
        if method == "getMe":
            result = {"id": 999, "is_bot": True, "username": "nc_test_bot"}
        elif method == "sendMessage":
            self.next_id += 1
            result = {"message_id": self.next_id, "chat": {"id": body["chat_id"]}}
        elif method == "getUpdates":
            result = []
        return httpx.Response(200, json={"ok": True, "result": result})

    def sent(self, method: str = "sendMessage") -> list[dict[str, Any]]:
        return [b for m, b in self.calls if m == method]


@pytest_asyncio.fixture
async def world(schema: str, tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    db = Database(url=schema)
    fake = FakeTelegram()
    secrets = Secrets(tmp_path / "secrets.json")
    async with db.session() as s:
        await sync_roles(s)
        ids = IdentityService(s)
        owner = await ids.create(*OWNER, ["owner"])
        viewer = await ids.create(*VIEWER, ["viewer"])
        engineer = await ids.create(*ENGINEER, ["engineer"])
        s.add(Project(id=PROJECT, name="Ledger"))
        s.add(Project(id=HIDDEN, name="Payroll", restricted=True))
    transport = httpx.MockTransport(fake)
    resumed: list[tuple[str, int, bool]] = []

    async def resume(_db: Any, _gw: Any, ref: str, step: int, approved: bool) -> None:
        resumed.append((ref, step, approved))

    relay = Relay(db, None, secrets, Bus(), public_url="https://nc.example.com",  # type: ignore[arg-type]
                  base="https://tg.test", transport=transport, resume=resume)
    try:
        yield {"db": db, "fake": fake, "secrets": secrets, "relay": relay, "transport": transport,
               "owner": owner, "viewer": viewer, "engineer": engineer, "resumed": resumed}
    finally:
        await relay.stop()
        async with db.session() as s:
            await s.execute(delete(Approval).where(Approval.project_id.in_([PROJECT, HIDDEN])))
            await s.execute(delete(Approval).where(Approval.ref.like("TG-%")))
            await s.execute(delete(ActivityEvent).where(ActivityEvent.project_id.in_([PROJECT, HIDDEN])))
            await s.execute(delete(ActivityEvent).where(ActivityEvent.detail.like("TG-%")))
            await s.execute(delete(Project).where(Project.id.in_([PROJECT, HIDDEN])))
            await s.execute(delete(Setting).where(Setting.key.like("channels.telegram%")))
            await s.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
            await s.execute(text("DELETE FROM audit_log"))
            await s.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))
            await s.execute(text("DELETE FROM sessions"))
            await s.execute(text("DELETE FROM user_roles"))
            await s.execute(text("DELETE FROM users"))
        await db.close()


async def _configured(w: dict[str, Any]) -> None:
    async with w["db"].session() as s:
        service = ChannelService(s, w["secrets"], base="https://tg.test", transport=w["transport"])
        await service.configure(TOKEN, w["owner"])


async def _link(w: dict[str, Any], who: Any, chat_id: int) -> None:
    async with w["db"].session() as s:
        code = (await ChannelService(s, w["secrets"]).link_code(who))["code"]
    await w["relay"].handle({"message": {"chat": {"id": chat_id, "type": "private"}, "text": f"/start {code}",
                                         "from": {"username": f"u{chat_id}"}}})


async def _gate(w: dict[str, Any], ref: str, tool: str, project: str | None = PROJECT, **extra: Any) -> None:
    async with w["db"].session() as s:
        s.add(Approval(id=f"id-{ref}", ref=ref, title=f"{tool} for {ref}", agent="Backend Engineer", tool=tool,
                       risk="MEDIUM", status="pending", project_id=project, payload="npm test", **extra))


async def _status(w: dict[str, Any], ref: str) -> Approval:
    async with w["db"].session() as s:
        return (await s.execute(select(Approval).where(Approval.ref == ref))).scalar_one()


def _press(chat_id: int, data: str) -> dict[str, Any]:
    return {"callback_query": {"id": "cb1", "data": data, "message": {"chat": {"id": chat_id}}}}


async def test_a_token_is_checked_with_telegram_before_it_is_kept(world: dict[str, Any]):
    w = world
    async with w["db"].session() as s:
        service = ChannelService(s, w["secrets"], base="https://tg.test", transport=w["transport"])
        try:
            await service.configure("not-a-token", w["owner"])
            raise AssertionError("a malformed token was accepted")
        except telegram.Refused as e:
            assert "not a bot token" in str(e)
        w["fake"].refuse.add("getMe")
        try:
            await service.configure(TOKEN, w["owner"])
            raise AssertionError("a token Telegram refused was kept")
        except telegram.Refused as e:
            assert "Telegram refused that token" in str(e)
        assert w["secrets"].get(telegram.TOKEN_KEY) is None
        w["fake"].refuse.clear()
        status = await service.configure(TOKEN, w["owner"])
    assert status["telegram"]["configured"] is True and status["telegram"]["bot"] == "@nc_test_bot"
    assert w["secrets"].get(telegram.TOKEN_KEY) == TOKEN


async def test_a_chat_is_linked_by_a_code_that_works_once(world: dict[str, Any]):
    w = world
    await _configured(w)
    async with w["db"].session() as s:
        made = await ChannelService(s, w["secrets"]).link_code(w["owner"])
    assert made["url"] == f"https://t.me/nc_test_bot?start={made['code']}"
    start = {"message": {"chat": {"id": 42, "type": "private"}, "text": f"/start {made['code']}",
                         "from": {"username": "asha"}}}
    await w["relay"].handle(start)
    assert w["fake"].sent()[-1]["text"].startswith("Linked to NeuroCode as Asha.")
    await w["relay"].handle({**start, "message": {**start["message"], "chat": {"id": 43, "type": "private"}}})
    assert "A code works once" in w["fake"].sent()[-1]["text"]             # the same code, used again
    async with w["db"].session() as s:
        assert (await ChannelService(s, w["secrets"]).person_for(42)).name == "Asha"
        assert await ChannelService(s, w["secrets"]).person_for(43) is None
    # A group chat is never linked, whatever it sends.
    await w["relay"].handle({"message": {"chat": {"id": -5, "type": "group"}, "text": "/start ABCDEFGH"}})
    assert all(b["chat_id"] != -5 for b in w["fake"].sent())


async def test_a_waiting_gate_goes_only_to_whoever_may_decide_it(world: dict[str, Any]):
    w = world
    await _configured(w)
    await _link(w, w["owner"], 42)
    await _link(w, w["viewer"], 43)            # may use the workspace, may not decide a gate
    await _link(w, w["engineer"], 44)          # holds no approvals:decide either
    await _gate(w, "TG-1", "Command(npm test)")
    before = len(w["fake"].sent())
    assert await w["relay"].announce("TG-1") == 1
    [msg] = w["fake"].sent()[before:]
    assert msg["chat_id"] == 42 and msg["text"].startswith("Needs you · TG-1 · Ledger")
    labels = [b["text"] for row in msg["reply_markup"]["inline_keyboard"] for b in row]
    assert labels == ["Allow once", "Allow for run", "Deny", "Open"]
    assert await w["relay"].announce("TG-1") == 0                          # once, not on every change


async def test_a_button_decides_as_the_web_does_and_only_once(world: dict[str, Any]):
    w = world
    await _configured(w)
    await _link(w, w["owner"], 42)
    await _gate(w, "TG-2", "Bash(make test)", run_ref="RUN-7", step=2)
    await w["relay"].announce("TG-2")
    await w["relay"].handle(_press(42, "g:TG-2:a"))
    assert w["fake"].sent("answerCallbackQuery")[-1]["text"] == "Allow: TG-2."
    decided = await _status(w, "TG-2")
    assert decided.status == "approved" and decided.decided_by == w["owner"].id
    await asyncio.gather(*w["relay"].jobs, return_exceptions=True)
    assert w["resumed"] == [("RUN-7", 2, True)]                              # the run carries on
    await w["relay"].handle(_press(42, "g:TG-2:d"))
    assert "a decision is final" in w["fake"].sent("answerCallbackQuery")[-1]["text"]
    assert (await _status(w, "TG-2")).status == "approved"
    # The answered gate's message loses its buttons and says who answered it.
    await w["relay"].announce("TG-2")
    assert w["fake"].sent("editMessageReplyMarkup")[-1]["reply_markup"] == {"inline_keyboard": []}
    assert w["fake"].sent()[-1]["text"] == "TG-2 was approved by Asha."


async def test_a_signature_and_a_hidden_project_are_never_decided_from_a_chat(world: dict[str, Any]):
    w = world
    await _configured(w)
    await _link(w, w["owner"], 42)
    await _link(w, w["viewer"], 43)
    await _gate(w, "TG-3", "Merge(neurocode/task-3)")
    await w["relay"].announce("TG-3")
    msg = w["fake"].sent()[-1]
    assert "Sign it where you can read the diff." in msg["text"]
    assert [b["text"] for row in msg["reply_markup"]["inline_keyboard"] for b in row] == ["Open"]
    await w["relay"].handle(_press(42, "g:TG-3:a"))                       # a forged button
    assert w["fake"].sent("answerCallbackQuery")[-1]["text"] == "Sign it where you can read the diff."
    assert (await _status(w, "TG-3")).status == "pending"

    await _gate(w, "TG-4", "Command(ls)")
    await w["relay"].handle(_press(43, "g:TG-4:o"))                       # a viewer, forging a press
    assert w["fake"].sent("answerCallbackQuery")[-1]["text"] == "Your role cannot decide that gate."
    await w["relay"].handle(_press(99, "g:TG-4:o"))                       # a chat linked to nobody
    assert w["fake"].sent("answerCallbackQuery")[-1]["text"] == "This chat is not linked to anyone any more."
    await w["relay"].handle(_press(42, "g:TG-4:x"))                       # an answer the gate does not take
    assert w["fake"].sent("answerCallbackQuery")[-1]["text"] == "That answer does not fit this gate."
    assert (await _status(w, "TG-4")).status == "pending"


async def test_the_http_routes_are_fenced(world: dict[str, Any]):
    from app.api.app import create_api
    from app.api import deps
    w = world
    app = create_api(db=w["db"])
    app.state.settings = app.state.settings.model_copy(update={"telegram_url": "https://tg.test"})

    class Gw:
        secrets = w["secrets"]
    app.dependency_overrides[deps.gateway] = lambda: Gw()
    headers = {"X-NC-Client": "test"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api", headers=headers) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER[0], "password": VIEWER[2]})
        assert (await viewer.get("/channels")).json()["telegram"]["configured"] is False
        assert (await viewer.put("/channels/telegram", json={"token": TOKEN})).status_code == 403
        refused = await viewer.post("/channels/telegram/link")
        assert refused.status_code == 409 and "not set up" in refused.json()["detail"]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api", headers=headers) as stranger:
        assert (await stranger.get("/channels")).status_code == 401
