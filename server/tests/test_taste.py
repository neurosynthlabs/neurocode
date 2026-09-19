"""Taste: the signals a person's decisions leave, the rules a model proposes from them, and a person's word
on each rule — then those rules handed to the compiler.

The gateway is the real one on one scripted lane (`tests/fixtures/lanes.py`); git is real, in a temporary
repository; nothing reaches the network. Signals the runtime would capture are also read back out of what
is recorded (`harvest`), and the test proves both paths key a moment the same way, so none counts twice.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Approval, Run, RunLog, TasteRule, TasteSignal
from app.services.maintenance import MaintenanceService
from app.services.taste import TasteService
from tests.fixtures.lanes import PLAN, answering, no_lane
from tests import test_plan_routes as routes
from tests.test_plan_routes import TAX, VIEWER, _client, compile_tax

# The plan routes' own fixtures: the API over this test's session, and a client signed in as the Owner.
api = routes.api
client = routes.client


async def edited_plan(client: AsyncClient, monkeypatch) -> dict:
    """A compiled plan with two of a person's edits on it: two plan_edit signals for erp."""
    answering(monkeypatch, PLAN)
    plan = await compile_tax(client)
    steps = plan["steps"]
    await client.patch(f"/plans/{plan['ref']}/steps/{steps[1]['id']}", json={"detail": "Money is Decimal, never float."})
    await client.post(f"/plans/{plan['ref']}/steps", json={"label": "Write the test first", "agent": "QA Engineer",
                                                           "at": 2})
    return plan


async def signal_ids(client: AsyncClient, **params) -> list[int]:
    return [s["id"] for s in (await client.get("/taste/signals", params=params)).json()]


async def test_learning_proposes_rules_that_rest_on_the_signals_and_counts_them_itself(client: AsyncClient,
                                                                                        monkeypatch):
    await edited_plan(client, monkeypatch)
    ids = await signal_ids(client, project="erp")
    assert len(ids) == 2
    before = (await client.get("/taste/rules", params={"project": "erp"})).json()
    assert before["items"] == [] and before["signals"]["unread"] == 2
    assert before["signals"]["byKind"]["plan_edit"] == 2

    sent = answering(monkeypatch, {"rules": [
        {"text": "Write the test before the fix.", "supports": [ids[0], 424242], "contradicts": [ids[1]]},
        {"text": "Money is Decimal, never float", "supports": [ids[1]]},
        {"text": "A rule with nothing behind it", "supports": [], "contradicts": []},
        {"text": "short", "supports": [ids[0]]},
    ], "existing": [{"ref": "TASTE-999999", "supports": [ids[0]]}]})
    learnt = await client.post("/taste/learn", json={"projectId": "erp"})
    assert learnt.status_code == 200, learnt.text
    body = learnt.json()
    assert body["read"] == 2 and body["model"].startswith("groq · ")
    proposed = {r["text"]: r for r in body["proposed"]}
    # An id it was never handed is not evidence, a rule with none is not a rule, and the counts are ours.
    assert set(proposed) == {"Write the test before the fix.", "Money is Decimal, never float"}
    first = proposed["Write the test before the fix."]
    assert first["support"] == 1 and first["contradict"] == 1 and first["evidence"] == 2
    assert first["status"] == "proposed" and first["projectId"] == "erp" and first["ref"] == f"TASTE-{first['id']}"
    prompt = sent[0][1]["content"]
    assert f"[{ids[0]}] plan_edit · " in prompt and "Money is Decimal, never float." in prompt
    assert "Propose at most 8 rules" in sent[0][0]["content"]

    after = (await client.get("/taste/rules", params={"project": "erp"})).json()
    assert after["counts"] == {"proposed": 2, "active": 0, "retired": 0} and after["signals"]["unread"] == 0
    log = (await client.get("/activity")).json()
    assert any(e["action"] == "Taste learnt" and "2 signals read · 2 rules proposed" in e["detail"] for e in log)

    evidence = (await client.get(f"/taste/rules/{first['id']}/evidence")).json()
    stances = {s["id"]: s["stance"] for s in evidence["signals"]}
    assert stances == {ids[0]: "for", ids[1]: "against"}

    nothing = await client.post("/taste/learn", json={"projectId": "erp"})
    assert nothing.status_code == 409 and "Nothing new to learn from" in nothing.json()["detail"]


async def test_the_same_rule_said_again_is_more_evidence_not_a_second_rule(client: AsyncClient, monkeypatch):
    await edited_plan(client, monkeypatch)
    ids = await signal_ids(client, project="erp")
    answering(monkeypatch, {"rules": [{"text": "Write the test before the fix", "supports": [ids[1]]}]})
    first = (await client.post("/taste/learn", json={"projectId": "erp"})).json()["proposed"][0]

    plan = (await client.get("/plans")).json()[0]
    await client.patch(f"/plans/{plan['ref']}/steps/{plan['steps'][0]['id']}", json={"label": "Test first, then map"})
    fresh = (await signal_ids(client, project="erp", unread=True))
    answering(monkeypatch, {"rules": [{"text": "write the test before the fix.", "supports": fresh}],
                            "existing": [{"ref": first["ref"], "supports": fresh}]})
    again = (await client.post("/taste/learn", json={"projectId": "erp"})).json()
    assert again["proposed"] == []
    assert [r["id"] for r in again["weighed"]] == [first["id"]] and again["weighed"][0]["support"] == 2


async def test_a_person_adopts_rejects_rewords_and_switches_rules(api: FastAPI, client: AsyncClient,
                                                                  session: AsyncSession, monkeypatch):
    await edited_plan(client, monkeypatch)
    ids = await signal_ids(client, project="erp")
    answering(monkeypatch, {"rules": [{"text": "Money is Decimal, never float", "supports": ids},
                                      {"text": "Every step names the files it touches", "supports": [ids[0]]}]})
    made = {r["text"]: r for r in (await client.post("/taste/learn", json={"projectId": "erp"})).json()["proposed"]}
    money, files = made["Money is Decimal, never float"], made["Every step names the files it touches"]

    adopted = await client.patch(f"/taste/rules/{money['id']}", json={"status": "active"})
    assert adopted.status_code == 200 and adopted.json()["status"] == "active"
    assert adopted.json()["adoptedBy"] == "Rajat" and adopted.json()["adoptedAt"]
    rejected = await client.patch(f"/taste/rules/{files['id']}", json={"status": "retired"})
    assert rejected.json()["status"] == "retired"
    reworded = await client.patch(f"/taste/rules/{money['id']}", json={"text": "Money is Decimal, never a float."})
    assert reworded.json()["text"] == "Money is Decimal, never a float."
    assert (await client.patch(f"/taste/rules/{money['id']}", json={"text": "no"})).status_code == 422
    assert (await client.patch(f"/taste/rules/{files['id']}", json={"text": "Reword a retired one"})).status_code == 409
    assert (await client.patch("/taste/rules/999999", json={"status": "active"})).status_code == 404
    log = [e["detail"] for e in (await client.get("/activity")).json() if e["action"] == "Taste rule changed"]
    assert any("adopted" in d for d in log) and any("rejected" in d for d in log) and any("reworded" in d for d in log)

    # An adopted rule is handed to the compiler after the instructions, and the plan says it was.
    sent = answering(monkeypatch, PLAN)
    plan = await compile_tax(client)
    prompt = sent[0][1]["content"]
    assert f"- [{money['ref']}] Money is Decimal, never a float." in prompt
    assert "Every step names the files" not in prompt                   # a rejected rule is never handed over
    assert prompt.index(money["ref"]) < prompt.index(TAX)
    assert {"kind": "taste", "ref": money["ref"], "path": ""} in plan["grounding"]
    compiled = next(e for e in (await client.get("/activity")).json() if e["action"] == "Requirement compiled")
    assert "1 taste rule" in compiled["detail"]

    switched = await client.patch(f"/taste/rules/{money['id']}", json={"status": "retired"})
    assert switched.json()["status"] == "retired"
    sent = answering(monkeypatch, PLAN)
    await compile_tax(client)
    assert money["ref"] not in sent[0][1]["content"]

    await client.post("/admin/users", json=VIEWER)
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.get("/taste/rules", params={"project": "erp"})).status_code == 200
        assert (await viewer.patch(f"/taste/rules/{money['id']}", json={"status": "active"})).status_code == 403
        assert (await viewer.post("/taste/learn", json={"projectId": "erp"})).status_code == 403


async def test_learning_with_no_model_marks_nothing_read(client: AsyncClient, monkeypatch):
    await edited_plan(client, monkeypatch)
    no_lane(monkeypatch)
    refused = await client.post("/taste/learn", json={"projectId": "erp"})
    assert refused.status_code == 409
    assert len(await signal_ids(client, project="erp", unread=True)) == 2


# ── the moments the runtime leaves, read back ────────────────────
async def test_decisions_and_reworks_are_gathered_once_whoever_sees_them_first(client: AsyncClient,
                                                                               session: AsyncSession):
    me = (await client.get("/auth/me")).json()["user"]
    common = {"project_id": "erp", "worktree": "/nowhere", "repo": "/nowhere"}
    session.add_all([
        Run(id="r-ok", ref="RUN-OK", branch="neurocode/ok", requirement="Round tax once\n\nmore", status="done",
            review={"findings": [{"severity": "HIGH", "file": "tax.py", "note": "float used for money"}],
                    "verdict": "fix the float"}, **common),
        Run(id="r-no", ref="RUN-NO", branch="neurocode/no", requirement="Rename things", status="cancelled", **common),
        Run(id="r-back", ref="RUN-BACK", branch="neurocode/back", requirement="Add IGST", status="cancelled",
            review={"reworkedAs": "RUN-AGAIN"}, **common),
        Run(id="r-again", ref="RUN-AGAIN", branch="neurocode/again", requirement="Add IGST", **common),
        Run(id="r-auto", ref="RUN-AUTO", branch="neurocode/auto", requirement="Add CESS", status="cancelled",
            review={"reworkedAs": "RUN-AUTO2"}, **common),
        Run(id="r-auto2", ref="RUN-AUTO2", branch="neurocode/auto2", requirement="Add CESS", **common),
    ])
    await session.flush()
    session.add_all([
        Approval(id="ap-ok", ref="APR-OK", title="Accept RUN-OK", tool="Merge(neurocode/ok)", status="approved",
                 run_ref="RUN-OK", step=4, project_id="erp", decided_by=me["id"]),
        Approval(id="ap-no", ref="APR-NO", title="Accept RUN-NO", tool="Merge(neurocode/no)", status="denied",
                 run_ref="RUN-NO", step=4, project_id="erp", decided_by=me["id"]),
        Approval(id="ap-back", ref="APR-BACK", title="Accept RUN-BACK", tool="Merge(neurocode/back)",
                 status="denied", run_ref="RUN-BACK", step=4, project_id="erp"),
        Approval(id="ap-tests", ref="APR-TESTS", title="Run tests", tool="Bash(pytest)", status="approved",
                 run_ref="RUN-OK", step=2, project_id="erp"),
        RunLog(run_id="r-again", level="info", line="rework of RUN-BACK, sent back by Rajat: Keep IGST in its own column"),
        RunLog(run_id="r-auto2", level="info", line="rework of RUN-AUTO, sent back by Completion check: criterion 2"),
    ])
    await session.flush()

    # The runtime saw the accepted one as it happened.
    taste = TasteService(session)
    ok = await session.get(Run, "r-ok")
    gate = await session.get(Approval, "ap-ok")
    assert await taste.on_verdict(ok, gate, approved=True, by_user_id=me["id"]) is not None

    assert await taste.harvest("erp") == 2                     # the refusal and the rework; nothing twice
    assert await taste.harvest("erp") == 0
    signals = (await client.get("/taste/signals", params={"project": "erp"})).json()
    by_kind = {s["kind"]: s for s in signals}
    assert set(by_kind) == {"accept", "refuse", "rework_note"}
    accept = by_kind["accept"]
    assert accept["payload"]["run"] == "RUN-OK" and accept["payload"]["requirement"] == "Round tax once"
    assert accept["payload"]["findings"][0]["note"] == "float used for money" and accept["by"] == "Rajat"
    assert "the review found: HIGH float used for money" in accept["summary"]
    assert by_kind["refuse"]["payload"]["run"] == "RUN-NO"
    rework = by_kind["rework_note"]
    assert rework["payload"]["notes"] == "Keep IGST in its own column" and rework["payload"]["again"] == "RUN-AGAIN"
    assert rework["by"] == "Rajat"                              # the name on the run is this account's
    # A run sent back by the completion check is the runtime trying again, and a refusal that sent a run
    # back is the rework already: neither is a signal of its own.
    assert not any(s["payload"].get("run") in ("RUN-AUTO", "RUN-BACK") and s["kind"] != "rework_note"
                   for s in signals)
    assert sum(1 for s in signals if s["payload"].get("run") == "RUN-AUTO") == 0


def _git(repo: Path, *args: str, who: tuple[str, str] = ("Rajat", "rajat@example.com")) -> str:
    done = subprocess.run(["git", "-c", f"user.name={who[0]}", "-c", f"user.email={who[1]}", "-c",
                           "commit.gpgsign=false", *args], cwd=repo, capture_output=True, text=True, check=True)
    return done.stdout.strip()


async def test_a_persons_commits_after_the_agents_are_edit_deltas(client: AsyncClient, session: AsyncSession,
                                                                  tmp_path: Path):
    repo = tmp_path / "shop"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "tax.py").write_text("RATE = 0.18\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "neurocode/t9")
    (repo / "tax.py").write_text("RATE = 0.18\n\ndef tax(x):\n    return x * RATE\n")
    _git(repo, "commit", "-q", "-am", "agent: add tax()", who=("NeuroCode", "neurocode@localhost"))
    (repo / "tax.py").write_text("from decimal import Decimal\nRATE = Decimal('0.18')\n\ndef tax(x):\n    return x * RATE\n")
    _git(repo, "commit", "-q", "-am", "Money is Decimal")
    session.add(Run(id="r-merged", ref="RUN-MERGED", project_id="erp", branch="neurocode/t9", base=base,
                    worktree=str(tmp_path / "wt"), repo=str(repo), status="done", requirement="Add tax()",
                    merged={"into": "main", "commit": "abc", "by": "Rajat", "at": "2026-09-19T10:00:00+00:00"}))
    await session.flush()

    taste = TasteService(session)
    assert await taste.harvest("erp") == 1
    assert await taste.harvest("erp") == 0                     # the same commit is one signal, however often read
    delta = next(s for s in (await client.get("/taste/signals", params={"project": "erp"})).json()
                 if s["kind"] == "edit_delta")
    assert delta["payload"]["subject"] == "Money is Decimal" and delta["payload"]["files"] == ["tax.py"]
    assert "+from decimal import Decimal" in delta["payload"]["patch"]
    assert delta["summary"] == 'Changed what an agent wrote on RUN-MERGED before merging: "Money is Decimal" in tax.py'


async def test_emptying_the_workspace_takes_the_taste_with_it(session: AsyncSession):
    session.add_all([TasteSignal(kind="plan_edit", payload={"plan": "PLAN-1", "op": "edit"}),
                     TasteRule(text="Workspace-wide rule", status="active")])
    await session.flush()
    await MaintenanceService(session).empty()
    for model in (TasteSignal, TasteRule):
        assert (await session.execute(select(func.count()).select_from(model))).scalar_one() == 0
