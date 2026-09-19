"""Tool rules: how a decision is weighed, and the routes that write, list, change, remove and try them.

The weighing is tested as the pure function it is, against rules built in memory, so every tie-break
reads as one line. The routes run inside the rolled-back transaction, and a rule written through them
is then decided through `decide` itself — what the runtime will call — so "the screen says allow" and
"the runtime allows" cannot drift apart.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps
from app.api.app import create_api
from app.data.catalogue import ROLE_BY_ID
from app.services.identity import IdentityService
from app.services.tool_rules import decide, weigh
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}


def rule(n: int, tool: str, pattern: str, action: str, project: str | None = None) -> m.ToolRule:
    return m.ToolRule(id=n, tool=tool, pattern=pattern, action=action, project_id=project, note="")


# ── weighing ─────────────────────────────────────────────────────
def test_no_rule_asks():
    decided = weigh([], "command", "npm test")
    assert (decided.action, decided.rule_id) == ("ask", None) and "asks" in decided.why


def test_a_projects_rule_beats_the_workspaces_whatever_their_length():
    rules = [rule(1, "edit", "src/api/payments/*", "deny"), rule(2, "edit", "*", "allow", "erp")]
    assert weigh(rules, "edit", "src/api/payments/charge.py").rule_id == 2


def test_the_longer_pattern_wins_counted_without_wildcards():
    rules = [rule(1, "edit", "src/*", "allow"), rule(2, "edit", "src/api/*", "deny"),
             rule(3, "edit", "**********************", "ask")]
    decided = weigh(rules, "edit", "src/api/tax.py")
    assert (decided.action, decided.rule_id) == ("deny", 2)
    assert weigh(rules, "edit", "src/ui/app.tsx").rule_id == 1


def test_deny_beats_ask_beats_allow_on_a_tie():
    rules = [rule(1, "command", "npm *", "allow"), rule(2, "command", "npm *", "deny"),
             rule(3, "command", "npm *", "ask")]
    assert weigh(rules, "command", "npm publish").action == "deny"
    assert weigh(rules[::2], "command", "npm publish").action == "ask"


def test_a_rule_for_another_tool_never_matches_and_matching_is_case_sensitive():
    rules = [rule(1, "read", "*", "deny"), rule(2, "web_fetch", "https://docs.example.com/*", "allow")]
    assert weigh(rules, "web_fetch", "https://docs.example.com/guide").action == "allow"
    assert weigh(rules, "web_fetch", "https://DOCS.example.com/guide").action == "ask"
    assert weigh(rules, "edit", "README.md").action == "ask"


# ── the routes ───────────────────────────────────────────────────
@pytest_asyncio.fixture
async def api(session: AsyncSession) -> FastAPI:
    await load_workspace(session)
    made = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    made.dependency_overrides[deps.session] = use_the_test_session
    return made


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


async def headers_for(session: AsyncSession, role: str) -> dict[str, str]:
    identity = IdentityService(session)
    person = await identity.create(f"{role}@example.com", role.title(), "correct horse battery", [role])
    return {**HEADERS, "Authorization": f"Bearer {await identity.start_session(person.id)}"}


def test_only_the_owner_and_admin_roles_may_write_rules():
    holders = sorted(r for r, role in ROLE_BY_ID.items() if "rules:manage" in role.permissions)
    assert holders == ["admin", "owner"]


async def test_a_rule_is_written_listed_and_decided_the_way_the_screen_says(client: AsyncClient,
                                                                              session: AsyncSession):
    made = await client.post("/permissions/tool-rules", json={
        "tool": "web_fetch", "pattern": "https://*.internal.example/*", "action": "deny",
        "note": "Never read the intranet"})
    assert made.status_code == 201, made.text
    body = made.json()
    assert set(body) == {"id", "projectId", "projectName", "tool", "pattern", "action", "note", "createdBy",
                         "createdAt", "updatedAt"}
    assert (body["projectId"], body["action"], body["createdBy"]) == (None, "deny", "Rajat")

    listed = (await client.get("/permissions/tool-rules")).json()
    assert [r["id"] for r in listed] == [body["id"]]

    decided = await decide(session, "web_fetch", "https://wiki.internal.example/payroll", None)
    assert (decided.action, decided.rule_id) == ("deny", body["id"])
    tried = (await client.post("/permissions/tool-rules/test", json={
        "tool": "web_fetch", "subject": "https://wiki.internal.example/payroll"})).json()
    assert (tried["action"], tried["ruleId"], tried["rule"]["pattern"]) == ("deny", body["id"],
                                                                              "https://*.internal.example/*")
    assert "Never read the intranet" in tried["why"]
    elsewhere = (await client.post("/permissions/tool-rules/test", json={
        "tool": "web_fetch", "subject": "https://docs.python.org/3/"})).json()
    assert (elsewhere["action"], elsewhere["ruleId"], elsewhere["rule"]) == ("ask", None, None)

    # Written into both logs, with who wrote it.
    audited = (await session.execute(select(m.AuditEntry.action, m.AuditEntry.target).where(
        m.AuditEntry.action.like("tool_rule.%")))).all()
    assert [tuple(a) for a in audited] == [("tool_rule.create", f"tool rule #{body['id']}")]
    said = (await session.execute(select(m.ActivityEvent.detail).where(
        m.ActivityEvent.action == "Tool rule added"))).scalars().all()
    assert said and "https://*.internal.example/*" in said[0]


async def test_a_projects_rule_only_holds_there_and_filters_by_project(client: AsyncClient, session: AsyncSession):
    workspace = (await client.post("/permissions/tool-rules", json={
        "tool": "command", "pattern": "npm *", "action": "ask"})).json()
    erp = (await client.post("/permissions/tool-rules", json={
        "tool": "command", "pattern": "npm test*", "action": "allow", "projectId": "erp"})).json()
    assert erp["projectName"] == "Legacy ERP"                         # named from the project row

    assert (await decide(session, "command", "npm test -- --run", "erp")).rule_id == erp["id"]
    assert (await decide(session, "command", "npm test -- --run", "hims")).rule_id == workspace["id"]
    assert (await decide(session, "command", "npm test", None)).action == "ask"

    only_erp = (await client.get("/permissions/tool-rules", params={"project": "erp"})).json()
    assert [r["id"] for r in only_erp] == [erp["id"]]
    only_workspace = (await client.get("/permissions/tool-rules", params={"project": "workspace"})).json()
    assert [r["id"] for r in only_workspace] == [workspace["id"]]
    everything = (await client.get("/permissions/tool-rules")).json()
    assert [r["id"] for r in everything] == [workspace["id"], erp["id"]]      # workspace first
    assert (await client.get("/permissions/tool-rules", params={"tool": "mcp"})).json() == []
    assert len((await client.get("/permissions/tool-rules", params={"limit": 1})).json()) == 1


async def test_a_rule_is_changed_and_removed_and_each_change_is_audited(client: AsyncClient, session: AsyncSession):
    made = (await client.post("/permissions/tool-rules", json={
        "tool": "mcp", "pattern": "github/*", "action": "ask"})).json()
    changed = await client.patch(f"/permissions/tool-rules/{made['id']}", json={"action": "allow",
                                                                                "pattern": "github/search_*"})
    assert changed.status_code == 200
    assert (changed.json()["action"], changed.json()["pattern"]) == ("allow", "github/search_*")
    assert (await decide(session, "mcp", "github/search_issues", None)).action == "allow"
    assert (await decide(session, "mcp", "github/create_issue", None)).action == "ask"

    removed = await client.delete(f"/permissions/tool-rules/{made['id']}")
    assert removed.status_code == 200 and removed.json() == {"ok": True, "id": made["id"]}
    assert (await client.get("/permissions/tool-rules")).json() == []
    assert (await client.delete(f"/permissions/tool-rules/{made['id']}")).status_code == 404
    audited = (await session.execute(select(m.AuditEntry.action, m.AuditEntry.detail).where(
        m.AuditEntry.action.like("tool_rule.%")).order_by(m.AuditEntry.seq))).all()
    assert [a for a, _ in audited] == ["tool_rule.create", "tool_rule.update", "tool_rule.delete"]
    assert audited[1][1]["action"] == {"from": "ask", "to": "allow"}


async def test_what_cannot_be_a_rule_is_refused_in_words(client: AsyncClient):
    good = {"tool": "edit", "pattern": "src/**", "action": "deny"}
    assert (await client.post("/permissions/tool-rules", json=good)).status_code == 201
    again = await client.post("/permissions/tool-rules", json={**good, "pattern": "./src/**"})
    assert again.status_code == 409 and "already" in again.json()["detail"]      # `./` is the same path
    assert (await client.post("/permissions/tool-rules", json={**good, "tool": "shell"})).status_code == 422
    assert (await client.post("/permissions/tool-rules", json={**good, "action": "maybe"})).status_code == 422
    assert (await client.post("/permissions/tool-rules", json={**good, "pattern": "   "})).status_code == 422
    assert (await client.post("/permissions/tool-rules",
                              json={**good, "pattern": "x", "projectId": "nope"})).status_code == 404
    assert (await client.patch("/permissions/tool-rules/999999", json={"action": "allow"})).status_code == 404
    assert (await client.post("/permissions/tool-rules/test",
                              json={"tool": "edit", "subject": "a", "projectId": "nope"})).status_code == 404


async def test_writing_needs_rules_manage_and_reading_or_trying_needs_a_session(api: FastAPI, client: AsyncClient,
                                                                               session: AsyncSession):
    made = (await client.post("/permissions/tool-rules", json={
        "tool": "read", "pattern": ".env*", "action": "deny"})).json()
    approver = await headers_for(session, "approver")              # signs gates, but writes no rules
    refused = await client.post("/permissions/tool-rules", headers=approver,
                                json={"tool": "read", "pattern": "*", "action": "allow"})
    assert refused.status_code == 403 and "rules:manage" in refused.json()["detail"]
    assert (await client.patch(f"/permissions/tool-rules/{made['id']}", headers=approver,
                               json={"action": "allow"})).status_code == 403
    assert (await client.delete(f"/permissions/tool-rules/{made['id']}", headers=approver)).status_code == 403

    viewer = await headers_for(session, "viewer")
    assert len((await client.get("/permissions/tool-rules", headers=viewer)).json()) == 1
    tried = await client.post("/permissions/tool-rules/test", headers=viewer,
                              json={"tool": "read", "subject": ".env.local"})
    assert tried.status_code == 200 and tried.json()["action"] == "deny"
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as stranger:
        assert (await stranger.get("/permissions/tool-rules")).status_code == 401
