"""The Telegram relay held to the web route it mirrors, found by an adversarial review: a disabled account,
a token scoped to less, a narrowed per-project role, a new bot's updates, a dead lock connection, a long
inbox and a chat tapped with someone else's code. Each test names the rule it holds. The fixture is
`test_telegram.world`'s, with an Approver (approvals:decide, not an admin) added.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, text

from app import models as m
from app.data.engine import Database
from app.data.loader import sync_roles
from app.events import Bus
from app.models import ActivityEvent, Approval, Project, Setting
from app.secrets import Secrets

from app.services.identity import IdentityService
from app.services.telegram import ChannelService, Relay
from app.services.tokens import TokenService
from tests.test_telegram import FakeTelegram

PROJECT = "tga-proj"
HIDDEN = "tga-hidden"
TOKEN = "123456789:" + "A" * 35
OTHER_TOKEN = "987654321:" + "B" * 35
OWNER = ("tga-owner@example.com", "Asha", "a long enough password")
APPROVER = ("tga-appr@example.com", "Priya", "another long passphrase")


class Fake(FakeTelegram):
    """Two bots, told apart by the token in the path, and updates a test queues for getUpdates."""

    def __init__(self) -> None:
        super().__init__()
        self.updates: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = path.rsplit("/", 1)[-1]
        if method == "getMe":
            self.calls.append((method, {}))
            bot = 1000 if OTHER_TOKEN in path else 999
            return httpx.Response(200, json={"ok": True, "result": {"id": bot, "is_bot": True,
                                                                    "username": f"bot{bot}"}})
        if method == "getUpdates":
            body = json.loads(request.content or b"{}")
            self.calls.append((method, body))
            out, self.updates = self.updates, []
            return httpx.Response(200, json={"ok": True, "result": out})
        return super().__call__(request)


@pytest_asyncio.fixture
async def w(schema: str, tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    db = Database(url=schema)
    fake = Fake()
    secrets = Secrets(tmp_path / "secrets.json")
    async with db.session() as s:
        await sync_roles(s)
        ids = IdentityService(s)
        owner = await ids.create(*OWNER, ["owner"])
        approver = await ids.create(*APPROVER, ["approver"])
        s.add(Project(id=PROJECT, name="Ledger"))
        s.add(Project(id=HIDDEN, name="Payroll", restricted=True))
    transport = httpx.MockTransport(fake)
    resumed: list[tuple[str, int, bool]] = []

    async def resume(_db: Any, _gw: Any, ref: str, step: int, approved: bool) -> None:
        resumed.append((ref, step, approved))

    relay = Relay(db, None, secrets, Bus(), public_url="https://nc.example.com",  # type: ignore[arg-type]
                  base="https://tg.test", transport=transport, resume=resume)
    extra: list[Relay] = []
    try:
        yield {"db": db, "fake": fake, "secrets": secrets, "relay": relay, "transport": transport,
               "owner": owner, "approver": approver, "resumed": resumed, "extra": extra}
    finally:
        for r in [relay, *extra]:
            try:
                await r.stop()
            except Exception:  # noqa: BLE001 — a terminated lock connection may not close cleanly
                pass
        async with db.session() as s:
            await s.execute(delete(Approval).where(Approval.project_id.in_([PROJECT, HIDDEN])))
            await s.execute(delete(Approval).where(Approval.ref.like("TGA-%")))
            await s.execute(delete(ActivityEvent).where(ActivityEvent.project_id.in_([PROJECT, HIDDEN])))
            await s.execute(delete(ActivityEvent).where(ActivityEvent.detail.like("TGA-%")))
            await s.execute(delete(m.ProjectRole).where(m.ProjectRole.project_id.in_([PROJECT, HIDDEN])))
            await s.execute(delete(Project).where(Project.id.in_([PROJECT, HIDDEN])))
            await s.execute(delete(Setting).where(Setting.key.like("channels.telegram%")))
            await s.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
            await s.execute(text("DELETE FROM audit_log"))
            await s.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))
            await s.execute(text("DELETE FROM api_tokens"))
            await s.execute(text("DELETE FROM sessions"))
            await s.execute(text("DELETE FROM user_roles"))
            await s.execute(text("DELETE FROM users"))
        await db.close()


async def _configured(w: dict[str, Any], token: str = TOKEN) -> None:
    async with w["db"].session() as s:
        await ChannelService(s, w["secrets"], base="https://tg.test", transport=w["transport"]).configure(
            token, w["owner"])


async def _link(w: dict[str, Any], who: Any, chat_id: int) -> None:
    async with w["db"].session() as s:
        code = (await ChannelService(s, w["secrets"]).link_code(who))["code"]
    await w["relay"].handle({"message": {"chat": {"id": chat_id, "type": "private"}, "text": f"/start {code}",
                                         "from": {"username": f"u{chat_id}"}}})


async def _gate(w: dict[str, Any], ref: str, tool: str, project: str | None = PROJECT, **extra: Any) -> None:
    async with w["db"].session() as s:
        s.add(Approval(id=f"id-{ref}", ref=ref, title=f"{tool} for {ref}", agent="Backend Engineer", tool=tool,
                       risk="MEDIUM", status="pending", project_id=project, payload="SECRET PAYLOAD", **extra))


async def _status(w: dict[str, Any], ref: str) -> str:
    async with w["db"].session() as s:
        return (await s.execute(select(Approval.status).where(Approval.ref == ref))).scalar_one()


def _press(chat_id: int, data: str) -> dict[str, Any]:
    return {"callback_query": {"id": "cb1", "data": data, "message": {"chat": {"id": chat_id}}}}


def _app(w: dict[str, Any]):
    from app.api import deps
    from app.api.app import create_api
    app = create_api(db=w["db"])
    app.state.settings = app.state.settings.model_copy(update={"telegram_url": "https://tg.test"})

    class Gw:
        secrets = w["secrets"]
    app.dependency_overrides[deps.gateway] = lambda: Gw()
    return app


# ── 1. a disabled account keeps its chat, and its chat keeps deciding ──────────────────────────
async def test_a_disabled_person_can_no_longer_decide_from_their_chat(w: dict[str, Any]):
    await _configured(w)
    await _link(w, w["approver"], 50)
    async with w["db"].session() as s:
        await IdentityService(s).update(w["approver"].id, actor=w["owner"], status="disabled")
    await _gate(w, "TGA-1", "Command(npm test)")
    before = len(w["fake"].sent())
    sent = await w["relay"].announce("TGA-1")
    to_disabled = [b for b in w["fake"].sent()[before:] if b["chat_id"] == 50]
    await w["relay"].handle(_press(50, "g:TGA-1:o"))
    answer = w["fake"].sent("answerCallbackQuery")[-1]["text"]
    status = await _status(w, "TGA-1")
    # A disabled account is signed out everywhere and the web answers it 401; its chat must be the same.
    assert (sent, len(to_disabled), status) == (0, 0, "pending"), (
        f"disabled person was sent {len(to_disabled)} gate message(s) and the press answered {answer!r}; "
        f"gate is now {status}")


# ── 2. a narrowly scoped access token mints a chat link that decides with the person's full rights ──
async def test_a_token_without_approvals_decide_cannot_link_a_chat_that_decides(w: dict[str, Any]):
    await _configured(w)
    async with w["db"].session() as s:
        _row, pat = await TokenService(s).create(w["owner"], name="read only", scopes=["ops:read"],
                                                 expires_days=None)
    await _gate(w, "TGA-2", "Command(npm test)")
    app = _app(w)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api",
                           headers={"Authorization": f"Bearer {pat}"}) as client:
        web = await client.post("/approvals/TGA-2/approve")
        linked = await client.post("/channels/telegram/link")
    assert web.status_code == 403                                   # the token may not decide on the web
    if linked.status_code == 200:                                   # ...but it may make a link code
        await w["relay"].handle({"message": {"chat": {"id": 66, "type": "private"},
                                             "text": f"/start {linked.json()['code']}", "from": {}}})
        await w["relay"].handle(_press(66, "g:TGA-2:o"))
    status = await _status(w, "TGA-2")
    assert linked.status_code in (401, 403) and status == "pending", (
        f"an ops:read token got {linked.status_code} from POST /channels/telegram/link and the chat it "
        f"linked decided the gate: {status}")


# ── 3. a different bot's token keeps the old bot's getUpdates offset ─────────────────────────────
async def test_a_new_bot_starts_from_its_own_updates(w: dict[str, Any]):
    await _configured(w)
    w["fake"].updates = [{"update_id": 900_000_000, "message": {"chat": {"id": 7, "type": "private"},
                                                                "text": "hi", "from": {}}}]
    await w["relay"].poll()
    await _configured(w, OTHER_TOKEN)                                # the admin pastes another bot's token
    await w["relay"].poll()
    asked = [b for m_, b in w["fake"].calls if m_ == "getUpdates"][-1]
    # Update ids are per bot: asking bot 1000 for offset 900000001 confirms (drops) every update of its
    # below that number, so linking and every button go silent.
    assert asked["offset"] == 0, f"the new bot was polled with the old bot's offset {asked['offset']}"


# ── 4. a narrowed per-project role: the relay matches the web route, and neither narrows ─────────
async def _narrow(w: dict[str, Any]) -> None:
    async with w["db"].session() as s:
        s.add(m.ProjectRole(project_id=HIDDEN, user_id=w["approver"].id, role_id="viewer"))
    async with w["db"].session() as s:
        who = await IdentityService(s).person(w["approver"].id)
    assert who is not None and "approvals:decide" not in who.in_project(HIDDEN, True)
    assert who.may_see(HIDDEN, True)


async def test_a_grant_that_narrows_away_approvals_decide_stops_the_chat(w: dict[str, Any]):
    await _configured(w)
    await _narrow(w)
    await _link(w, w["approver"], 51)
    await _gate(w, "TGA-4", "Command(ls)", project=HIDDEN)
    sent = await w["relay"].announce("TGA-4")
    await w["relay"].handle(_press(51, "g:TGA-4:o"))
    assert (sent, await _status(w, "TGA-4")) == (0, "pending"), \
        f"narrowed approver was sent {sent} message(s); gate is {await _status(w, 'TGA-4')}"


async def test_a_grant_that_narrows_away_approvals_decide_stops_the_web_route(w: dict[str, Any]):
    await _narrow(w)
    await _gate(w, "TGA-5", "Command(ls)", project=HIDDEN)
    app = _app(w)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api",
                           headers={"X-NC-Client": "test"}) as client:
        await client.post("/auth/login", json={"email": APPROVER[0], "password": APPROVER[2]})
        web = await client.post("/approvals/TGA-5/approve")
    assert web.status_code in (403, 404), f"web route let the narrowed approver decide: {web.status_code}"


# ── 5. the leader lock is never re-checked after its connection dies ──────────────────────────
async def test_one_leader_after_the_lock_connection_dies(w: dict[str, Any]):
    await _configured(w)
    a = w["relay"]
    assert await a._lead()
    pid = (await a._lock_conn.execute(text("SELECT pg_backend_pid()"))).scalar()
    async with w["db"].engine.connect() as admin:
        await admin.execute(text("SELECT pg_terminate_backend(:p)"), {"p": pid})
    b = Relay(w["db"], None, w["secrets"], Bus(), base="https://tg.test", transport=w["transport"])  # type: ignore[arg-type]
    w["extra"].append(b)
    both = (await a._lead(), await b._lead())
    assert both != (True, True), "two processes both believe they hold the poller lock"


# ── 6. /inbox cuts to 20 before it filters, so hidden gates hide visible ones ────────────────────
async def test_inbox_lists_a_visible_gate_behind_twenty_hidden_ones(w: dict[str, Any]):
    await _configured(w)
    await _link(w, w["approver"], 52)                                # no grant in HIDDEN: may not see it
    await _gate(w, "TGA-100", "Command(ls)")                         # older, and theirs to decide
    for i in range(21):
        await _gate(w, f"TGA-{200 + i}", "Command(ls)", project=HIDDEN)
    await w["relay"].handle({"message": {"chat": {"id": 52, "type": "private"}, "text": "/inbox", "from": {}}})
    reply = w["fake"].sent()[-1]["text"]
    assert "TGA-100" in reply, f"/inbox answered {reply!r}"


# ── 7. a code sent from someone else's chat moves that chat to the code's owner (deep-link CSRF) ─
async def test_a_chat_linked_to_one_person_is_not_silently_moved_by_a_code(w: dict[str, Any]):
    await _configured(w)
    await _link(w, w["owner"], 42)                                   # Asha's phone
    await _link(w, w["approver"], 42)                                # Priya's code, tapped in Asha's chat
    async with w["db"].session() as s:
        now = await ChannelService(s, w["secrets"]).person_for(42)
    assert now is not None and now.name == "Asha", f"chat 42 now acts as {now.name if now else None}"
