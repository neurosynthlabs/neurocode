"""A project's instruction files: what is read, what is refused, which rules apply, and the cap.

`resolve` is pure — a folder in, text and a list out — so most of this file builds a checkout in a
temporary folder and reads it. The route and the session's system prompt are checked against the same
folder. Nothing here reaches a model or the network.
"""
from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.app import create_api
from app.models import Chat, Project
from app.schemas import chat_json
from app.services import instructions
from app.services.chat import system_prompt
from app.services.instructions import MAX_BYTES, MAX_HOPS, matching, resolve

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}
PID = "rules-lab"


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def paths(found: instructions.Resolved) -> list[str]:
    return [f["path"] for f in found.files]


# ── what is read, in what order ──────────────────────────────────
def test_the_root_files_and_the_rules_are_read_in_order(tmp_path: Path):
    write(tmp_path, "CLAUDE.md", "Claude notes")
    write(tmp_path, "AGENTS.md", "Agents first")
    write(tmp_path, "CLAUDE.local.md", "Mine only")
    write(tmp_path, ".claude/CLAUDE.md", "Nested claude")
    write(tmp_path, ".claude/rules/b.md", "Rule b")
    write(tmp_path, ".claude/rules/a/deep.md", "Rule deep")
    write(tmp_path, ".neurocode/rules/n.md", "Rule n")
    write(tmp_path, "docs/AGENTS.md", "Not at the root, so not read")

    found = resolve(tmp_path)
    assert paths(found) == ["AGENTS.md", "CLAUDE.md", ".claude/CLAUDE.md", "CLAUDE.local.md",
                            ".claude/rules/a/deep.md", ".claude/rules/b.md", ".neurocode/rules/n.md"]
    assert [f["scope"] for f in found.files] == ["project"] * 4 + ["rules"] * 3
    assert found.text.index("Agents first") < found.text.index("Claude notes") < found.text.index("Rule n")
    assert "=== AGENTS.md ===" in found.text and "Not at the root" not in found.text
    agents = found.files[0]
    assert agents["sha1"] == hashlib.sha1(b"Agents first").hexdigest() and agents["bytes"] == 12
    assert agents["applied"] and agents["matched"] is None and agents["importedBy"] is None
    assert found.refused == [] and found.capped is False


def test_a_folder_above_the_checkout_is_never_read(tmp_path: Path):
    write(tmp_path, "AGENTS.md", "The parent's instructions")
    (tmp_path / "repo").mkdir()
    assert resolve(tmp_path / "repo").files == []
    assert resolve(tmp_path / "missing").text == ""


def test_html_comments_are_stripped(tmp_path: Path):
    write(tmp_path, "AGENTS.md", "Keep this.\n<!-- a note\nfor editors -->\nAnd this.")
    found = resolve(tmp_path)
    assert "Keep this." in found.text and "And this." in found.text and "editors" not in found.text


# ── imports ──────────────────────────────────────────────────────
def test_imports_are_followed_inside_the_checkout(tmp_path: Path):
    write(tmp_path, "AGENTS.md", "See @docs/setup.md and mail me@example.com, not `@ignored/in-code.md`.")
    write(tmp_path, "docs/setup.md", "Setup: run make. Also @./style.md")
    write(tmp_path, "docs/style.md", "Style: four spaces. Back to @../AGENTS.md")

    found = resolve(tmp_path)
    assert paths(found) == ["AGENTS.md", "docs/setup.md", "docs/style.md"]
    assert found.files[1]["importedBy"] == "AGENTS.md" and found.files[2]["importedBy"] == "docs/setup.md"
    assert found.text.index("mail me") < found.text.index("Setup: run make") < found.text.index("four spaces")
    assert found.refused == []                              # a loop back to AGENTS.md is read once, not refused


def test_imports_that_leave_the_checkout_are_refused_and_listed(tmp_path: Path):
    outside = tmp_path / "secret.md"
    outside.write_text("the password is hunter2")
    root = tmp_path / "repo"
    write(root, "AGENTS.md", "@../secret.md\n@/etc/passwd\n@~/.ssh/config\n@docs/missing.md\n@docs/\n@link.md\n@alice")
    (root / "docs").mkdir()
    os.symlink(outside, root / "link.md")

    found = resolve(root)
    assert paths(found) == ["AGENTS.md"]
    assert "hunter2" not in found.text
    why = {r["path"]: r["why"] for r in found.refused}
    assert why == {"../secret.md": "outside the checkout", "/etc/passwd": "outside the checkout",
                   "~/.ssh/config": "outside the checkout", "docs/missing.md": "no such file",
                   "docs/": "a folder, not a file", "link.md": "outside the checkout"}
    assert all(r["from"] == "AGENTS.md" for r in found.refused)   # @alice is a mention, not a path


def test_a_root_file_that_links_outside_is_refused(tmp_path: Path):
    outside = tmp_path / "elsewhere.md"
    outside.write_text("not this project's")
    root = tmp_path / "repo"
    root.mkdir()
    os.symlink(outside, root / "CLAUDE.md")
    found = resolve(root)
    assert found.files == [] and found.refused[0]["path"] == "CLAUDE.md"


def test_imports_stop_after_four_hops(tmp_path: Path):
    write(tmp_path, "AGENTS.md", "@h1.md")
    for n in range(1, MAX_HOPS + 2):
        write(tmp_path, f"h{n}.md", f"hop {n} @h{n + 1}.md")
    found = resolve(tmp_path)
    assert paths(found) == ["AGENTS.md", *(f"h{n}.md" for n in range(1, MAX_HOPS + 1))]
    assert found.refused == [{"path": f"h{MAX_HOPS + 1}.md", "why": f"more than {MAX_HOPS} imports deep",
                              "from": f"h{MAX_HOPS}.md"}]


# ── rules scoped to paths ────────────────────────────────────────
def test_a_rule_with_paths_applies_only_when_a_target_matches(tmp_path: Path):
    write(tmp_path, ".claude/rules/sql.md", "---\npaths:\n  - \"db/**/*.sql\"\n---\nNever DROP a column.\n@sql-more.md")
    write(tmp_path, ".claude/rules/sql-more.md", "Name migrations by date.")
    write(tmp_path, ".claude/rules/web.md", "---\npaths: src/**/*.{ts,tsx}, web/*.css\n---\nUse tokens.")
    write(tmp_path, ".claude/rules/always.md", "Be kind.")

    none = resolve(tmp_path)
    assert "Be kind." in none.text and "Never DROP" not in none.text and "Use tokens" not in none.text
    sql = next(f for f in none.files if f["path"] == ".claude/rules/sql.md")
    assert sql["applied"] is False and sql["paths"] == ["db/**/*.sql"] and sql["matched"] is None

    found = resolve(tmp_path, ["./db/migrations/2026/01_add.sql", "src/app/page.tsx"])
    assert "Never DROP a column." in found.text and "Use tokens." in found.text
    assert "Name migrations by date." in found.text      # what an applied rule imports comes with it
    by_path = {f["path"]: f for f in found.files}
    assert by_path[".claude/rules/sql.md"]["matched"] == "db/**/*.sql"
    assert by_path[".claude/rules/web.md"]["matched"] == "src/**/*.{ts,tsx}"
    assert "---" not in found.text                       # the front matter is not handed over


def test_globs_keep_to_their_folders():
    assert matching(["src/*.ts"], ["src/a.ts"]) == "src/*.ts"
    assert matching(["src/*.ts"], ["src/x/a.ts"]) is None
    assert matching(["**/*.py"], ["a.py", "x/y/z.py"]) == "**/*.py"
    assert matching([".github/**"], [".github/workflows/ci.yml"]) == ".github/**"
    assert matching(["lib/**/*.go"], ["lib/a.go"]) == "lib/**/*.go"
    assert matching(["lib/**/*.go"], ["other/lib/a.go"]) is None


# ── the cap ──────────────────────────────────────────────────────
def test_the_text_is_capped_and_says_what_it_cut(tmp_path: Path):
    line = "x" * 99 + "\n"
    write(tmp_path, "AGENTS.md", line * 200)             # 20 KB
    write(tmp_path, "CLAUDE.md", line * 100)             # 10 KB: crosses the cap
    write(tmp_path, "CLAUDE.local.md", "never reached")

    found = resolve(tmp_path)
    assert found.bytes <= MAX_BYTES and found.capped is True
    cut = {f["path"]: f["cut"] for f in found.files}
    assert cut == {"AGENTS.md": False, "CLAUDE.md": True, "CLAUDE.local.md": True}
    assert "more bytes of CLAUDE.md did not fit" in found.text and "never reached" not in found.text


# ── over HTTP, and in a session ──────────────────────────────────
@pytest_asyncio.fixture
async def client(seeded: AsyncSession, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield seeded

    api.dependency_overrides[deps.session] = use_the_test_session
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    write(root, "AGENTS.md", "Run `make test`. @../outside.md")
    write(root, ".claude/rules/api.md", "---\npaths: api/**\n---\nEvery route checks a permission.")
    return root


async def test_the_route_lists_files_rules_refusals_and_the_cap(client: AsyncClient, seeded: AsyncSession,
                                                                 repo: Path):
    seeded.add(Project(id=PID, name="Rules Lab", source_kind="local", source_repo=str(repo)))
    await seeded.flush()

    body = (await client.get(f"/projects/{PID}/instructions")).json()
    assert [(f["path"], f["scope"], f["applied"]) for f in body["files"]] == [
        ("AGENTS.md", "project", True), (".claude/rules/api.md", "rules", False)]
    assert body["refused"] == [{"path": "../outside.md", "why": "outside the checkout", "from": "AGENTS.md"}]
    assert body["capped"] is False and body["cap"] == MAX_BYTES and body["bytes"] > 0

    scoped = (await client.get(f"/projects/{PID}/instructions", params={"target": ["api/routes.py"]})).json()
    assert scoped["files"][1]["applied"] is True and scoped["files"][1]["matched"] == "api/**"
    assert scoped["bytes"] > body["bytes"]


async def test_a_project_with_no_code_here_has_no_instructions(client: AsyncClient):
    assert (await client.get("/projects/erp/instructions")).json() == {
        "files": [], "refused": [], "bytes": 0, "capped": False, "cap": MAX_BYTES}
    assert (await client.get("/projects/nope/instructions")).status_code == 404


async def test_the_route_needs_a_session(seeded: AsyncSession):
    api = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield seeded

    api.dependency_overrides[deps.session] = use_the_test_session
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        assert (await c.get("/projects/erp/instructions")).status_code == 401


async def test_a_session_is_handed_the_instructions_and_says_which(repo: Path):
    project = Project(id=PID, name="Rules Lab", source_kind="local", source_repo=str(repo))
    found = await instructions.for_project(project)
    prompt = system_prompt("Rules Lab", (), found.text)
    assert "The project's instructions" in prompt and "Run `make test`." in prompt
    assert "Every route checks a permission." not in prompt   # scoped to api/**, and nothing targeted it
    assert prompt.index("Run `make test`.") < prompt.index("Answer with one JSON object")
    assert "The project's instructions" not in system_prompt("Rules Lab")

    chat = Chat(id="s1", ref="SES-1", project_id=PID, title="t", status="idle", started_by="Rajat",
                turns=0, tool_calls=0, model="", lane="", note="")
    assert chat_json(chat, instructions=found.brief())["instructions"] == [
        {"path": "AGENTS.md", "bytes": (repo / "AGENTS.md").stat().st_size}]
    assert "instructions" not in chat_json(chat)             # never read is not the same as none
