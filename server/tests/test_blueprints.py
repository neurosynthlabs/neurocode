"""The Blueprint wizard, over HTTP: the catalogue and its schema, blueprints and their revisions, a model's
review and a person's decisions on it, finalizing into memory, files in and out, and scaffolding a
blueprint into a real folder, a real git repository and a real plan.

The model is the scripted lane from `tests/fixtures/lanes.py` — the gateway, routing and ledger are the
real ones — and nothing reaches the network. The machine roots are pointed at a temporary folder, so a
scaffold can only ever write there.
"""
from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import Gateway
from app.ai.ledger import MemoryLedger
from app.api import deps, routes_blueprints
from app.api.app import create_api
from app.data.engine import Database
from app.models import Blueprint, MemoryFact, Plan, Project, ProjectSource
from app.secrets import Secrets
from app.services import blueprints as bps
from app.services import machine
from app.services.errors import NO_MODEL, Refused
from app.services.onboarding import Spec, onboard
from app.services.runs import _setup
from app.settings import settings as real_settings
from tests.fixtures.lanes import PLAN, answering, no_lane

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
VIEWER = {"email": "view@example.com", "name": "Viewer", "password": "another long passphrase", "roles": ["viewer"]}
ADMIN = {"email": "admin@example.com", "name": "Admin", "password": "another long passphrase", "roles": ["admin"]}
HEADERS = {"X-NC-Client": "test"}
PYTHON_WEB = {"idea": "A booking tool for a chain of clinics.", "productType": "web-app", "team": ["python"],
              "data": ["relational"], "teamSize": "small", "budget": "minimal"}


@pytest_asyncio.fixture
async def api(catalogued: AsyncSession, tmp_path: Path) -> FastAPI:
    """The built-in roles and roster, read from the catalogue as a server start does — so the Owner holds
    `machine:access` and the compiler's agents are on the roster."""
    made = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield catalogued

    made.dependency_overrides[deps.session] = use_the_test_session
    made.dependency_overrides[deps.gateway] = lambda: Gateway(MemoryLedger(), Secrets(tmp_path / "secrets.json"))
    return made


def _client(made: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=made), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


@pytest.fixture
def roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The only machine root: `work/`. `elsewhere/` sits beside it, outside."""
    root = tmp_path / "work"
    root.mkdir()
    (tmp_path / "elsewhere").mkdir()
    configured = real_settings().model_copy(update={"machine_roots": str(root), "machine_access": True})
    monkeypatch.setattr(machine, "settings", lambda: configured)
    return root


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, tuple[Any, ...]]]:
    """The onboarding jobs a scaffold hands off, recorded instead of run."""
    seen: list[tuple[str, tuple[Any, ...]]] = []

    async def first(_db, _gw, pid, spec):
        seen.append(("onboard", (pid, spec.source, spec.repo)))

    async def further(_db, _gw, pid, sid):
        seen.append(("source", (pid, sid)))

    monkeypatch.setattr(routes_blueprints, "onboard", first)
    monkeypatch.setattr(routes_blueprints, "onboard_source", further)
    return seen


async def make(client: AsyncClient, template: str | None = "spa-api-react-fastapi", name: str = "Clinic bookings",
               answers: dict[str, Any] | None = None) -> dict[str, Any]:
    made = await client.post("/blueprints", json={"name": name, "templateId": template,
                                                  "answers": PYTHON_WEB if answers is None else answers})
    assert made.status_code == 201, made.text
    return made.json()


def git_log(root: Path) -> list[str]:
    out = subprocess.run(["git", "log", "--format=%s", "--name-only"], cwd=root, capture_output=True, text=True,
                         check=True).stdout
    return [line for line in out.splitlines() if line.strip()]


# ── the catalogue ────────────────────────────────────────────────
def test_every_template_fits_the_schema_and_names_only_catalogued_technologies():
    schema = json.loads(bps.SCHEMA_PATH.read_text())
    files = sorted(p for p in bps.BANK.glob("*.json") if not p.name.startswith("_"))
    assert len(files) >= 16
    known = {t["id"] for t in bps.CATALOGUE.tech}
    for path in files:
        tpl = json.loads(path.read_text())
        assert bps.problems(tpl, schema) == [], path.name
        assert tpl["id"] == path.stem
        assert not [t for t in bps.technologies_named(tpl) if t not in known], path.name
        # Every condition speaks of a real question and one of its real answers.
        for cond in tpl["fitsWhen"] + tpl["avoidWhen"]:
            q = bps.QUESTION_BY_ID[cond["answer"]]
            assert {v for v in cond["is"]} <= {o["id"] for o in q["options"]}, (path.name, cond)
        # Talks-to names something the template has, and its architecture fits the blueprint shape too.
        arch = bps.architecture_of(tpl)
        assert bps.checks(arch) == [], path.name
        assert bps.spec_problems(arch) == [], path.name


def test_the_technology_catalogue_is_whole():
    tech = bps.CATALOGUE.tech
    assert len(tech) >= 150
    categories = {"frontend", "mobile", "backend", "language", "database", "cache", "queue", "search", "auth",
                  "ai-ml", "data", "infra", "ci-cd", "observability", "testing", "hosting"}
    ids = {t["id"] for t in tech}
    for t in tech:
        assert set(t) == {"id", "name", "category", "languages", "kind", "maturity", "license", "pairsWith", "notes"}
        assert t["category"] in categories and t["notes"] and t["license"], t["id"]
        assert set(t["pairsWith"]) <= ids, t["id"]
    assert categories == {t["category"] for t in tech}      # every category has something in it
    for wanted in ("react", "fastapi", "postgres", "kafka", "pgvector", "kubernetes", "github-actions", "vllm",
                   "oracle-cloud", "tauri", "flutter", "playwright"):
        assert wanted in ids


def test_the_schema_checker_says_what_is_wrong():
    tpl = copy.deepcopy(bps.CATALOGUE.templates["django-monolith"])
    tpl["layers"]["backend"]["choice"] = 7
    tpl["services"][0]["kind"] = "robot"
    tpl["surprise"] = True
    del tpl["adrs"]
    said = bps.problems(tpl, bps.CATALOGUE.schema)
    assert "layers.backend.choice: must be string or null" in said
    assert any(s.startswith("services[0].kind: must be one of") for s in said)
    assert "surprise: is not a field this shape has" in said
    assert "the document: is missing adrs" in said
    with pytest.raises(ValueError):
        bps.problems({}, {"oneOf": []})         # a keyword it cannot check is an error, never a pass


def test_yaml_reads_back_what_it_writes_and_refuses_what_it_cannot_read():
    for tpl in bps.CATALOGUE.templates.values():
        assert bps.from_yaml(bps.to_yaml(tpl)) == tpl
    tricky = {"text": "yes", "n": "12", "colon": "a: b", "hash": "x #y", "empty": "", "none": None, "multi": "a\n  b\n",
              "unicode": "नमस्ते — ok", "list": [[], {}, [1, 2], {"k": [True, False]}]}
    assert bps.from_yaml(bps.to_yaml(tricky)) == tricky
    hand = "name: Shop # a comment\nlist:\n  - one\n  - 'it''s'\nflow: [a, \"b\", {c: 1}]\nblock: >-\n  folded\n  line\n"
    assert bps.from_yaml(hand) == {"name": "Shop", "list": ["one", "it's"], "flow": ["a", "b", {"c": 1}],
                                   "block": "folded line"}
    for bad in ("a: &x 1\nb: *x\n", "a: !!python/object foo\n", "a: 1\n  b: 2\n", "a: [1, 2\n"):
        with pytest.raises(bps.YamlError):
            bps.from_yaml(bad)


def test_paths_reach_into_an_architecture_and_nowhere_else():
    arch = bps.architecture_of(bps.CATALOGUE.templates["spa-api-react-fastapi"])
    assert bps.get_at(arch, "layers.backend.choice") == "fastapi"
    assert bps.get_at(arch, "services[api].tech") == "fastapi"
    assert bps.get_at(arch, "services[1].name") == "api"
    assert bps.get_at(arch, "layers.search.choice") is bps.MISSING
    bps.set_at(arch, "layers.search.choice", "meilisearch")          # a missing layer is made
    assert arch["layers"]["search"] == {"choice": "meilisearch", "alternatives": [], "why": ""}
    bps.set_at(arch, "services[+]", {"name": "mailer", "kind": "worker", "tech": "celery", "responsibilities": [],
                                     "talksTo": []})
    assert arch["services"][-1]["name"] == "mailer"
    bps.set_at(arch, "services[mailer]", None)
    assert "mailer" not in [s["name"] for s in arch["services"]]
    for bad in ("secrets.key", "../x", "services[nope].tech", "summary.x"):
        with pytest.raises(Refused):
            bps.set_at(arch, bad, "x")


# ── reading ──────────────────────────────────────────────────────
async def test_the_catalogue_route_serves_the_bank_the_questions_and_the_layers(client: AsyncClient, api: FastAPI):
    got = (await client.get("/blueprints/catalogue")).json()
    assert len(got["tech"]) == len(bps.CATALOGUE.tech) and len(got["templates"]) == len(bps.CATALOGUE.templates)
    assert got["mine"] == [] and got["mineTotal"] == 0
    assert [q["id"] for q in got["questions"]][:2] == ["idea", "productType"]
    assert got["layers"][0] == {"id": "frontend", "label": "Front end", "category": "frontend"}
    one = (await client.get("/blueprints/templates/go-microservices")).json()
    assert one["source"] == "catalogue" and one["spec"]["layers"]["queue"]["choice"] == "nats"
    assert one["diagram"].startswith("flowchart LR") and "s_gateway --> s_accounts" in one["diagram"]
    assert (await client.get("/blueprints/templates/nothing-here")).status_code == 404
    async with _client(api) as stranger:
        assert (await stranger.get("/blueprints/catalogue")).status_code == 401


async def test_templates_are_ranked_by_the_answers_that_match_them(client: AsyncClient):
    ranked = (await client.post("/blueprints/rank", json={"answers": PYTHON_WEB})).json()["catalogue"]
    top = ranked[0]
    assert top["id"] == "django-monolith"
    assert top["fit"]["score"] == 5 and top["fit"]["against"] == []
    assert {f["answer"] for f in top["fit"]["fits"]} == {"team", "productType", "data", "teamSize", "budget"}
    scores = [t["fit"]["score"] for t in ranked]
    assert scores == sorted(scores, reverse=True)
    # A template the answers argue against says which answer did.
    seo = (await client.post("/blueprints/rank", json={"answers": {"needs": ["seo"]}})).json()["catalogue"]
    spa = next(t for t in seo if t["id"] == "spa-api-react-fastapi")
    assert spa["fit"]["score"] == -1 and spa["fit"]["against"][0]["values"] == ["seo"]
    assert seo[0]["id"] in ("nextjs-fullstack", "static-jamstack")
    # Nothing answered: nothing counted either way.
    blank = (await client.post("/blueprints/rank", json={"answers": {}})).json()["catalogue"]
    assert all(t["fit"]["score"] == 0 and t["fit"]["considered"] == 0 for t in blank)
    bad = await client.post("/blueprints/rank", json={"answers": {"team": ["cobol"]}})
    assert bad.status_code == 422


# ── blueprints and revisions ─────────────────────────────────────
async def test_a_blueprint_starts_from_a_template_and_every_edit_is_a_revision(client: AsyncClient,
                                                                               catalogued: AsyncSession):
    bp = await make(client)
    assert bp["status"] == "draft" and bp["revision"] == 1 and bp["template"] == "spa-api-react-fastapi"
    assert bp["templateName"] == "SPA + API: React, FastAPI and Postgres" and bp["createdBy"] == "Rajat"
    assert bp["spec"]["layers"]["backend"]["choice"] == "fastapi" and bp["answers"]["team"] == ["python"]
    assert bp["checks"] == [] and bp["review"] is None and bp["final"] is None
    # The template itself is untouched by the edit that follows.
    spec = copy.deepcopy(bp["spec"])
    spec["layers"]["backend"]["choice"] = "django"
    saved = await client.patch(f"/blueprints/{bp['id']}", json={"expectRevision": 1, "spec": spec})
    assert saved.status_code == 200 and saved.json()["revision"] == 2
    assert saved.json()["spec"]["layers"]["backend"]["choice"] == "django"
    assert bps.CATALOGUE.templates["spa-api-react-fastapi"]["layers"]["backend"]["choice"] == "fastapi"
    # Saving against an older revision is refused, not merged.
    stale = await client.patch(f"/blueprints/{bp['id']}", json={"expectRevision": 1, "name": "Other"})
    assert stale.status_code == 409 and "revision 2" in stale.json()["detail"]
    # A shape the architecture does not have is refused with where.
    spec["services"][0]["kind"] = "robot"
    wrong = await client.patch(f"/blueprints/{bp['id']}", json={"expectRevision": 2, "spec": spec})
    assert wrong.status_code == 422 and "services[0].kind" in wrong.json()["detail"]
    # A dangling "talks to" is saved but shown as a check.
    spec["services"][0]["kind"] = "web"
    spec["services"][0]["talksTo"] = ["ghost"]
    kept = (await client.patch(f"/blueprints/{bp['id']}", json={"expectRevision": 2, "spec": spec})).json()
    assert kept["revision"] == 3 and kept["checks"][0]["problem"] == "web talks to ghost, which is neither a service nor a data store."
    # Nothing changed is no new revision.
    same = (await client.patch(f"/blueprints/{bp['id']}", json={"expectRevision": 3, "spec": spec})).json()
    assert same["revision"] == 3

    blank = await make(client, template=None, name="Blank one", answers={})
    assert blank["spec"]["layers"] == {} and blank["spec"]["services"] == [] and blank["template"] is None
    listed = (await client.get("/blueprints", params={"limit": 1})).json()
    assert listed["total"] == 2 and len(listed["items"]) == 1 and listed["limit"] == 1
    everything = (await client.get("/blueprints")).json()["items"]
    row = next(x for x in everything if x["id"] == bp["id"])
    assert row["layers"]["backend"] == "django" and row["services"] == 3 and "spec" not in row
    refused = await client.post("/blueprints", json={"name": "x", "answers": {"nonsense": 1}})
    assert refused.status_code == 422
    assert (await client.delete(f"/blueprints/{blank['id']}")).json() == {"ok": True, "id": blank["id"]}
    assert (await catalogued.get(Blueprint, blank["id"])) is None


# ── the review ───────────────────────────────────────────────────
REVIEW = {"summary": "Solid for a small Python team; the queue is heavier than the answers need.",
          "changes": [
              {"path": "layers.queue.choice", "from": "rabbitmq", "to": "temporal", "why": "Retries of long jobs."},
              {"path": "layers.search", "to": {"choice": "meilisearch", "alternatives": [], "why": "Search is asked for."},
               "why": "The answers ask for search."},
              {"path": "services[+]", "to": {"name": "Bad Name", "kind": "worker", "tech": "celery",
                                             "responsibilities": [], "talksTo": []}, "why": "Breaks the shape."},
              {"path": "layers.backend.choice", "to": "fastapi", "why": "Already so."},
              {"path": "secrets.everything", "to": 1, "why": "Not a place."}]}


async def test_a_review_proposes_and_a_person_decides(client: AsyncClient, monkeypatch):
    bp = await make(client)
    sent = answering(monkeypatch, REVIEW)
    reviewed = await client.post(f"/blueprints/{bp['id']}/suggest", json={"expectRevision": 1})
    assert reviewed.status_code == 200, reviewed.text
    body = reviewed.json()
    assert "Clinic bookings" in sent[0][1]["content"] and "fastapi (FastAPI, backend)" in sent[0][1]["content"]
    review = body["review"]
    assert review["model"] and review["revision"] == 1 and review["summary"].startswith("Solid")
    assert [c["path"] for c in review["changes"]] == ["layers.queue.choice", "layers.search"]
    # `from` is what is really there, not what the model quoted.
    assert review["changes"][0]["from"] == "celery" and review["changes"][1]["from"] is None
    assert len(review["dropped"]) == 3
    # Nothing was applied by the review itself.
    assert body["revision"] == 1 and body["spec"]["layers"]["queue"]["choice"] == "celery"
    assert "search" not in body["spec"]["layers"]

    first, second = review["changes"]
    applied = await client.post(f"/blueprints/{bp['id']}/apply", json={
        "expectRevision": 1, "changes": [second], "rejected": [first["id"]]})
    assert applied.status_code == 200, applied.text
    after = applied.json()
    assert after["revision"] == 2 and after["spec"]["layers"]["search"]["choice"] == "meilisearch"
    assert after["spec"]["layers"]["queue"]["choice"] == "celery"
    assert after["review"]["decided"] == {first["id"]: "rejected", second["id"]: "accepted"}
    # A change judged against a value that has since moved is refused.
    moved = await client.post(f"/blueprints/{bp['id']}/apply", json={
        "expectRevision": 2, "changes": [{**first, "from": "rabbitmq"}]})
    assert moved.status_code == 409 and "no longer what the review saw" in moved.json()["detail"]


async def test_with_no_model_there_is_no_review(client: AsyncClient, monkeypatch):
    bp = await make(client)
    no_lane(monkeypatch)
    refused = await client.post(f"/blueprints/{bp['id']}/suggest")
    assert refused.status_code == 409 and refused.json()["detail"] == NO_MODEL
    assert (await client.get(f"/blueprints/{bp['id']}")).json()["review"] is None


# ── finalizing ───────────────────────────────────────────────────
async def test_finalizing_writes_the_document_the_diagram_and_the_decisions(client: AsyncClient,
                                                                            catalogued: AsyncSession):
    bp = await make(client)
    done = await client.post(f"/blueprints/{bp['id']}/finalize", json={"expectRevision": 1})
    assert done.status_code == 200, done.text
    final = done.json()["final"]
    assert done.json()["status"] == "final" and final["revision"] == 1 and final["stale"] is False
    assert final["document"].startswith("# Clinic bookings") and "```mermaid" in final["document"]
    assert "| Back end | FastAPI |" in final["document"] and "A booking tool for a chain of clinics." in final["document"]
    assert final["diagram"].startswith("flowchart LR") and "s_web --> s_api" in final["diagram"]
    facts = (await catalogued.execute(select(MemoryFact).where(MemoryFact.ref.in_(final["facts"])))).scalars().all()
    tpl = bps.CATALOGUE.templates["spa-api-react-fastapi"]
    assert len(facts) == len(tpl["layers"]) + len(tpl["adrs"])
    assert {f.category for f in facts} == {"decisions"}
    back = next(f for f in facts if f.title == "ADR: Back end: FastAPI")
    assert back.evidence == [f"Blueprint {bp['id']} · Clinic bookings · revision 1"] and back.project_id is None
    assert "Considered: Django, NestJS, Flask." in back.body

    # Editing makes it a draft again, and the document says which revision it described.
    spec = copy.deepcopy(done.json()["spec"])
    spec["layers"]["backend"]["choice"] = "django"
    edited = (await client.patch(f"/blueprints/{bp['id']}", json={"expectRevision": 1, "spec": spec})).json()
    assert edited["status"] == "draft" and edited["final"]["stale"] is True
    again = (await client.post(f"/blueprints/{bp['id']}/finalize")).json()["final"]
    await catalogued.refresh(back)
    assert back.archived                                              # the old decision is kept, archived
    kept = set(final["facts"]) & set(again["facts"])
    assert len(kept) == len(final["facts"]) - 1                       # everything still true is untouched
    new = (await catalogued.execute(select(MemoryFact).where(MemoryFact.ref.in_(set(again["facts"]) - kept)))).scalars().all()
    assert [f.title for f in new] == ["ADR: Back end: Django"]


async def test_a_blueprint_with_nothing_chosen_or_a_broken_reference_is_not_finalized(client: AsyncClient):
    blank = await make(client, template=None, name="Blank", answers={})
    empty = await client.post(f"/blueprints/{blank['id']}/finalize")
    assert empty.status_code == 422 and "at least one layer" in empty.json()["detail"]
    bp = await make(client)
    spec = copy.deepcopy(bp["spec"])
    spec["services"][0]["talksTo"] = ["ghost"]
    await client.patch(f"/blueprints/{bp['id']}", json={"expectRevision": 1, "spec": spec})
    broken = await client.post(f"/blueprints/{bp['id']}/finalize")
    assert broken.status_code == 422 and "ghost" in broken.json()["detail"]


# ── files in and out ─────────────────────────────────────────────
@pytest.mark.parametrize("fmt", ["json", "yaml"])
async def test_a_blueprint_goes_out_as_a_file_and_comes_back_the_same(client: AsyncClient, fmt: str):
    bp = await make(client, template="rag-llm-app", answers={"idea": "Answers from our handbook: “ask it”.",
                                                              "needs": ["ai", "search"]})
    out = (await client.get(f"/blueprints/{bp['id']}/export", params={"format": fmt})).json()
    assert out["filename"] == f"clinic-bookings.{fmt}" and out["mime"].endswith(fmt)
    if fmt == "yaml":
        assert out["text"].startswith("format: neurocode.blueprint\nversion: 1\n")
    back = await client.post("/blueprints/import", json={"text": out["text"]})
    assert back.status_code == 201, back.text
    got = back.json()
    assert got["kind"] == "blueprint"
    copy_ = got["blueprint"]
    assert copy_["id"] != bp["id"] and copy_["status"] == "draft" and copy_["revision"] == 1
    assert copy_["spec"] == bp["spec"] and copy_["answers"] == bp["answers"] and copy_["template"] == "rag-llm-app"


async def test_templates_are_saved_exported_imported_and_deleted(client: AsyncClient, api: FastAPI):
    bp = await make(client)
    saved = await client.post("/blueprints/templates", json={"blueprintId": bp["id"], "name": "Our clinic stack",
                                                             "description": "What we build clinics on."})
    assert saved.status_code == 201, saved.text
    mine = saved.json()
    assert mine["id"].startswith("mine-our-clinic-stack-") and mine["source"] == "mine"
    assert mine["spec"]["layers"] == bp["spec"]["layers"] and mine["createdBy"] == "Rajat"
    listed = (await client.get("/blueprints/templates")).json()
    assert [t["id"] for t in listed["mine"]] == [mine["id"]] and listed["mineTotal"] == 1
    # A new blueprint can start from it.
    from_mine = await make(client, template=mine["id"], name="Second clinic")
    assert from_mine["templateName"] == "Our clinic stack" and from_mine["spec"]["layers"] == bp["spec"]["layers"]

    yaml_out = (await client.get(f"/blueprints/templates/{mine['id']}/export", params={"format": "yaml"})).json()
    assert yaml_out["text"].startswith("format: neurocode.template\n")
    imported = (await client.post("/blueprints/import", json={"text": yaml_out["text"]})).json()
    assert imported["kind"] == "template" and imported["template"]["id"] != mine["id"]
    # A catalogue file read in as it ships is a template too.
    shipped = (bps.BANK / "cli-library.json").read_text()
    as_shipped = (await client.post("/blueprints/import", json={"text": shipped})).json()["template"]
    assert as_shipped["fitsWhen"] == bps.CATALOGUE.templates["cli-library"]["fitsWhen"]
    ranked = (await client.post("/blueprints/rank", json={"answers": {"productType": "cli-library"}})).json()
    assert next(t for t in ranked["mine"] if t["id"] == as_shipped["id"])["fit"]["score"] == 1

    for text, words in (("{not json", "not valid JSON"), ("- a\n- b\n", "expected an object"),
                        ('{"format": "neurocode.session"}', "neither a NeuroCode blueprint"),
                        ("a: &x 1\n", "anchors")):
        bad = await client.post("/blueprints/import", json={"text": text})
        assert bad.status_code == 422 and words in bad.json()["detail"], (text, bad.text)

    assert (await client.delete("/blueprints/templates/django-monolith")).status_code == 409
    await client.post("/admin/users", json=ADMIN)
    async with _client(api) as admin:
        await admin.post("/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]})
        # An administrator may remove anyone's template.
        assert (await admin.delete(f"/blueprints/templates/{imported['template']['id']}")).status_code == 200
    assert (await client.delete(f"/blueprints/templates/{mine['id']}")).json() == {"ok": True, "id": mine["id"]}
    assert (await client.get(f"/blueprints/templates/{mine['id']}")).status_code == 404


# ── scaffolding ──────────────────────────────────────────────────
async def finalized(client: AsyncClient, template: str = "spa-api-react-fastapi", name: str = "Clinic bookings") -> dict[str, Any]:
    bp = await make(client, template=template, name=name)
    done = await client.post(f"/blueprints/{bp['id']}/finalize")
    assert done.status_code == 200, done.text
    return done.json()


async def test_scaffolding_makes_the_repository_the_project_and_a_plan_and_writes_no_file(
        client: AsyncClient, catalogued: AsyncSession, roots: Path, jobs: list, monkeypatch):
    bp = await finalized(client)
    folder = roots / "clinic"
    folder.mkdir()
    (folder / ".DS_Store").write_bytes(b"")                          # a Finder file does not make it "not empty"
    sent = answering(monkeypatch, {**PLAN, "openQuestions": [], "affectedFiles": ["api/app/main.py"]})
    made = await client.post(f"/blueprints/{bp['id']}/scaffold", json={"folder": str(folder)})
    assert made.status_code == 201, made.text
    body = made.json()

    project = await catalogued.get(Project, body["project"]["id"])
    assert project is not None and project.name == "Clinic bookings" and project.id == "clinic-bookings"
    assert project.source_kind == "local" and project.source_repo == str(folder.resolve())
    assert jobs == [("onboard", (project.id, "local", str(folder.resolve())))]
    # One empty commit, so a run can branch — and not a single file of the scaffold on disk.
    assert git_log(folder) == [f"Start Clinic bookings from blueprint {bp['id']}"]
    assert sorted(p.name for p in folder.iterdir()) == [".DS_Store", ".git"]
    # The plan's requirement is the recipe, and it names the blueprint it came from.
    plan = (await catalogued.execute(select(Plan).where(Plan.ref == body["planRef"]))).scalar_one()
    assert plan.project_id == project.id and plan.status == "draft"
    assert f"blueprint {bp['id']}" in plan.raw_requirement and "- api/app/main.py:" in plan.raw_requirement
    assert "- AGENTS.md:" in plan.raw_requirement
    assert plan.raw_requirement in sent[0][-1]["content"]
    scaffolded = body["blueprint"]
    assert scaffolded["status"] == "scaffolded" and scaffolded["projectId"] == project.id
    assert scaffolded["scaffolded"]["planRef"] == body["planRef"] and scaffolded["projectName"] == "Clinic bookings"
    # The decisions belong to the project now.
    facts = (await catalogued.execute(select(MemoryFact).where(MemoryFact.ref.in_(scaffolded["final"]["facts"])))).scalars().all()
    assert facts and {f.project_id for f in facts} == {project.id}
    # And it stays as it was built.
    again = await client.patch(f"/blueprints/{bp['id']}", json={"expectRevision": 1, "answers": {}})
    assert again.status_code == 409 and "save it as a template" in again.json()["detail"].lower()
    twice = await client.post(f"/blueprints/{bp['id']}/scaffold", json={"folder": str(roots)})
    assert twice.status_code == 409


async def test_a_scaffold_of_several_repositories_makes_a_source_for_each(
        client: AsyncClient, catalogued: AsyncSession, roots: Path, jobs: list, monkeypatch):
    bp = await finalized(client, template="mobile-api", name="Field app")
    folder = roots / "field"
    folder.mkdir()
    answering(monkeypatch, {**PLAN, "openQuestions": []})
    made = await client.post(f"/blueprints/{bp['id']}/scaffold", json={"folder": str(folder)})
    assert made.status_code == 201, made.text
    pid = made.json()["project"]["id"]
    real = folder.resolve()
    project = await catalogued.get(Project, pid)
    assert project.source_repo == str(real / "mobile")
    sources = (await catalogued.execute(select(ProjectSource).where(ProjectSource.project_id == pid))).scalars().all()
    assert [(s.label, s.repo, s.kind) for s in sources] == [("api", str(real / "api"), "local")]
    assert jobs == [("onboard", (pid, "local", str(real / "mobile"))), ("source", (pid, sources[0].id))]
    for label in ("mobile", "api"):
        assert git_log(real / label) == [f"Start Field app from blueprint {bp['id']}"]
    plan = (await catalogued.execute(select(Plan).where(Plan.ref == made.json()["planRef"]))).scalar_one()
    # The first repository's paths are plain, the second's under its label.
    assert "- app/_layout.tsx:" in plan.raw_requirement and "- api/app/main.py:" in plan.raw_requirement


async def test_scaffolding_is_refused_before_it_touches_anything(client: AsyncClient, catalogued: AsyncSession,
                                                                roots: Path, jobs: list, monkeypatch, tmp_path: Path):
    draft = await make(client)
    empty = roots / "empty"
    empty.mkdir()
    not_final = await client.post(f"/blueprints/{draft['id']}/scaffold", json={"folder": str(empty)})
    assert not_final.status_code == 409 and "Finalize" in not_final.json()["detail"]

    bp = await finalized(client, name="Second")
    full = roots / "full"
    full.mkdir()
    (full / "notes.txt").write_text("mine\n")
    busy = await client.post(f"/blueprints/{bp['id']}/scaffold", json={"folder": str(full)})
    assert busy.status_code == 409 and "not empty" in busy.json()["detail"]
    outside = await client.post(f"/blueprints/{bp['id']}/scaffold", json={"folder": str(tmp_path / "elsewhere")})
    assert outside.status_code == 403
    unknown = await client.post(f"/blueprints/{bp['id']}/scaffold", json={"folder": str(empty), "repos": ["nope"]})
    assert unknown.status_code == 422

    # No model: nothing compiled, and the folder is exactly as it was.
    no_lane(monkeypatch)
    refused = await client.post(f"/blueprints/{bp['id']}/scaffold", json={"folder": str(empty)})
    assert refused.status_code == 409 and refused.json()["detail"] == NO_MODEL
    assert list(empty.iterdir()) == [] and jobs == []
    assert (await catalogued.execute(select(Project).where(Project.id == "second"))).scalar_one_or_none() is None
    assert (await client.get(f"/blueprints/{bp['id']}")).json()["status"] == "final"


async def test_an_empty_scaffolded_repository_onboards_and_a_run_can_branch_from_it(schema: str, tmp_path: Path):
    """The folder a scaffold makes, read by the real onboarding job: the project becomes active, and the
    runtime finds a repository with a commit to branch from."""
    root = tmp_path / "fresh"
    await __import__("asyncio").to_thread(bps.prepare, [root], "Start Fresh from blueprint bp-test")
    db = Database(url=schema)
    pid = "blueprint-fresh"
    try:
        async with db.session() as s:
            await s.execute(delete(Project).where(Project.id == pid))
            s.add(Project(id=pid, name="Fresh", source_kind="local", source_repo=str(root), status="onboarding"))
        await onboard(db, _NoLanes(), pid, Spec(source="local", repo=str(root)))
        async with db.read() as s:
            project = await s.get(Project, pid)
            assert project is not None and project.status == "active" and project.files_count == 0
        found = _setup(project)
        assert found["base"] and found["tests"] is None
    finally:
        async with db.session() as s:
            await s.execute(delete(Project).where(Project.id == pid))
        await db.close()


class _NoLanes:
    """What retrieval asks of a gateway, answered by a gateway with no embedding lane."""

    def embed_lane(self):
        return None


# ── permissions ──────────────────────────────────────────────────
async def test_a_viewer_reads_and_cannot_design(client: AsyncClient, api: FastAPI, roots: Path):
    bp = await finalized(client)
    await client.post("/admin/users", json=VIEWER)
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.get(f"/blueprints/{bp['id']}")).status_code == 200
        assert (await viewer.get("/blueprints/catalogue")).status_code == 200
        for call in (viewer.post("/blueprints", json={"name": "x"}),
                     viewer.patch(f"/blueprints/{bp['id']}", json={"expectRevision": 1, "name": "y"}),
                     viewer.post(f"/blueprints/{bp['id']}/suggest"),
                     viewer.post(f"/blueprints/{bp['id']}/finalize"),
                     viewer.post("/blueprints/import", json={"text": "{}"}),
                     viewer.delete(f"/blueprints/{bp['id']}")):
            answer = await call
            assert answer.status_code == 403 and "plans:compile" in answer.json()["detail"]


async def test_scaffolding_needs_the_machine(client: AsyncClient, api: FastAPI, roots: Path, jobs: list,
                                             monkeypatch):
    bp = await finalized(client)
    folder = roots / "x"
    folder.mkdir()
    await client.post("/admin/users", json=ADMIN)
    async with _client(api) as admin:
        await admin.post("/auth/login", json={"email": ADMIN["email"], "password": ADMIN["password"]})
        denied = await admin.post(f"/blueprints/{bp['id']}/scaffold", json={"folder": str(folder)})
        assert denied.status_code == 403 and "machine:access" in denied.json()["detail"]
    off = real_settings().model_copy(update={"machine_roots": str(roots), "machine_access": False})
    monkeypatch.setattr(machine, "settings", lambda: off)
    gone = await client.post(f"/blueprints/{bp['id']}/scaffold", json={"folder": str(folder)})
    assert gone.status_code == 404 and list(folder.iterdir()) == []
