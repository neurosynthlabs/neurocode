"""Compiling a requirement, settling what it could not decide, and dispatching it.

A plan is only ever a model's, so the gateway here is a real one with one lane whose provider call
answers from a script (`tests/fixtures/lanes.py`) — the routing, the parsing and the ledger are all the
real ones. With no lane at all, compiling is refused and writes nothing. Nothing in this file reaches
the network.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway import Gateway, ProviderError
from app.ai.ledger import MemoryLedger
from app.api import deps
from app.api.app import create_api
from app.models import Chunk, CodeFile, CodeIndexRun, Plan, Project, Task
from app.secrets import Secrets
from app.services.errors import NO_MODEL
from tests.fixtures.lanes import LANE_MODEL, PLAN, answering, no_lane
from tests.fixtures.workspace import load_workspace

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}
VIEWER = {"email": "view@example.com", "name": "Viewer", "password": "another long passphrase", "roles": ["viewer"]}
TAX = "Invoice tax is wrong: CGST and SGST come out reversed on interstate orders. Fix the TRANS_INVOICE table."


@pytest_asyncio.fixture
async def api(session: AsyncSession, tmp_path: Path) -> FastAPI:
    await load_workspace(session)
    made = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

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


async def compile_tax(client: AsyncClient) -> dict:
    made = await client.post("/plans/compile", json={"requirement": TAX, "projectId": "erp"})
    assert made.status_code == 201, made.text
    return made.json()


async def plan_count(session: AsyncSession) -> tuple[int, int]:
    return ((await session.execute(select(func.count()).select_from(Plan))).scalar_one(),
            (await session.execute(select(func.count()).select_from(Task))).scalar_one())


async def test_a_requirement_becomes_a_plan_and_the_task_that_carries_it(client: AsyncClient, monkeypatch):
    sent = answering(monkeypatch, PLAN)
    plan = await compile_tax(client)

    assert plan["ref"].startswith("PLAN-") and plan["projectId"] == "erp"
    assert plan["rawRequirement"] == TAX                      # your words are kept, not rewritten away
    assert plan["compiler"]["provider"] == "groq" and plan["compiler"]["model"] == LANE_MODEL
    assert TAX in sent[0][1]["content"]                       # the model was asked about this requirement
    assert plan["risk"] == "HIGH" and plan["confidence"] == 72     # the model's words, not a formula's
    assert [s["agent"] for s in plan["steps"]] == ["Architect", "Backend Engineer", "QA Engineer", "Code Reviewer"]
    assert plan["steps"][0]["state"] == "todo" and plan["workflowId"] is None
    assert plan["status"] == "draft" and plan["requestedBy"] == "Rajat"

    task = plan["task"]
    assert task["ref"].startswith("TASK-") and task["status"] == "planning"
    assert len(task["checklist"]) == len(plan["steps"])
    assert task["agents"] and task["projectId"] == "erp"

    listed = (await client.get("/plans")).json()
    assert plan["ref"] in [p["ref"] for p in listed]


async def test_a_model_that_gives_no_confidence_is_given_none(client: AsyncClient, monkeypatch):
    answering(monkeypatch, {k: v for k, v in PLAN.items() if k != "confidence"})
    assert (await compile_tax(client))["confidence"] is None


async def test_with_no_model_compiling_is_refused_and_writes_nothing(client: AsyncClient,
                                                                     session: AsyncSession, monkeypatch):
    no_lane(monkeypatch)
    before = await plan_count(session)
    refused = await client.post("/plans/compile", json={"requirement": TAX, "projectId": "erp"})
    assert refused.status_code == 409 and refused.json()["detail"] == NO_MODEL
    assert "Models → Keys" in NO_MODEL and "Groq, Gemini or Cloudflare" in NO_MODEL
    assert await plan_count(session) == before


async def test_a_provider_that_fails_is_passed_through_with_its_reason(client: AsyncClient,
                                                                       session: AsyncSession, monkeypatch):
    answering(monkeypatch, ProviderError(503, "the lane is down"))
    before = await plan_count(session)
    failed = await client.post("/plans/compile", json={"requirement": TAX, "projectId": "erp"})
    assert failed.status_code == 502 and "the lane is down" in failed.json()["detail"]
    assert await plan_count(session) == before


@pytest.mark.parametrize(("why", "status"), [("no lane", 409), ("provider failed", 502)])
async def test_recompiling_without_an_answer_is_refused_and_leaves_the_plan_as_it_was(
        client: AsyncClient, monkeypatch, why: str, status: int):
    answering(monkeypatch, PLAN)
    plan = await compile_tax(client)
    if why == "no lane":
        no_lane(monkeypatch)
    else:
        answering(monkeypatch, ProviderError(503, "the lane is down"))
    refused = await client.post(f"/plans/{plan['ref']}/recompile")
    assert refused.status_code == status
    kept = (await client.get(f"/plans/{plan['ref']}")).json()
    assert kept["steps"] == plan["steps"] and kept["compiler"] == plan["compiler"]


async def test_it_refuses_to_guess_and_dispatch_waits_for_you(client: AsyncClient, monkeypatch):
    answering(monkeypatch, PLAN)
    plan = await compile_tax(client)
    assert plan["openQuestions"] == PLAN["openQuestions"]

    refused = await client.post(f"/plans/{plan['ref']}/dispatch")
    assert refused.status_code == 409 and "open question" in refused.json()["detail"]

    for _ in list(plan["openQuestions"]):
        answered = await client.post(f"/plans/{plan['ref']}/questions/0", json={"defer": True})
        assert answered.status_code == 200

    dispatched = await client.post(f"/plans/{plan['ref']}/dispatch")
    assert dispatched.status_code == 200
    assert dispatched.json()["status"] == "dispatched"
    assert "runRef" not in dispatched.json()                  # this project has no code on this machine

    again = await client.post(f"/plans/{plan['ref']}/dispatch")
    assert again.status_code == 409 and "already under way" in again.json()["detail"]


async def test_dispatching_moves_the_task_it_carries(client: AsyncClient, monkeypatch):
    answering(monkeypatch, PLAN)
    plan = await compile_tax(client)
    for _ in list(plan["openQuestions"]):
        await client.post(f"/plans/{plan['ref']}/questions/0", json={"defer": True})
    await client.post(f"/plans/{plan['ref']}/dispatch")

    task = (await client.get(f"/tasks/{plan['task']['ref']}")).json()
    assert task["status"] == "in_progress"


async def test_recompiling_keeps_what_was_already_answered(client: AsyncClient, monkeypatch):
    sent = answering(monkeypatch, PLAN, PLAN)
    plan = await compile_tax(client)
    question = plan["openQuestions"][0]
    await client.post(f"/plans/{plan['ref']}/questions/0",
                      json={"answer": "Backfill the 41 affected invoices."})

    again = await client.post(f"/plans/{plan['ref']}/recompile")
    assert again.status_code == 200
    body = again.json()
    assert {"q": question, "a": "Backfill the 41 affected invoices."} in body["answered"]
    assert question not in body["openQuestions"]              # settled once, never asked again
    assert body["steps"] and body["compiler"]["provider"] == "groq"
    assert "Backfill the 41 affected invoices." in sent[1][1]["content"]   # the answer went to the model


# ── grounding: instructions, retrieval, and the files checked ───
LAB = "tax-lab"
GST = "Interstate invoices charge CGST instead of IGST — fix apply_gst so the place of supply decides."
INDEXED = ("billing/tax.py", "billing/tax_rules.py", "billing/util.py", "reports/util.py")
GROUNDED_PLAN = {**PLAN, "affectedFiles": ["billing/tax.py", "./tax_rules.py", "billing/igst.py", "util.py"],
                 "acceptanceCriteria": ["An interstate invoice shows one IGST line and no CGST line",
                                        "  An intrastate invoice still splits CGST and SGST  ", "", 7,
                                        "An interstate invoice shows one IGST line and no CGST line"]}


@pytest_asyncio.fixture
async def lab(session: AsyncSession, tmp_path: Path) -> Path:
    """A project whose checkout has an AGENTS.md and a path-scoped rule, an index of four files, and the
    retrieval pieces a search for the invoice requirement finds."""
    root = tmp_path / "lab"
    (root / ".claude" / "rules").mkdir(parents=True)
    (root / "AGENTS.md").write_text("# Tax lab\n\nRun the tests with `pytest -q`.\n<!-- editors only -->\n")
    (root / ".claude" / "rules" / "billing.md").write_text(
        "---\npaths: billing/**/*.py\n---\nMoney is Decimal, never float.\n")
    (root / ".claude" / "rules" / "ui.md").write_text("---\npaths: web/**/*.tsx\n---\nUse the design tokens.\n")
    for path in INDEXED:
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("def apply_gst(invoice):\n    return invoice\n")
    session.add(Project(id=LAB, name="Tax Lab", source_kind="local", source_repo=str(root)))
    await session.flush()
    session.add_all([CodeFile(project_id=LAB, path=path, lang="Python", module=path.split("/")[0], lines=2)
                     for path in INDEXED])
    session.add(CodeIndexRun(project_id=LAB, root=str(root), ms=10, files=len(INDEXED)))
    session.add_all([
        Chunk(project_id=LAB, kind="code", ref="billing/tax.py#apply_gst:1", path="billing/tax.py", line=1,
              title="apply_gst · function", body="billing/tax.py:1 · function apply_gst\n"
                                                  "def apply_gst(invoice): interstate invoices place of supply IGST"),
        Chunk(project_id=LAB, kind="doc", ref="docs/tax.md#0", path="docs/tax.md", line=1,
              title="docs/tax.md · Tax", body="How interstate invoices are taxed: IGST, never CGST."),
    ])
    await session.flush()
    return root


async def compile_gst(client: AsyncClient) -> dict:
    made = await client.post("/plans/compile", json={"requirement": GST, "projectId": LAB})
    assert made.status_code == 201, made.text
    return made.json()


async def test_the_compiler_is_handed_the_instructions_and_the_pieces_in_a_stable_order(
        client: AsyncClient, lab: Path, monkeypatch):
    sent = answering(monkeypatch, GROUNDED_PLAN)
    plan = await compile_gst(client)

    prompt = sent[0][1]["content"]
    assert "=== AGENTS.md ===" in prompt and "pytest -q" in prompt
    assert "editors only" not in prompt                       # an HTML comment is for people, not the model
    assert "Money is Decimal" in prompt                       # retrieval found billing code, so the rule applies
    assert "design tokens" not in prompt                      # a rule about other files is not handed over
    assert "[code · billing/tax.py#apply_gst:1]" in prompt and "[doc · docs/tax.md#0]" in prompt
    # The stable parts first, so a provider's prompt cache can reuse the opening: instructions, then the
    # pieces this requirement found, then the requirement itself.
    assert prompt.index("pytest -q") < prompt.index("[code · billing/tax.py") < prompt.index(GST)
    assert "acceptanceCriteria" in sent[0][0]["content"]      # the schema asks for criteria

    kinds = [(g["kind"], g["path"]) for g in plan["grounding"]]
    assert ("instructions", "AGENTS.md") in kinds and ("instructions", ".claude/rules/billing.md") in kinds
    assert ("instructions", ".claude/rules/ui.md") not in kinds
    assert {"kind": "code", "ref": "billing/tax.py#apply_gst:1", "path": "billing/tax.py"} in plan["grounding"]
    assert {"kind": "doc", "ref": "docs/tax.md#0", "path": "docs/tax.md"} in plan["grounding"]
    agents = next(g for g in plan["grounding"] if g["path"] == "AGENTS.md")
    assert len(agents["ref"]) == 12                           # the content's sha1, to tell if it changed since

    stored = (await client.get(f"/plans/{plan['ref']}")).json()
    assert stored["grounding"] == plan["grounding"]


async def test_named_files_are_checked_against_the_index_and_new_ones_are_said_to_be_new(
        client: AsyncClient, lab: Path, monkeypatch):
    answering(monkeypatch, GROUNDED_PLAN)
    plan = await compile_gst(client)

    # Kept as the index holds them; a bare name that matches one indexed file becomes that file; a name
    # the index does not hold stays, marked new; a name that matches two files is not guessed between.
    assert plan["affectedFiles"] == ["billing/tax.py", "billing/tax_rules.py", "billing/igst.py", "util.py"]
    assert plan["fileCheck"] == {"checked": True, "newFiles": ["billing/igst.py"],
                                 "ambiguous": {"util.py": ["billing/util.py", "reports/util.py"]}}
    assert plan["task"]["files"] == 4
    assert plan["compiler"] == {"provider": "groq", "model": LANE_MODEL, "ms": plan["compiler"]["ms"]}


async def test_with_no_index_nothing_is_called_new(client: AsyncClient, monkeypatch):
    answering(monkeypatch, {**PLAN, "affectedFiles": ["billing/tax.py"]})
    plan = await compile_tax(client)                         # erp was never indexed
    assert plan["affectedFiles"] == ["billing/tax.py"]
    assert plan["fileCheck"] == {"checked": False, "newFiles": [], "ambiguous": {}}
    assert plan["grounding"] == []                            # no checkout, no pieces: it says it had nothing


async def test_the_compiler_proposes_acceptance_criteria_and_a_person_can_edit_them(
        api: FastAPI, client: AsyncClient, lab: Path, monkeypatch):
    answering(monkeypatch, GROUNDED_PLAN, GROUNDED_PLAN)
    plan = await compile_gst(client)
    assert plan["acceptanceCriteria"] == ["An interstate invoice shows one IGST line and no CGST line",
                                          "An intrastate invoice still splits CGST and SGST"]
    assert plan["criteriaEdited"] is False

    mine = ["An interstate invoice shows IGST", "   ", "A credit note mirrors its invoice"]
    edited = await client.patch(f"/plans/{plan['ref']}", json={"acceptanceCriteria": mine})
    assert edited.status_code == 200, edited.text
    assert edited.json()["acceptanceCriteria"] == ["An interstate invoice shows IGST",
                                                   "A credit note mirrors its invoice"]
    assert edited.json()["criteriaEdited"] is True
    log = (await client.get("/activity")).json()
    assert any(e["action"] == "Acceptance criteria edited" and plan["ref"] in e["detail"] for e in log)

    # A re-compile rewrites the plan, not a person's words.
    again = (await client.post(f"/plans/{plan['ref']}/recompile")).json()
    assert again["acceptanceCriteria"] == edited.json()["acceptanceCriteria"]

    too_many = await client.patch(f"/plans/{plan['ref']}", json={"acceptanceCriteria": ["x"] * 13})
    assert too_many.status_code == 422

    await client.post("/admin/users", json=VIEWER)
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        denied = await viewer.patch(f"/plans/{plan['ref']}", json={"acceptanceCriteria": ["mine"]})
        assert denied.status_code == 403

    for _ in list(again["openQuestions"]):
        await client.post(f"/plans/{plan['ref']}/questions/0", json={"defer": True})
    assert (await client.post(f"/plans/{plan['ref']}/dispatch")).status_code == 200
    settled = await client.patch(f"/plans/{plan['ref']}", json={"acceptanceCriteria": ["late"]})
    assert settled.status_code == 409 and "already under way" in settled.json()["detail"]


async def test_a_re_compile_replaces_criteria_nobody_edited(client: AsyncClient, lab: Path, monkeypatch):
    answering(monkeypatch, GROUNDED_PLAN, {**GROUNDED_PLAN, "acceptanceCriteria": ["IGST only, interstate"]})
    plan = await compile_gst(client)
    again = (await client.post(f"/plans/{plan['ref']}/recompile")).json()
    assert again["acceptanceCriteria"] == ["IGST only, interstate"] and again["criteriaEdited"] is False


async def test_a_criteria_edit_on_a_missing_plan_is_404(client: AsyncClient):
    missing = await client.patch("/plans/PLAN-404404", json={"acceptanceCriteria": ["x"]})
    assert missing.status_code == 404


# ── drafting a first AGENTS.md ───────────────────────────────────
async def test_drafting_agents_md_compiles_a_plan_from_the_code_index(client: AsyncClient, lab: Path,
                                                                       monkeypatch):
    refused = await client.post(f"/projects/{LAB}/instructions/draft")
    assert refused.status_code == 409 and "already has an AGENTS.md" in refused.json()["detail"]

    (lab / "AGENTS.md").unlink()
    sent = answering(monkeypatch, {**PLAN, "affectedFiles": ["AGENTS.md"], "openQuestions": []})
    made = await client.post(f"/projects/{LAB}/instructions/draft")
    assert made.status_code == 201, made.text
    plan = made.json()
    assert plan["projectId"] == LAB and plan["task"]["ref"].startswith("TASK-")
    assert plan["rawRequirement"].startswith("Write AGENTS.md at the root of the Tax Lab repository")
    # The ask carries what the index measured, not the project's name alone.
    assert "Python (4 files, 8 lines)" in plan["rawRequirement"]
    assert "billing (3 files, 6 lines)" in plan["rawRequirement"]
    assert plan["rawRequirement"] in sent[0][1]["content"]
    assert plan["fileCheck"]["newFiles"] == ["AGENTS.md"]   # it will be written, not found


async def test_drafting_agents_md_needs_code_and_an_index(client: AsyncClient, session: AsyncSession,
                                                          tmp_path: Path, monkeypatch):
    answering(monkeypatch, PLAN)
    nowhere = await client.post("/projects/erp/instructions/draft")
    assert nowhere.status_code == 409 and "no code on this machine" in nowhere.json()["detail"]

    session.add(Project(id="bare-lab", name="Bare Lab", source_kind="local", source_repo=str(tmp_path)))
    await session.flush()
    unindexed = await client.post("/projects/bare-lab/instructions/draft")
    assert unindexed.status_code == 409 and "Index Bare Lab first" in unindexed.json()["detail"]

    assert (await client.post("/projects/nope/instructions/draft")).status_code == 404
