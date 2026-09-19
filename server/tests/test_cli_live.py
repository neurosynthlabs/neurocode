"""The terminal client against a running API: a real uvicorn on a free local port, a committed database,
and the model a local server speaking the provider's streaming shape. Nothing leaves the machine.

Two layers are proven here. The client layer (`cli/neurocode_cli/client.py`, which needs only httpx) is
imported and driven directly: signing in to make a token, the token's reach, a question answered over the
live stream word by word. Then `nc` itself — the installed command, in a subprocess, with a config folder
of its own and no keychain, so its token lands in a 0600 file — runs the commands a person would, with
`--json`. Those tests are skipped when the client has not been installed into `cli/.venv`.

The database is committed (a live server cannot share the suite's rolled-back transaction), so
everything the module wrote is emptied away afterwards — every table but the catalogue's.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn
from sqlalchemy import text

from app.ai import lanes
from app.ai.gateway import Gateway
from app.ai.ledger import MemoryLedger
from app.api.app import create_api
from app.data.engine import Database
from app.data.loader import sync_agents, sync_roles
from app.events import Bus
from app.models import Project
from app.secrets import Secrets
from app.settings import Settings
from tests.test_gateway import Provider, sse

CLI = Path(__file__).resolve().parents[2] / "cli"
sys.path.insert(0, str(CLI))

from neurocode_cli.client import ApiError, Client, Listener  # noqa: E402 — the client lives beside the server
from neurocode_cli.conversation import Conversation  # noqa: E402

NC = CLI / ".venv" / "bin" / "nc"
needs_nc = pytest.mark.skipif(not NC.exists(), reason="nc is not installed in cli/.venv (uv pip install -e cli)")

OWNER = {"workspace": "Terminal", "name": "Rajat", "email": "cli-owner@example.com",
         "password": "correct horse battery"}
PROJECT = "cli-live"
#: Tables that hold the catalogue, which a server start writes and every test relies on.
KEPT = ("alembic_version", "roles", "role_permissions", "agents")


def answer(words: str) -> list[str]:
    """A streamed answer, in three pieces, as a lane sends it."""
    body = json.dumps({"answer": words})
    third = len(body) // 3
    return sse(*({"choices": [{"delta": {"content": body[i:j]}}]}
                 for i, j in ((0, third), (third, 2 * third), (2 * third, len(body)))),
               {"choices": [{"delta": {}, "finish_reason": "stop"}]})


class Live:
    def __init__(self, url: str, provider: Provider) -> None:
        self.url = url
        self.provider = provider


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _prepare(url: str) -> None:
    db = Database(url=url)
    try:
        async with db.session() as s:
            await sync_roles(s)
            await sync_agents(s)
            s.add(Project(id=PROJECT, name="CLI Live", description="The terminal client's project"))
    finally:
        await db.close()


async def _empty(url: str) -> None:
    db = Database(url=url)
    try:
        async with db.session() as s:
            names = [n for (n,) in (await s.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))).all() if n not in KEPT]
            await s.execute(text("TRUNCATE " + ", ".join(f'"{n}"' for n in names) + " RESTART IDENTITY CASCADE"))
    finally:
        await db.close()


@pytest.fixture(scope="module")
def live(schema: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Live]:
    home = tmp_path_factory.mktemp("claude-home")
    provider = Provider()
    with pytest.MonkeyPatch.context() as patch:
        for lane in lanes.LANES:
            if lane.env:
                patch.delenv(lane.env, raising=False)
        patch.setenv("NEUROCODE_COMPILER", "deepseek")
        patch.setenv("NEUROCODE_CLAUDE_HOME", str(home))
        asyncio.run(_prepare(schema))

        app = create_api(db=Database(url=schema, bus=Bus()))
        app.state.settings = Settings().model_copy(update={"database_url": schema, "test_database_url": schema})
        secrets = Secrets(tmp_path_factory.mktemp("secrets") / "secrets.json")
        secrets.set("deepseek_api_key", "test-key")
        app.state.gateway = Gateway(MemoryLedger({"ai.lane.deepseek": {"baseUrl": provider.url}}), secrets)

        port = _free_port()
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                                               timeout_graceful_shutdown=2, lifespan="on"))
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]  # not the main thread
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 20
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started, "the API did not start"
        url = f"http://127.0.0.1:{port}"
        with Client(url) as first:
            first.post("/auth/setup", OWNER)
        try:
            yield Live(url, provider)
        finally:
            server.should_exit = True
            thread.join(10)
            provider.close()
            asyncio.run(_empty(schema))


def signed_in(url: str) -> Client:
    """A session client, as `nc login` holds one for a moment."""
    client = Client(url)
    client.sign_in(OWNER["email"], OWNER["password"])
    return client


# ── the client layer ─────────────────────────────────────────────
def test_signing_in_makes_a_token_and_the_token_is_the_person_without_a_shell(live: Live):
    with signed_in(live.url) as browser:
        made = browser.make_token("nc on the test machine")
        browser.sign_out()
        with pytest.raises(ApiError) as gone:
            browser.me()
        assert gone.value.status == 401                     # the session made the token, then ended
    assert made["token"].startswith("nc_pat_") and made["allScopes"] is True

    with Client(live.url, made["token"]) as nc:
        me = nc.me()["user"]
        assert me["email"] == OWNER["email"] and "machine:access" not in me["permissions"]
        assert "sessions:chat" in me["permissions"]
        assert PROJECT in [p["id"] for p in nc.projects()]
        with pytest.raises(ApiError) as refused:
            nc.make_token("another")
        assert refused.value.status == 403 and "signed-in session" in refused.value.detail

    with signed_in(live.url) as browser:
        browser.revoke_token(made["id"])
    with Client(live.url, made["token"]) as nc, pytest.raises(ApiError) as dead:
        nc.projects()
    assert dead.value.status == 401 and "not valid" in dead.value.detail


def test_a_question_is_answered_word_by_word_over_the_live_stream(live: Live):
    with signed_in(live.url) as browser:
        token = browser.make_token("stream")["token"]
    live.provider.replies.append((200, answer("Tax is rounded once, on the invoice total.")))
    with Client(live.url, token) as nc:
        listener = Listener(nc).start()
        try:
            assert listener.ready.is_set()
            started = nc.start_session(PROJECT, "tax")
            asked = nc.ask(started["ref"], "where is tax rounded?")
            talk = Conversation(nc, started["ref"], since=int(asked["message"]["id"]))
            pieces = 0
            deadline = time.monotonic() + 20
            while not talk.answered() and time.monotonic() < deadline:
                try:
                    event = listener.events.get(timeout=0.5)
                except Exception:                           # noqa: BLE001 — queue.Empty: read on
                    continue
                update = talk.feed(event)
                if update is not None and update.kind == "stream":
                    pieces += 1
        finally:
            listener.stop()
        assert talk.answered(), "no answer arrived on the stream"
        final = [m for m in talk.after() if m["role"] == "assistant"][-1]
        assert final["text"] == "Tax is rounded once, on the invoice total."
        assert pieces >= 1 and talk.pending.answer in ("", final["text"])
        # The answer is written a moment before the session is marked idle again (think()'s `finally`), so the
        # status is waited for rather than read in that moment.
        deadline = time.monotonic() + 10
        while (stored := nc.session(started["ref"]))["status"] != "idle" and time.monotonic() < deadline:
            time.sleep(0.1)
        assert stored["status"] == "idle" and stored["messages"][-1]["text"] == final["text"]


# ── nc itself ────────────────────────────────────────────────────
def nc(live: Live, home: Path, *args: str, stdin: str = "", env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("NC_")}
    clean.update({"NC_CONFIG_DIR": str(home), "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
                  "NO_COLOR": "1", "COLUMNS": "160", **(env or {})})
    return subprocess.run([str(NC), *args], input=stdin, capture_output=True, text=True, env=clean, timeout=60)


def data(done: subprocess.CompletedProcess) -> Any:
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


@needs_nc
def test_nc_logs_in_keeps_its_token_private_and_works_with_json(live: Live, tmp_path: Path):
    home = tmp_path / "nc"
    signed = data(nc(live, home, "login", live.url, "--email", OWNER["email"], "--password-stdin",
                     "--name", "nc test", "--days", "7", "--json", stdin=OWNER["password"] + "\n"))
    assert signed["server"] == live.url and signed["tokenStore"] == "file"
    assert signed["user"]["email"] == OWNER["email"] and signed["token"]["name"] == "nc test"
    assert "token" not in signed["token"]                              # the secret is never printed
    kept = home / "credentials.json"
    assert stat.S_IMODE(kept.stat().st_mode) == 0o600
    secret = json.loads(kept.read_text())[live.url]
    assert secret.startswith("nc_pat_")

    status = data(nc(live, home, "status", "--json"))
    assert status["user"]["email"] == OWNER["email"] and status["health"]["ok"] is True
    assert status["approvalsPending"] == 0 and status["tokenStore"] == "file"

    projects = data(nc(live, home, "projects", "--json"))
    assert [p["id"] for p in projects] == [PROJECT]
    assert data(nc(live, home, "use", PROJECT, "--json"))["id"] == PROJECT

    added = data(nc(live, home, "memory", "add", "Invoice rounding", "Tax is rounded once on the total.",
                    "--category", "business_rules", "--json"))
    assert added[0]["projectId"] == PROJECT and added[0]["category"] == "business_rules"
    found = data(nc(live, home, "memory", "search", "rounded", "--json"))
    assert [f["ref"] for f in found] == [added[0]["ref"]]

    assert data(nc(live, home, "approvals", "--json")) == []
    assert data(nc(live, home, "runs", "--json")) == []
    assert data(nc(live, home, "plans", "--json")) == []

    refused = nc(live, home, "runs", "RUN-404", "--json")
    assert refused.returncode == 1 and "not found" in refused.stderr

    # The environment wins over what is saved: a script can run with a token it was handed.
    blank = tmp_path / "blank"
    anonymous = nc(live, blank, "projects", "--json", env={"NC_URL": live.url, "NC_TOKEN": "nc_pat_nope"})
    assert anonymous.returncode == 1 and "not valid" in anonymous.stderr
    assert data(nc(live, blank, "projects", "--json", env={"NC_URL": live.url, "NC_TOKEN": secret}))[0]["id"] == PROJECT


@needs_nc
def test_nc_ask_streams_an_answer_and_reports_it_as_json(live: Live, tmp_path: Path):
    home = tmp_path / "nc"
    data(nc(live, home, "login", live.url, "--email", OWNER["email"], "--password-stdin", "--json",
            stdin=OWNER["password"] + "\n"))
    live.provider.replies.append((200, answer("The ledger closes at midnight UTC.")))
    asked = data(nc(live, home, "ask", "when does the ledger close?", "--project", PROJECT, "--json"))
    assert asked["question"]["text"] == "when does the ledger close?"
    said = [m for m in asked["messages"] if m["role"] == "assistant"]
    assert said and said[-1]["text"] == "The ledger closes at midnight UTC."
    assert asked["session"]["projectId"] == PROJECT

    listed = data(nc(live, home, "sessions", "--project", PROJECT, "--json"))
    assert asked["session"]["ref"] in [s["ref"] for s in listed]

    live.provider.replies.append((200, answer("Twice: once per line, then on the total.")))
    shown = nc(live, home, "ask", "and how often is it rounded?", "--session", asked["session"]["ref"])
    assert shown.returncode == 0, shown.stderr
    assert "Twice: once per line, then on the total." in shown.stdout
