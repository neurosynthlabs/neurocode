import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import compiler
from app.db import TABLES, Store
from app.main import create_app

SEED = json.loads((Path(__file__).resolve().parent.parent / "seed" / "seed.json").read_text())
TAX = "Invoice mein tax galat aa raha hai — CGST/SGST interstate orders pe reverse ho raha hai. Fix karo."


@pytest.fixture
def client(tmp_path, monkeypatch):
    # never reach a real model from a test, whatever server/.env on this machine says
    monkeypatch.setenv("NEUROCODE_COMPILER", "rules")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with TestClient(create_app(str(tmp_path / "test.db"), env_file=None)) as c:
        yield c


def listen(client):
    got = []
    client.app.state.bus.listen(lambda kind, data: got.append((kind, data)))
    return got


def compile_tax(client, requirement=TAX):
    r = client.post("/plans/compile", json={"requirement": requirement, "projectId": "erp"})
    assert r.status_code == 201, r.text
    return r.json()


# ── the seed and the original surface ────────────────────────────
def test_health_counts_match_the_seed(client):
    body = client.get("/health").json()
    for key in TABLES:
        assert body["counts"][key] == len(SEED.get(key, [])), key
    assert body["compiler"] == {"provider": "rules", "model": "offline planner"}


def test_an_older_database_gains_the_new_tables(tmp_path):
    db = tmp_path / "old.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, doc TEXT NOT NULL)")
    c.execute("""INSERT INTO projects VALUES ('mine', '{"id": "mine"}')""")
    c.commit()
    c.close()
    store = Store(str(db))
    assert store.count("projects") == 1                      # its own data survives
    assert store.count("plans") == len(SEED["plans"])        # the missing tables arrive seeded


def test_approving_is_final_and_logged(client):
    pending = client.get("/approvals", params={"status": "pending"}).json()
    ref = pending[0]["ref"]
    r = client.post(f"/approvals/{ref}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["decidedAt"]
    assert client.post(f"/approvals/{ref}/deny").status_code == 409
    assert len(client.get("/approvals", params={"status": "pending"}).json()) == len(pending) - 1
    top = client.get("/activity", params={"limit": 1}).json()[0]
    assert top["action"] == "Approved" and ref in top["detail"] and top["actorKind"] == "human"


def test_unknown_approval_and_bad_decision(client):
    assert client.post("/approvals/APPR-999/approve").status_code == 404
    assert client.post("/approvals/APPR-118/maybe").status_code == 422


def test_every_change_is_streamed_as_a_document_and_a_log_line(client):
    got = listen(client)
    client.post("/approvals/APPR-120/deny")
    change = next(d for k, d in got if k == "change")
    event = next(d for k, d in got if k == "activity")
    assert change["op"] == "put" and change["collection"] == "approvals" and change["doc"]["status"] == "denied"
    assert event["action"] == "Denied" and "APPR-120" in event["detail"]


def test_memory_full_text_search(client):
    refs = [f["ref"] for f in client.get("/memory", params={"q": "TRANS"}).json()]
    assert "MEM-142" in refs
    # punctuation and FTS operators in user input must never reach FTS5 as syntax
    assert client.get("/memory", params={"q": 'TRANS_* AND "('}).status_code == 200


def test_pin_and_archive(client):
    fact = client.get("/memory").json()[-1]
    ref = fact["ref"]
    assert client.post(f"/memory/{ref}/pin", json={"pinned": not fact["pinned"]}).json()["pinned"] is (not fact["pinned"])
    client.post(f"/memory/{ref}/archive")
    assert ref not in [f["ref"] for f in client.get("/memory").json()]
    assert ref in [f["ref"] for f in client.get("/memory", params={"include_archived": True}).json()]


def test_checklist_toggle_persists(client):
    task = client.get("/tasks/TASK-492").json()
    item = next(c for c in task["checklist"] if not c["done"])
    client.post(f"/tasks/TASK-492/checklist/{item['id']}", json={"done": True})
    again = client.get("/tasks/TASK-492").json()
    assert next(c for c in again["checklist"] if c["id"] == item["id"])["done"] is True


def test_task_status_is_validated(client):
    assert client.patch("/tasks/TASK-492", json={"status": "shipped"}).status_code == 422
    assert client.patch("/tasks/TASK-000", json={"status": "done"}).status_code == 404
    assert client.patch("/tasks/TASK-492", json={"status": "review"}).json()["status"] == "review"


def test_reset_needs_explicit_confirmation(client):
    assert client.post("/admin/reset").status_code == 400
    client.post("/approvals/APPR-118/approve")
    assert client.post("/admin/reset", headers={"X-Confirm": "reset"}).status_code == 200
    assert client.get("/approvals", params={"status": "pending"}).json()[0]["status"] == "pending"


# ── onboarding ───────────────────────────────────────────────────
def test_onboarding_a_local_folder_measures_it(client, tmp_path):
    repo = tmp_path / "shop"
    (repo / "src" / "Billing").mkdir(parents=True)
    (repo / "src" / "Billing" / "InvoiceService.cs").write_text("class InvoiceService {\n}\n")
    (repo / "db").mkdir()
    (repo / "db" / "schema.sql").write_text("CREATE TABLE TRANS_INVOICE (id int);\nCREATE OR ALTER PROCEDURE SP_CalculateTax AS SELECT 1;\n")
    (repo / "node_modules" / "x").mkdir(parents=True)
    (repo / "node_modules" / "x" / "big.js").write_text("x\n" * 1000)
    r = client.post("/projects", json={"source": "local", "repo": str(repo),
                                       "rules": [{"id": "solid", "label": "Strict SOLID", "note": "one reason to change"}]})
    assert r.status_code == 201 and r.json()["status"] == "onboarding"
    p = next(x for x in client.get("/projects").json() if x["id"] == r.json()["id"])
    assert p["status"] == "active" and p["files"] == 2 and p["dbTables"] == 1 and p["storedProcs"] == 1
    assert {lang["name"] for lang in p["languages"]} == {"C#", "T-SQL"}   # node_modules is never read
    assert p["rules"][0]["label"] == "Strict SOLID" and 0 < p["understoodPct"] < 100
    actions = [e["action"] for e in client.get("/activity", params={"limit": 10}).json()]
    assert "Tree mapped" in actions and "Onboarding paused" in actions


def test_onboarding_refuses_unsafe_or_missing_input(client, tmp_path):
    assert client.post("/projects", json={"source": "git", "repo": "--upload-pack=touch /tmp/pwned"}).status_code == 422
    assert client.post("/projects", json={"source": "git", "repo": "git@github.com:org/repo.git", "branch": "-x"}).status_code == 422
    assert client.post("/projects", json={"source": "local", "repo": str(tmp_path / "nope")}).status_code == 422
    assert client.post("/projects", json={"source": "local", "repo": "relative/path"}).status_code == 422


def test_git_credentials_never_reach_the_record(client):
    # nothing listens on port 9, so the clone fails at once, and the token must not be stored anywhere
    r = client.post("/projects", json={"source": "git", "repo": "https://user:s3cret@127.0.0.1:9/org/repo.git"})
    assert r.status_code == 201
    everything = json.dumps(client.get("/projects").json()) + json.dumps(client.get("/activity").json())
    assert "s3cret" not in everything
    p = next(x for x in client.get("/projects").json() if x["id"] == r.json()["id"])
    assert p["status"] == "paused" and p["description"].startswith("Onboarding stopped")


# ── MCP ──────────────────────────────────────────────────────────
def test_mcp_registration_is_unique_and_untrusted(client):
    body = {"name": "ledger-tools", "transport": "stdio", "command": "npx -y ledger-tools-mcp", "scope": "project",
            "defaultEffect": "ask", "config": '{"mcpServers": {}}'}
    a = client.post("/mcp/servers", json=body).json()
    b = client.post("/mcp/servers", json=body).json()
    assert a["id"] == "ledger-tools" and b["id"] == "ledger-tools-2"
    assert a["status"] == "disconnected" and a["untrusted"] is True
    assert client.post("/mcp/servers", json={**body, "config": "not json"}).status_code == 422
    assert client.post("/mcp/servers", json={**body, "name": "Bad Name"}).status_code == 422


# ── memory conflicts ─────────────────────────────────────────────
def test_keeping_one_side_of_a_conflict_archives_the_other(client):
    conflict = client.get("/memory/conflicts").json()[0]
    loser = next(f for f in client.get("/memory").json() if f["id"] == conflict["b"])
    assert client.post(f"/memory/conflicts/{conflict['id']}/resolve", json={"keep": "a"}).status_code == 200
    assert conflict["id"] not in [c["id"] for c in client.get("/memory/conflicts").json()]
    assert loser["ref"] not in [f["ref"] for f in client.get("/memory").json()]
    assert client.post(f"/memory/conflicts/{conflict['id']}/resolve", json={"keep": "b"}).status_code == 409


# ── the compiler ─────────────────────────────────────────────────
def test_compiling_a_requirement_creates_a_plan_and_its_task(client):
    plan = compile_tax(client)
    assert plan["compiler"]["provider"] == "rules" and plan["status"] == "draft"
    assert plan["risk"] == "HIGH" and plan["steps"][-1]["label"] == "Your approval"   # money: your signature
    assert plan["cited"]                                                              # memory was consulted
    task = client.get(f"/tasks/{plan['taskRef']}").json()
    assert task["status"] == "planning" and len(task["checklist"]) == len(plan["steps"])
    assert client.get("/plans").json()[0]["ref"] == plan["ref"]                       # newest first


def test_a_model_answer_is_used_and_a_bad_one_falls_back(client, monkeypatch):
    monkeypatch.setenv("NEUROCODE_COMPILER", "auto")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    good = {"title": "Fix the interstate GST split", "businessRequirement": "b", "technicalRequirement": "t",
            "risk": "high", "confidence": "82", "layers": ["backend", "nonsense"], "openQuestions": [],
            "steps": [{"label": "Fix TaxService", "agent": "backend"}, {"label": "Test it", "agent": "QA"}]}
    monkeypatch.setitem(compiler.CALLS, "deepseek", lambda messages: "```json\n" + json.dumps(good) + "\n```")
    plan = compile_tax(client, "fix the tax split")
    assert plan["compiler"]["provider"] == "deepseek" and plan["risk"] == "HIGH" and plan["confidence"] == 82
    assert [s["agent"] for s in plan["steps"]] == ["Backend Engineer", "QA Engineer"]
    assert client.get(f"/tasks/{plan['taskRef']}").json()["layers"] == ["Backend"]

    monkeypatch.setitem(compiler.CALLS, "deepseek", lambda messages: "Sorry, I can't help with that.")
    plan = compile_tax(client, "fix the tax split")
    assert plan["compiler"]["provider"] == "rules"
    assert any(e["action"] == "Compiler fell back" for e in client.get("/activity", params={"limit": 5}).json())


def test_a_plan_dispatches_only_when_nothing_is_left_to_guess(client):
    plan = compile_tax(client)
    assert plan["openQuestions"]
    assert client.post(f"/plans/{plan['ref']}/dispatch").status_code == 409
    first = plan["openQuestions"][0]
    after = client.post(f"/plans/{plan['ref']}/questions/0", json={"answer": "Round at invoice level from 1 Oct."}).json()
    assert first not in after["openQuestions"] and after["answered"][0] == {"q": first, "a": "Round at invoice level from 1 Oct."}
    rule = next(f for f in client.get("/memory").json() if f["body"] == "Round at invoice level from 1 Oct.")
    assert rule["category"] == "business_rules" and rule["title"] == first
    for _ in after["openQuestions"]:
        client.post(f"/plans/{plan['ref']}/questions/0", json={"defer": True})
    done = client.post(f"/plans/{plan['ref']}/dispatch").json()
    assert done["status"] == "dispatched" and done["steps"][0]["state"] == "active"
    assert client.get(f"/tasks/{plan['taskRef']}").json()["status"] == "in_progress"
    assert client.post(f"/plans/{plan['ref']}/dispatch").status_code == 409
    assert client.post(f"/plans/{plan['ref']}/questions/0", json={"answer": "late"}).status_code == 404


def test_an_empty_answer_is_refused(client):
    plan = compile_tax(client)
    assert client.post(f"/plans/{plan['ref']}/questions/0", json={"answer": "   "}).status_code == 422


def test_recompiling_keeps_settled_questions_settled(client):
    plan = compile_tax(client)
    q = plan["openQuestions"][0]
    client.post(f"/plans/{plan['ref']}/questions/0", json={"defer": True})
    again = client.post(f"/plans/{plan['ref']}/recompile").json()
    assert q not in again["openQuestions"] and q in again["deferred"]


def test_a_rejected_key_is_reported_and_not_retried(client, monkeypatch):
    monkeypatch.setenv("NEUROCODE_COMPILER", "auto")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "a-revoked-key")
    monkeypatch.setattr(compiler, "_rejected", {})
    monkeypatch.setattr(compiler, "ollama_ready", lambda: False)
    calls = []

    def reject(messages):
        calls.append(messages)
        raise compiler.ProviderError(401, "Authentication Fails, your api key is invalid")

    monkeypatch.setitem(compiler.CALLS, "deepseek", reject)
    assert compile_tax(client)["compiler"]["provider"] == "rules"
    assert compile_tax(client)["compiler"]["provider"] == "rules"
    assert len(calls) == 1                                    # a key known to be bad is not sent again
    status = client.get("/health").json()["compiler"]
    assert status["provider"] == "rules" and "rejected" in status["note"]


def test_the_offline_planner_follows_memory_into_the_database(client):
    plan = compile_tax(client)
    assert "Database" in client.get(f"/tasks/{plan['taskRef']}").json()["layers"]
    assert plan["affectedFiles"] and all(not f.endswith((".md", ".csv")) for f in plan["affectedFiles"])
    assert any("backfill" in q for q in plan["openQuestions"])


# ── screen settings and final decisions ─────────────────────────
def test_a_setting_persists_and_a_described_change_is_logged(client):
    r = client.put("/prefs/skills.enabled", json={"value": {"api-contract": False}, "detail": "Skill api-contract disabled"})
    assert r.status_code == 200
    assert client.get("/prefs").json() == [{"id": "skills.enabled", "value": {"api-contract": False}}]
    top = client.get("/activity", params={"limit": 1}).json()[0]
    assert top["action"] == "Setting changed" and "api-contract" in top["detail"]
    client.put("/prefs/skills.enabled", json={"value": {"api-contract": True}})   # no detail: saved, not logged
    assert client.get("/prefs").json()[0]["value"] == {"api-contract": True}
    assert client.get("/activity", params={"limit": 1}).json()[0]["id"] == top["id"]
    assert client.put("/prefs/bad key!", json={"value": 1}).status_code == 422


def test_a_decision_is_final_and_logged(client):
    body = {"value": "accepted", "action": "Review accepted", "detail": "REV-2451 · tax resolver", "projectId": "erp"}
    assert client.post("/decisions/review:rv1", json=body).status_code == 201
    assert client.post("/decisions/review:rv1", json={**body, "value": "changes"}).status_code == 409
    assert client.get("/decisions").json()[0]["value"] == "accepted"
    assert client.get("/activity", params={"limit": 1}).json()[0]["action"] == "Review accepted"
