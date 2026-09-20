"""Hooks that may run, and the workspace's own plugin folder.

A hook in a settings file is somebody else's shell script. What is tested here is that this stays true
of it: nothing fires because it is configured, a `hook` rule is the only thing that changes that, the
rule is matched against the command as it is really written rather than the redacted text a screen
shows, and a blocking hook that exits 2 stops what was about to happen. Every firing leaves a line in
the activity feed, including the ones that did not run.

The plugin half is about ownership. Claude Code's plugin cache is read and never written; a plugin this
workspace installed lives in a folder of its own, can be listed with what it brought, and can be removed
again — and what may be installed from is narrow on purpose: an https URL, or a folder inside the
machine's roots.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps
from app.api.app import create_api
from app.services import extensions as ext
from app.services import machine
from app.services.errors import Refused
from app.services.extensions import ExtensionService, fire, matches, registry, workspace_plugins_dir
from app.services.custom_tools import CustomToolService
from app.services.identity import IdentityService
from app.settings import settings as real_settings
from tests.fixtures.workspace import load_workspace

HEADERS = {"X-NC-Client": "test"}
PID = "erp"
#: The one hook the tests allow: a command with a secret in it, so the redaction is under test too.
HOOK_COMMAND = "sh -c 'echo API_TOKEN=abc123secret fired; echo $NEUROCODE_HOOK_EVENT >> fired.log'"


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


@pytest_asyncio.fixture
async def lab(session: AsyncSession, tmp_path: Path,
              monkeypatch: pytest.MonkeyPatch) -> tuple[AsyncSession, Any, Path, Path]:
    """A Claude home with two hooks, a checkout for the ERP, and an empty plugin folder of our own."""
    home = tmp_path / "claude"
    write(home / "settings.json", json.dumps({"hooks": {
        "PreToolUse": [{"matcher": "build|deploy", "hooks": [
            {"type": "command", "command": HOOK_COMMAND, "timeout": 20}]}],
        "Notification": [{"hooks": [{"type": "command", "command": "echo quiet"}]}],
    }}))
    monkeypatch.setenv("NEUROCODE_CLAUDE_HOME", str(home))
    monkeypatch.setenv("NEUROCODE_PLUGINS_DIR", str(tmp_path / "plugins"))

    repo = tmp_path / "erp"
    repo.mkdir()
    await load_workspace(session)
    identity = IdentityService(session)
    made = await identity.create("owner@example.com", "Rajat", "correct horse battery", ["owner"])
    who = await identity.whoami(await identity.start_session(made.id))
    assert who is not None
    project = await session.get(m.Project, PID)
    assert project is not None
    project.source_kind, project.source_repo = "local", str(repo)
    await session.flush()

    configured = real_settings().model_copy(update={"machine_roots": str(tmp_path), "machine_access": True})
    monkeypatch.setattr(machine, "settings", lambda: configured)
    monkeypatch.setattr(ext, "settings", lambda: configured)
    return session, who, repo, tmp_path


async def one_hook(session: AsyncSession, project_id: str | None = PID) -> dict[str, Any]:
    body = await ExtensionService(session).hooks(project_id)
    return next(h for h in body["hooks"] if h["event"] == "PreToolUse")


async def allow(session: AsyncSession, pattern: str) -> m.ToolRule:
    rule = m.ToolRule(project_id=PID, tool="hook", pattern=pattern, action="allow", note="")
    session.add(rule)
    await session.flush()
    return rule


async def actions(session: AsyncSession) -> list[str]:
    return list((await session.execute(
        select(m.ActivityEvent.detail).where(m.ActivityEvent.action == "Hook fired"))).scalars())


# ── which hooks run ──────────────────────────────────────────────
def test_a_matcher_is_a_few_names_separated_by_pipes():
    assert matches("", "build") and matches("*", "anything")
    assert matches("build|deploy", "deploy") and not matches("build|deploy", "test")
    assert matches("Edit*", "EditFile")
    assert not matches("Edit", "")                 # a matcher that narrows, on an event with nothing to narrow


async def test_a_hook_no_rule_allows_is_shown_and_never_runs(lab):
    session, who, repo, _ = lab
    shown = await one_hook(session)
    assert shown["allowed"] is False
    assert "No tool rule allows this hook" in shown["why"]
    # The command reached the screen with its secret taken out, and so did the pattern beside it: the
    # unredacted command is what a rule is matched against, and it never leaves the server.
    assert "abc123secret" not in shown["command"] and "API_TOKEN=***" in shown["command"]
    assert "abc123secret" not in json.dumps(shown)
    assert shown["pattern"] == f"PreToolUse/{shown['command']}"

    fired = await fire(session, "PreToolUse", await session.get(m.Project, PID),
                       {"tool_name": "build"}, subject="build")
    assert len(fired) == 1 and fired[0].ran is False and fired[0].blocked is False
    assert not (repo / "fired.log").exists()
    assert await actions(session) == []             # nothing fired, so nothing is claimed to have fired


async def test_the_pattern_a_person_copies_off_the_screen_really_allows_that_hook(lab):
    """The Hooks screen offers a pattern built from the redacted command. If that pattern did not match
    the real one, the button would be a lie — so this walks the path a person walks."""
    session, who, repo, _ = lab
    await allow(session, (await one_hook(session))["pattern"])

    assert (await one_hook(session))["allowed"] is True
    fired = await fire(session, "PreToolUse", await session.get(m.Project, PID),
                       {"tool_name": "build"}, subject="build")
    assert fired[0].ran is True and (repo / "fired.log").is_file()


async def test_a_rule_for_another_hook_does_not_let_this_one_run(lab):
    """A rule allows one hook, not a class of them. The subject carries the command as the settings file
    writes it, so a pattern written for a different command — or for the same command on another event —
    matches nothing here."""
    session, who, repo, _ = lab
    await allow(session, "PreToolUse/*prettier*")
    await allow(session, "PostToolUse/*fired*")

    fired = await fire(session, "PreToolUse", await session.get(m.Project, PID),
                       {"tool_name": "build"}, subject="build")
    assert fired[0].ran is False
    assert not (repo / "fired.log").exists()


async def test_a_rule_that_allows_one_hook_runs_it_at_its_event_and_logs_the_firing(lab):
    session, who, repo, _ = lab
    await allow(session, "PreToolUse/*fired*")

    assert (await one_hook(session))["allowed"] is True
    fired = await fire(session, "PreToolUse", await session.get(m.Project, PID),
                       {"tool_name": "build"}, subject="build", actor="Rajat")
    assert len(fired) == 1 and fired[0].ran is True and fired[0].exit_code == 0
    assert (repo / "fired.log").read_text().strip() == "PreToolUse"     # it ran in the checkout
    assert "abc123secret" not in fired[0].output                        # even its own output is redacted
    logged = await actions(session)
    assert len(logged) == 1 and "exit 0" in logged[0] and "abc123secret" not in logged[0]


async def test_only_the_hooks_on_that_event_and_matcher_fire(lab):
    session, who, repo, _ = lab
    await allow(session, "*")                       # every hook, on purpose

    assert await fire(session, "PreToolUse", await session.get(m.Project, PID),
                      {"tool_name": "test"}, subject="test") == []     # the matcher says build|deploy
    assert (await fire(session, "Notification", await session.get(m.Project, PID), {}))[0].ran is True


async def test_a_blocking_hook_that_exits_two_refuses_what_was_about_to_happen(lab, monkeypatch, tmp_path):
    session, who, repo, root = lab
    write(tmp_path / "claude" / "settings.json", json.dumps({"hooks": {"PreToolUse": [
        {"hooks": [{"type": "command", "command": "echo 'not on a Friday' && exit 2"}]}]}}))
    await allow(session, "*")

    fired = await fire(session, "PreToolUse", await session.get(m.Project, PID), {"tool_name": "build"},
                       subject="build")
    assert fired[0].ran is True and fired[0].exit_code == 2 and fired[0].blocked is True
    assert "not on a Friday" in fired[0].output
    assert "refused the action" in (await actions(session))[0]


async def test_a_hook_with_machine_access_off_is_allowed_and_still_does_not_run(lab, monkeypatch):
    session, who, repo, root = lab
    await allow(session, "*")
    off = real_settings().model_copy(update={"machine_roots": str(root), "machine_access": False})
    monkeypatch.setattr(ext, "settings", lambda: off)

    fired = await fire(session, "PreToolUse", await session.get(m.Project, PID), {"tool_name": "build"},
                       subject="build")
    assert fired[0].ran is False and "machine access is off" in fired[0].why
    assert not (repo / "fired.log").exists()


async def test_a_blocking_hook_stops_a_custom_tool_before_it_runs(lab, tmp_path):
    """The two halves of this wave meeting: a custom tool is the one place a lifecycle event fires today,
    and a PreToolUse hook a person allowed can refuse the call with its own words. Nothing ran."""
    session, who, repo, root = lab
    write(tmp_path / "claude" / "settings.json", json.dumps({"hooks": {"PreToolUse": [
        {"matcher": "build", "hooks": [{"type": "command", "command": "echo 'not on a Friday' && exit 2"}]}]}}))
    await allow(session, "*")
    script = write(repo / "say.sh", "#!/bin/sh\ntouch ran.log\n")
    script.chmod(0o755)
    made = await CustomToolService(session).create(
        name="build", description="Builds it", kind="command",
        spec={"argv": ["./say.sh"], "arguments": {}}, project_id=PID, who=who)
    row = await session.get(m.CustomTool, made["id"])
    assert row is not None

    with pytest.raises(Refused, match="A hook refused this call before it ran: not on a Friday"):
        await CustomToolService(session).call(row, {}, await session.get(m.Project, PID))
    assert not (repo / "ran.log").exists()
    assert "refused the action" in (await actions(session))[0]


# ── the workspace's own plugin folder ────────────────────────────
def a_plugin(at: Path) -> Path:
    write(at / "skills" / "invoices" / "SKILL.md", "---\nname: Invoices\ndescription: Read one.\n---\nbody\n")
    write(at / "commands" / "bill.md", "---\ndescription: Bill it\n---\nBill $ARGUMENTS\n")
    write(at / ".claude-plugin" / "plugin.json", json.dumps({"name": "billing", "version": "2.0.0"}))
    return at


async def test_a_plugin_is_copied_from_a_folder_listed_with_what_it_brought_and_removed_again(lab):
    session, who, repo, root = lab
    source = a_plugin(root / "source")

    made = await ExtensionService(session).install_plugin(str(source), "billing", who)
    assert made["provides"]["skills"] == 1 and made["provides"]["commands"] == 1
    assert (workspace_plugins_dir() / "billing" / "skills" / "invoices" / "SKILL.md").is_file()

    body = await ExtensionService(session).plugins(PID)
    mine = next(p for p in body["installed"] if p["id"] == "billing@workspace")
    assert mine["workspace"] is True and mine["sourceKind"] == "path" and mine["version"] == "2.0.0"
    assert mine["installCommand"] == ""             # there is no Claude Code command for ours

    # Its skills and commands really reach a session on this project.
    skills = await ExtensionService(session).skills(PID)
    assert "plugin:billing@workspace/invoices" in {s["id"] for s in skills["skills"]}

    await ExtensionService(session).remove_plugin("billing", who)
    assert not (workspace_plugins_dir() / "billing").exists()
    after = await ExtensionService(session).plugins(PID)
    assert not any(p["id"] == "billing@workspace" for p in after["installed"])


async def test_installing_twice_under_one_name_is_refused_and_leaves_the_first_alone(lab):
    session, who, repo, root = lab
    source = a_plugin(root / "source")
    await ExtensionService(session).install_plugin(str(source), "billing", who)

    with pytest.raises(Refused, match="already installed here"):
        await ExtensionService(session).install_plugin(str(source), "billing", who)
    assert (workspace_plugins_dir() / "billing" / "commands" / "bill.md").is_file()


async def test_a_folder_that_is_not_a_plugin_is_refused_and_nothing_is_kept(lab):
    session, who, repo, root = lab
    write(root / "notes" / "todo.txt", "just some notes")

    with pytest.raises(Refused, match="There is no plugin in"):
        await ExtensionService(session).install_plugin(str(root / "notes"), "notes", who)
    assert not (workspace_plugins_dir() / "notes").exists()
    assert list(workspace_plugins_dir().glob(".installing-*")) == []


async def test_only_https_is_cloned_and_only_a_path_inside_the_roots_is_copied(lab):
    session, who, repo, root = lab
    for url in ("git@github.com:acme/x.git", "ssh://git@github.com/acme/x.git", "http://x.example.com/x.git"):
        with pytest.raises(Refused, match="cloned from an https:// URL only"):
            await ExtensionService(session).install_plugin(url, "x", who)
    with pytest.raises(Refused, match="outside the folders this server opens"):
        await ExtensionService(session).install_plugin("/etc", "etc", who)


async def test_a_name_that_is_not_a_folder_name_is_refused_before_anything_is_fetched(lab):
    session, who, repo, root = lab
    source = a_plugin(root / "source")
    for name in ("../escape", "Billing", "a" * 41):
        with pytest.raises(Refused, match="1 to 40 characters of lowercase"):
            await ExtensionService(session).install_plugin(str(source), name, who)


async def test_removing_a_plugin_claude_code_installed_is_not_something_this_server_does(lab):
    session, who, repo, root = lab
    from app.repositories import NotFound

    with pytest.raises(NotFound):
        await ExtensionService(session).remove_plugin("toolbox", who)


async def test_the_registry_is_a_file_and_says_so_when_it_is_not_there(lab):
    session, who, repo, root = lab
    empty = registry()
    assert empty["exists"] is False and empty["plugins"] == [] and empty["path"].endswith("registry.json")

    workspace_plugins_dir().mkdir(parents=True, exist_ok=True)
    write(workspace_plugins_dir() / "registry.json", json.dumps({"name": "Acme's list", "plugins": [
        {"name": "billing", "source": "https://github.com/acme/billing.git", "description": "Invoices"},
        {"no_name": True},                          # half a row is not offered rather than guessed at
    ]}))
    full = registry()
    assert full["exists"] is True and full["name"] == "Acme's list"
    assert [p["name"] for p in full["plugins"]] == ["billing"]

    body = await ExtensionService(session).plugins(PID)
    assert body["registry"]["plugins"][0]["source"] == "https://github.com/acme/billing.git"


# ── the routes ───────────────────────────────────────────────────
@pytest_asyncio.fixture
async def client(lab) -> AsyncIterator[AsyncClient]:
    session, *_ = lab
    app = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[deps.session] = use_the_test_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api", headers=HEADERS) as c:
        signed = await c.post("/auth/login", json={"email": "owner@example.com",
                                                   "password": "correct horse battery"})
        assert signed.status_code == 200, signed.text
        yield c


async def test_the_hooks_screen_says_which_may_run_and_the_button_refuses_the_rest(client: AsyncClient, lab):
    session, who, repo, _ = lab
    body = (await client.get("/extensions/hooks", params={"projectId": PID})).json()
    assert body["allowed"] == 0 and body["canRun"] is True and body["checkout"] == str(repo)
    hook = next(h for h in body["hooks"] if h["event"] == "PreToolUse")

    refused = await client.post(f"/extensions/hooks/{hook['id']}/run", params={"projectId": PID})
    assert refused.status_code == 200 and refused.json()["ran"] is False
    assert not (repo / "fired.log").exists()

    await allow(session, "PreToolUse/*fired*")
    ran = await client.post(f"/extensions/hooks/{hook['id']}/run", params={"projectId": PID})
    assert ran.status_code == 200 and ran.json()["ran"] is True and ran.json()["exitCode"] == 0
    assert (repo / "fired.log").is_file()
    audited = (await session.execute(select(m.AuditEntry.action))).scalars().all()
    assert "hook.run" in audited


async def test_a_plugin_is_installed_and_removed_over_http(client: AsyncClient, lab):
    session, who, repo, root = lab
    source = a_plugin(root / "source")

    made = await client.post("/extensions/plugins/install", json={"source": str(source), "name": "billing"})
    assert made.status_code == 201, made.text
    assert made.json()["provides"]["skills"] == 1

    listed = (await client.get("/extensions/plugins", params={"projectId": PID})).json()
    assert any(p["id"] == "billing@workspace" for p in listed["installed"])
    assert listed["workspaceRoot"].endswith("plugins")

    gone = await client.delete("/extensions/plugins/billing")
    assert gone.status_code == 200 and gone.json()["ok"] is True
    assert (await client.get("/extensions/plugins", params={"projectId": PID})).json()["installed"] == []
