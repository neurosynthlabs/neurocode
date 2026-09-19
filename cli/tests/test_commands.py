"""Every command end to end against the in-memory API: what it sends, what it prints, what --json prints,
and the exit codes a script relies on."""
from __future__ import annotations

import json
import stat
from pathlib import Path

from typer.testing import CliRunner

from neurocode_cli import config
from neurocode_cli.client import Event, parse_events
from neurocode_cli.conversation import Conversation, Pending
from neurocode_cli.main import WAITING, app

from .conftest import API, TOKEN, URL, FakeApi, sse

runner = CliRunner()


def run(*args: str, stdin: str | None = None):
    return runner.invoke(app, list(args), input=stdin, catch_exceptions=False)


# ── signing in ───────────────────────────────────────────────────
def test_login_signs_in_makes_a_token_signs_out_and_keeps_it_private(api: FakeApi, tmp_path: Path):
    done = run("login", URL, "--email", "owner@example.com", "--password-stdin", "--scope", "sessions:chat",
               "--days", "30", "--json", stdin="correct horse battery\n")
    assert done.exit_code == 0, done.output
    said = json.loads(done.stdout)
    # The web app's address answers /api/health, so the API is under /api and the web app is the address.
    assert said["server"] == API and said["web"] == URL and said["tokenStore"] == "file"
    assert "token" not in said["token"]                                # never printed
    made = api.tokens_made[0]
    assert made["scopes"] == ["sessions:chat"] and made["name"].startswith("nc on ")
    assert [(m, p) for m, p, _ in api.seen if p in ("/api/auth/login", "/api/tokens", "/api/auth/logout")] == [
        ("POST", "/api/auth/login"), ("POST", "/api/tokens"), ("POST", "/api/auth/logout")]
    assert next(b for _, p, b in api.seen if p == "/api/tokens")["expiresInDays"] == 30

    kept = config.home() / "credentials.json"
    assert stat.S_IMODE(kept.stat().st_mode) == 0o600
    assert json.loads(kept.read_text()) == {API: TOKEN}
    profile = config.load()
    assert profile.server == API and profile.token == TOKEN and profile.token_store == "file"

    out = run("logout")
    assert out.exit_code == 0 and "Revoke it in Settings" in out.stdout
    assert config.load().token is None and json.loads(kept.read_text()) == {}


def test_login_says_plainly_when_the_password_is_wrong_or_nobody_set_up(api: FakeApi):
    wrong = run("login", URL, "--email", "owner@example.com", "--password-stdin", stdin="nope\n")
    assert wrong.exit_code == 1 and "Wrong email or password." in wrong.stderr
    api.needs_setup = True
    empty = run("login", URL, "--email", "owner@example.com", "--password-stdin", stdin="x\n")
    assert empty.exit_code == 1 and "no Owner yet" in empty.stderr
    nowhere = run("login", "http://nowhere.test:1")
    assert nowhere.exit_code == 1


def test_login_with_a_token_checks_it_first(api: FakeApi):
    bad = run("login", URL, "--token", "nc_pat_wrong")
    assert bad.exit_code == 1 and "Sign in to continue." in bad.stderr and config.load().token is None
    good = run("login", URL, "--token", "-", stdin=TOKEN + "\n")
    assert good.exit_code == 0 and "Signed in to" in good.stdout
    assert not api.tokens_made                                          # a given token is used, not replaced


def test_without_a_login_every_command_says_how_to_sign_in(api: FakeApi):
    done = run("projects")
    assert done.exit_code == 1 and "nc login" in done.stderr


# ── reading ──────────────────────────────────────────────────────
def test_projects_status_and_the_default_project(signed: FakeApi):
    listed = run("projects", "--json")
    assert json.loads(listed.stdout)[0]["id"] == "erp"
    shown = run("projects")
    assert "ERP" in shown.stdout and "erp" in shown.stdout
    assert run("use", "nowhere").exit_code == 1
    assert run("use", "erp").exit_code == 0 and config.load().project == "erp"

    status = json.loads(run("status", "--json").stdout)
    assert status["approvalsPending"] == 0 and status["project"] == "erp" and status["tokenStore"] == "env"
    assert "Nothing is waiting on a person." in run("status").stdout


# ── asking ───────────────────────────────────────────────────────
def test_ask_starts_a_session_and_prints_the_answer(signed: FakeApi):
    done = run("ask", "where is tax rounded?")
    assert done.exit_code == 0, done.output
    assert "Tax is rounded" in done.stdout and "grounded the question" in done.stdout
    assert "The rule says so." not in done.stdout                      # reasoning stays folded
    with_reasoning = run("ask", "again?", "--reasoning")
    assert "The rule says so." in with_reasoning.stdout


def test_ask_as_json_is_the_question_and_what_followed(signed: FakeApi):
    said = json.loads(run("ask", "where is tax rounded?", "--project", "erp", "--json").stdout)
    assert said["question"]["text"] == "where is tax rounded?"
    assert [m["role"] for m in said["messages"]] == ["tool", "assistant"]
    assert said["session"]["projectId"] == "erp" and "messages" not in said["session"]


def test_a_permission_card_is_never_answered_for_the_person(signed: FakeApi):
    signed.after_ask = "card"
    done = run("ask", "read example.org", "--json")
    assert done.exit_code == WAITING
    said = json.loads(done.stdout)
    assert said["waitingOn"]["permission"]["state"] == "pending"
    assert not [p for _, p, _ in signed.seen if "/permissions/" in p]
    plain = run("ask", "read example.org")
    assert plain.exit_code == WAITING and "Waiting on you" in plain.stderr


# ── gates, runs, memory ──────────────────────────────────────────
def test_approve_and_deny_send_the_decision_and_its_scope(signed: FakeApi):
    signed.approvals = [{"ref": "APR-1", "kind": "command", "risk": "HIGH", "status": "pending",
                         "title": "Run npm test", "runRef": "RUN-1", "requestedAt": None},
                        {"ref": "APR-2", "kind": "signature", "risk": "LOW", "status": "pending",
                         "title": "Sign the diff", "requestedAt": None}]
    assert "APR-1" in run("approvals").stdout
    done = run("approve", "APR-1", "--scope", "run")
    assert done.exit_code == 0 and "Approved APR-1 (run)" in done.stdout and "RUN-1 carries on" in done.stdout
    assert ("POST", "/api/approvals/APR-1/approve", {"scope": "run"}) in signed.seen
    assert run("approve", "APR-2", "--scope", "forever").exit_code == 2
    assert json.loads(run("deny", "APR-2", "--json").stdout)["status"] == "denied"


def test_runs_watch_prints_the_log_and_exits_with_the_runs_outcome(signed: FakeApi):
    signed.runs["RUN-7"] = {"ref": "RUN-7", "projectId": "erp", "projectName": "ERP", "status": "running",
                            "branch": "neurocode/T-1", "requirement": "Round tax once", "steps": [],
                            "tests": {}, "diff": {}, "logs": [{"id": 1, "at": None, "step": 1, "level": "info",
                                                              "line": "writing src/tax.py"}]}
    signed.stream = [("run", {"runRef": "RUN-7", "id": 2, "at": None, "step": 1, "level": "ok", "line": "tests passed"}),
                     ("change", {"op": "put", "collection": "runs", "doc": {"ref": "RUN-7", "status": "done"}})]
    lines = run("runs", "RUN-7", "--watch", "--json")
    assert lines.exit_code == 0, lines.output
    parsed = [json.loads(line) for line in lines.stdout.splitlines()]
    assert [p["kind"] for p in parsed] == ["log", "log", "run"]
    assert parsed[1]["line"] == "tests passed"

    signed.runs["RUN-8"] = {**signed.runs["RUN-7"], "ref": "RUN-8", "status": "waiting", "waitingOn": "APR-3",
                            "logs": []}
    assert run("runs", "RUN-8", "--watch").exit_code == WAITING
    assert run("runs", "RUN-404").exit_code == 1


def test_memory_add_then_search(signed: FakeApi):
    added = json.loads(run("memory", "add", "Invoice rounding", "Once on the total.", "--project", "global",
                           "--json").stdout)
    assert added[0]["projectId"] is None                               # global: the whole workspace
    assert "Invoice rounding" in run("memory", "search", "rounding").stdout
    assert "Nothing remembered" in run("memory", "search", "payroll").stdout


def test_open_says_what_it_needs_when_there_is_no_web_address(signed: FakeApi, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    done = json.loads(run("open", "erp", "--browser", "--json").stdout)
    assert done["opened"] == "browser" and opened == [URL + "/workbench?project=erp"]
    monkeypatch.delenv("NC_WEB")
    assert run("open", "erp", "--browser").exit_code == 1


# ── the pieces underneath ────────────────────────────────────────
def test_the_stream_parser_reads_events_and_skips_keepalives():
    lines = sse(("chat", {"sessionRef": "S", "id": 1}), ("run", {"runRef": "R"})).decode().split("\n")
    found = list(parse_events(iter(lines)))
    assert found == [Event("chat", {"sessionRef": "S", "id": 1}), Event("run", {"runRef": "R"})]


def test_a_missed_piece_stops_the_answer_being_shown_out_of_order():
    p = Pending()
    p.add({"step": 0, "answer": "Tax ", "answerAt": 0})
    p.add({"step": 0, "answer": "is ", "answerAt": 4})
    assert p.answer == "Tax is " and not p.broken
    p.add({"step": 0, "answer": "late", "answerAt": 99})
    assert p.broken and p.answer == "Tax is "
    p.add({"step": 1, "answer": "New", "answerAt": 0})
    assert p.answer == "New" and not p.broken


def test_a_conversation_ends_on_an_answer_or_a_card_after_the_question(signed: FakeApi):
    from neurocode_cli.client import Client
    talk = Conversation(Client(API, TOKEN), "SES-1", since=5)
    assert talk.feed(Event("chat", {"sessionRef": "SES-2", "id": 6, "role": "assistant"})) is None
    talk.feed(Event("chat", {"sessionRef": "SES-1", "id": 5, "role": "you", "text": "q"}))
    assert not talk.answered()
    talk.feed(Event("chat", {"sessionRef": "SES-1", "id": 6, "role": "tool", "tool": "permission",
                             "permission": {"state": "pending"}}))
    assert talk.answered() and talk.waiting() is not None
