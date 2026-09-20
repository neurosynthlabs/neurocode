"""Every command end to end against the in-memory API: what it sends, what it prints, what --json prints,
and the exit codes a script relies on."""
from __future__ import annotations

import json
import re
import stat
from pathlib import Path

from typer.testing import CliRunner

from neurocode_cli import config
from neurocode_cli.client import Event, parse_events
from neurocode_cli.conversation import Conversation, Pending
from neurocode_cli.main import WAITING, app

from .conftest import API, PATCH, TOKEN, URL, FakeApi, sse

runner = CliRunner()
#: Colour, stripped before a line is measured: what a window fits is characters, not escape codes.
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return ANSI.sub("", text)


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


# ── reading the change, and answering it ─────────────────────────
def a_run(api: FakeApi, ref: str = "RUN-7", *, state: str = "waiting", by: str = "cerebras/qwen-3-coder",
          gate: bool = True, requirement: str = "Round tax once", branch: str = "neurocode/task-492"):
    """A run parked at its signature, with a review behind it and a second pending gate of its own —
    so `nc accept` has to pick the right one rather than the first."""
    api.runs[ref] = {
        "ref": ref, "projectId": "erp", "projectName": "ERP", "status": state, "branch": branch,
        "worktree": f"/tmp/worktrees/{ref}", "requirement": requirement,
        "steps": [{"n": 1, "kind": "code", "label": "write it", "status": "done"},
                  {"n": 2, "kind": "review", "label": "read the diff", "status": "done"},
                  {"n": 3, "kind": "handoff", "label": "your signature", "status": "waiting"}],
        "tests": {}, "checks": [{"step": 2, "name": "ruff", "status": "done", "summary": "no findings"}],
        "goal": None, "logs": [],
        "diff": {"files": 2, "insertions": 4, "deletions": 1, "commits": 1},
        "review": {"findings": [{"severity": "HIGH", "file": "src/tax.py", "line": 2,
                                 "note": "credit notes still round twice"}],
                   "verdict": "One rule fixed, one left.", "by": by,
                   "receipt": {"sha256": "sha256:abc123def4567890"}},
        **({"waitingOn": "APR-9"} if gate else {})}
    if gate:
        api.approvals += [
            {"ref": "APR-8", "kind": "command", "risk": "HIGH", "status": "pending", "title": "Run npm test",
             "runRef": ref, "step": 1, "requestedAt": None},
            {"ref": "APR-9", "kind": "signature", "risk": "LOW", "status": "pending", "title": "Sign the diff",
             "runRef": ref, "step": 3, "requestedAt": None}]
    return api.runs[ref]


def test_nc_diff_pipes_the_patch_byte_for_byte_and_keeps_only_the_files_asked_for(signed: FakeApi):
    a_run(signed)
    asked = run("diff", "RUN-7", "--raw")
    assert asked.exit_code == 0 and asked.stdout == PATCH          # `git apply --3way` would take it
    piped = run("diff", "RUN-7")                                   # not a terminal: the same bytes
    assert piped.stdout == PATCH
    just_tests = run("diff", "RUN-7", "--file", "tests/*")
    assert just_tests.stdout.startswith("diff --git a/tests/test_tax.py")
    assert "src/tax.py" not in just_tests.stdout
    nothing = run("diff", "RUN-7", "--file", "docs/*")
    assert nothing.exit_code == 1 and "No file in RUN-7's diff matches docs/*" in nothing.stderr
    assert json.loads(run("diff", "RUN-7", "--json").stdout)["patch"] == PATCH

    # No ref: the one run here that waits on a person. Two, and naming it is the person's to do.
    assert run("diff", "--raw").stdout == PATCH
    a_run(signed, ref="RUN-11")
    both = run("diff", "--raw")
    assert both.exit_code == 2 and "RUN-7" in both.stderr and "RUN-11" in both.stderr


def test_nc_diff_says_when_the_patch_was_cut_or_the_worktree_is_gone(signed: FakeApi):
    a_run(signed)
    signed.truncated = True
    cut = run("diff", "RUN-7", "--raw")
    assert cut.exit_code == 0 and "The rest is in the worktree: /tmp/worktrees/RUN-7" in cut.stderr
    signed.truncated, signed.gone = False, True
    gone = run("diff", "RUN-7")
    assert gone.exit_code == 1 and "worktree was removed" in gone.stderr


def test_nc_diff_in_a_window_cuts_every_line_and_gives_up_the_patch_when_there_is_no_room(
        signed: FakeApi, monkeypatch):
    a_run(signed)
    monkeypatch.setenv("FORCE_COLOR", "1")                         # Rich: treat this as a terminal
    monkeypatch.setenv("COLUMNS", "80")
    shown = run("diff", "RUN-7")
    said = plain(shown.stdout)
    assert all(len(line) <= 80 for line in said.splitlines())
    assert "RUN-7" in said and "neurocode/task-492" in said and "2 files" in said
    assert "1 of 2 files shown" in plain(run("diff", "RUN-7", "--file", "src/*").stdout)
    assert "src/tax.py" in said and "@@ -1,4 +1,5 @@" in said
    assert "index 1111111..2222222 100644" not in said             # machinery, not for a person
    assert "and the tests now prove." not in said                  # cut at the edge, never wrapped

    monkeypatch.setenv("COLUMNS", "40")
    narrow = run("diff", "RUN-7")
    tight = plain(narrow.stdout)
    assert all(len(line) <= 40 for line in tight.splitlines())
    assert "RUN-7" in tight and "counted from the patch" in tight and "--file" in tight
    assert "@@" not in tight                                       # no room for a patch line at 40

    monkeypatch.setenv("COLUMNS", "80")
    counted = plain(run("diff", "RUN-7", "--stat").stdout)
    assert "+2" in counted and "−1" in counted and "counted from the patch" in counted

    monkeypatch.delenv("FORCE_COLOR")                              # asked for by name, so answered
    into_a_pipe = run("diff", "RUN-7", "--stat").stdout
    assert "counted from the patch" in into_a_pipe and "@@" not in into_a_pipe


def test_nc_review_puts_the_findings_in_front_of_the_person_and_waits_for_the_signature(signed: FakeApi):
    a_run(signed)
    waiting = run("review", "RUN-7")
    assert waiting.exit_code == WAITING
    said = plain(waiting.stdout)
    assert "HIGH" in said and "src/tax.py:2" in said and "credit notes still round twice" in said
    assert "cerebras/qwen-3-coder" in said and "One rule fixed, one left." in said
    assert "abc123def456" in said                                  # the receipt the merge is checked against
    assert "nc accept RUN-7" in said and 'nc send-back RUN-7 --notes "…"' in said

    signed.approvals[-1].update(status="approved", decidedBy="Rajat", decidedAt=None)
    decided = run("review", "RUN-7")
    assert decided.exit_code == 0 and "approved by Rajat" in plain(decided.stdout)


def test_a_review_read_by_rules_says_so_and_names_no_model(signed: FakeApi):
    a_run(signed, ref="RUN-8", by="offline rules")
    said = plain(run("review", "RUN-8").stdout)
    assert "read by" in said and "offline rules" in said and "qwen" not in said


def test_nc_review_again_says_what_it_costs_before_it_asks_for_it(signed: FakeApi):
    a_run(signed)
    again = run("review", "RUN-7", "--again")
    assert "one model call" in again.stderr
    assert ("POST", "/api/runs/RUN-7/review", None) in signed.seen


def test_nc_accept_signs_the_handoff_gate_and_not_another_pending_one(signed: FakeApi):
    a_run(signed)
    done = run("accept", "RUN-7")
    assert done.exit_code == 0 and "signed APR-9" in done.stdout
    assert ("POST", "/api/approvals/APR-9/approve", None) in signed.seen
    assert not [p for _, p, _ in signed.seen if p == "/api/approvals/APR-8/approve"]

    a_run(signed, ref="RUN-9", state="running", gate=False)
    nope = run("accept", "RUN-9")
    assert nope.exit_code == 1 and "RUN-9 is running" in nope.stderr
    assert "no signature of yours waiting" in nope.stderr


def test_send_back_reads_its_notes_from_stdin_and_posts_them_verbatim(signed: FakeApi):
    a_run(signed)
    done = run("send-back", "RUN-7", "--notes", "-", stdin="credit notes still round twice\n")
    assert done.exit_code == 0, done.output
    posted = next(b for _, p, b in signed.seen if p == "/api/runs/RUN-7/rework")
    assert posted == {"notes": "credit notes still round twice"}
    assert "costs what the plan costs" in done.stderr
    assert "--watch" in done.stdout
    assert [a["status"] for a in signed.approvals if a["ref"] == "APR-9"] == ["denied"]


def test_stop_discard_and_revert_reach_the_routes_that_already_exist(signed: FakeApi):
    a_run(signed, state="running", gate=False)
    assert run("stop", "RUN-7").exit_code == 0 and signed.runs["RUN-7"]["status"] == "cancelled"
    assert run("discard", "RUN-7").exit_code == 0 and signed.runs["RUN-7"]["removed"] is True
    assert run("revert", "RUN-7", "2", "--redo").exit_code == 0
    assert ("POST", "/api/runs/RUN-7/steps/2/revert", {"redo": True}) in signed.seen


# ── stdin, continuing, and what a script can trust ───────────────
def test_a_question_can_come_in_on_stdin_alone_or_beside_one(signed: FakeApi):
    only = run("ask", stdin="why does invoice rounding drift?\n")
    assert only.exit_code == 0, only.output
    assert next(b for _, p, b in signed.seen if p.endswith("/messages"))["text"] \
        == "why does invoice rounding drift?\n"

    both = run("ask", "why does this fail", stdin="Traceback (most recent call last):\n")
    text = [b for _, p, b in signed.seen if p.endswith("/messages")][-1]["text"]
    assert text.startswith("why does this fail") and "--- piped input ---" in text and "Traceback" in text
    assert "from stdin with the question" in both.stderr


def test_too_much_on_stdin_is_refused_with_both_sizes_named(signed: FakeApi):
    huge = run("ask", "why?", stdin="x" * 200_000)
    assert huge.exit_code == 1
    assert "stdin is 200 KB" in huge.stderr and "the cap is 100 KB" in huge.stderr
    assert not [p for _, p, _ in signed.seen if p.endswith("/messages")]


def test_nc_plan_takes_its_requirement_from_a_file_or_from_stdin(signed: FakeApi, tmp_path: Path):
    written = tmp_path / "requirement.md"
    written.write_text("Round tax once, on the total.\n")
    assert run("plan", "--from", str(written), "--project", "erp", "--json").exit_code == 0
    assert run("plan", "--from", "-", "--project", "erp", "--json",
               stdin="Round tax once, on the total.\n").exit_code == 0
    sent = [b["requirement"] for _, p, b in signed.seen if p == "/api/plans/compile"]
    assert len(sent) == 2 and sent[0] == sent[1]


def test_plan_answer_settles_one_open_question_and_reprints_what_is_left(signed: FakeApi):
    signed.plans["PLAN-3"] = {"ref": "PLAN-3", "projectId": "erp", "status": "draft", "risk": "LOW",
                              "confidence": "HIGH", "steps": [], "answered": [], "affectedFiles": [],
                              "businessRequirement": "Round tax once",
                              "openQuestions": ["Which rounding mode?", "Credit notes too?"]}
    done = run("plan-answer", "PLAN-3", "2", "yes, credit notes too")
    assert done.exit_code == 0, done.output
    assert ("POST", "/api/plans/PLAN-3/questions/1", {"answer": "yes, credit notes too"}) in signed.seen
    said = plain(done.stdout)
    assert "Credit notes too?" in said and "1 open question left" in said
    assert "Which rounding mode?" in said                          # the one still open, in the plan

    missing = run("plan-answer", "PLAN-3", "9", "…")
    assert missing.exit_code == 1 and "open question #8" in missing.stderr


def test_on_permission_refuse_finishes_the_answer_without_the_tool(signed: FakeApi):
    signed.after_ask = "card"
    refused = run("ask", "read example.org", "--on-permission", "refuse")
    assert refused.exit_code == 0, refused.output
    assert ("POST", "/api/sessions/SES-1/permissions/2", {"decision": "refuse"}) in signed.seen
    assert "refused (--on-permission refuse)" in refused.stdout
    assert run("ask", "x", "--on-permission", "always").exit_code == 2


def test_continue_takes_the_session_you_were_last_in_then_the_newest(signed: FakeApi):
    run("ask", "first question")
    run("ask", "second question")
    again = run("ask", "and another", "-c")
    assert again.exit_code == 0, again.output
    assert "Continuing SES-2" in again.stderr and "the one you were last in" in again.stderr
    assert len(signed.sessions) == 2                                # nothing new was started

    config.save(lastSession={})                                     # a fresh machine, same server
    newest = run("ask", "once more", "-c")
    assert "Continuing SES-2" in newest.stderr and "the newest one here" in newest.stderr


def test_watch_gives_up_at_its_timeout_and_says_the_run_is_still_going(signed: FakeApi):
    signed.runs["RUN-5"] = {"ref": "RUN-5", "projectId": "erp", "projectName": "ERP", "status": "running",
                            "branch": "neurocode/T-5", "requirement": "…", "steps": [], "tests": {},
                            "diff": {}, "logs": []}
    done = run("runs", "RUN-5", "--watch", "--timeout", "1")
    assert done.exit_code == 4
    assert "Timed out after 1 s" in done.stderr and "RUN-5 is still running" in done.stderr
    assert "Nothing was stopped" in done.stderr


def test_a_list_that_came_back_full_says_it_is_a_page(signed: FakeApi):
    for n in range(3):
        a_run(signed, ref=f"RUN-{n}", gate=False)
    full = run("runs", "--limit", "3", "--json")
    assert len(json.loads(full.stdout)) == 3
    assert "showing 3 — the server's page" in full.stderr
    assert "showing" not in run("runs", "--limit", "10", "--json").stderr


def test_a_list_gives_way_as_the_window_narrows_and_is_not_cut_to_eighty_in_a_pipe(
        signed: FakeApi, monkeypatch):
    a_run(signed, requirement="Round tax once on the total, not on every line.",
          branch="neurocode/task-492-round-the-total-not-the-lines")
    monkeypatch.setenv("COLUMNS", "200")
    wide = plain(run("runs").stdout)
    assert "neurocode/task-492-round-the-total-not-the-lines" in wide and "erp" in wide

    monkeypatch.setenv("COLUMNS", "40")
    narrow = plain(run("runs").stdout)
    assert all(len(line) <= 40 for line in narrow.splitlines())
    assert "RUN-7" in narrow and "neurocode/task-492" not in narrow

    monkeypatch.delenv("COLUMNS")                                   # piped: no window, so no 80 columns
    into_a_pipe = plain(run("runs").stdout)
    assert "neurocode/task-492-round-the-total-not-the-lines" in into_a_pipe
    assert "Round tax once on the total, not on every line." in into_a_pipe
