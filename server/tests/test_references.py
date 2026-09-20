"""A project reading other projects — and a project's own reference sources — read only, everywhere.

The shop references the payments service it calls. What that must mean, and what this file proves:
the reference is a row a person adds, changes and removes (with the refusals that keep it sane, and a
line in the activity and audit logs each time); retrieval hands a model the payments service's pieces
beside the shop's own — labelled "Payments · reference", named with the `payments:` prefix, and never
crowding the shop's own code out; a session reads a payments file through that prefix and is refused an
escape or a project the shop does not reference; the `@` list offers those files; and nothing there is
ever writable. A source whose role is `reference` is labelled and held read only the same way.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps, routes_platform
from app.api.app import create_api
from app.services.chat import Tools
from app.services.code import Source, is_writable, reference_labels, roots, writable, writable_at
from app.services.errors import Refused
from app.services.references import referenced_ids
from app.repositories.words import terms
from app.services.retrieval import RetrievalService, near_enough
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
VIEWER = {"email": "view@example.com", "name": "Viewer", "password": "another long passphrase", "roles": ["viewer"]}
HEADERS = {"X-NC-Client": "test"}


class NoLanes:
    """Retrieval asks for an embedding lane; there is none, so it searches by words. Nothing is called."""

    def embed_lane(self) -> None:
        return None


@pytest_asyncio.fixture
async def world(session: AsyncSession, tmp_path: Path) -> dict[str, Any]:
    """Three projects on disk — the shop, the payments service it calls, and a stranger — with chunks and
    an index for each, and a design system added to the shop as a reference source."""
    await load_workspace(session)
    folders = {}
    for pid, files in {"shop": {"web/cart.py": "def checkout_total(cart):\n    return sum(cart)\n"},
                       "payments": {"app/charge.py": "def charge_card(amount):\n    return amount\n",
                                    "docs/refunds.md": "# Refunds\nA refund reverses a charge.\n"},
                       "stranger": {"secret.py": "TOKEN = 'no'\n"},
                       "design": {"tokens.md": "# Tokens\nThe checkout button is marigold.\n"}}.items():
        root = tmp_path / pid
        for rel, body in files.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(body)
        folders[pid] = root
    (tmp_path / "outside.txt").write_text("not yours\n")
    for pid, name in (("shop", "Shop"), ("payments", "Payments"), ("stranger", "Stranger")):
        session.add(m.Project(id=pid, name=name, source_kind="local", source_repo=str(folders[pid]), status="active"))
    await session.flush()
    session.add(m.ProjectSource(project_id="shop", label="design", kind="local", repo=str(folders["design"]),
                                status="active", role="reference", position=1))
    chunks = [
        ("shop", "code", "web/cart.py#checkout_total:1", "web/cart.py", "checkout total of the cart"),
        ("shop", "doc", "design/tokens.md#0", "design/tokens.md", "checkout button colour tokens"),
        ("payments", "code", "app/charge.py#charge_card:1", "app/charge.py", "charge the card at checkout"),
        ("payments", "doc", "docs/refunds.md#0", "docs/refunds.md", "a refund reverses a checkout charge"),
        ("stranger", "code", "secret.py#TOKEN:1", "secret.py", "checkout secret token"),
    ]
    for pid, kind, ref, path, body in chunks:
        session.add(m.Chunk(project_id=pid, kind=kind, ref=ref, path=path, line=1, title=ref, body=body))
    for pid, path in (("shop", "web/cart.py"), ("shop", "design/tokens.md"), ("payments", "app/charge.py"),
                      ("stranger", "secret.py")):
        session.add(m.CodeFile(project_id=pid, path=path, lang="Python", module=path.split("/")[0], lines=2))
    await session.flush()
    charge = (await session.execute(select(m.CodeFile).where(m.CodeFile.project_id == "payments"))).scalar_one()
    session.add(m.CodeSymbol(project_id="payments", file_id=charge.id, name="charge_card", kind="function", line=1))
    await session.flush()
    return {"folders": folders, "tmp": tmp_path}


@pytest_asyncio.fixture
async def api(world: dict[str, Any], session: AsyncSession):
    app = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[deps.session] = use_the_test_session
    return app


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: Any) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


# ── the rows, over HTTP ──────────────────────────────────────────
async def test_a_reference_is_added_listed_both_ways_changed_and_removed(client: AsyncClient, session: AsyncSession):
    made = await client.post("/projects/shop/references", json={"referencedId": "payments",
                                                                "note": "The service checkout calls"})
    assert made.status_code == 201, made.text
    body = made.json()
    assert body["project"] == {"id": "payments", "name": "Payments", "status": "active", "understoodPct": None}
    assert body["note"] == "The service checkout calls" and body["createdAt"]

    listed = (await client.get("/projects/shop/references")).json()
    assert [r["project"]["id"] for r in listed["references"]] == ["payments"] and listed["referencedBy"] == []
    assert listed["readAtMost"] == 5
    back = (await client.get("/projects/payments/references")).json()
    assert [r["project"]["id"] for r in back["referencedBy"]] == ["shop"] and back["references"] == []
    # The project cards carry it, so the navigator can show it under the project.
    assert (await client.get("/projects/shop")).json()["references"] == ["payments"]
    cards = {p["id"]: p for p in (await client.get("/projects")).json()}
    assert cards["shop"]["references"] == ["payments"] and cards["payments"]["references"] == []

    changed = await client.patch(f"/projects/shop/references/{body['id']}", json={"note": "Refunds too"})
    assert changed.status_code == 200 and changed.json()["note"] == "Refunds too"
    gone = await client.delete(f"/projects/shop/references/{body['id']}")
    assert gone.json() == {"ok": True, "id": body["id"], "referencedId": "payments"}
    assert (await client.get("/projects/shop/references")).json()["references"] == []

    audit = [a.action for a in (await session.execute(select(m.AuditEntry).where(
        m.AuditEntry.target == "shop").order_by(m.AuditEntry.seq))).scalars()]
    assert audit == ["project.reference.add", "project.reference.note", "project.reference.remove"]
    feed = [e["action"] for e in (await client.get("/activity", params={"project": "shop"})).json()]
    assert {"Reference added", "Reference changed", "Reference removed"} <= set(feed)


async def test_a_reference_is_refused_for_itself_twice_and_nowhere(api: Any, client: AsyncClient):
    itself = await client.post("/projects/shop/references", json={"referencedId": "shop"})
    assert itself.status_code == 422 and "cannot reference itself" in itself.json()["detail"]
    unknown = await client.post("/projects/shop/references", json={"referencedId": "nowhere"})
    assert unknown.status_code == 404
    assert (await client.post("/projects/shop/references", json={"referencedId": "payments"})).status_code == 201
    twice = await client.post("/projects/shop/references", json={"referencedId": "payments"})
    assert twice.status_code == 409 and "already references Payments" in twice.json()["detail"]
    assert (await client.get("/projects/nowhere/references")).status_code == 404
    assert (await client.patch("/projects/shop/references/999999", json={"note": "x"})).status_code == 404

    assert (await client.post("/admin/users", json=VIEWER)).status_code in (200, 201)
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.get("/projects/shop/references")).status_code == 200      # reading is for everyone
        refused = await viewer.post("/projects/shop/references", json={"referencedId": "stranger"})
        assert refused.status_code == 403 and "projects:onboard" in refused.json()["detail"]


async def test_a_source_can_be_a_reference_and_back(client: AsyncClient, world: dict[str, Any], monkeypatch):
    async def recorded(*_a: Any) -> None:
        return None

    monkeypatch.setattr(routes_platform, "onboard_source", recorded)
    listed = (await client.get("/projects/shop/sources")).json()
    assert [(s["label"], s["role"]) for s in listed] == [("shop", "code"), ("design", "reference")]
    extra = world["tmp"] / "lib"
    extra.mkdir()
    made = await client.post("/projects/shop/sources", json={"label": "lib", "kind": "local", "repo": str(extra),
                                                             "role": "reference"})
    assert made.status_code == 201 and made.json()["role"] == "reference"
    flipped = await client.patch(f"/projects/shop/sources/{made.json()['id']}", json={"role": "code"})
    assert flipped.status_code == 200 and flipped.json()["role"] == "code"
    card = (await client.get("/projects/shop")).json()
    assert {s["label"]: s["role"] for s in card["sources"]} == {"shop": "code", "design": "reference", "lib": "code"}
    feed = (await client.get("/activity", params={"project": "shop"})).json()
    assert any(e["action"] == "Source changed" and "lib is now code, worked on" in e["detail"] for e in feed)
    assert (await client.patch(f"/projects/shop/sources/{made.json()['id']}", json={"role": "owner"})).status_code == 422


# ── what reads there, and what never writes there ────────────────
async def test_retrieval_hands_over_a_referenced_projects_pieces_labelled(client: AsyncClient, session: AsyncSession):
    await client.post("/projects/shop/references", json={"referencedId": "payments"})
    service = RetrievalService(session, NoLanes())  # type: ignore[arg-type]

    found = await service.search("shop", "checkout charge", limit=6)
    refs = [x["ref"] for x in found]
    assert refs[0] == "web/cart.py#checkout_total:1" or refs[0] == "design/tokens.md#0"   # its own lead
    theirs = [x for x in found if x["ref"].startswith("payments:")]
    assert theirs and all(x["reference"] == "Payments · reference" and x["path"].startswith("payments:")
                          for x in theirs)
    assert len(theirs) <= 6 // 3                                         # a third of the slots at most
    assert not any("secret" in x["ref"] for x in found)                  # a project not referenced is never read
    design = next(x for x in found if x["ref"] == "design/tokens.md#0")
    assert design["reference"] == "design · reference"                   # a reference source is labelled too
    assert "reference" not in next(x for x in found if x["ref"] == "web/cart.py#checkout_total:1")

    alone = await service.search("shop", "checkout charge", limit=6, references=False)
    assert not any(x["ref"].startswith("payments:") for x in alone)

    text, pieces = await service.grounding("shop", "checkout charge", limit=6)
    assert "[code · payments:app/charge.py#charge_card:1 · Payments · reference]" in text
    # Grounding hands on what the search found, less whatever the relevance floor refused: the design
    # tokens piece shares one word with "checkout charge" and is about neither of them.
    assert "read only" in text and pieces == [x for x in found if near_enough(x, len(terms("checkout charge")))]
    assert pieces and len(pieces) < len(found)
    assert await referenced_ids(session, "shop") == ["payments"]


async def test_a_session_reads_a_referenced_file_by_its_prefix_and_nothing_else(client: AsyncClient,
                                                                                 session: AsyncSession,
                                                                                 world: dict[str, Any]):
    await client.post("/projects/shop/references", json={"referencedId": "payments"})
    shop = await session.get(m.Project, "shop")
    tools = Tools(session, NoLanes(), shop)  # type: ignore[arg-type]

    text, detail = await tools.read_file({"path": "payments:app/charge.py"})
    assert "def charge_card(amount):" in text and text.startswith("payments:app/charge.py · lines 1-2 of 2")
    listed, _ = await tools.list_files({"directory": "payments:app"})
    assert "charge.py" in listed
    named, _ = await tools.search_code({"query": "charge_card"})
    assert "payments:app/charge.py" in named

    with pytest.raises(Refused) as refused:                              # a project the shop does not reference
        await tools.read_file({"path": "stranger:secret.py"})
    assert refused.value.status == 403 and "not a project Shop references" in str(refused.value)
    for escape in ("payments:../outside.txt", "payments:/etc/passwd", "payments:app/../../outside.txt"):
        with pytest.raises(Refused):
            await tools.read_file({"path": escape})
    # And no write path there: a prefixed path or a reference source is never writable.
    sources = await roots(session, shop)
    assert writable_at(sources, "web/cart.py") is True
    assert writable_at(sources, "design/tokens.md") is False
    assert writable_at(sources, "payments:app/charge.py") is False
    assert await is_writable(session, shop, "design/tokens.md") is False
    assert reference_labels(sources) == {"design"}
    assert writable(Source(label="design", root=Path("."), kind="local", primary=False, role="reference")) is False


async def test_the_mentions_offer_referenced_files_read_only(client: AsyncClient):
    await client.post("/projects/shop/references", json={"referencedId": "payments"})
    items = (await client.get("/projects/shop/mentions", params={"q": ""})).json()["items"]
    files = {i["ref"]: i["detail"] for i in items if i["kind"] == "file"}
    assert "payments:app/charge.py" in files and "Payments · reference, read only" in files["payments:app/charge.py"]
    assert files["design/tokens.md"].endswith("reference, read only")
    assert not files["web/cart.py"].endswith("read only")
    assert not any(ref.startswith("stranger:") for ref in files)
