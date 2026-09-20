"""`nc pr` and `nc forges`, end to end against an API that answers in the server's shapes.

This file brings its own transport rather than the shared fake, so what it proves is exactly what `nc`
sends and exactly what it prints — the server's own suite proves the shapes are real.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from neurocode_cli import client as client_module
from neurocode_cli.main import app

from .conftest import API, TOKEN, URL

runner = CliRunner()
ANSI = re.compile(r"\x1b\[[0-9;]*m")

PR = {"number": 42, "url": "https://github.com/acme/shop/pull/42", "state": "open", "draft": False,
      "host": "github.com", "noun": "pull request", "via": "gh", "base": "main", "by": "Rajat",
      "at": "2026-03-01T12:00:00+00:00", "checkedAt": "2026-03-01T12:00:00+00:00",
      "draftBecause": "ready: signed, and its review found nothing."}
COMPARE = "https://github.com/acme/shop/compare/main...neurocode/task-9001?expand=1"


class Api:
    """What the API answers, and every request it was sent."""

    def __init__(self) -> None:
        self.seen: list[tuple[str, str, Any]] = []
        self.pr: dict[str, Any] | None = None
        self.forges = [
            {"host": "github.com", "noun": "pull request", "tool": "gh", "reach": "gh", "takesToken": True,
             "hasToken": False, "tokenMask": None, "tokenSource": None, "why": ""},
            {"host": "bitbucket.org", "noun": "pull request", "tool": None, "reach": None, "takesToken": False,
             "hasToken": False, "tokenMask": None, "tokenSource": None,
             "why": "NeuroCode cannot open a pull request on bitbucket.org from here: it drives `gh` for "
                    "GitHub and `glab` for GitLab, and knows no API for bitbucket.org."},
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.seen.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/api/runs/forges":
            return httpx.Response(200, json=self.forges)
        if path == "/api/runs/forges/github.com/token" and request.method == "PUT":
            self.forges[0] = {**self.forges[0], "hasToken": bool(body["token"]),
                              "tokenMask": f"••••{body['token'][-4:]}" if body["token"] else None}
            return httpx.Response(200, json=self.forges[0])
        if path == "/api/runs/RUN-9001/pr" and request.method == "POST":
            self.pr = PR
            return httpx.Response(200, json={"ref": "RUN-9001", "branch": "neurocode/task-9001",
                                             "pushed": {"remote": "origin", "compareUrl": COMPARE,
                                                        "pullRequest": self.pr}})
        if path == "/api/runs/RUN-9001/pr":
            return httpx.Response(200, json={"ref": "RUN-9001", "pullRequest": self.pr,
                                             "compareUrl": COMPARE, "forge": None,
                                             "why": "" if self.pr else "No pull request has been opened yet."})
        if path == "/api/runs/RUN-8/pr":
            return httpx.Response(409, json={"detail": "RUN-8's branch is not on a remote yet. Push it first."})
        return httpx.Response(404, json={"detail": f"no route for {path}"})


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Api]:
    fake = Api()
    monkeypatch.setattr(client_module, "TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setenv("NC_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    monkeypatch.setenv("NC_URL", API)
    monkeypatch.setenv("NC_TOKEN", TOKEN)
    monkeypatch.setenv("NC_WEB", URL)
    monkeypatch.delenv("NC_PROJECT", raising=False)
    yield fake


def run(*args: str, stdin: str | None = None):
    return runner.invoke(app, list(args), input=stdin, catch_exceptions=False)


def plain(text: str) -> str:
    return ANSI.sub("", text)


def test_nc_pr_opens_it_and_prints_the_number_the_state_and_why_it_went_up_that_way(api: Api):
    done = run("pr", "RUN-9001")
    assert done.exit_code == 0, done.output
    said = plain(done.stdout)
    assert "Pull request #42" in said and "github.com" in said and "open" in said
    assert PR["url"] in said
    assert "ready: signed, and its review found nothing." in said
    assert ("POST", "/api/runs/RUN-9001/pr", None) in api.seen


def test_nc_pr_check_reads_the_state_back_and_opens_nothing(api: Api):
    api.pr = {**PR, "state": "merged"}
    read = run("pr", "RUN-9001", "--check")
    assert read.exit_code == 0, read.output
    assert "merged" in plain(read.stdout)
    assert [m for m, _, _ in api.seen] == ["GET"]


def test_nc_pr_check_says_plainly_when_there_is_none_and_hands_back_the_link(api: Api):
    read = run("pr", "RUN-9001", "--check")
    assert read.exit_code == 0
    said = plain(read.stdout)
    assert "No pull request has been opened yet." in said and COMPARE in said


def test_nc_pr_json_prints_the_api_answer_and_nothing_else(api: Api):
    done = run("pr", "RUN-9001", "--json")
    assert done.exit_code == 0
    assert json.loads(done.stdout)["pushed"]["pullRequest"]["number"] == 42


def test_nc_pr_exits_one_with_the_servers_own_words_when_nothing_is_pushed(api: Api):
    refused = run("pr", "RUN-8")
    assert refused.exit_code == 1
    assert "not on a remote yet" in plain(refused.stderr)


def test_nc_forges_says_what_this_machine_can_open_a_request_with(api: Api):
    listed = run("forges")
    assert listed.exit_code == 0, listed.output
    said = plain(listed.stdout)
    assert "github.com" in said and "your own gh" in said
    assert "bitbucket.org" in said and "knows no API for bitbucket.org" in said
    assert json.loads(run("forges", "--json").stdout)[0]["host"] == "github.com"


def test_a_forge_token_is_read_from_stdin_and_only_its_last_four_are_ever_printed(api: Api):
    kept = run("forges", "--set-token", "github.com", stdin="ghp_secret_1234\n")
    assert kept.exit_code == 0, kept.output
    assert "••••1234" in plain(kept.stdout) and "ghp_secret_1234" not in kept.output
    sent = next(b for m, p, b in api.seen if m == "PUT")
    assert sent == {"token": "ghp_secret_1234"}
    # The token never travels as an argument, so it cannot be left behind in a shell history.
    assert "ghp_secret_1234" not in " ".join(str(p) for _, p, _ in api.seen)

    cleared = run("forges", "--set-token", "github.com", stdin="\n")
    assert cleared.exit_code == 0 and "token cleared" in plain(cleared.stdout)
