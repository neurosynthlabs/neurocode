"""The AI features on the new stack: ask memory, brainstorm, extract.

Mostly no model is configured here, which is the path worth testing for two of the three: asking and
extracting still answer, and the answer says the offline rules wrote it. A brainstorm has no offline
version — with no lane it is refused and nothing is kept — so its tests give the real gateway one lane
whose provider call answers from a script (`tests/fixtures/lanes.py`). Nothing in this file reaches
the network.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai import features
from app.ai import gateway as gateway_module
from app.ai.features import CATEGORIES, HINTS
from app.ai.gateway import Gateway, ProviderError
from app.ai.ledger import MemoryLedger
from app.api import deps
from app.api.app import create_api
from app.data.loader import sync_roles
from app.models import Chunk, RetrievalRun
from app.repositories.work import ActivityRepository
from app.secrets import Secrets
from app.services.errors import NO_MODEL
from app.services.identity import IdentityService
from tests.fixtures.lanes import BRIEF, LANE_MODEL, answering
from tests.fixtures.workspace import load_workspace, rows

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}
IDEA = "A queue that tells outpatients their real waiting time, by SMS."
NOTES = ("Tax must always be rounded per line item, never on the invoice total. "
         "The client decided that GST slabs come only from the MST_TAX table. "
         "The small things can wait until later.")


@pytest_asyncio.fixture
async def client(session: AsyncSession, monkeypatch, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    # "rules" is what a laptop with no API key looks like to the router: no lane may answer, so
    # nothing here opens a socket to a provider.
    monkeypatch.setenv("NEUROCODE_COMPILER", "rules")
    await load_workspace(session)
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    api.dependency_overrides[deps.session] = use_the_test_session
    api.dependency_overrides[deps.gateway] = lambda: Gateway(MemoryLedger(),
                                                             Secrets(tmp_path / "secrets.json"))
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)           # an Owner holds every permission
        yield c


async def viewer_headers(session: AsyncSession) -> dict[str, str]:
    """Someone signed in who holds nothing: the Viewer role carries no permissions at all."""
    identity = IdentityService(session)
    person = await identity.create("viewer@example.com", "Vik", "correct horse battery", ["viewer"])
    return {**HEADERS, "Authorization": f"Bearer {await identity.start_session(person.id)}"}


async def test_asking_memory_answers_from_facts_and_cites_only_those(client: AsyncClient):
    answered = await client.post("/ai/ask", json={"question": "tax rounding", "projectId": "erp"})
    assert answered.status_code == 200, answered.text

    body = answered.json()
    assert set(body) == {"answer", "citations", "provider", "model", "ms"}
    assert body["provider"] == "rules" and body["model"]      # honest about who answered
    assert body["citations"] and all(set(c) == {"ref", "title"} for c in body["citations"])
    assert any("rounding" in c["title"].lower() for c in body["citations"])
    assert all(c["ref"] in body["answer"] for c in body["citations"])


async def test_a_question_nothing_is_remembered_about_still_gets_an_answer(client: AsyncClient):
    """Made-up words on purpose. A question is searched for *any* of its words — which is what makes
    a real question findable at all — so ordinary ones would match something and prove nothing."""
    answered = await client.post("/ai/ask", json={"question": "zorblax quintafril wemmelbore?"})
    assert answered.status_code == 200
    assert answered.json()["answer"] and answered.json()["citations"] == []


async def test_a_real_question_finds_the_fact_that_answers_it(client: AsyncClient):
    """The thing the old route's `mode="any"` was for: every word of a question is never in a fact,
    so requiring all of them answered every question with nothing at all."""
    answered = await client.post(
        "/ai/ask", json={"question": "Where does invoice rounding happen, and why there?"})
    assert answered.status_code == 200 and answered.json()["citations"]


async def test_an_indexed_project_is_answered_from_what_retrieval_ranks(client: AsyncClient,
                                                                        session: AsyncSession):
    """The chunk holds a word the fact itself does not, so only retrieval can find it — and what it
    finds is still a fact, with its ref, because an answer cites memory rather than a chunk."""
    session.add_all([
        Chunk(project_id=None, kind="memory", ref="MEM-142", path="architecture",
              title="Architecture Decision #142",
              body="Kaboom: TRANS tables are written through the repository layer only."),
        RetrievalRun(project_id="erp", chunks=1),
    ])
    await session.flush()

    body = (await client.post("/ai/ask", json={"question": "kaboom", "projectId": "erp"})).json()
    assert [c["ref"] for c in body["citations"]] == ["MEM-142"]
    assert body["citations"][0]["title"] == rows("memory", ref="MEM-142")[0]["title"]


async def test_a_brainstorm_is_a_row_that_comes_back_as_the_brief_the_page_reads(client: AsyncClient,
                                                                                  monkeypatch):
    sent = answering(monkeypatch, BRIEF)
    made = await client.post("/ai/brainstorm", json={"idea": IDEA, "projectId": "hims"})
    assert made.status_code == 201, made.text

    doc = made.json()
    assert set(doc) == {"id", "ref", "idea", "projectId", "brief", "compiler", "by", "createdAt"}
    assert doc["ref"] == "IDEA-1" and doc["by"] == "Rajat" and doc["projectId"] == "hims"
    assert doc["idea"] == IDEA and doc["createdAt"]
    assert doc["compiler"]["provider"] == "groq" and doc["compiler"]["model"] == LANE_MODEL
    assert IDEA in sent[0][1]["content"]                      # the model was asked about this idea

    brief = doc["brief"]
    assert set(brief) == {"title", "problem", "audience", "value", "mvp", "risks", "metrics",
                          "questions", "roadmap"}
    assert brief == BRIEF                                     # the model's brief, every section of it

    listed = (await client.get("/ai/brainstorms")).json()
    assert [b["ref"] for b in listed] == ["IDEA-1"]
    assert listed[0]["brief"] == brief                       # the row reassembles into the same brief


async def test_with_no_model_a_brainstorm_is_refused_and_nothing_is_kept(client: AsyncClient):
    refused = await client.post("/ai/brainstorm", json={"idea": IDEA, "projectId": "hims"})
    assert refused.status_code == 409 and refused.json()["detail"] == NO_MODEL
    assert (await client.get("/ai/brainstorms")).json() == []


async def test_a_provider_that_fails_a_brainstorm_is_passed_through_with_its_reason(client: AsyncClient,
                                                                                    monkeypatch):
    answering(monkeypatch, ProviderError(503, "the lane is down"))
    failed = await client.post("/ai/brainstorm", json={"idea": IDEA})
    assert failed.status_code == 502 and "the lane is down" in failed.json()["detail"]
    assert (await client.get("/ai/brainstorms")).json() == []


async def test_the_next_reference_comes_from_the_database_not_from_counting(client: AsyncClient, monkeypatch):
    answering(monkeypatch, BRIEF, BRIEF)
    first = await client.post("/ai/brainstorm", json={"idea": IDEA})
    second = await client.post("/ai/brainstorm", json={"idea": "Memory should decay when it is not used."})
    assert (first.json()["ref"], second.json()["ref"]) == ("IDEA-1", "IDEA-2")
    assert first.json()["id"] != second.json()["id"]
    assert first.json()["projectId"] is None

    listed = (await client.get("/ai/brainstorms")).json()
    assert {b["ref"] for b in listed} == {"IDEA-1", "IDEA-2"}
    assert (await client.get("/ai/brainstorms", params={"project": "erp"})).json() == []


async def test_extract_proposes_facts_and_remembers_nothing(client: AsyncClient):
    before = len((await client.get("/memory")).json())
    out = await client.post("/ai/extract", json={"text": NOTES, "projectId": "erp"})
    assert out.status_code == 200

    body = out.json()
    assert set(body) == {"facts", "provider", "model", "ms"} and body["provider"] == "rules"
    assert body["facts"], "the offline rules should find the sentences that state a rule"
    assert all(set(f) == {"title", "body", "category", "confidence", "reason"} for f in body["facts"])
    assert all(f["category"] in CATEGORIES and f["confidence"] in ("HIGH", "MEDIUM", "LOW")
               for f in body["facts"])
    assert len((await client.get("/memory")).json()) == before      # proposed, never kept


def test_the_offline_rules_know_no_one_business_s_words(tmp_path: Path, monkeypatch):
    """The rules filed a sentence by the vocabulary of the sample workspace — tax, GST, invoices, its
    TRANS_ and MST_ tables — so any other business's notes came out under the wrong heading."""
    monkeypatch.setenv("NEUROCODE_COMPILER", "rules")
    for word in ("tax", "GST", "invoice", "TRANS_INVOICE", "MST_TAX", "rounding", "price"):
        assert not any(rx.search(f"the {word} here") for _, rx in HINTS), word

    text = "Invoice tax should be rounded per line item. TRANS_INVOICE should keep every revision."
    out = features.extract(Gateway(MemoryLedger(), Secrets(tmp_path / "secrets.json")), text, None).data
    assert [f.category for f in out.facts] == ["project", "project"]


async def test_a_project_that_does_not_exist_is_refused_rather_than_left_dangling(client: AsyncClient,
                                                                                    monkeypatch):
    answering(monkeypatch, BRIEF)
    missing = await client.post("/ai/brainstorm", json={"idea": IDEA, "projectId": "nope"})
    assert missing.status_code == 404 and "nope" in missing.json()["detail"]
    assert (await client.get("/ai/brainstorms")).json() == []


async def test_the_three_that_cost_a_model_call_need_ai_use(client: AsyncClient, session: AsyncSession):
    viewer = await viewer_headers(session)
    asked = (("/ai/ask", {"question": "tax rounding"}), ("/ai/brainstorm", {"idea": IDEA}),
             ("/ai/extract", {"text": NOTES}))
    for path, body in asked:
        refused = await client.post(path, json=body, headers=viewer)
        assert refused.status_code == 403 and "ai:use" in refused.json()["detail"]
    # Reading what was brainstormed is not one of them: being signed in is enough.
    assert (await client.get("/ai/brainstorms", headers=viewer)).status_code == 200


async def test_a_lane_that_fails_still_answers_and_the_feed_says_which(
        client: AsyncClient, session: AsyncSession, monkeypatch):
    def refuse(messages: list[dict[str, str]], cfg: dict[str, object]) -> str:
        raise ProviderError(503, "the lane is down")

    monkeypatch.setenv("NEUROCODE_COMPILER", "groq")     # one lane, and it is about to fail
    monkeypatch.setenv("GROQ_API_KEY", "not-a-real-key")
    monkeypatch.setitem(gateway_module.CALLS, "groq", refuse)

    answered = await client.post("/ai/ask", json={"question": "tax rounding", "projectId": "erp"})
    assert answered.status_code == 200 and answered.json()["provider"] == "rules"

    feed = (await ActivityRepository(session).recent()).items
    fell_back = next(e for e in feed if e.action == "AI fell back")
    assert fell_back.level == "warn" and fell_back.actor_kind == "system"
    assert "llama-3.3-70b-versatile" in fell_back.detail and fell_back.project_id == "erp"
    assert any(e.action == "Asked memory" and e.actor == "Rajat" for e in feed)


@pytest_asyncio.fixture
async def empty_client(session: AsyncSession, monkeypatch, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    """A workspace with an Owner and nothing else — what a real one looks like before onboarding."""
    monkeypatch.setenv("NEUROCODE_COMPILER", "rules")
    await sync_roles(session)
    await session.flush()
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    api.dependency_overrides[deps.session] = use_the_test_session
    api.dependency_overrides[deps.gateway] = lambda: Gateway(MemoryLedger(),
                                                             Secrets(tmp_path / "secrets.json"))
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


async def test_a_workspace_with_no_projects_can_still_ask(empty_client: AsyncClient, monkeypatch):
    """The feed entry used to be filed under a project id written into the source — `aios` — which is
    a foreign key now. Every workspace that had not been seeded crashed on its first question."""
    for path, body in (("/ai/ask", {"question": "anything at all"}), ("/ai/extract", {"text": NOTES})):
        answered = await empty_client.post(path, json=body)
        assert answered.status_code == 200, f"{path} -> {answered.status_code} {answered.text[:200]}"
    answering(monkeypatch, BRIEF)
    made = await empty_client.post("/ai/brainstorm", json={"idea": IDEA})
    assert made.status_code == 201, made.text[:200]


async def test_a_brainstorm_id_is_never_a_bare_number(empty_client: AsyncClient, monkeypatch):
    """A bare `b1` once opened a different brainstorm on the page; an id carries more than its number."""
    answering(monkeypatch, BRIEF)
    made = (await empty_client.post("/ai/brainstorm", json={"idea": IDEA})).json()
    assert made["id"] not in {f"b{n}" for n in range(1, 10)}
    assert made["ref"].startswith("IDEA-")
