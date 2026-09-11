import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai import gateway
from app.auth import COOKIE
from app.db import TABLES, Store
from app.main import create_app

SEED = json.loads((Path(__file__).resolve().parent.parent / "seed" / "seed.json").read_text())
TAX = "Invoice mein tax galat aa raha hai — CGST/SGST interstate orders pe reverse ho raha hai. Fix karo."
H = {"X-NC-Client": "test"}
OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
PASSWORD = "a long enough password"


@pytest.fixture
def app(tmp_path, monkeypatch):
    # never reach a real model from a test, whatever server/.env on this machine says
    monkeypatch.setenv("NEUROCODE_COMPILER", "rules")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("NEUROCODE_SECRETS", raising=False)
    return create_app(str(tmp_path / "test.db"), env_file=None)


@pytest.fixture
def anon(app):
    with TestClient(app, headers=H) as c:
        yield c


@pytest.fixture
def client(app):
    """Signed in as the workspace's first Owner."""
    with TestClient(app, headers=H) as c:
        assert c.post("/auth/setup", json=OWNER).status_code == 201
        yield c


def person(client, role, email=None):
    """Someone else with one role, signed in on a client of their own."""
    email = email or f"{role}@example.com"
    r = client.post("/admin/users", json={"email": email, "name": role.title(), "password": PASSWORD, "roles": [role]})
    assert r.status_code == 201, r.text
    other = TestClient(client.app, headers=H)
    assert other.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return other


def pending(client):
    return client.get("/approvals", params={"status": "pending"}).json()[0]["ref"]


def listen(client):
    got = []
    client.app.state.bus.listen(lambda kind, data: got.append((kind, data)))
    return got


def compile_tax(client, requirement=TAX):
    r = client.post("/plans/compile", json={"requirement": requirement, "projectId": "erp"})
    assert r.status_code == 201, r.text
    return r.json()


# ── first run, signing in, sessions ──────────────────────────────
def test_first_run_setup_happens_once(anon):
    assert anon.get("/health").json()["needsSetup"] is True
    assert anon.get("/auth/status").json() == {"needsSetup": True, "user": None, "workspace": None}
    assert anon.post("/auth/setup", json={**OWNER, "password": "short"}).status_code == 422
    r = anon.post("/auth/setup", json=OWNER)
    assert r.status_code == 201 and r.json()["user"]["roles"] == ["owner"]
    status = anon.get("/auth/status").json()
    assert status["needsSetup"] is False and status["user"]["email"] == OWNER["email"] and status["workspace"] == {"name": "Acme"}
    assert anon.post("/auth/setup", json={**OWNER, "email": "second@example.com"}).status_code == 409


def test_sign_in_and_out(anon, client):
    r = anon.post("/auth/login", json={"email": OWNER["email"].upper(), "password": OWNER["password"]})
    assert r.status_code == 200 and r.json()["user"]["name"] == "Rajat"
    assert anon.get("/auth/me").json()["workspace"] == {"name": "Acme"}
    assert anon.post("/auth/logout").status_code == 200
    assert anon.get("/auth/me").status_code == 401
    assert client.get("/auth/me").status_code == 200          # other sessions are untouched


def test_wrong_passwords_lock_the_address_for_a_while(anon, client):
    bad = {"email": OWNER["email"], "password": "not the password"}
    assert [anon.post("/auth/login", json=bad).status_code for _ in range(5)] == [401] * 5
    assert anon.post("/auth/login", json={**bad, "password": OWNER["password"]}).status_code == 429
    assert [a["action"] for a in client.get("/admin/audit").json()].count("auth.login_failed") == 6


def test_changing_a_password_ends_the_other_sessions(anon, client):
    assert anon.post("/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]}).status_code == 200
    assert client.post("/auth/password", json={"current": "wrong", "new": PASSWORD}).status_code == 403
    assert client.post("/auth/password", json={"current": OWNER["password"], "new": PASSWORD}).status_code == 200
    assert client.get("/auth/me").status_code == 200          # this session is kept
    assert anon.get("/auth/me").status_code == 401            # the other one ended


def test_a_cookie_session_must_send_the_client_header(client):
    token = client.cookies.get(COOKIE)
    bare = TestClient(client.app, headers={"Cookie": f"{COOKIE}={token}"})    # what a hostile page could make a browser send
    assert bare.get("/tasks").status_code == 200
    assert bare.patch("/tasks/TASK-492", json={"status": "review"}).status_code == 403
    script = TestClient(client.app, headers={"Authorization": f"Bearer {token}"})   # scripts use a bearer token instead
    assert script.patch("/tasks/TASK-492", json={"status": "review"}).status_code == 200


# ── who may do what ──────────────────────────────────────────────
def test_reads_need_a_session_and_changes_need_a_permission(anon, client):
    assert anon.get("/tasks").status_code == 401
    assert anon.post(f"/approvals/{pending(client)}/approve").status_code == 401
    viewer = person(client, "viewer")
    assert viewer.get("/tasks").status_code == 200
    assert viewer.patch("/tasks/TASK-492", json={"status": "review"}).status_code == 403
    assert viewer.post("/ai/ask", json={"question": "tax"}).status_code == 403
    assert viewer.get("/admin/users").status_code == 403
    assert viewer.get("/admin/audit").status_code == 403
    assert viewer.post("/admin/reset", headers={"X-Confirm": "reset"}).status_code == 403
    engineer = person(client, "engineer")
    assert engineer.patch("/tasks/TASK-492", json={"status": "review"}).status_code == 200
    assert engineer.post(f"/approvals/{pending(client)}/approve").status_code == 403
    approver = person(client, "approver")
    r = approver.post(f"/approvals/{pending(client)}/approve")
    assert r.status_code == 200 and r.json()["decidedBy"] == "Approver"


def test_people_are_managed_with_guard_rails(client):
    body = {"email": "arjun@example.com", "name": "Arjun", "password": PASSWORD, "roles": ["engineer"]}
    arjun = client.post("/admin/users", json=body).json()
    assert arjun["roles"] == ["engineer"] and arjun["status"] == "active"
    assert client.post("/admin/users", json=body).status_code == 409                          # the email is taken
    assert client.post("/admin/users", json={**body, "email": "x@example.com", "password": "short"}).status_code == 422
    assert client.post("/admin/users", json={**body, "email": "y@example.com", "roles": ["wizard"]}).status_code == 422
    me = client.get("/auth/me").json()["user"]
    assert client.patch(f"/admin/users/{me['id']}", json={"status": "disabled"}).status_code == 409    # not yourself
    assert client.patch(f"/admin/users/{me['id']}", json={"roles": ["admin"]}).status_code == 409      # the last Owner
    admin = person(client, "admin")
    assert admin.post("/admin/users", json={**body, "email": "z@example.com", "roles": ["owner"]}).status_code == 403
    assert admin.patch(f"/admin/users/{arjun['id']}", json={"roles": ["approver"]}).json()["roles"] == ["approver"]
    signed_in = TestClient(client.app, headers=H)
    assert signed_in.post("/auth/login", json={"email": body["email"], "password": PASSWORD}).status_code == 200
    assert client.patch(f"/admin/users/{arjun['id']}", json={"status": "disabled"}).json()["status"] == "disabled"
    assert signed_in.get("/auth/me").status_code == 401                                        # out at once
    assert signed_in.post("/auth/login", json={"email": body["email"], "password": PASSWORD}).status_code == 403
    audit = [a["action"] for a in client.get("/admin/audit").json()]
    assert audit.count("user.create") == 2 and audit.count("user.update") == 2


def test_custom_roles_sit_beside_the_built_in_ones(client):
    role = client.post("/admin/roles", json={"name": "Release manager", "permissions": ["approvals:decide", "tasks:write"]}).json()
    assert role["id"] == "release-manager" and role["permissions"] == ["tasks:write", "approvals:decide"]   # catalogue order
    assert client.post("/admin/roles", json={"name": "Bad", "permissions": ["root"]}).status_code == 422
    assert client.patch("/admin/roles/viewer", json={"permissions": ["tasks:write"]}).status_code == 409
    assert client.delete("/admin/roles/owner").status_code == 409
    rm = person(client, "release-manager")
    assert rm.post(f"/approvals/{pending(client)}/approve").status_code == 200
    assert rm.post("/ai/ask", json={"question": "tax"}).status_code == 403
    client.patch("/admin/roles/release-manager", json={"permissions": []})
    assert rm.patch("/tasks/TASK-492", json={"status": "review"}).status_code == 403            # takes effect at once
    assert client.delete("/admin/roles/release-manager").status_code == 409                     # someone still has it
    assert [r["id"] for r in client.get("/admin/roles").json()][:5] == ["owner", "admin", "approver", "engineer", "viewer"]
    assert len(client.get("/admin/permissions").json()) == 15


def test_teams_hold_known_people(client):
    me = client.get("/auth/me").json()["user"]["id"]
    team = client.post("/admin/teams", json={"name": "Core", "members": [me, me]}).json()
    assert team["members"] == [me]
    assert client.post("/admin/teams", json={"name": "Core"}).status_code == 409
    assert client.post("/admin/teams", json={"name": "Ghosts", "members": ["u_nobody"]}).status_code == 422
    assert client.get("/admin/users").json()[0]["teams"] == [team["id"]]
    assert client.patch(f"/admin/teams/{team['id']}", json={"members": []}).json()["members"] == []
    assert client.delete(f"/admin/teams/{team['id']}").status_code == 200
    assert client.get("/admin/teams").json() == []


def test_the_workspace_can_be_renamed(client):
    assert client.patch("/admin/workspace", json={"name": "Acme Labs"}).json()["name"] == "Acme Labs"
    assert client.get("/auth/status").json()["workspace"] == {"name": "Acme Labs"}


# ── AI providers and keys ────────────────────────────────────────
def test_a_key_is_stored_apart_masked_and_never_logged(client, tmp_path):
    key = "sk-test-0123456789abcd"
    ai = client.put("/admin/ai", json={"deepseekKey": key, "preference": "auto", "deepseekModel": "deepseek-chat"}).json()
    assert ai["deepseek"]["hasKey"] and ai["deepseek"]["keyMask"].endswith("abcd") and ai["deepseek"]["keySource"] == "workspace"
    assert ai["preferenceLocked"] is True                     # NEUROCODE_COMPILER pins the tests to the rules
    secrets = tmp_path / "secrets.json"
    assert secrets.stat().st_mode & 0o777 == 0o600 and key in secrets.read_text()
    shown = json.dumps(client.get("/admin/audit").json()) + json.dumps(client.get("/admin/ai").json()) \
        + json.dumps(client.get("/activity").json()) + json.dumps(client.get("/health").json())
    assert key not in shown and "0123456789" not in shown
    entry = client.get("/admin/audit").json()[0]
    assert entry["action"] == "ai.update" and entry["detail"]["deepseekKey"] == "set" and entry["user"] == "Rajat"
    assert client.put("/admin/ai", json={"deepseekKey": ""}).json()["deepseek"]["hasKey"] is False
    assert key not in secrets.read_text()


def test_the_connection_test_says_whether_a_provider_answers(client, monkeypatch):
    assert client.post("/admin/ai/test", json={"provider": "deepseek"}).json()["detail"] == "No API key is set."
    client.put("/admin/ai", json={"deepseekKey": "sk-test-key-1234"})
    seen = []
    monkeypatch.setitem(gateway.CALLS, "deepseek", lambda messages, cfg: seen.append(cfg["key"]) or '{"ok": true}')
    r = client.post("/admin/ai/test", json={"provider": "deepseek"}).json()
    assert r["ok"] is True and "answered" in r["detail"] and seen == ["sk-test-key-1234"]

    def reject(messages, cfg):
        raise gateway.ProviderError(401, "Authentication Fails, your api key is invalid")

    monkeypatch.setitem(gateway.CALLS, "deepseek", reject)
    assert client.post("/admin/ai/test", json={"provider": "deepseek"}).json()["ok"] is False
    assert client.get("/admin/ai").json()["deepseek"]["rejected"] is True
    client.put("/admin/ai", json={"deepseekKey": "sk-test-new-5678"})    # a new key gets a fresh chance
    assert client.get("/admin/ai").json()["deepseek"]["rejected"] is False
    assert person(client, "approver").post("/admin/ai/test", json={"provider": "rules"}).status_code == 403


# ── AI features ──────────────────────────────────────────────────
def test_ai_features_work_without_a_key(client):
    r = client.post("/ai/ask", json={"question": "How do we split GST tax on an interstate invoice?", "projectId": "erp"}).json()
    assert r["provider"] == "rules" and r["model"] == "memory search" and r["answer"] and r["citations"]
    idea = client.post("/ai/brainstorm", json={"idea": "a vendor portal for invoice disputes", "projectId": "erp"})
    assert idea.status_code == 201
    doc = idea.json()
    assert doc["ref"] == "IDEA-1" and doc["brief"]["title"] == "A vendor portal for invoice disputes"
    assert doc["compiler"]["model"] == "offline template" and doc["brief"]["roadmap"]
    assert client.get("/ai/brainstorms").json()[0]["id"] == doc["id"]
    text = ("Standup notes. Invoices must round at the invoice level, never per line. Lunch was good.\n"
            "We decided to freeze the GST tables until October.")
    found = client.post("/ai/extract", json={"text": text}).json()
    assert found["model"] == "offline rules"
    assert [f["category"] for f in found["facts"]] == ["business_rules", "decisions"]
    added = client.post("/memory/facts", json={"projectId": "erp", "facts": found["facts"]})
    assert added.status_code == 201 and len(added.json()) == 2
    hits = [f["ref"] for f in client.get("/memory", params={"q": "round invoice level"}).json()]
    assert added.json()[0]["ref"] in hits


def test_ai_features_use_the_model_when_one_is_set(client, monkeypatch):
    monkeypatch.setenv("NEUROCODE_COMPILER", "auto")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    answer = {"answer": "Split it by the place of supply [MEM-142].", "citations": ["MEM-142", "MEM-999"]}
    monkeypatch.setitem(gateway.CALLS, "deepseek", lambda messages, cfg: json.dumps(answer))
    r = client.post("/ai/ask", json={"question": "TRANS tables for tax"}).json()
    assert r["provider"] == "deepseek" and r["answer"].startswith("Split")
    assert "MEM-999" not in [c["ref"] for c in r["citations"]]    # never cite what it was not given
    monkeypatch.setitem(gateway.CALLS, "deepseek", lambda messages, cfg: "not json at all")
    doc = client.post("/ai/brainstorm", json={"idea": "offline billing"}).json()
    assert doc["compiler"]["provider"] == "rules"
    assert any(e["action"] == "AI fell back" for e in client.get("/activity", params={"limit": 5}).json())


# ── the seed and the original surface ────────────────────────────
def test_health_counts_match_the_seed(client):
    body = client.get("/health").json()
    for key in TABLES:
        assert body["counts"][key] == len(SEED.get(key, [])), key
    assert body["compiler"] == {"provider": "rules", "model": "offline planner"} and body["needsSetup"] is False


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
    assert store.row("SELECT COUNT(*) FROM users")[0] == 0   # and so does the access schema


def test_approving_is_final_and_logged(client):
    before = client.get("/approvals", params={"status": "pending"}).json()
    ref = before[0]["ref"]
    r = client.post(f"/approvals/{ref}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["decidedAt"]
    assert client.post(f"/approvals/{ref}/deny").status_code == 409
    assert len(client.get("/approvals", params={"status": "pending"}).json()) == len(before) - 1
    top = client.get("/activity", params={"limit": 1}).json()[0]
    assert top["action"] == "Approved" and ref in top["detail"] and top["actorKind"] == "human" and top["actor"] == "Rajat"


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


def test_reset_needs_explicit_confirmation_and_keeps_people(client):
    assert client.post("/admin/reset").status_code == 400
    client.post("/approvals/APPR-118/approve")
    assert client.post("/admin/reset", headers={"X-Confirm": "reset"}).status_code == 200
    assert client.get("/approvals", params={"status": "pending"}).json()[0]["status"] == "pending"
    assert client.get("/auth/me").status_code == 200 and client.get("/admin/audit").json()[0]["action"] == "data.reset"


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
    assert plan["compiler"]["provider"] == "rules" and plan["status"] == "draft" and plan["requestedBy"] == "Rajat"
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
    monkeypatch.setitem(gateway.CALLS, "deepseek", lambda messages, cfg: "```json\n" + json.dumps(good) + "\n```")
    plan = compile_tax(client, "fix the tax split")
    assert plan["compiler"]["provider"] == "deepseek" and plan["risk"] == "HIGH" and plan["confidence"] == 82
    assert [s["agent"] for s in plan["steps"]] == ["Backend Engineer", "QA Engineer"]
    assert client.get(f"/tasks/{plan['taskRef']}").json()["layers"] == ["Backend"]

    monkeypatch.setitem(gateway.CALLS, "deepseek", lambda messages, cfg: "Sorry, I can't help with that.")
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
    monkeypatch.setattr(client.app.state.ctx.gateway, "ollama_ready", lambda: False)
    calls = []

    def reject(messages, cfg):
        calls.append(messages)
        raise gateway.ProviderError(401, "Authentication Fails, your api key is invalid")

    monkeypatch.setitem(gateway.CALLS, "deepseek", reject)
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
    decided = client.get("/decisions").json()[0]
    assert decided["value"] == "accepted" and decided["decidedBy"] == "Rajat"
    assert client.get("/activity", params={"limit": 1}).json()[0]["action"] == "Review accepted"
