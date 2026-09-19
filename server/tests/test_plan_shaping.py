"""A plan shaped before it is dispatched: its steps edited by hand, comments left on it, a revision written
from those comments, the gate that pauses between steps, and files the plan may not change.

Everything goes through the HTTP routes, with the real gateway on one scripted lane (`tests/fixtures/lanes.py`)
— nothing here reaches the network. The step gate's runtime half belongs to the runtime; what is proven
here is the function it calls (`plans.step_gate_for`) and that dispatching records the choice.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Approval, CodeFile, CodeIndexRun, Plan, Project, ProjectReference, ProjectSource, Run, RunStep
from app.schemas.work import step_changes
from app.services.plans import step_gate_for
from tests.fixtures.lanes import PLAN, answering, no_lane
from tests import test_plan_routes as routes
from tests.test_plan_routes import VIEWER, _client, compile_tax

# The plan routes' own fixtures: the API over this test's session, and a client signed in as the Owner.
api = routes.api
client = routes.client

REVISION = {
    "steps": [{"label": "Map the change", "agent": "Architect", "detail": "Find every caller."},
              {"label": "Fix the place-of-supply check", "agent": "Backend Engineer", "detail": "Only the check."},
              {"label": "Fix the rounding", "agent": "Backend Engineer", "detail": "Round once, at the end."},
              {"label": "Tests", "agent": "QA Engineer", "detail": "Interstate and intrastate cases."},
              {"label": "Review", "agent": "Code Reviewer", "detail": "Against the tax rules."}],
    "openQuestions": ["Does this ship before the month-end close?"],
    "summary": "Split the fix into the check and the rounding, as asked.",
    "replies": [],
}


async def settled(client: AsyncClient, plan: dict) -> None:
    for _ in list(plan["openQuestions"]):
        assert (await client.post(f"/plans/{plan['ref']}/questions/0", json={"defer": True})).status_code == 200


async def activity(client: AsyncClient, action: str) -> list[dict]:
    return [e for e in (await client.get("/activity")).json() if e["action"] == action]


# ── steps edited by hand ─────────────────────────────────────────
async def test_a_step_is_edited_and_the_log_the_task_and_taste_all_hear_of_it(client: AsyncClient, monkeypatch):
    answering(monkeypatch, PLAN)
    plan = await compile_tax(client)
    second = plan["steps"][1]

    edited = await client.patch(f"/plans/{plan['ref']}/steps/{second['id']}",
                                json={"label": "Fix the place-of-supply check", "agent": "database engineer"})
    assert edited.status_code == 200, edited.text
    doc = edited.json()
    step = doc["steps"][1]
    assert step["label"] == "Fix the place-of-supply check" and step["agent"] == "Database Engineer"
    assert step["detail"] == second["detail"]                   # what was not sent is not touched
    assert doc["updatedAt"] > plan["updatedAt"]

    said = await activity(client, "Plan step edited")
    assert said and plan["ref"] in said[0]["detail"]
    assert '"Fix the calculation" → "Fix the place-of-supply check"' in said[0]["detail"]
    assert "Backend Engineer" in said[0]["detail"] and "Database Engineer" in said[0]["detail"]

    task = (await client.get(f"/tasks/{plan['task']['ref']}")).json()
    assert [i["label"] for i in task["checklist"]] == [s["label"] for s in doc["steps"]]
    assert "Database Engineer" in task["agents"]

    signals = (await client.get("/taste/signals", params={"project": "erp"})).json()
    edit = next(s for s in signals if s["kind"] == "plan_edit")
    assert edit["payload"]["op"] == "edit" and edit["payload"]["plan"] == plan["ref"]
    assert edit["payload"]["before"]["label"] == "Fix the calculation"
    assert edit["payload"]["after"]["agent"] == "Database Engineer"
    assert "Fix the place-of-supply check" in edit["summary"]


async def test_steps_are_added_removed_and_reordered_and_numbered_again(client: AsyncClient, monkeypatch):
    answering(monkeypatch, PLAN)
    plan = await compile_tax(client)
    ref = plan["ref"]

    added = await client.post(f"/plans/{ref}/steps", json={"label": "Back up TRANS_INVOICE", "agent": "DevOps Engineer",
                                                           "detail": "A dump before the fix.", "at": 2})
    assert added.status_code == 201, added.text
    steps = added.json()["steps"]
    assert [s["n"] for s in steps] == [1, 2, 3, 4, 5]
    assert steps[1]["label"] == "Back up TRANS_INVOICE" and steps[1]["agent"] == "DevOps Engineer"
    assert steps[2]["label"] == "Fix the calculation"
    assert len({s["id"] for s in steps}) == 5

    last = await client.post(f"/plans/{ref}/steps", json={"label": "Announce it", "agent": "Documentation Agent"})
    assert last.json()["steps"][-1]["label"] == "Announce it"

    gone = await client.delete(f"/plans/{ref}/steps/{steps[1]['id']}")
    assert gone.status_code == 200
    left = gone.json()["steps"]
    assert "Back up TRANS_INVOICE" not in [s["label"] for s in left]
    assert [s["n"] for s in left] == list(range(1, len(left) + 1))

    order = [s["id"] for s in reversed(left)]
    moved = await client.patch(f"/plans/{ref}/steps", json={"order": order})
    assert moved.status_code == 200
    assert [s["id"] for s in moved.json()["steps"]] == order
    assert [s["n"] for s in moved.json()["steps"]] == list(range(1, len(order) + 1))
    task = (await client.get(f"/tasks/{plan['task']['ref']}")).json()
    assert [i["label"] for i in task["checklist"]] == [s["label"] for s in moved.json()["steps"]]

    for action in ("Plan step added", "Plan step removed", "Plan steps reordered"):
        assert await activity(client, action), action
    ops = [s["payload"]["op"] for s in (await client.get("/taste/signals", params={"project": "erp"})).json()]
    assert {"add", "remove", "move"} <= set(ops)


async def test_shaping_refuses_what_it_cannot_do(api: FastAPI, client: AsyncClient, monkeypatch):
    answering(monkeypatch, PLAN)
    plan = await compile_tax(client)
    ref, first = plan["ref"], plan["steps"][0]["id"]

    nobody = await client.patch(f"/plans/{ref}/steps/{first}", json={"agent": "Somebody"})
    assert nobody.status_code == 422 and "not an agent here" in nobody.json()["detail"]
    blank = await client.patch(f"/plans/{ref}/steps/{first}", json={"label": "   "})
    assert blank.status_code == 422
    missing = await client.patch(f"/plans/{ref}/steps/{plan['id']}-s99", json={"label": "x"})
    assert missing.status_code == 404
    half = await client.patch(f"/plans/{ref}/steps", json={"order": [first]})
    assert half.status_code == 422 and "every step" in half.json()["detail"]
    assert (await client.patch("/plans/PLAN-404404/steps/x", json={"label": "x"})).status_code == 404

    for step in plan["steps"][1:]:
        assert (await client.delete(f"/plans/{ref}/steps/{step['id']}")).status_code == 200
    only = await client.delete(f"/plans/{ref}/steps/{first}")
    assert only.status_code == 409 and "only step" in only.json()["detail"]

    await client.post("/admin/users", json=VIEWER)
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        denied = await viewer.patch(f"/plans/{ref}/steps/{first}", json={"label": "mine"})
        assert denied.status_code == 403
        assert (await viewer.post(f"/plans/{ref}/comments", json={"body": "hm"})).status_code == 403
        assert (await viewer.get(f"/plans/{ref}/comments")).status_code == 200    # reading is for anyone signed in

    await settled(client, plan)
    assert (await client.post(f"/plans/{ref}/dispatch")).status_code == 200
    late = await client.patch(f"/plans/{ref}/steps/{first}", json={"label": "late"})
    assert late.status_code == 409 and "already under way" in late.json()["detail"]
    assert (await client.post(f"/plans/{ref}/comments", json={"body": "late"})).status_code == 409


# ── comments, and the revision they ask for ──────────────────────
async def test_comments_become_the_next_revision_and_what_changed_is_shown(client: AsyncClient, monkeypatch):
    answering(monkeypatch, PLAN)
    plan = await compile_tax(client)
    ref, fix = plan["ref"], plan["steps"][1]

    split = await client.post(f"/plans/{ref}/comments",
                              json={"kind": "split", "body": "Too big: the check and the rounding are two changes.",
                                    "stepId": fix["id"]})
    assert split.status_code == 201, split.text
    assert split.json()["step"] == {"n": 2, "label": "Fix the calculation"}
    assert split.json()["revision"] == 1 and split.json()["by"] == "Rajat" and split.json()["resolved"] is False
    why = await client.post(f"/plans/{ref}/comments", json={"kind": "why", "body": "Why an architect first?"})
    assert why.json()["step"] is None
    whole = await client.post(f"/plans/{ref}/comments", json={"kind": "remove", "body": "Drop it"})
    assert whole.status_code == 422                            # a remove is about a step
    listed = (await client.get(f"/plans/{ref}/comments")).json()
    assert listed["open"] == 2 and [c["kind"] for c in listed["items"]] == ["split", "why"]

    answer = {**REVISION, "replies": [{"comment": why.json()["id"], "reply": "Four callers need mapping first."},
                                      {"comment": 999999, "reply": "a comment it was never shown"}]}
    sent = answering(monkeypatch, answer)
    revised = await client.post(f"/plans/{ref}/revise")
    assert revised.status_code == 200, revised.text
    doc = revised.json()
    prompt = sent[-1][1]["content"]
    assert f"[{split.json()['id']}] split · on step 2 \"Fix the calculation\"" in prompt
    assert f"[{why.json()['id']}] why · on the whole plan" in prompt
    assert "1. Map the change (Architect)" in prompt           # the plan as it stood
    assert "split: the step it is on is too big" in sent[-1][0]["content"]

    assert doc["revision"] == 2
    assert [s["label"] for s in doc["steps"]] == [s["label"] for s in REVISION["steps"]]
    ops = {(c["op"], c["label"]) for c in doc["changes"]}
    assert ("changed", "Fix the place-of-supply check") in ops and ("added", "Fix the rounding") in ops
    assert ("same", "Map the change") in ops and ("moved", "Tests") in ops
    changed = next(c for c in doc["changes"] if c["op"] == "changed")
    assert changed["before"]["label"] == "Fix the calculation" and changed["was"] == 2 and changed["n"] == 2

    kept = doc["revisions"][0]
    assert kept["revision"] == 1 and kept["by"] == "Rajat" and kept["summary"] == REVISION["summary"]
    assert [s["label"] for s in kept["steps"]] == [s["label"] for s in PLAN["steps"]]
    assert kept["changes"] == doc["changes"]
    # Questions settled stay settled; a question already open is asked once, not twice.
    assert doc["openQuestions"].count("Does this ship before the month-end close?") == 1

    after = (await client.get(f"/plans/{ref}/comments")).json()
    assert after["open"] == 0 and after["revision"] == 2
    by_id = {c["id"]: c for c in after["items"]}
    assert all(c["resolved"] and c["revision"] == 1 for c in after["items"])
    assert by_id[why.json()["id"]]["reply"] == "Four callers need mapping first."
    assert by_id[split.json()["id"]]["step"] == {"n": 2, "label": "Fix the calculation"}   # the step it was on

    said = await activity(client, "Plan revised")
    assert said and "revision 1 → 2" in said[0]["detail"] and "2 comments" in said[0]["detail"]
    revise_signal = next(s for s in (await client.get("/taste/signals", params={"project": "erp"})).json()
                         if s["payload"].get("op") == "revise")
    assert revise_signal["payload"]["comments"][0]["kind"] == "split"

    nothing = await client.post(f"/plans/{ref}/revise")
    assert nothing.status_code == 409 and "no open comments" in nothing.json()["detail"]


async def test_a_revision_with_no_model_changes_nothing(client: AsyncClient, monkeypatch):
    answering(monkeypatch, PLAN)
    plan = await compile_tax(client)
    await client.post(f"/plans/{plan['ref']}/comments", json={"body": "Smaller steps, please."})
    no_lane(monkeypatch)
    refused = await client.post(f"/plans/{plan['ref']}/revise")
    assert refused.status_code == 409
    kept = (await client.get(f"/plans/{plan['ref']}")).json()
    assert kept["steps"] == plan["steps"] and kept["revision"] == 1 and kept["revisions"] == []
    assert (await client.get(f"/plans/{plan['ref']}/comments")).json()["open"] == 1


async def test_a_comment_is_resolved_and_reopened(client: AsyncClient, monkeypatch):
    answering(monkeypatch, PLAN)
    plan = await compile_tax(client)
    made = (await client.post(f"/plans/{plan['ref']}/comments", json={"kind": "risky", "body": "Touches money",
                                                                      "stepId": plan["steps"][1]["id"]})).json()
    done = await client.post(f"/plans/{plan['ref']}/comments/{made['id']}/resolve")
    assert done.status_code == 200 and done.json()["resolved"] is True and done.json()["by"] == "Rajat"
    again = await client.post(f"/plans/{plan['ref']}/comments/{made['id']}/resolve", json={"resolved": False})
    assert again.json()["resolved"] is False
    assert (await client.post(f"/plans/{plan['ref']}/comments/999999/resolve")).status_code == 404


def test_step_changes_pairs_steps_by_their_label():
    before = [{"n": 1, "label": "A", "agent": "x", "detail": ""}, {"n": 2, "label": "B", "agent": "x", "detail": ""},
              {"n": 3, "label": "C", "agent": "x", "detail": ""}]
    after = [{"n": 1, "label": "C", "agent": "x", "detail": ""}, {"n": 2, "label": "A", "agent": "y", "detail": ""},
             {"n": 3, "label": "D", "agent": "x", "detail": ""}]
    got = [(c["op"], c["label"], c["was"], c["n"]) for c in step_changes(before, after)]
    # C went from last to first; A kept its label and changed its owner; B's place was taken by D, paired
    # as one step changed rather than one removed and one added.
    assert got == [("moved", "C", 3, 1), ("changed", "A", 1, 2), ("changed", "D", 2, 3)]
    assert step_changes(before, before[:2])[-1] == {"op": "removed", "n": None, "was": 3, "label": "C",
                                                    "agent": "x", "detail": ""}
    assert step_changes(before, before) == [{"op": "same", "n": i, "was": i, "label": s["label"], "agent": "x",
                                             "detail": ""} for i, s in enumerate(before, 1)]


# ── pausing between steps ─────────────────────────────────────────
async def test_dispatch_can_pause_before_each_step_and_the_gate_says_when(client: AsyncClient, session: AsyncSession,
                                                                          monkeypatch):
    answering(monkeypatch, PLAN)
    plan = await compile_tax(client)
    await settled(client, plan)
    assert plan["stepGate"] is False
    dispatched = await client.post(f"/plans/{plan['ref']}/dispatch", json={"stepGate": True})
    assert dispatched.status_code == 200, dispatched.text
    assert dispatched.json()["stepGate"] is True
    said = await activity(client, "Plan dispatched")
    assert "pausing before each step" in said[0]["detail"]

    run = Run(id="r-gate", ref="RUN-GATE", project_id="erp", plan_id=plan["id"], branch="neurocode/t-gate",
              worktree="/nowhere", repo="/nowhere", diff_files=2, diff_insertions=10, diff_deletions=1)
    run.steps = [RunStep(n=1, kind="edit", label="Map the change", agent="Architect", status="done",
                         detail="Wrote 1 file."),
                 RunStep(n=2, kind="edit", label="Fix the calculation", agent="Backend Engineer",
                         detail="Place of supply."),
                 RunStep(n=3, kind="test", label="Run the project's tests", agent="QA Engineer")]
    session.add(run)
    await session.flush()

    assert await step_gate_for(session, run, run.steps[0]) is None       # dispatching was the go-ahead
    assert await step_gate_for(session, run, run.steps[2]) is None       # tests have a gate of their own
    gate = await step_gate_for(session, run, run.steps[1])
    assert gate is not None and gate.tool == "Step(2)" and gate.risk == "LOW"
    assert gate.title == "Continue RUN-GATE: step 2 of 2 · Fix the calculation"
    assert "done: step 1 · Map the change (Architect) — done · Wrote 1 file." in gate.payload
    assert "next: step 2 · Fix the calculation (Backend Engineer)" in gate.payload
    assert "refuse and the run stops here" in gate.reason

    session.add(Approval(id="ap-run-gate-2", ref="APR-GATE", title=gate.title, tool=gate.tool, status="approved",
                         run_ref="RUN-GATE", step=2, project_id="erp"))
    await session.flush()
    assert await step_gate_for(session, run, run.steps[1]) is None       # a person already said go on

    ungated = await session.get(Plan, plan["id"])
    ungated.step_gate = False
    await session.flush()
    run.steps[2].kind = "edit"                                           # an edit step, but the plan has no gate
    assert await step_gate_for(session, run, run.steps[2]) is None


# ── files the plan may not change ────────────────────────────────
async def test_a_file_in_a_reference_or_a_referenced_project_is_read_only(client: AsyncClient, session: AsyncSession,
                                                                          tmp_path: Path, monkeypatch):
    root = tmp_path / "shop"
    root.mkdir()
    session.add(Project(id="shop", name="Shop", source_kind="local", source_repo=str(root)))
    await session.flush()
    session.add_all([
        ProjectSource(project_id="shop", label="design", kind="local", repo=str(tmp_path / "design"), status="active",
                      role="reference"),
        ProjectSource(project_id="shop", label="api", kind="local", repo=str(tmp_path / "api"), status="active"),
        ProjectReference(project_id="shop", referenced_id="erp", note="the ledger it posts to"),
        CodeFile(project_id="shop", path="app/cart.py", lang="Python", module="app", lines=3),
        CodeIndexRun(project_id="shop", root=str(root), ms=1, files=1),
    ])
    await session.flush()
    sent = answering(monkeypatch, {**PLAN, "openQuestions": [], "affectedFiles": [
        "app/cart.py", "design/tokens.css", "erp/billing/tax.py", "api/routes.py"]})
    made = await client.post("/plans/compile", json={"requirement": "Show tax in the cart", "projectId": "shop"})
    assert made.status_code == 201, made.text
    check = made.json()["fileCheck"]
    assert check["readOnly"] == {"design/tokens.css": "reference, read only",
                                 "erp/billing/tax.py": "referenced project Legacy ERP, read only"}
    assert check["newFiles"] == ["api/routes.py"]               # a code source's new file is still new
    assert made.json()["affectedFiles"] == ["app/cart.py", "design/tokens.css", "erp/billing/tax.py", "api/routes.py"]
    prompt = sent[0][1]["content"]
    assert "Read only — read these for context" in prompt and "- design/ — reference, read only" in prompt
    assert "- erp/ — referenced project" in prompt
