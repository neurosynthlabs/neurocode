"""Skills, commands, hooks and plugins over HTTP: read off a real Claude home on disk, never run.

Each test gets a Claude home of its own in tmp_path, laid out the way Claude Code lays one out, and a
project whose checkout is a real git repository with its own `.claude/`. What is under test is what
the screens are promised: every item comes from a file that exists, every count from a turn a session
really wrote, no secret from a settings file reaches the JSON, and a key or a symlink cannot walk
discovery out of the roots it reads.
"""
from __future__ import annotations

import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps
from app.api.app import create_api
from app.services import chat as chat_service
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
VIEWER = {"email": "view@example.com", "name": "Neha", "password": "another long passphrase", "roles": ["viewer"]}
HEADERS = {"X-NC-Client": "test"}
PID = "extlab"
PLUGIN = "toolbox@acme-market"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={"GIT_AUTHOR_NAME": "Priya Menon", "GIT_AUTHOR_EMAIL": "p@example.com",
                        "GIT_COMMITTER_NAME": "Priya Menon", "GIT_COMMITTER_EMAIL": "p@example.com",
                        "HOME": str(cwd), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"})


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A Claude home: a workspace skill and command, hooks with a secret in them, one installed plugin."""
    root = tmp_path / "claude"
    write(root / "skills" / "pdf" / "SKILL.md",
          "---\nname: PDF reading\ndescription: Read a PDF page by page.\nallowed-tools: Read, Bash(pdftotext:*)\n"
          "version: 1.2.0\n---\n# pdf\n\nOpen the file, then read each page.\n")
    write(root / "skills" / "broken" / "SKILL.md", "---\nname: never closed\n# no end to the front matter\n")
    outside = write(tmp_path / "elsewhere" / "sneaky" / "SKILL.md", "---\nname: sneaky\n---\nnot yours\n")
    (root / "skills" / "sneaky").symlink_to(outside.parent, target_is_directory=True)
    write(root / "commands" / "plan.md",
          "---\ndescription: Plan a change\nargument-hint: <requirement>\nmodel: claude-opus-4\n---\n"
          "Plan $ARGUMENTS carefully. First word: $1.\nStatus: !`git status`\n")
    write(root / "settings.json", json.dumps({
        "env": {"OPENAI_API_KEY": "sk-live-never-shown"},
        "permissions": {"allow": ["Bash(rm:*)"]},
        "enabledPlugins": {PLUGIN: True},
        "hooks": {
            "PreToolUse": [{"matcher": "Write|Edit", "hooks": [
                {"type": "command", "command": "API_TOKEN=abc123secret ./guard.sh --password hunter22",
                 "timeout": 30}]}],
            "Notification": [{"hooks": [{"type": "command", "command": "curl https://bot:tok999@hooks.example.com"}]}],
        },
    }))
    install = root / "plugins" / "cache" / "acme-market" / "toolbox" / "1.0.0"
    write(install / ".claude-plugin" / "plugin.json", json.dumps({"name": "toolbox", "author": {"name": "Acme"}}))
    write(install / "skills" / "pdf" / "SKILL.md",
          "---\nname: pdf\ndescription: The plugin's own PDF skill.\n---\nbody\n")
    write(install / "commands" / "plan.md", "---\ndescription: The plugin's plan\n---\nPlugin plan for $ARGUMENTS\n")
    write(install / "agents" / "reviewer.md", "# reviewer\n")
    write(install / "hooks" / "hooks.json", json.dumps({"description": "Formats after edits.", "hooks": {
        "PostToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "fmt ${FILE}"}]}]}}))
    write(install / ".mcp.json", json.dumps({"mcpServers": {"docs": {"type": "http", "url": "https://x"}}}))
    stray = write(tmp_path / "stray" / "skills" / "evil" / "SKILL.md", "---\nname: evil\n---\nno\n").parents[2]
    write(root / "plugins" / "installed_plugins.json", json.dumps({"version": 2, "plugins": {
        PLUGIN: [{"scope": "user", "installPath": str(install), "version": "1.0.0",
                  "lastUpdated": "2026-09-01T10:00:00.000Z"}],
        "stray@acme-market": [{"scope": "user", "installPath": str(stray), "version": "9"}],
    }}))
    market = root / "plugins" / "marketplaces" / "acme-market"
    write(root / "plugins" / "known_marketplaces.json", json.dumps({"acme-market": {
        "installLocation": str(market), "lastUpdated": "2026-09-02T10:00:00.000Z"}}))
    write(market / ".claude-plugin" / "marketplace.json", json.dumps({"owner": {"name": "Acme"}, "plugins": [
        {"name": "toolbox", "description": "Tools in a box", "category": "productivity"},
        {"name": "linter", "description": "Lints things", "category": "quality", "source": "./plugins/linter"},
        {"name": "remote", "description": "Lives elsewhere", "source": {"source": "url", "url": "https://x.git"}},
    ]}))
    write(market / "plugins" / "linter" / "commands" / "lint.md", "Lint $ARGUMENTS\n")
    write(root / "plugins" / "install-counts-cache.json", json.dumps({
        "fetchedAt": "2026-09-03T00:00:00Z", "counts": [{"plugin": PLUGIN, "unique_installs": 412}]}))
    monkeypatch.setenv("NEUROCODE_CLAUDE_HOME", str(root))
    return root


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The project's checkout: a git repository with a committed project skill and a local hook."""
    root = tmp_path / "repo"
    write(root / ".claude" / "skills" / "deploy" / "SKILL.md", "---\ndescription: Ship it.\n---\nRun the checks.\n")
    write(root / ".claude" / "commands" / "plan.md",
          "---\ndescription: The project's plan\n---\nProject plan: $ARGUMENTS\n")
    write(root / ".claude" / "settings.local.json", json.dumps({"hooks": {"Stop": [{"matcher": "", "hooks": [
        {"type": "command", "command": "make check"}]}]}}))
    git(root, "init", "-q")
    git(root, "add", ".")
    git(root, "commit", "-qm", "skills")
    return root


@pytest_asyncio.fixture
async def api(session: AsyncSession, home: Path, repo: Path, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    await load_workspace(session)
    session.add(m.Project(id=PID, name="Extension Lab", source_kind="local", source_repo=str(repo)))
    await session.flush()
    app = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    async def no_thinking(*_: object) -> None:
        """The answering loop has tests of its own; here only what the route writes is checked."""

    app.dependency_overrides[deps.session] = use_the_test_session
    monkeypatch.setattr(chat_service, "think", no_thinking)
    return app


def _client(api: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


async def _chat(session: AsyncSession, ref: str, by: str) -> m.Chat:
    chat = m.Chat(id=ref.lower(), ref=ref, project_id=PID, title="t", started_by=by)
    session.add(chat)
    await session.flush()
    return chat


# ── skills ───────────────────────────────────────────────────────

async def test_skills_come_from_every_root_and_a_symlink_cannot_leave_one(client: AsyncClient):
    body = (await client.get("/extensions/skills", params={"projectId": PID})).json()
    by_key = {s["id"]: s for s in body["skills"]}
    assert set(by_key) == {"global/pdf", "global/broken", f"project:{PID}/deploy", f"plugin:{PLUGIN}/pdf"}
    assert not any("sneaky" in k or "evil" in k for k in by_key)          # symlink and stray plugin skipped

    pdf = by_key["global/pdf"]
    assert pdf["name"] == "PDF reading" and pdf["scope"] == "global" and pdf["project"] == "all"
    assert pdf["tools"] == ["Read", "Bash(pdftotext:*)"] and pdf["version"] == "1.2.0"
    assert pdf["source"].endswith("skills/pdf/SKILL.md") and "body" not in pdf
    assert pdf["tokens"] == -(-len("# pdf\n\nOpen the file, then read each page.\n") // 4)
    assert pdf["loads24h"] == 0 and pdf["usedBy"] == [] and pdf["lastUsed"] == "" and pdf["rules"] == []

    assert by_key[f"plugin:{PLUGIN}/pdf"]["author"] == "Acme"
    assert by_key[f"project:{PID}/deploy"]["project"] == PID
    assert body["sessions24h"] == 0
    assert any(u.endswith("broken/SKILL.md") for u in body["unreadable"])
    assert {r["scope"] for r in body["roots"]} == {"global", "project", "plugin"}


async def test_a_skill_detail_carries_its_body_and_git_answers_for_a_project_skill(client: AsyncClient):
    detail = (await client.get("/extensions/skills/detail",
                               params={"projectId": PID, "key": f"project:{PID}/deploy"})).json()
    assert detail["body"] == "Run the checks.\n" and detail["truncated"] is False
    assert detail["author"] == "Priya Menon" and len(detail["version"]) >= 7

    for key in ("global/nope", "../../etc/passwd", "global/sneaky"):
        missing = await client.get("/extensions/skills/detail", params={"projectId": PID, "key": key})
        assert missing.status_code == 404
    assert (await client.get("/extensions/skills", params={"projectId": "nope"})).status_code == 404


async def test_loads_and_loaded_by_are_the_turns_sessions_really_wrote(client: AsyncClient, session: AsyncSession):
    chat = await _chat(session, "CHAT-7001", "Arjun Shah")
    session.add_all([
        m.ChatMessage(chat_id=chat.id, role="you", body="read the pdf", by="Arjun Shah"),
        m.ChatMessage(chat_id=chat.id, role="tool", body="…", tool="load_skill", detail="global/pdf", ok=True),
        m.ChatMessage(chat_id=chat.id, role="tool", body="…", tool="load_skill", detail="global/pdf", ok=True),
        m.ChatMessage(chat_id=chat.id, role="tool", body="no", tool="load_skill", detail="refused", ok=False),
    ])
    await session.flush()

    body = (await client.get("/extensions/skills", params={"projectId": PID})).json()
    pdf = next(s for s in body["skills"] if s["id"] == "global/pdf")
    assert pdf["loads24h"] == 2 and pdf["invocations"] == 2 and pdf["usedBy"] == ["Arjun Shah"]
    assert pdf["lastUsed"] and body["sessions24h"] == 1


async def test_a_plugin_kept_out_of_neurocode_leaves_its_skills_and_commands_behind(client: AsyncClient):
    saved = await client.put("/prefs/plugins.installed", json={"value": {PLUGIN: False}, "detail": ""})
    assert saved.status_code == 200
    skills = (await client.get("/extensions/skills", params={"projectId": PID})).json()["skills"]
    assert not any(s["scope"] == "plugin" for s in skills)
    plugins = (await client.get("/extensions/plugins", params={"projectId": PID})).json()
    assert plugins["installed"][0]["usedInNeuroCode"] is False


# ── commands ─────────────────────────────────────────────────────

async def test_commands_show_their_body_their_shell_lines_and_who_wins_a_name(client: AsyncClient):
    body = (await client.get("/extensions/commands", params={"projectId": PID})).json()
    by_key = {c["id"]: c for c in body["commands"]}
    assert set(by_key) == {"global/plan", f"project:{PID}/plan", f"plugin:{PLUGIN}/plan"}
    plan = by_key["global/plan"]
    assert plan["name"] == "/plan" and plan["args"] == "<requirement>" and plan["model"] == "claude-opus-4"
    assert plan["agent"] == "Session" and plan["runs"] == 0 and "$ARGUMENTS" in plan["body"]
    assert by_key[f"plugin:{PLUGIN}/plan"]["name"] == "/toolbox:plan"
    assert body["shellLines"] == {"global/plan": ["git status"]}

    [conflict] = body["conflicts"]
    assert conflict["command"] == "/plan" and conflict["a"] == "project"
    assert "/toolbox:plan" in conflict["resolution"]


async def test_a_command_in_a_session_becomes_a_turn_the_model_receives(client: AsyncClient):
    ref = (await client.post("/sessions", json={"projectId": PID})).json()["ref"]
    asked = await client.post(f"/sessions/{ref}/messages", json={"text": "/toolbox:plan invoice tax"})
    assert asked.status_code == 201 and asked.json()["message"]["text"] == "/toolbox:plan invoice tax"

    turns = (await client.get(f"/sessions/{ref}")).json()["messages"]
    assert [t["role"] for t in turns] == ["you", "tool"]
    assert turns[1]["tool"] == "command" and turns[1]["detail"] == f"plugin:{PLUGIN}/plan"
    assert turns[1]["text"] == "Plugin plan for invoice tax\n"
    assert turns[1]["arguments"] == {"name": "/toolbox:plan", "args": "invoice tax"}

    runs = (await client.get("/extensions/commands", params={"projectId": PID})).json()["commands"]
    assert next(c for c in runs if c["id"] == f"plugin:{PLUGIN}/plan")["runs"] == 1


async def test_a_command_fills_in_its_arguments_and_never_runs_its_shell_lines(client: AsyncClient, repo: Path):
    (repo / ".claude" / "commands" / "plan.md").unlink()
    ref = (await client.post("/sessions", json={"projectId": PID})).json()["ref"]
    await client.post(f"/sessions/{ref}/messages", json={"text": "/plan invoice tax"})
    expansion = (await client.get(f"/sessions/{ref}")).json()["messages"][1]["text"]
    assert expansion.startswith("Plan invoice tax carefully. First word: invoice.")
    assert "[not run by NeuroCode: git status]" in expansion


async def test_a_slash_that_names_no_command_is_just_a_question_and_a_switched_off_one_is_refused(
        client: AsyncClient):
    ref = (await client.post("/sessions", json={"projectId": PID})).json()["ref"]
    plain = await client.post(f"/sessions/{ref}/messages", json={"text": "/etc/hosts, what is it?"})
    assert plain.status_code == 201
    assert [t["role"] for t in (await client.get(f"/sessions/{ref}")).json()["messages"]] == ["you"]

    await client.put("/prefs/commands.enabled", json={"value": {f"project:{PID}/plan": False}})
    other = (await client.post("/sessions", json={"projectId": PID})).json()["ref"]
    refused = await client.post(f"/sessions/{other}/messages", json={"text": "/plan something"})
    assert refused.status_code == 422 and "switched off" in refused.json()["detail"]


# ── hooks ────────────────────────────────────────────────────────

async def test_hooks_are_listed_with_their_secrets_taken_out(client: AsyncClient):
    answer = await client.get("/extensions/hooks", params={"projectId": PID})
    raw = answer.text
    for secret in ("abc123secret", "hunter22", "tok999", "sk-live-never-shown", "Bash(rm:*)"):
        assert secret not in raw

    hooks = answer.json()["hooks"]
    guard = next(h for h in hooks if h["event"] == "PreToolUse")
    assert guard["command"] == "API_TOKEN=*** ./guard.sh --password ***"
    assert guard["blocking"] is True and guard["timeoutS"] == 30 and guard["scope"] == "global"
    assert len(guard["id"]) == 16 and "fires24h" not in guard

    assert next(h for h in hooks if h["event"] == "Notification")["blocking"] is False
    stop = next(h for h in hooks if h["scope"] == "local")
    assert stop["command"] == "make check" and stop["blocking"] is True
    plugin = next(h for h in hooks if h["scope"] == "plugin")
    assert plugin["description"] == "Formats after edits." and plugin["source"] == f"plugin {PLUGIN}"

    again = (await client.get("/extensions/hooks", params={"projectId": PID})).json()["hooks"]
    assert [h["id"] for h in again] == [h["id"] for h in hooks]                  # stable for a switch


# ── plugins ──────────────────────────────────────────────────────

async def test_plugins_are_counted_from_disk_and_install_is_a_command_to_copy(client: AsyncClient):
    body = (await client.get("/extensions/plugins", params={"projectId": PID})).json()
    [installed] = body["installed"]                                           # the stray path is skipped
    assert installed["id"] == PLUGIN and installed["publisher"] == "Acme" and installed["installed"] is True
    assert installed["provides"] == {"agents": 1, "skills": 1, "commands": 1, "hooks": 1, "mcp": 1}
    assert installed["stars"] == 412 and installed["enabledInClaude"] is True
    assert installed["category"] == "productivity"
    assert installed["installCommand"] == f"/plugin uninstall {PLUGIN}"
    assert body["countsFetchedAt"] == "2026-09-03T00:00:00Z"

    market = {p["id"]: p for p in body["marketplace"]}
    assert set(market) == {"linter@acme-market", "remote@acme-market"}
    assert market["linter@acme-market"]["providesKnown"] is True
    assert market["linter@acme-market"]["provides"]["commands"] == 1
    assert market["remote@acme-market"]["providesKnown"] is False
    assert market["linter@acme-market"]["installCommand"] == "/plugin install linter@acme-market"
    assert body["conflicts"][0]["command"] == "/plan"


# ── no project yet ───────────────────────────────────────────────

async def test_with_no_project_the_screens_read_this_machine_alone(client: AsyncClient, session: AsyncSession):
    """A new workspace has no project, and the Claude home is real before one exists. Leaving projectId
    out reads that home and nothing a project owns: no project root, no project skill, no session count."""
    chat = await _chat(session, "CHAT-7101", "Arjun Shah")
    session.add(m.ChatMessage(chat_id=chat.id, role="tool", body="…", tool="load_skill", detail="global/pdf", ok=True))
    await session.flush()

    skills = (await client.get("/extensions/skills")).json()
    assert {s["id"] for s in skills["skills"]} == {"global/pdf", "global/broken", f"plugin:{PLUGIN}/pdf"}
    assert {r["scope"] for r in skills["roots"]} == {"global", "plugin"}
    # The load belongs to a project's session, so it is not counted where no project was asked about.
    assert skills["sessions24h"] == 0 and all(s["loads24h"] == 0 for s in skills["skills"])
    detail = await client.get("/extensions/skills/detail", params={"key": "global/pdf"})
    assert detail.status_code == 200 and "Open the file" in detail.json()["body"]
    assert (await client.get("/extensions/skills/detail", params={"key": f"project:{PID}/deploy"})).status_code == 404

    commands = (await client.get("/extensions/commands")).json()
    assert {c["scope"] for c in commands["commands"]} == {"global", "plugin"}

    hooks = (await client.get("/extensions/hooks")).json()["hooks"]
    assert hooks and not any(h["scope"] in ("project", "local") for h in hooks)

    plugins = (await client.get("/extensions/plugins")).json()
    assert [p["id"] for p in plugins["installed"]] == [PLUGIN]

    # Named and missing is still a mistake, and an empty id is not "none".
    assert (await client.get("/extensions/hooks", params={"projectId": "nope"})).status_code == 404
    assert (await client.get("/extensions/hooks", params={"projectId": ""})).status_code == 422


# ── who may ──────────────────────────────────────────────────────

async def test_reading_needs_a_session_and_a_viewer_may_read_but_not_switch_or_ask(api: FastAPI, client: AsyncClient):
    async with _client(api) as stranger:
        for path in ("skills", "commands", "hooks", "plugins"):
            assert (await stranger.get(f"/extensions/{path}", params={"projectId": PID})).status_code == 401

    await client.post("/admin/users", json=VIEWER)
    ref = (await client.post("/sessions", json={"projectId": PID})).json()["ref"]
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.get("/extensions/skills", params={"projectId": PID})).status_code == 200
        assert (await viewer.put("/prefs/skills.enabled", json={"value": {"global/pdf": False}})).status_code == 403
        assert (await viewer.post(f"/sessions/{ref}/messages", json={"text": "/plan x"})).status_code == 403


@pytest.mark.parametrize(("command", "secret"), [
    ('curl -H "x-api-key: sk-ant-api03-SECRETVALUEabcdefghij" https://x', "SECRETVALUE"),
    ('curl -H "Authorization: Basic dXNlcjpwYXNz" https://x', "dXNlcjpwYXNz"),
    ("curl -u admin:hunter22 https://x", "hunter22"),
    ('mysql -psecretpw -e "select 1"', "secretpw"),
    ("notify --webhook https://hooks.slack.com/services/T000/B000/XXXXSECRET", "XXXXSECRET"),
    ("echo ghp_abcdefghijklmnopqrstuvwxyz0123 | gh auth login --with-token", "ghp_abcdef"),
    ("curl -H 'Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload' https://x", "eyJhbGci"),
    ("TOKEN=abc123 ./deploy.sh", "abc123"),
])
def test_a_hook_command_never_carries_its_secret_to_the_page(command: str, secret: str):
    """The Hooks page is open to a Viewer, and a hook is where people paste a webhook with its key."""
    from app.services.extensions import redact

    shown = redact(command)
    assert secret not in shown and "***" in shown


@pytest.mark.parametrize("command", ["npm run lint -- --fix src/", "python -m pytest -p no:cacheprovider tests/",
                                     "prettier --write \"$CLAUDE_FILE_PATHS\""])
def test_a_hook_command_with_nothing_secret_comes_through_whole(command: str):
    from app.services.extensions import redact

    assert redact(command) == command
