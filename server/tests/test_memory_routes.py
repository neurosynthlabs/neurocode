"""Memory's measured side: every recall is a row, archived facts say when, and a person can file the
contradiction nothing else would notice.

The routes run inside the rolled-back transaction. The session test does not: `think` opens sessions of
its own, so its rows are committed for real and deleted afterwards — the facts' recalls go with them,
through their foreign key.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.ai.gateway import Gateway, Provider, Result
from app.ai.ledger import MemoryLedger
from app.api import deps
from app.api.app import create_api
from app.data.engine import Database
from app.repositories.knowledge import HITS_CEILING
from app.repositories import ChatRepository
from app.secrets import Secrets
from app.services.chat import think
from app.services.identity import IdentityService
from app.services.knowledge import MemoryService
from tests.fixtures.workspace import load_workspace, rows

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}


@pytest_asyncio.fixture
async def client(session: AsyncSession, monkeypatch, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("NEUROCODE_COMPILER", "rules")      # no lane may answer: nothing leaves the machine
    await load_workspace(session)
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    api.dependency_overrides[deps.session] = use_the_test_session
    api.dependency_overrides[deps.gateway] = lambda: Gateway(MemoryLedger(), Secrets(tmp_path / "secrets.json"))
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


async def viewer_headers(session: AsyncSession) -> dict[str, str]:
    identity = IdentityService(session)
    person = await identity.create("viewer@example.com", "Vik", "correct horse battery", ["viewer"])
    return {**HEADERS, "Authorization": f"Bearer {await identity.start_session(person.id)}"}


def fact(facts: list[dict[str, Any]], ref: str) -> dict[str, Any]:
    return next(f for f in facts if f["ref"] == ref)


# ── recalls ──────────────────────────────────────────────────────
async def test_a_fact_carries_counted_use_and_no_invented_strength(client: AsyncClient):
    facts = (await client.get("/memory")).json()
    assert facts
    for f in facts:
        assert "strength" not in f and "hits" not in f
        assert f["hits24h"] == 0                            # nothing has recalled anything in this test
        assert f["archived"] is False and f["archivedAt"] is None
    assert (await client.get("/memory/hits")).json() == []


async def test_an_answer_records_one_recall_per_fact_it_cited(client: AsyncClient):
    answered = (await client.post("/ai/ask", json={"question": "Where does invoice rounding happen?"})).json()
    cited = [c["ref"] for c in answered["citations"]]
    assert cited

    hits = (await client.get("/memory/hits")).json()
    assert sorted(h["ref"] for h in hits) == sorted(cited)
    assert all(h["feature"] == "ask" and h["context"] is None and h["at"] for h in hits)
    assert {h["title"] for h in hits} == {rows("memory", ref=r)[0]["title"] for r in cited}

    facts = (await client.get("/memory")).json()
    for ref in cited:
        assert fact(facts, ref)["hits24h"] == 1 and fact(facts, ref)["lastUsedAt"]

    await client.post("/ai/ask", json={"question": "Where does invoice rounding happen?"})
    again = (await client.get("/memory")).json()
    assert fact(again, cited[0])["hits24h"] == 2            # a second answer is a second recall
    assert len((await client.get("/memory/hits", params={"limit": 1})).json()) == 1


async def test_a_recall_counts_each_fact_once_and_ignores_what_is_not_a_fact(seeded: AsyncSession):
    ref = rows("memory")[0]["ref"]
    used = await MemoryService(seeded).recall([ref, ref, "src/tax.py#apply:3", ""], via="chat",
                                              context="CHAT-1")
    assert [f.ref for f in used] == [ref]
    counted = (await seeded.execute(select(func.count(m.MemoryHit.id)))).scalar_one()
    assert counted == 1
    assert used[0].hits_24h == 1 and used[0].last_used_at is not None
    assert await MemoryService(seeded).recall(["NOT-A-FACT"], via="chat", context=None) == []


async def test_the_recall_list_has_a_ceiling(client: AsyncClient, session: AsyncSession):
    assert (await client.get("/memory/hits", params={"limit": HITS_CEILING + 1})).status_code == 422
    assert (await client.get("/memory/hits", params={"limit": 0})).status_code == 422
    assert (await client.get("/memory/hits", headers=await viewer_headers(session))).status_code == 200


async def test_a_session_records_what_grounding_and_its_tools_handed_the_model(schema: str):
    """Grounding hands the model a fact, and a memory tool hands it the same fact and one more: the
    first is one recall by retrieval, the second one by the session — never two of the same fact."""
    db = Database(url=schema)
    project, ref = "recall-test-project", "CHAT-RECALL-1"
    async with db.session() as s:
        s.add(m.Project(id=project, name="Recall Test"))
    async with db.session() as s:
        s.add_all([
            m.MemoryFact(id="rc-1", ref="RCMEM-1", category="business_rules", project_id=project,
                         title="Zephyrquill rounding", body="Zephyrquill totals round once."),
            m.MemoryFact(id="rc-2", ref="RCMEM-2", category="decisions", project_id=project,
                         title="Zephyrquill ledger", body="Zephyrquill posts through the ledger."),
            m.Chunk(project_id=project, kind="memory", ref="RCMEM-1", path="business_rules",
                    title="Zephyrquill rounding", body="Zephyrquill totals round once."),
        ])
        chats = ChatRepository(s)
        chat = await chats.add(m.Chat(id="recall-1", ref=ref, project_id=project, title="zephyrquill",
                                      started_by="Rajat", status="thinking"))
        await chats.say(chat.id, role="you", body="zephyrquill", by="Rajat")

    class Scripted:
        script = ['{"tool": "search_memory", "arguments": {"query": "zephyrquill"}}', '{"answer": "Once."}']

        def embed_lane(self) -> None:
            return None

        def ask(self, _messages: Any, parse: Any, **_: Any) -> Result[Any]:
            return Result(parse(self.script.pop(0)), Provider("groq", "llama"), 5)

    try:
        await think(db, Scripted(), ref, "Rajat")  # type: ignore[arg-type]
        async with db.read() as s:
            recalled = (await s.execute(
                select(m.MemoryFact.ref, m.MemoryHit.feature, m.MemoryHit.ref)
                .join(m.MemoryFact, m.MemoryFact.id == m.MemoryHit.fact_id)
                .where(m.MemoryFact.project_id == project).order_by(m.MemoryFact.ref))).all()
        assert [tuple(r) for r in recalled] == [("RCMEM-1", "retrieval", ref), ("RCMEM-2", "chat", ref)]
    finally:
        async with db.session() as s:
            await s.execute(delete(m.Chat).where(m.Chat.project_id == project))
            await s.execute(delete(m.Project).where(m.Project.id == project))
        await db.close()


# ── archives ─────────────────────────────────────────────────────
async def test_an_archived_fact_says_when_and_is_listed_only_when_asked_for(client: AsyncClient):
    ref = (await client.get("/memory")).json()[0]["ref"]
    archived = (await client.post(f"/memory/{ref}/archive")).json()
    assert archived["archived"] is True and archived["archivedAt"]

    assert all(f["ref"] != ref for f in (await client.get("/memory")).json())
    kept = fact((await client.get("/memory", params={"include_archived": True})).json(), ref)
    assert kept["archived"] is True and kept["archivedAt"] == archived["archivedAt"]


# ── conflicts a person files ─────────────────────────────────────
async def test_a_person_files_a_conflict_and_it_is_listed_like_any_other(client: AsyncClient):
    a, b = (f["ref"] for f in (await client.get("/memory")).json()[:2])
    made = await client.post("/memory/conflicts", json={"a": a, "b": b, "topic": "Rounding",
                                                         "detail": "One says per line, one per invoice.",
                                                         "severity": "high"})
    assert made.status_code == 201
    doc = made.json()
    facts = (await client.get("/memory")).json()
    assert doc["a"] == fact(facts, a)["id"] and doc["b"] == fact(facts, b)["id"]
    assert doc["severity"] == "HIGH" and doc["status"] == "open" and doc["detected"]
    listed = next(c for c in (await client.get("/memory/conflicts")).json() if c["id"] == doc["id"])
    assert listed == doc

    # The same pair, either way round, is already filed while the first is open.
    twice = await client.post("/memory/conflicts", json={"a": b, "b": a, "topic": "Again"})
    assert twice.status_code == 409 and "already marked" in twice.json()["detail"]

    # Settled, the pair can be filed again: a new contradiction between the same two is a new ruling.
    assert (await client.post(f"/memory/conflicts/{doc['id']}/resolve", json={"keep": "a"})).status_code == 200
    kept = fact((await client.get("/memory", params={"include_archived": True})).json(), b)
    assert kept["archived"] is True


async def test_a_conflict_needs_two_real_different_facts(client: AsyncClient):
    ref = (await client.get("/memory")).json()[0]["ref"]
    same = await client.post("/memory/conflicts", json={"a": ref, "b": ref, "topic": "Self"})
    assert same.status_code == 422
    unknown = await client.post("/memory/conflicts", json={"a": ref, "b": "MEM-999999", "topic": "Ghost"})
    assert unknown.status_code == 404
    bad = await client.post("/memory/conflicts", json={"a": ref, "b": "MEM-1", "topic": "x", "severity": "urgent"})
    assert bad.status_code == 422


async def test_filing_a_conflict_needs_memory_write(client: AsyncClient, session: AsyncSession):
    a, b = (f["ref"] for f in (await client.get("/memory")).json()[:2])
    refused = await client.post("/memory/conflicts", json={"a": a, "b": b, "topic": "Rounding"},
                                headers=await viewer_headers(session))
    assert refused.status_code == 403 and "memory:write" in refused.json()["detail"]


async def test_a_conflict_is_settled_by_keeping_a_side_not_by_an_adr_nobody_writes(client: AsyncClient):
    conflict = (await client.get("/memory/conflicts")).json()[0]
    assert (await client.post(f"/memory/conflicts/{conflict['id']}/resolve",
                              json={"keep": "adr"})).status_code == 422


# ── an archived fact is kept, never used ─────────────────────────
async def test_archiving_a_fact_removes_its_retrieval_chunk_and_it_is_never_recalled(client: AsyncClient,
                                                                                     session: AsyncSession):
    ref = (await client.get("/memory")).json()[0]["ref"]
    session.add(m.Chunk(project_id=None, kind="memory", ref=ref, path="project", title="t", body="a fact"))
    await session.flush()

    assert (await client.post(f"/memory/{ref}/archive")).status_code == 200
    left = await session.scalar(select(func.count()).select_from(m.Chunk).where(m.Chunk.ref == ref))
    assert left == 0                                        # no index can hand it to a model any more
    assert await MemoryService(session).recall([ref], via="chat", context="CHAT-1") == []
    assert (await session.scalar(select(func.count(m.MemoryHit.id)))) == 0


async def test_ask_never_cites_a_fact_archived_after_the_index_was_built(client: AsyncClient,
                                                                        session: AsyncSession, tmp_path: Path):
    """An index built before a fact was archived still names it; the lookup by ref must not serve it."""
    from app.services.ai_features import AiFeatureService

    held = (await client.get("/memory")).json()
    stale, live = held[0], held[1]
    project = rows("projects")[0]["id"]
    words = "zephyrine quokka ledger"                      # words nothing else in the workspace holds
    session.add_all([
        m.Chunk(project_id=project, kind="code", ref="src/a.py#f:1", path="src/a.py", title="f", body=words),
        m.Chunk(project_id=None, kind="memory", ref=stale["ref"], path="project", title="t", body=words),
        m.Chunk(project_id=None, kind="memory", ref=live["ref"], path="project", title="t", body=words),
    ])
    fact_row = await session.scalar(select(m.MemoryFact).where(m.MemoryFact.ref == stale["ref"]))
    fact_row.archived = True                              # archived with the old index still standing
    await session.flush()

    service = AiFeatureService(session, Gateway(MemoryLedger(), Secrets(tmp_path / "secrets.json")))
    handed = [f["ref"] for f in await service._facts(words, project)]
    assert live["ref"] in handed and stale["ref"] not in handed


async def test_an_archived_fact_cannot_be_put_into_a_conflict(client: AsyncClient):
    a, b = (f["ref"] for f in (await client.get("/memory")).json()[:2])
    await client.post(f"/memory/{b}/archive")
    refused = await client.post("/memory/conflicts", json={"a": a, "b": b, "topic": "Rounding"})
    assert refused.status_code == 409 and f"{b} is archived" in refused.json()["detail"]


async def test_a_side_archived_since_the_conflict_was_filed_cannot_be_kept(client: AsyncClient):
    a, b = (f["ref"] for f in (await client.get("/memory")).json()[:2])
    made = (await client.post("/memory/conflicts", json={"a": a, "b": b, "topic": "Rounding"})).json()
    await client.post(f"/memory/{a}/archive")

    refused = await client.post(f"/memory/conflicts/{made['id']}/resolve", json={"keep": "a"})
    assert refused.status_code == 409 and "archived since" in refused.json()["detail"]
    live = fact((await client.get("/memory")).json(), b)
    assert live["archived"] is False                        # the live side was not archived with it
    assert (await client.post(f"/memory/conflicts/{made['id']}/resolve", json={"keep": "b"})).status_code == 200


# ── the screen's figures ─────────────────────────────────────────
async def test_the_memory_figures_are_counted_not_taken_from_a_page(client: AsyncClient, session: AsyncSession):
    from datetime import timedelta

    from app.data.base import utcnow

    before = (await client.get("/memory/stats")).json()
    total = await session.scalar(select(func.count()).select_from(m.MemoryFact)
                                 .where(m.MemoryFact.archived.is_(False)))
    assert before["held"] == total and sum(c["held"] for c in before["byCategory"].values()) == total
    assert before["retiredDays"] == 30

    # More facts than any page of the list holds.
    session.add_all([m.MemoryFact(id=f"mbulk{n}", ref=f"MEM-9{n:04d}", category="legacy", title=f"Bulk {n}",
                                  body="b", reason="r", source="s", confidence="LOW") for n in range(520)])
    # Two retired within the window, one long before it.
    session.add_all([m.MemoryFact(id=f"mold{n}", ref=f"MEM-8{n:04d}", category="bugs", title=f"Old {n}", body="b",
                                  reason="r", source="s", confidence="LOW", archived=True,
                                  archived_at=utcnow() - timedelta(days=days))
                     for n, days in enumerate((1, 29, 45))])
    await session.flush()

    after = (await client.get("/memory/stats")).json()
    assert after["held"] == before["held"] + 520 > 500
    legacy_before = before["byCategory"].get("legacy", {"held": 0})["held"]
    assert after["byCategory"]["legacy"]["held"] == legacy_before + 520
    assert after["global"] == before["global"] + 520                      # none of them is a project's
    assert after["retired"] == before["retired"] + 2
    assert len((await client.get("/memory")).json()) < after["held"]      # the list is a page; the count is not

    # Scoped like the list: a project's own facts with the workspace's; "global" is the workspace's alone.
    project = rows("projects")[0]["id"]
    session.add(m.MemoryFact(id="mproj1", ref="MEM-70001", category="bugs", title="Project fact", body="b",
                             reason="r", source="s", confidence="LOW", project_id=project))
    await session.flush()
    scoped = (await client.get("/memory/stats", params={"project": project})).json()
    workspace = (await client.get("/memory/stats", params={"project": "global"})).json()
    in_project = await session.scalar(select(func.count()).select_from(m.MemoryFact).where(
        m.MemoryFact.archived.is_(False), m.MemoryFact.project_id == project))
    assert scoped["held"] == workspace["held"] + in_project
    assert workspace["held"] == workspace["global"] == after["global"]


async def test_the_memory_figures_count_recalls_and_pins_per_category(client: AsyncClient):
    answered = (await client.post("/ai/ask", json={"question": "Where does invoice rounding happen?"})).json()
    cited = [c["ref"] for c in answered["citations"]]
    stats = (await client.get("/memory/stats")).json()
    assert stats["recalled24h"] == len(cited) == sum(c["recalled24h"] for c in stats["byCategory"].values())
    facts = (await client.get("/memory")).json()
    assert stats["pinned"] == sum(1 for f in facts if f["pinned"])
    used = [c for c in stats["byCategory"].values() if c["recalled24h"]]
    assert used and all(c["lastUsedAt"] for c in used)
