import json
import sqlite3
import subprocess
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
    assert len(client.get("/admin/permissions").json()) == 17


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


# ── lanes: several free models instead of one paid one ───────────
def lane_up(client, monkeypatch, lane_id, answer=None):
    """Give a lane a key and a stand-in that answers, so a test never reaches a real provider."""
    monkeypatch.setenv("NEUROCODE_COMPILER", "auto")
    assert client.put("/admin/ai", json={"lane": lane_id, "key": f"test-{lane_id}-0000"}).status_code == 200
    monkeypatch.setitem(gateway.CALLS, lane_id, answer or (lambda messages, cfg: '{"ok": true}'))


def lanes_of(client) -> dict:
    return {x["id"]: x for x in client.get("/admin/ai").json()["lanes"]}


def test_a_free_lane_takes_a_key_and_answers_without_touching_deepseek(client, monkeypatch):
    lane_up(client, monkeypatch, "groq")
    groq = lanes_of(client)["groq"]
    assert groq["free"] and groq["hasKey"] and groq["keyMask"] == "••••0000" and groq["ready"]
    assert "test-groq" not in json.dumps(client.get("/admin/audit").json())      # the key never reaches the log
    assert client.get("/admin/ai").json()["active"]["provider"] == "groq"        # it answers now, with no paid key
    assert client.put("/admin/ai", json={"lane": "groq", "key": ""}).json()["lanes"][0]["hasKey"] is False
    assert client.put("/admin/ai", json={"lane": "nothing", "key": "x"}).status_code == 404


def test_a_lane_that_spends_its_free_allowance_steps_aside(client, monkeypatch):
    lane_up(client, monkeypatch, "groq")
    lane_up(client, monkeypatch, "cerebras")
    client.put("/admin/ai", json={"lane": "groq", "rpd": 1})
    assert client.post("/admin/ai/test", json={"provider": "groq"}).json()["ok"]  # that is the whole day's allowance
    groq = lanes_of(client)["groq"]
    assert groq["ready"] is False and "today" in groq["blocked"] and groq["spent"]["today"] == 1
    assert client.get("/admin/ai").json()["active"]["provider"] == "cerebras"     # the work moves to the next lane


def test_a_lane_that_fails_hands_the_call_to_the_next_lane(client, monkeypatch):
    def refuse(messages, cfg):
        raise gateway.ProviderError(429, "too many requests")
    good = {"title": "Fix the interstate GST split", "businessRequirement": "b", "technicalRequirement": "t",
            "risk": "high", "confidence": "80", "layers": ["backend"], "openQuestions": [],
            "steps": [{"label": "Fix TaxService", "agent": "backend"}, {"label": "Test it", "agent": "QA"}]}
    lane_up(client, monkeypatch, "groq", refuse)
    lane_up(client, monkeypatch, "cerebras", lambda messages, cfg: json.dumps(good))
    plan = compile_tax(client, "fix the tax split")
    assert plan["compiler"]["provider"] == "cerebras"                             # not the offline rules
    assert lanes_of(client)["groq"]["spent"]["today"] == 1                        # the attempt is still ledgered


def test_agents_working_at_once_are_spread_over_different_lanes(client, tmp_path, monkeypatch):
    def wrote(messages, cfg):
        return json.dumps({"summary": "Nothing to change here.", "files": []})
    lane_up(client, monkeypatch, "groq", wrote)
    lane_up(client, monkeypatch, "cerebras", wrote)
    pid = onboard_git(client, tmp_path / "shop")
    ref = client.post(f"/plans/{ready_plan(client, pid)}/dispatch").json()["runRef"]
    used = [a["lane"] for a in agents_of(client, client.get(f"/runs/{ref}").json())]
    assert len(used) >= 2 and len(set(used)) == 2 and set(used) <= {"groq", "cerebras"}


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
    assert body["compiler"]["provider"] == "rules" and body["compiler"]["model"] == "offline planner"
    assert body["compiler"]["lanes"] == 0 and body["needsSetup"] is False       # no key here, so no lane is open


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
    assert list((tmp_path / "backups").glob("*before-0001*"))  # copied aside before the schema changed


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


# ── the database itself ──────────────────────────────────────────
def test_the_audit_log_is_append_only(client):
    store = client.app.state.store
    with pytest.raises(sqlite3.DatabaseError):
        store.execute("UPDATE audit_log SET action = 'rewritten'")
    with pytest.raises(sqlite3.DatabaseError):
        store.execute("DELETE FROM audit_log")
    assert client.get("/admin/audit").json()[-1]["action"] == "workspace.setup"


def test_the_database_is_backed_up_checked_and_optimized(client, tmp_path):
    info = client.get("/admin/database").json()
    assert {"users", "ai_calls", "code_files", "audit_log"} <= {t["name"] for t in info["tables"]}
    assert "code_fts_data" not in {t["name"] for t in info["tables"]}          # search internals stay out of the list
    assert [m["version"] for m in info["migrations"]] == [1, 2, 3, 4, 5, 6, 7]
    made = client.post("/admin/database/backup")
    assert made.status_code == 201
    path = tmp_path / "backups" / made.json()["name"]
    assert path.is_file() and path.stat().st_mode & 0o777 == 0o600
    copy = sqlite3.connect(path)
    assert copy.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1       # a complete, readable copy
    copy.close()
    assert client.post("/admin/database/check").json()["ok"] is True
    assert client.post("/admin/database/optimize").status_code == 200
    assert {"database.backup", "database.optimize"} <= {a["action"] for a in client.get("/admin/audit").json()}
    assert person(client, "approver").get("/admin/database").status_code == 403


def test_a_reset_is_backed_up_first(client):
    reset = client.post("/admin/reset", headers={"X-Confirm": "reset"}).json()
    assert "before-reset" in reset["backup"]
    assert any(b["name"] == reset["backup"] for b in client.get("/admin/database").json()["backups"])


def test_every_model_call_is_in_the_usage_ledger(client, monkeypatch):
    client.post("/ai/ask", json={"question": "tax on invoices"})                       # offline rules
    monkeypatch.setenv("NEUROCODE_COMPILER", "auto")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    answer = json.dumps({"answer": "From memory.", "citations": []})
    monkeypatch.setitem(gateway.CALLS, "deepseek", lambda messages, cfg: (answer, {"in": 120, "out": 30}))
    client.post("/ai/ask", json={"question": "tax on invoices"})                       # a model, 150 tokens
    monkeypatch.setitem(gateway.CALLS, "deepseek", lambda messages, cfg: "not json")
    client.post("/ai/brainstorm", json={"idea": "a vendor portal"})                    # a failed model, then the rules
    u = client.get("/usage").json()
    assert u["totals"] | {"avgMs": 0} == {"calls": 4, "modelCalls": 2, "offline": 2, "failures": 1, "tokensIn": 120,
                                          "tokensOut": 30, "avgMs": 0}
    assert {f["feature"] for f in u["byFeature"]} == {"ask", "brainstorm"}
    assert u["recent"][0]["feature"] == "brainstorm" and u["recent"][0]["provider"] == "rules"
    assert u["recent"][0]["by"] == "Rajat" and u["byPerson"][0]["calls"] == 4
    viewer = person(client, "viewer").get("/usage").json()
    assert viewer["totals"]["calls"] == 4 and viewer["recent"][0]["by"] is None and "byPerson" not in viewer


# ── the code index ───────────────────────────────────────────────
REPO = {
    "pkg/__init__.py": "",
    "pkg/core.py": "def total(x):\n    if x > 1:\n        return x\n    return 0\n\n\nclass Ledger:\n    def post(self):\n        pass\n",
    "pkg/api.py": "from .core import total\n\n\ndef handler():\n    return total(2)\n",
    "tests/test_core.py": "from pkg.core import total\n\n\ndef test_total():\n    assert total(2) == 2\n",
    "web/src/lib/money.ts": "export function round(n: number) { return Math.round(n) }\nexport const RATE = 18\n",
    "web/src/App.tsx": "import { round } from './lib/money'\nimport React from 'react'\n\nexport default function App() {\n  return round(1)\n}\n",
    "db/schema.sql": "CREATE TABLE ORDERS (id int);\nCREATE PROCEDURE SP_GET_ORDERS AS SELECT * FROM ORDERS;\n",
    "Billing/OrderWriter.cs": 'public class OrderWriter {\n    public void Save() {\n        Run("INSERT INTO ORDERS VALUES (1)");\n'
                              '        Run("EXEC SP_GET_ORDERS");\n    }\n}\n',
}


def onboard_repo(client, root):
    for rel, text in REPO.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    r = client.post("/projects", json={"source": "local", "repo": str(root)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_onboarding_indexes_the_code(client, tmp_path):
    pid = onboard_repo(client, tmp_path / "shop")
    s = client.get(f"/projects/{pid}/code").json()
    assert s["indexed"] and s["run"]["files"] == 8 and s["run"]["unresolved"] == 0
    assert s["run"]["parsers"] == {"C#": "patterns", "Python": "python-ast", "T-SQL": "patterns", "TypeScript": "patterns"}
    assert s["database"]["top"][0]["name"] == "ORDERS" and s["database"]["top"][0]["writers"] == 1
    project = next(p for p in client.get("/projects").json() if p["id"] == pid)
    assert project["codeIndex"]["files"] == 8 and project["understoodPct"] == round(100 * 4 / 15)
    assert {c["label"]: c["pct"] for c in project["coverage"]}["Syntax & symbols"] == 100
    actions = [e["action"] for e in client.get("/activity", params={"limit": 4}).json()]
    assert "Code indexed" in actions


def test_a_file_knows_its_symbols_users_and_blast_radius(client, tmp_path):
    pid = onboard_repo(client, tmp_path / "shop")
    core = client.get(f"/projects/{pid}/code/file", params={"path": "pkg/core.py"}).json()
    assert {s["name"] for s in core["symbols"]} == {"total", "Ledger", "Ledger.post"}
    assert {u["path"] for u in core["usedBy"]} == {"pkg/api.py", "tests/test_core.py"}
    assert core["file"]["complexity"] == 1 and core["impact"]["counts"]["tests"] == 1
    app = client.get(f"/projects/{pid}/code/file", params={"path": "web/src/App.tsx"}).json()
    assert {(d["path"], d["target"]) for d in app["dependsOn"]} == {("web/src/lib/money.ts", "./lib/money"), (None, "react")}
    assert ("App", "component") in {(s["name"], s["kind"]) for s in app["symbols"]}
    writer = client.get(f"/projects/{pid}/code/file", params={"path": "Billing/OrderWriter.cs"}).json()
    assert {(d["object"], d["kind"]) for d in writer["database"]} == {("ORDERS", "writes"), ("SP_GET_ORDERS", "calls")}
    orders = client.get(f"/projects/{pid}/code/impact", params={"object": "ORDERS"}).json()
    assert orders["blastRadius"][0]["items"] == ["Billing/OrderWriter.cs"] and orders["risk"] == "HIGH"
    assert client.get(f"/projects/{pid}/code/impact", params={"module": "pkg"}).json()["counts"]["tests"] == 1
    assert client.get(f"/projects/{pid}/code/file", params={"path": "nope.py"}).status_code == 404


def test_the_code_index_searches_browses_and_maps_modules(client, tmp_path):
    pid = onboard_repo(client, tmp_path / "shop")
    assert {"Ledger", "Ledger.post"} <= {h["name"] for h in client.get(f"/projects/{pid}/code/search", params={"q": "ledger"}).json()}
    assert client.get(f"/projects/{pid}/code/search", params={"q": 'x" OR *'}).status_code == 200
    root = client.get(f"/projects/{pid}/code/files").json()
    assert {d["name"] for d in root["dirs"]} == {"Billing", "db", "pkg", "tests", "web"}
    pkg = client.get(f"/projects/{pid}/code/files", params={"dir": "pkg"}).json()
    assert [f["name"] for f in pkg["files"]] == ["__init__.py", "api.py", "core.py"]
    assert next(f for f in pkg["files"] if f["name"] == "core.py")["fanIn"] == 2
    g = client.get(f"/projects/{pid}/code/graph").json()
    assert {"m:pkg", "m:tests", "d:ORDERS"} <= {n["id"] for n in g["nodes"]}
    assert any(e["from"] == "m:tests" and e["to"] == "m:pkg" for e in g["edges"])
    assert any(e["from"] == "m:Billing" and e["to"] == "d:ORDERS" and e["kind"] == "writes" for e in g["edges"])


# ── the agent runtime ────────────────────────────────────────────
TINY = {
    "Makefile": "test:\n\t@echo '2 passed'\n",
    "pkg/__init__.py": "",
    "pkg/core.py": "def total(x):\n    return x\n",
}


def git_project(root: Path, files: dict[str, str] | None = None) -> Path:
    """A small git repository with one commit: what a run branches from."""
    for rel, text in (files or TINY).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "test@neurocode.local"],
                 ["config", "user.name", "Test"], ["add", "-A"],
                 ["-c", "commit.gpgsign=false", "commit", "-qm", "start"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    return root


def onboard_git(client, root: Path, files: dict[str, str] | None = None) -> str:
    root.mkdir(parents=True, exist_ok=True)
    git_project(root, files)
    r = client.post("/projects", json={"source": "local", "repo": str(root)})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def ready_plan(client, pid: str, requirement: str = TAX) -> str:
    """A compiled plan with nothing left open, ready to dispatch."""
    plan = client.post("/plans/compile", json={"requirement": requirement, "projectId": pid}).json()
    for _ in list(plan["openQuestions"]):
        client.post(f"/plans/{plan['ref']}/questions/0", json={"defer": True})
    return plan["ref"]


def gate_of(client, run_ref: str) -> dict:
    return next(a for a in client.get("/approvals", params={"status": "pending"}).json() if a.get("runRef") == run_ref)


def model(monkeypatch, answer):
    monkeypatch.setenv("NEUROCODE_COMPILER", "auto")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setitem(gateway.CALLS, "deepseek", answer)


def agents_of(client, run: dict) -> list[dict]:
    return [client.get(f"/runs/{ref}").json() for ref in run.get("children", [])]


def test_dispatching_starts_agents_in_parallel_worktrees(client, tmp_path):
    pid = onboard_git(client, tmp_path / "shop")
    plan = client.post(f"/plans/{ready_plan(client, pid)}/dispatch").json()
    run = client.get(f"/runs/{plan['runRef']}").json()
    agents = agents_of(client, run)
    assert run["role"] == "integration" and len(agents) >= 2            # the compiler named several agents
    assert len({a["branch"] for a in agents}) == len(agents)            # a branch each
    assert len({a["worktree"] for a in agents}) == len(agents)          # and a worktree each
    assert all(a["status"] == "done" and a["parent"] == run["ref"] for a in agents)
    assert all({s["status"] for s in a["steps"]} == {"skipped"} for a in agents)  # no model: nothing invented
    assert "no model is configured" in " ".join(line["line"] for line in agents[0]["logs"]).lower()

    assert run["status"] == "waiting" and (Path(run["worktree"]) / "Makefile").is_file()
    assert {s["status"] for s in run["steps"] if s["kind"] == "merge"} == {"skipped"}  # nothing was written
    gate = gate_of(client, run["ref"])                                  # tests wait for a person, the first time
    assert gate["tool"] == "Bash(make test)" and gate["risk"] == "MEDIUM"
    client.post(f"/approvals/{gate['ref']}/approve")
    done = client.get(f"/runs/{run['ref']}").json()
    assert done["status"] == "done" and done["tests"]["status"] == "passed"
    assert "2 passed" in " ".join(line["line"] for line in done["logs"])
    assert done["steps"][-1]["status"] == "skipped"                     # nothing to accept: no file changed


def writer(files_for_agent, review=None):
    """A stubbed model: each agent writes its own file, and the reviewer gets its own answer."""
    def answer(messages, cfg):
        prompt = messages[1]["content"]
        if "reviewer" in messages[0]["content"].lower():
            return review or json.dumps({"findings": [], "verdict": "Fine."})
        agent = next((a for a in files_for_agent if a in prompt), None)
        path, content = files_for_agent.get(agent, (None, None))
        if not path:
            return json.dumps({"summary": "Nothing to do here.", "files": []})
        return json.dumps({"summary": f"{agent} wrote {path}.", "files": [{"path": path, "content": content}]})
    return answer


def test_agents_write_in_parallel_and_their_branches_are_merged_for_your_signature(client, tmp_path, monkeypatch):
    pid = onboard_git(client, tmp_path / "shop")
    ref = ready_plan(client, pid)
    plan = next(p for p in client.get("/plans").json() if p["ref"] == ref)
    names = [a for a in {s["agent"] for s in plan["steps"]} if a not in ("AI Commander", "AI Project Manager")]
    assert len(names) >= 2
    files = {name: (f"pkg/{i}.py", f"# {name}\nVALUE = {i}\n") for i, name in enumerate(names)}
    review = json.dumps({"findings": [{"severity": "MEDIUM", "file": "pkg/0.py", "note": "No test covers this."}],
                         "verdict": "Small and clear."})
    model(monkeypatch, writer(files, review))

    run = client.get(f"/runs/{client.post(f'/plans/{ref}/dispatch').json()['runRef']}").json()
    agents = agents_of(client, run)
    assert all(a["diff"]["files"] == 1 for a in agents)                      # each agent wrote in its own worktree
    assert run["diff"]["files"] == len(agents) and run["status"] == "waiting"  # and every branch merged cleanly
    for name, (path, content) in files.items():
        assert (Path(run["worktree"]) / path).read_text() == content, name
    assert not run["conflicts"]

    client.post(f"/approvals/{gate_of(client, run['ref'])['ref']}/approve")   # allow the tests
    waiting = client.get(f"/runs/{run['ref']}").json()
    assert waiting["tests"]["status"] == "passed" and waiting["review"]["by"] == "deepseek-chat"
    accept = gate_of(client, run["ref"])
    assert accept["tool"].startswith("Merge(neurocode/")
    client.post(f"/approvals/{accept['ref']}/approve")

    done = client.get(f"/runs/{run['ref']}").json()
    assert done["status"] == "done" and f"git merge {done['branch']}" in done["note"]
    assert client.get(f"/tasks/{done['taskRef']}").json()["status"] == "review"
    assert "VALUE = 0" in client.get(f"/runs/{run['ref']}/diff").json()["patch"]
    assert {f["feature"] for f in client.get("/usage").json()["byFeature"]} >= {"agent", "review"}


def test_two_agents_touching_one_file_collide_at_the_merge_not_mid_edit(client, tmp_path, monkeypatch):
    pid = onboard_git(client, tmp_path / "shop")
    ref = ready_plan(client, pid)
    plan = next(p for p in client.get("/plans").json() if p["ref"] == ref)
    names = [a for a in {s["agent"] for s in plan["steps"]} if a not in ("AI Commander", "AI Project Manager")]
    files = {name: ("pkg/core.py", f"def total(x):\n    return x + {i}\n") for i, name in enumerate(names)}
    model(monkeypatch, writer(files))

    run = client.get(f"/runs/{client.post(f'/plans/{ref}/dispatch').json()['runRef']}").json()
    assert [c["files"] for c in run["conflicts"]] == [["pkg/core.py"]] * len(run["conflicts"])
    assert len(run["conflicts"]) == len(names) - 1                       # the first branch merged, the rest collide
    collided = [s for s in run["steps"] if s["kind"] == "merge" and s["status"] == "failed"]
    assert collided and "the merge was undone" in collided[0]["detail"]
    assert "<<<<<<<" not in (Path(run["worktree"]) / "pkg" / "core.py").read_text()   # never half-applied
    assert gate_of(client, run["ref"])["risk"] in ("MEDIUM", "HIGH")


def test_an_accepted_run_can_be_merged_from_the_ui(client, tmp_path, monkeypatch):
    repo = tmp_path / "shop"
    pid = onboard_git(client, repo)
    ref = ready_plan(client, pid)
    plan = next(p for p in client.get("/plans").json() if p["ref"] == ref)
    names = [a for a in {s["agent"] for s in plan["steps"]} if a not in ("AI Commander", "AI Project Manager")]
    model(monkeypatch, writer({names[0]: ("pkg/core.py", "def total(x):\n    return round(x)\n")}))
    run_ref = client.post(f"/plans/{ref}/dispatch").json()["runRef"]
    client.post(f"/approvals/{gate_of(client, run_ref)['ref']}/approve")      # tests
    client.post(f"/approvals/{gate_of(client, run_ref)['ref']}/approve")      # your signature

    (repo / "untracked.txt").write_text("not committed\n")                   # a dirty tree is refused outright
    refused = client.post(f"/runs/{run_ref}/merge")
    assert refused.status_code == 409 and "not committed" in refused.json()["detail"]
    (repo / "untracked.txt").unlink()

    merged = client.post(f"/runs/{run_ref}/merge").json()
    assert merged["merged"] and merged["into"] == "main" and merged["undo"].startswith("git reset --hard ")
    assert (repo / "pkg" / "core.py").read_text() == "def total(x):\n    return round(x)\n"   # really in the repo
    assert merged["run"]["merged"]["by"] == "Rajat"
    assert client.post(f"/runs/{run_ref}/merge").status_code == 409           # only once
    assert {a["action"] for a in client.get("/admin/audit").json()} >= {"run.merge"}
    assert person(client, "engineer").post(f"/runs/{run_ref}/merge").status_code == 403


def test_the_agent_cannot_write_outside_its_worktree(client, tmp_path, monkeypatch):
    pid = onboard_git(client, tmp_path / "shop")
    ref = ready_plan(client, pid)
    escape = json.dumps({"summary": "…", "files": [{"path": "../../escaped.py", "content": "print('out')\n"}]})
    model(monkeypatch, lambda messages, cfg: escape)
    run = client.get(f"/runs/{client.post(f'/plans/{ref}/dispatch').json()['runRef']}").json()
    steps = [s for r in (run, *agents_of(client, run)) for s in r["steps"]]                 # every agent tried it
    assert not (tmp_path / "escaped.py").exists() and not (tmp_path.parent / "escaped.py").exists()
    assert any(s["status"] == "failed" and "outside the worktree" in s["detail"] for s in steps)
    assert run["diff"]["files"] == 0


def test_a_run_can_be_stopped_and_its_worktree_discarded(client, tmp_path):
    pid = onboard_git(client, tmp_path / "shop")
    ref = client.post(f"/plans/{ready_plan(client, pid)}/dispatch").json()["runRef"]
    tree = Path(client.get(f"/runs/{ref}").json()["worktree"])
    assert client.post(f"/runs/{ref}/discard").status_code == 409          # it is waiting on a person
    assert client.post(f"/runs/{ref}/cancel").json()["status"] == "cancelled"
    discarded = client.post(f"/runs/{ref}/discard").json()
    assert discarded["removed"] is True
    assert not tree.exists()
    for child in agents_of(client, discarded):                            # the agents' worktrees go with it
        assert child["removed"] is True and not Path(child["worktree"]).exists()
    assert client.post(f"/runs/{ref}/cancel").status_code == 409
    assert person(client, "viewer").post(f"/runs/{ref}/cancel").status_code == 403
    assert client.get("/runs").json()[0]["ref"] == ref


def test_a_project_with_no_code_here_dispatches_without_a_run(client):
    plan = compile_tax(client)
    for _ in list(plan["openQuestions"]):
        client.post(f"/plans/{plan['ref']}/questions/0", json={"defer": True})
    out = client.post(f"/plans/{plan['ref']}/dispatch").json()
    assert out["status"] == "dispatched" and "runRef" not in out
    assert any(e["action"] == "No run started" for e in client.get("/activity", params={"limit": 6}).json())
    assert client.get("/runs").json() == []


def test_reindexing_needs_code_on_this_machine_and_the_permission(client, tmp_path):
    pid = onboard_repo(client, tmp_path / "shop")
    (tmp_path / "shop" / "pkg" / "extra.py").write_text("from .core import Ledger\n")
    assert client.post(f"/projects/{pid}/code/reindex").status_code == 202
    assert client.get(f"/projects/{pid}/code").json()["run"]["files"] == 9
    assert client.get("/projects/erp/code").json() == {"indexed": False, "indexing": False, "canIndex": False}
    assert client.post("/projects/erp/code/reindex").status_code == 409
    assert person(client, "viewer").post(f"/projects/{pid}/code/reindex").status_code == 403
    assert TestClient(client.app).get(f"/projects/{pid}/code").status_code == 401
