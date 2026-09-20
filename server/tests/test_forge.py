"""Opening the pull request from here, and reading its state back.

No test in this file has ever reached a real forge, and none can: `gh` and `glab` are stand-ins written
into the test's own folder and put first on PATH, and the token path goes through one function
(`_forge_http`) that is replaced. What is proved is what NeuroCode asks for and what it keeps, which is
the whole of what this product does — the forge's half is the forge's business.
"""
from __future__ import annotations

import importlib
import json
import os
import stat
import subprocess
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import agent
from app import models as m
from app.api import deps
from app.api.app import create_api
from app.secrets import Secrets, forge_token, forge_token_source
from app.services.runs import pr_readiness
from tests.fixtures.workspace import load_workspace

#: The module — `app.agent` re-exports a `git()` function under that same name, so it is asked for here
#: by its full path rather than picked off the package.
forge = importlib.import_module("app.agent.git")

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
VIEWER = {"email": "view@example.com", "name": "Viewer", "password": "another long passphrase", "roles": ["viewer"]}
HEADERS = {"X-NC-Client": "test"}
PID, BRANCH = "forge-lab", "neurocode/task-9001"
SIGNED_AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)

#: What the stand-ins do. `gh` and `glab` read this file on every call and write down what they were
#: asked, so a test can both steer them and read back the exact command NeuroCode ran.
FAKE = r'''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

plan_file = Path(os.environ["FORGE_FAKE"])
plan = json.loads(plan_file.read_text())
argv = sys.argv[1:]
body = ""
if "--body-file" in argv:
    body = sys.stdin.read()
with (plan_file.parent / "calls.jsonl").open("a") as fh:
    fh.write(json.dumps({"tool": Path(sys.argv[0]).name, "argv": argv, "body": body, "cwd": os.getcwd()}) + "\n")

tool = Path(sys.argv[0]).name
verb = " ".join(argv[:2])
if verb == "auth status":
    sys.exit(int(plan.get("auth", 0)))
if verb in ("pr create", "mr create"):
    if plan.get("createSay"):
        print(plan["createSay"], file=sys.stderr)
        sys.exit(int(plan.get("createExit", 1)))
    print(plan["pr"]["url"] if tool == "gh" else plan["pr"]["web_url"])
    sys.exit(0)
if verb in ("pr view", "mr view"):
    print(json.dumps(plan["pr"]))
    sys.exit(0)
print(f"{tool}: the test did not plan for {verb!r}", file=sys.stderr)
sys.exit(2)
'''

GH_PR = {"number": 42, "url": "https://github.com/acme/shop/pull/42", "state": "OPEN",
         "isDraft": False, "title": "Round the total (RUN-9001)"}
GLAB_MR = {"iid": 7, "web_url": "https://gitlab.com/group/shop/-/merge_requests/7", "state": "opened",
           "draft": False, "title": "Round the total (RUN-9001)"}


def run_git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                           "-c", "commit.gpgsign=false", *args],
                          cwd=cwd, check=True, capture_output=True, text=True).stdout


@pytest.fixture
def fakes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`gh` and `glab`, as far as this machine is concerned — first on PATH and nowhere near a network."""
    where = tmp_path / "fake-bin"
    where.mkdir()
    for name in ("gh", "glab"):
        script = where / name
        script.write_text(FAKE)
        script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"auth": 0, "pr": GH_PR}))
    monkeypatch.setenv("FORGE_FAKE", str(plan))
    monkeypatch.setenv("PATH", f"{where}{os.pathsep}{os.environ['PATH']}")
    # A token in the environment would beat the point of a test about `gh`.
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)
    return plan


def plan(plan_file: Path, **fields: Any) -> None:
    plan_file.write_text(json.dumps({**json.loads(plan_file.read_text()), **fields}))


def calls(plan_file: Path) -> list[dict[str, Any]]:
    log = plan_file.parent / "calls.jsonl"
    return [json.loads(line) for line in log.read_text().splitlines()] if log.is_file() else []


@pytest_asyncio.fixture
async def api(session: AsyncSession, tmp_path: Path) -> FastAPI:
    await load_workspace(session)
    made = create_api(db=None)
    # The workspace's real keys file is never opened by a test: this one starts empty and dies with it.
    made.state.gateway.secrets = Secrets(tmp_path / "secrets.json")

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    made.dependency_overrides[deps.session] = use_the_test_session
    return made


def _client(made: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=made), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: FastAPI) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


@pytest_asyncio.fixture
async def lab(session: AsyncSession, client: AsyncClient, tmp_path: Path) -> dict[str, Any]:
    """A checkout whose remote names GitHub, a bare repository standing in for it, and a run that was
    reviewed, signed and pushed — the only state from which a pull request may be opened."""
    root, remote = tmp_path / "shop", tmp_path / "remote.git"
    root.mkdir()
    (root / "core.py").write_text("def total(x):\n    return x\n")
    run_git(["init", "-q", "-b", "main"], root)
    run_git(["add", "-A"], root)
    run_git(["commit", "-qm", "first"], root)
    run_git(["init", "-q", "--bare", str(remote)], tmp_path)
    run_git(["remote", "add", "origin", "git@github.com:acme/shop.git"], root)
    run_git(["config", "remote.origin.pushurl", str(remote)], root)
    base = run_git(["rev-parse", "HEAD"], root).strip()
    tree = tmp_path / "worktrees" / "RUN-9001"
    run_git(["worktree", "add", "-q", "-b", BRANCH, str(tree), base], root)
    (tree / "core.py").write_text("def total(x):\n    return round(x, 2)\n")
    run_git(["commit", "-qam", "round"], tree)
    patch, head = agent.branch_diff(root, base, BRANCH)

    session.add(m.Project(id=PID, name="Forge Lab", source_kind="local", source_repo=str(root)))
    await session.flush()
    session.add(m.Run(
        id="r-RUN-9001", ref="RUN-9001", project_id=PID, status="done", role="solo", branch=BRANCH,
        worktree=str(tree), repo=str(root), base=base, requirement="Round the total to two places",
        requested_by="Rajat", diff_files=1, diff_insertions=1, diff_deletions=1, diff_commits=1,
        tests_command="pytest -q", tests_status="passed", tests_summary="41 passed", tests_passed=41,
        review={"findings": [{"severity": "LOW", "file": "core.py", "line": 2, "note": "Rounding is money-shaped."}],
                "verdict": "Fine.", "by": "mistral-small",
                "checks": [{"name": "lint", "status": "passed", "summary": "clean"}],
                "receipt": {"sha256": agent.fingerprint(patch), "head": head, "base": base,
                            "at": (SIGNED_AT - timedelta(minutes=5)).isoformat(), "by": "mistral-small"}},
        steps=[m.RunStep(n=1, kind="edit", label="Round the total", status="done", commit_sha=head),
               m.RunStep(n=2, kind="review", label="Review the diff", status="done"),
               m.RunStep(n=3, kind="handoff", label="Your approval", status="done")],
        conflicts=[]))
    session.add(m.Approval(id="a-9001", ref="APPR-900", title="Accept RUN-9001", tool=f"Merge({BRANCH})",
                           risk="MEDIUM", status="approved", project_id=PID, run_ref="RUN-9001",
                           run_id="r-RUN-9001", step=3, decided_at=SIGNED_AT))
    await session.flush()
    return {"root": root, "remote": remote, "tree": tree, "head": head, "base": base}


async def pushed(client: AsyncClient) -> dict[str, Any]:
    sent = await client.post("/runs/RUN-9001/push", json={})
    assert sent.status_code == 200, sent.text
    return sent.json()["pushed"]


# ── what can be reached, and what is said when nothing can ───────
def test_a_remote_is_read_for_the_forge_it_is_on_and_nothing_else(tmp_path: Path):
    assert forge.forge_of("git@github.com:acme/shop.git") == ("github.com", "acme/shop")
    assert forge.forge_of("https://gitlab.com/group/sub/shop.git") == ("gitlab.com", "group/sub/shop")
    # A credential written into a remote never leaves this function.
    assert forge.forge_of("https://x:ghp_secret@github.com/acme/shop.git") == ("github.com", "acme/shop")
    for other in ("/srv/git/shop.git", "https://git.example.com/acme/shop.git", "https://github.com/acme"):
        assert forge.forge_of(other) is None


def test_with_no_gh_and_no_token_there_is_no_way_in_and_the_words_say_what_to_do(monkeypatch: pytest.MonkeyPatch,
                                                                                 tmp_path: Path):
    empty = tmp_path / "nothing"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    assert forge.forge_reach("github.com") is None
    assert forge.forge_reach("github.com", "a-token") == "token"
    said = forge.no_way_to_open("github.com", "https://github.com/acme/shop/compare/x?expand=1")
    assert "`gh`" in said and "gh auth login" in said and "compare/x" in said
    # Bitbucket has a compare page and nothing else here, and says so rather than pretending.
    assert "knows no API for bitbucket.org" in forge.no_way_to_open("bitbucket.org")


def test_a_signed_in_gh_is_the_first_choice_even_when_a_token_is_stored(fakes: Path):
    assert forge.forge_reach("github.com", "a-token") == "gh"
    plan(fakes, auth=1)
    assert forge.forge_reach("github.com", "a-token") == "token"
    assert forge.forge_reach("github.com") is None


def test_a_forge_token_is_kept_and_read_the_way_a_model_key_is(tmp_path: Path):
    keys = Secrets(tmp_path / "secrets.json")
    assert forge_token("github.com", keys, {}) is None
    keys.set("github_token", "ghp_abcd1234")
    assert forge_token("github.com", keys, {}) == "ghp_abcd1234"
    assert forge_token_source("github.com", keys, {}) == "workspace"
    assert Secrets.mask(forge_token("github.com", keys, {})) == "••••1234"
    assert stat.S_IMODE((tmp_path / "secrets.json").stat().st_mode) == 0o600
    # Somebody who already exports the token for their own scripts does not type it in again.
    other = Secrets(tmp_path / "none.json")
    assert forge_token("github.com", other, {"GITHUB_TOKEN": "env"}) == "env"
    assert forge_token_source("github.com", other, {"GITHUB_TOKEN": "env"}) == "environment"
    assert forge_token("bitbucket.org", keys, {"GITHUB_TOKEN": "env"}) is None


# ── draft or ready ───────────────────────────────────────────────
def test_a_draft_while_findings_stand_unanswered_and_ready_once_it_is_signed():
    run = m.Run(ref="RUN-1", requirement="x", branch="b", base="0" * 40,
                review={"findings": [{"severity": "HIGH", "note": "a hole"}], "verdict": "Hmm.",
                        "receipt": {"at": (SIGNED_AT - timedelta(minutes=1)).isoformat()}})
    assert pr_readiness(run, SIGNED_AT) == (False, "ready: signed after the review that found 1 finding.")
    # Read again *after* the signature: nobody has answered what that reading found.
    run.review = {**run.review, "receipt": {"at": (SIGNED_AT + timedelta(minutes=1)).isoformat()}}
    draft, why = pr_readiness(run, SIGNED_AT)
    assert draft and "read again after it was signed" in why and "1 finding" in why
    assert pr_readiness(run, None)[0] is True
    run.review = {"findings": [], "verdict": "Fine.", "receipt": {"at": SIGNED_AT.isoformat()}}
    assert pr_readiness(run, SIGNED_AT) == (False, "ready: signed, and its review found nothing.")


# ── opening it ───────────────────────────────────────────────────
async def test_opening_it_with_the_persons_own_gh_keeps_the_number_the_url_and_the_state(
        client: AsyncClient, lab: dict[str, Any], fakes: Path, session: AsyncSession):
    sent = await pushed(client)
    assert sent["baseBranch"] == "main"

    opened = await client.post("/runs/RUN-9001/pr")
    assert opened.status_code == 200, opened.text
    pr = opened.json()["pushed"]["pullRequest"]
    assert (pr["number"], pr["url"], pr["state"]) == (42, GH_PR["url"], "open")
    assert pr["via"] == "gh" and pr["host"] == "github.com" and pr["draft"] is False
    assert pr["by"] == "Rajat" and pr["at"] and pr["base"] == "main" and pr["noun"] == "pull request"
    assert pr["draftBecause"] == "ready: signed after the review that found 1 finding."
    # The compare link is still there: opening one from here never takes the other way away.
    assert opened.json()["pushed"]["compareUrl"].startswith("https://github.com/acme/shop/compare/")

    made = next(c for c in calls(fakes) if c["argv"][:2] == ["pr", "create"])
    assert made["argv"][:2] == ["pr", "create"] and "--draft" not in made["argv"]
    assert made["argv"][made["argv"].index("--repo") + 1] == "acme/shop"
    assert made["argv"][made["argv"].index("--head") + 1] == BRANCH
    assert made["argv"][made["argv"].index("--base") + 1] == "main"

    audit = (await session.execute(select(m.AuditEntry).where(m.AuditEntry.action == "run.pull_request"))).scalars().all()
    assert [a.target for a in audit] == [f"{BRANCH} → github.com #42"]
    line = next(e for e in (await client.get("/activity")).json() if e["action"] == "Pull request opened")
    assert "#42" in line["detail"]
    logs = (await client.get("/runs/RUN-9001")).json()["logs"]
    assert any("pull request #42 opened on github.com (ready)" in x["line"] for x in logs)


async def test_the_body_is_the_runs_own_record_and_carries_nothing_a_model_wrote_just_now(
        client: AsyncClient, lab: dict[str, Any], fakes: Path):
    await pushed(client)
    await client.post("/runs/RUN-9001/pr")
    body = next(c for c in calls(fakes) if c["argv"][:2] == ["pr", "create"])["body"]
    assert "Round the total to two places" in body
    assert "**RUN-9001**" in body and BRANCH in body
    assert "ready: signed after the review that found 1 finding." in body
    assert "Read by **mistral-small**: Fine." in body
    assert "LOW `core.py:2` — Rounding is money-shaped." in body
    assert "`pytest -q` — **passed** · 41 passed · 41 passed" in body
    assert "- lint — **passed**" in body
    assert "Opened from NeuroCode by Rajat, for run RUN-9001." in body
    assert "1 · Round the total — done" in body


async def test_a_run_whose_findings_were_read_after_it_was_signed_goes_up_as_a_draft(
        client: AsyncClient, lab: dict[str, Any], fakes: Path, session: AsyncSession):
    await pushed(client)
    run = await session.get(m.Run, "r-RUN-9001")
    review = dict(run.review)
    run.review = {**review, "receipt": {**review["receipt"], "at": (SIGNED_AT + timedelta(hours=1)).isoformat()}}
    await session.flush()
    plan(fakes, pr={**GH_PR, "isDraft": True})

    opened = await client.post("/runs/RUN-9001/pr")
    pr = opened.json()["pushed"]["pullRequest"]
    assert pr["draft"] is True and "read again after it was signed" in pr["draftBecause"]
    made = next(c for c in calls(fakes) if c["argv"][:2] == ["pr", "create"])
    assert "--draft" in made["argv"]
    assert "This request is a draft:" in made["body"]


async def test_pressing_it_twice_hands_back_the_request_that_already_exists(
        client: AsyncClient, lab: dict[str, Any], fakes: Path):
    await pushed(client)
    await client.post("/runs/RUN-9001/pr")
    plan(fakes, createSay="a pull request for branch neurocode/task-9001 already exists: #42", createExit=1)
    again = await client.post("/runs/RUN-9001/pr")
    assert again.status_code == 200, again.text
    assert again.json()["pushed"]["pullRequest"]["number"] == 42


# ── reading it back ──────────────────────────────────────────────
async def test_the_state_is_read_back_so_the_run_screen_says_merged_without_anybody_looking(
        client: AsyncClient, lab: dict[str, Any], fakes: Path):
    await pushed(client)
    await client.post("/runs/RUN-9001/pr")
    plan(fakes, pr={**GH_PR, "state": "MERGED"})

    read = await client.get("/runs/RUN-9001/pr")
    assert read.status_code == 200, read.text
    assert read.json()["pullRequest"]["state"] == "merged"
    assert read.json()["forge"] == {"host": "github.com", "path": "acme/shop", "noun": "pull request",
                                    "reach": "gh", "why": ""}
    # And it is kept, so the list of runs says it too, without asking the forge again.
    assert (await client.get("/runs/RUN-9001")).json()["pushed"]["pullRequest"]["state"] == "merged"
    logs = (await client.get("/runs/RUN-9001")).json()["logs"]
    assert any("#42 is merged on github.com" in x["line"] for x in logs)


async def test_a_forge_that_cannot_be_reached_answers_with_what_was_last_read_and_why(
        client: AsyncClient, lab: dict[str, Any], fakes: Path):
    await pushed(client)
    await client.post("/runs/RUN-9001/pr")
    plan(fakes, auth=1)                       # signed out since; no token either

    read = (await client.get("/runs/RUN-9001/pr")).json()
    assert read["pullRequest"]["number"] == 42 and read["pullRequest"]["state"] == "open"
    assert read["forge"]["reach"] is None and "gh auth login" in read["forge"]["why"]
    assert read["compareUrl"].startswith("https://github.com/acme/shop/compare/")


async def test_before_anything_is_pushed_the_answer_says_so_rather_than_failing(
        client: AsyncClient, lab: dict[str, Any], fakes: Path):
    read = (await client.get("/runs/RUN-9001/pr")).json()
    assert read["pullRequest"] is None and "not on a remote yet" in read["why"]
    refused = await client.post("/runs/RUN-9001/pr")
    assert refused.status_code == 409 and "Push it first" in refused.json()["detail"]
    assert (await client.get("/runs/RUN-0/pr")).status_code == 404


# ── the refusals ─────────────────────────────────────────────────
async def test_with_no_gh_and_no_token_the_refusal_keeps_the_link_and_nothing_is_opened(
        client: AsyncClient, lab: dict[str, Any], fakes: Path):
    await pushed(client)
    plan(fakes, auth=1)
    refused = await client.post("/runs/RUN-9001/pr")
    assert refused.status_code == 409
    said = refused.json()["detail"]
    assert "not signed in to github.com" in said and "gh auth login" in said
    assert "compare/main...neurocode%2Ftask-9001" in said or "compare/main...neurocode/task-9001" in said
    assert (await client.get("/runs/RUN-9001")).json()["pushed"].get("pullRequest") is None


async def test_a_remote_that_is_no_forge_is_refused_in_words_and_never_guessed_at(
        client: AsyncClient, lab: dict[str, Any], fakes: Path, tmp_path: Path):
    run_git(["remote", "set-url", "origin", str(tmp_path / "remote.git")], lab["root"])
    await pushed(client)
    refused = await client.post("/runs/RUN-9001/pr")
    assert refused.status_code == 409 and "is not GitHub or GitLab" in refused.json()["detail"]


async def test_a_branch_that_moved_since_the_push_is_refused_rather_than_described_wrongly(
        client: AsyncClient, lab: dict[str, Any], fakes: Path, session: AsyncSession):
    await pushed(client)
    run = await session.get(m.Run, "r-RUN-9001")
    run.pushed = {**run.pushed, "sha": "0" * 40}
    await session.flush()
    refused = await client.post("/runs/RUN-9001/pr")
    assert refused.status_code == 409
    assert "Push it again" in refused.json()["detail"]
    assert not any(c["argv"][:2] == ["pr", "create"] for c in calls(fakes))


async def test_opening_one_needs_runs_merge_and_a_stranger_gets_nowhere(
        api: FastAPI, client: AsyncClient, lab: dict[str, Any], fakes: Path):
    await pushed(client)
    await client.post("/admin/users", json=VIEWER)
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.post("/runs/RUN-9001/pr")).status_code == 403
    async with _client(api) as stranger:
        assert (await stranger.post("/runs/RUN-9001/pr")).status_code == 401
    assert not any(c["argv"][:2] == ["pr", "create"] for c in calls(fakes))


# ── GitLab, with the person's own glab ───────────────────────────
async def test_gitlab_opens_a_merge_request_and_is_called_a_merge_request_throughout(
        client: AsyncClient, lab: dict[str, Any], fakes: Path):
    run_git(["remote", "set-url", "origin", "https://gitlab.com/group/shop.git"], lab["root"])
    run_git(["config", "remote.origin.pushurl", str(lab["remote"])], lab["root"])
    plan(fakes, pr=GLAB_MR)
    await pushed(client)

    opened = await client.post("/runs/RUN-9001/pr")
    assert opened.status_code == 200, opened.text
    pr = opened.json()["pushed"]["pullRequest"]
    assert (pr["number"], pr["url"], pr["state"], pr["via"]) == (7, GLAB_MR["web_url"], "open", "glab")
    assert pr["noun"] == "merge request"
    made = next(c for c in calls(fakes) if c["argv"][:2] == ["mr", "create"])
    assert made["tool"] == "glab" and "--yes" in made["argv"]
    assert made["argv"][made["argv"].index("--source-branch") + 1] == BRANCH
    assert made["argv"][made["argv"].index("--target-branch") + 1] == "main"
    logs = (await client.get("/runs/RUN-9001")).json()["logs"]
    assert any("merge request #7 opened on gitlab.com" in x["line"] for x in logs)


# ── the token path, when neither CLI is there ────────────────────
async def test_a_stored_token_opens_it_when_no_cli_is_installed(
        api: FastAPI, client: AsyncClient, lab: dict[str, Any], fakes: Path, monkeypatch: pytest.MonkeyPatch):
    await pushed(client)
    plan(fakes, auth=1)                       # `gh` is there but signed out

    asked: list[tuple[str, str, dict[str, str], Any]] = []

    def answer(method: str, url: str, headers: dict[str, str], payload: Any = None) -> tuple[int, Any]:
        asked.append((method, url, headers, payload))
        return 201, {"number": 88, "html_url": "https://github.com/acme/shop/pull/88", "state": "open",
                     "draft": False, "title": "Round the total (RUN-9001)"}

    monkeypatch.setattr(forge, "_forge_http", answer)
    kept = await client.put("/runs/forges/github.com/token", json={"token": "ghp_secret_1234"})
    assert kept.status_code == 200, kept.text
    assert kept.json()["tokenMask"] == "••••1234" and kept.json()["reach"] == "token"
    assert "ghp_secret_1234" not in kept.text

    opened = await client.post("/runs/RUN-9001/pr")
    assert opened.status_code == 200, opened.text
    pr = opened.json()["pushed"]["pullRequest"]
    assert (pr["number"], pr["via"], pr["state"]) == (88, "token", "open")
    method, url, headers, payload = asked[0]
    assert (method, url) == ("POST", "https://api.github.com/repos/acme/shop/pulls")
    assert headers["Authorization"] == "Bearer ghp_secret_1234"
    assert payload["head"] == BRANCH and payload["base"] == "main" and payload["draft"] is False


async def test_the_forges_list_says_what_this_machine_can_do_and_never_shows_a_token(
        client: AsyncClient, api: FastAPI, fakes: Path):
    listed = await client.get("/runs/forges")
    assert listed.status_code == 200, listed.text
    by_host = {x["host"]: x for x in listed.json()}
    assert by_host["github.com"]["reach"] == "gh" and by_host["github.com"]["tool"] == "gh"
    assert by_host["bitbucket.org"]["reach"] is None and by_host["bitbucket.org"]["takesToken"] is False
    assert "knows no API for bitbucket.org" in by_host["bitbucket.org"]["why"]

    await client.put("/runs/forges/github.com/token", json={"token": "ghp_secret_1234"})
    again = {x["host"]: x for x in (await client.get("/runs/forges")).json()}
    assert again["github.com"]["hasToken"] is True and again["github.com"]["tokenMask"] == "••••1234"
    assert "ghp_secret_1234" not in (await client.get("/runs/forges")).text
    assert (await client.put("/runs/forges/example.com/token", json={"token": "x"})).status_code == 409

    await client.post("/admin/users", json=VIEWER)
    async with _client(api) as viewer:
        await viewer.post("/auth/login", json={"email": VIEWER["email"], "password": VIEWER["password"]})
        assert (await viewer.get("/runs/forges")).status_code == 403
        assert (await viewer.put("/runs/forges/github.com/token", json={"token": "x"})).status_code == 403
