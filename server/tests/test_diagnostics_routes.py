"""Problems from a project's own checkers, and hover / go to definition from a language server, over HTTP.

The checkers are small stand-ins the test writes into the project where the real ones live — a `tsc` in
`node_modules/.bin`, a `ruff` in `.venv/bin` — printing exactly what the real tools print (captured from
tsc 5 and ruff), so detection, running, the time limit, cancelling and the reading of their output are
all real, and nothing depends on what this machine has installed. PATH is narrowed to the system's
own folders, so a checker the project configures but does not install is reported missing.

The language server is `tests/fixtures/fake_lsp.py`, which speaks the protocol over stdio like pyright
does — never a real one. The parsers are also tested directly on real tools' output.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import models as m
from app.api import deps
from app.api.app import create_api
from app.api.routes_diagnostics import checks_of, servers_of
from app.services import diagnostics, lsp, machine
from app.settings import settings as real_settings

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
ADMIN = {"email": "admin@example.com", "name": "Asha", "password": "another long passphrase", "roles": ["admin"]}
HEADERS = {"X-NC-Client": "test"}
FAKE_LSP = Path(__file__).parent / "fixtures" / "fake_lsp.py"

TSC_SAYS = ("src/a.ts(1,7): error TS2322: Type 'string' is not assignable to type 'number'.\n"
            "src/a.ts(2,41): error TS2339: Property 'foo' does not exist on type 'string'.\n"
            "  The rest of a long message, indented under it.\n")


def tool(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def fake_tsc(folder: Path, body: str | None = None) -> Path:
    """`tsc` as the project installs it; it notes its arguments so the command can be checked."""
    said = body if body is not None else f"cat <<'SAID'\n{TSC_SAYS}SAID\nexit 2\n"
    return tool(folder / "node_modules" / ".bin" / "tsc", f"#!/bin/sh\necho \"$@\" > \"$PWD/.tsc-args\"\n{said}")


def fake_ruff(folder: Path) -> Path:
    """`ruff` in the project's venv, printing `ruff check --output-format json` for src/b.py."""
    report = [{"code": "F401", "message": "`os` imported but unused", "filename": str(folder / "src" / "b.py"),
               "location": {"row": 1, "column": 8}, "end_location": {"row": 1, "column": 10}},
              {"code": None, "message": "SyntaxError: Expected an expression", "filename": str(folder / "src" / "b.py"),
               "location": {"row": 3, "column": 5}, "end_location": {"row": 3, "column": 6}}]
    return tool(folder / ".venv" / "bin" / "ruff",
                f"#!{sys.executable}\nimport json, sys\nprint(json.dumps({report!r}))\n"
                "open('.ruff-args', 'w').write(' '.join(sys.argv[1:]))\nsys.exit(1)\n")


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The machine's only root, `work/`, holding a project `shop` with TypeScript and Python in it."""
    work = Path(os.path.realpath(tmp_path)) / "work"
    shop = work / "shop"
    (shop / "src").mkdir(parents=True)
    (shop / "src" / "a.ts").write_text("const a: number = 'x';\nexport function f(b: string) { return b.foo; }\n")
    (shop / "src" / "b.py").write_text("import os\n\nx = \n")
    (shop / "tsconfig.json").write_text('{\n  // strict, as the project wants\n'
                                        '  "compilerOptions": {"strict": true},\n}\n')
    (shop / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\n\n[tool.mypy]\nstrict = true\n")
    fake_tsc(shop)
    fake_ruff(shop)
    (tmp_path / "outside").mkdir()
    configured = real_settings().model_copy(update={"machine_roots": str(work), "machine_access": True})
    monkeypatch.setattr(machine, "settings", lambda: configured)
    # Only the system's own folders: no mypy, no language server, nothing of this machine's own.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    return work


@pytest_asyncio.fixture
async def api(catalogued: AsyncSession, root: Path) -> AsyncIterator[Any]:
    session = catalogued
    session.add(m.Project(id="shop", name="Shop", source_kind="local", source_repo=str(root / "shop")))
    await session.flush()
    app = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[deps.session] = use_the_test_session
    try:
        yield app
    finally:
        await checks_of(app).close_all()
        await servers_of(app).close_all()
        app.state.ledger.close()
        await app.state.db.close()


def _client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://api", headers=HEADERS)


@pytest_asyncio.fixture
async def client(api: Any) -> AsyncIterator[AsyncClient]:
    async with _client(api) as c:
        assert (await c.post("/auth/setup", json=OWNER)).status_code in (200, 201)
        yield c


async def finished(client: AsyncClient, check_id: str, **params: Any) -> dict[str, Any]:
    for _ in range(200):
        got = await client.get(f"/diagnostics/checks/{check_id}", params=params)
        assert got.status_code == 200, got.text
        if got.json()["status"] != "running":
            return got.json()
        await asyncio.sleep(0.05)
    raise AssertionError("the check did not finish")


# ── the parsers, on what real tools print ─────────────────────────
def test_text_reports_of_many_tools_read_into_problems():
    said = (TSC_SAYS +
            "# example/sub\n"
            "vet: sub/s.go:4:9: cannot use \"a\" (untyped string constant) as int value in return statement\n"
            "main.go:6:14: fmt.Printf format %d has arg \"x\" of wrong type string\n"
            "app/x.py:3:1: error: Name \"foo\" is not defined  [name-defined]\n"
            "app/x.py:4: note: See the docs\n"
            "F401 [*] `os` imported but unused\n --> src/b.py:2:8\n"
            "error[E0308]: mismatched types\n --> src/main.rs:2:18\n"
            "/home/x/src/app.js\n  12:5  error  'x' is not defined  no-undef\n"
            "   3:1  warning  Unexpected console  no-console\n"
            "\n  /abs/p.py:3:5 - error: \"foo\" is not defined (reportUndefinedVariable)\n"
            "/p/Program.cs(10,13): error CS1002: ; expected [/p/p.csproj]\n"
            "[ERROR] /p/src/main/java/A.java:[12,5] cannot find symbol\n"
            "e: file:///p/src/main/kotlin/A.kt:3:5 Unresolved reference: foo\n"
            "src/Main.java:5: error: ';' expected\n"
            "npm ERR! code ELIFECYCLE\nsee http://example.com:80: nothing here\n")
    found = [(f.file, f.line, f.col, f.severity, f.code) for f in diagnostics.parse_text(said)]
    assert found == [
        ("src/a.ts", 1, 7, "error", "TS2322"), ("src/a.ts", 2, 41, "error", "TS2339"),
        ("sub/s.go", 4, 9, "error", None), ("main.go", 6, 14, "error", None),
        ("app/x.py", 3, 1, "error", "name-defined"), ("app/x.py", 4, 1, "info", None),
        ("src/b.py", 2, 8, "warning", "F401"), ("src/main.rs", 2, 18, "error", "E0308"),
        ("/home/x/src/app.js", 12, 5, "error", "no-undef"), ("/home/x/src/app.js", 3, 1, "warning", "no-console"),
        ("/abs/p.py", 3, 5, "error", "reportUndefinedVariable"), ("/p/Program.cs", 10, 13, "error", "CS1002"),
        ("/p/src/main/java/A.java", 12, 5, "error", None), ("/p/src/main/kotlin/A.kt", 3, 5, "error", None),
        ("src/Main.java", 5, 1, "error", None)]
    ts = diagnostics.parse_text(TSC_SAYS)
    assert ts[1].message == ("Property 'foo' does not exist on type 'string'.\n"
                             "The rest of a long message, indented under it.")
    [cs] = diagnostics.parse_text("/p/Program.cs(10,13): error CS1002: ; expected [/p/p.csproj]")
    assert cs.message == "; expected"


def test_json_reports_read_into_problems():
    eslint = json.dumps([{"filePath": "/w/a.js", "messages": [
        {"ruleId": "no-undef", "severity": 2, "message": "'x' is not defined.", "line": 3, "column": 5,
         "endLine": 3, "endColumn": 6},
        {"ruleId": None, "severity": 1, "message": "File ignored", "line": 0, "column": 0}]}])
    assert [(f.file, f.line, f.col, f.end_col, f.severity, f.code) for f in diagnostics.parse_eslint_json(eslint)] == [
        ("/w/a.js", 3, 5, 6, "error", "no-undef"), ("/w/a.js", 1, 1, None, "warning", None)]

    pyright = json.dumps({"generalDiagnostics": [{"file": "/w/a.py", "severity": "information", "message": "m",
                                                  "rule": "reportX", "range": {"start": {"line": 0, "character": 4},
                                                                              "end": {"line": 0, "character": 9}}}]})
    [p] = diagnostics.parse_pyright_json(pyright)
    assert (p.line, p.col, p.end_line, p.end_col, p.severity, p.code) == (1, 5, 1, 10, "info", "reportX")

    oxlint = ('{ "diagnostics": [{"message": "Variable \'a\' is declared but never used.","code": '
              '"eslint(no-unused-vars)","severity": "warning","help": "Consider removing this declaration.",'
              '"filename": "a.ts","labels": [{"span": {"offset": 6,"length": 1,"line": 1,"column": 7}}]}],'
              '"number_of_files": 1}')
    [o] = diagnostics.parse_oxlint_json(oxlint)
    assert (o.file, o.line, o.col, o.severity, o.code) == ("a.ts", 1, 7, "warning", "eslint(no-unused-vars)")
    assert o.message.endswith("Consider removing this declaration.")

    cargo = "\n".join([
        json.dumps({"reason": "compiler-artifact"}),
        json.dumps({"reason": "compiler-message", "message": {
            "level": "error", "message": "mismatched types", "code": {"code": "E0308"},
            "spans": [{"file_name": "src/main.rs", "is_primary": False, "line_start": 2, "column_start": 12,
                       "line_end": 2, "column_end": 15},
                      {"file_name": "src/main.rs", "is_primary": True, "line_start": 2, "column_start": 18,
                       "line_end": 2, "column_end": 21, "label": "expected `i32`, found `&str`"}]}}),
        json.dumps({"reason": "compiler-message", "message": {"level": "error", "message": "aborting",
                                                              "spans": []}})])
    [c] = diagnostics.parse_cargo_json(cargo)
    assert (c.file, c.line, c.col, c.end_col, c.code) == ("src/main.rs", 2, 18, 21, "E0308")
    assert c.message == "mismatched types: expected `i32`, found `&str`"

    # npm prints a banner before the JSON; it is looked past.
    assert len(diagnostics.parse_eslint_json("> lint\n> eslint -f json .\n\n" + eslint)) == 2
    assert diagnostics.parse_ruff_json("not json at all") == []


def test_detection_reads_the_project_and_names_what_is_missing(root: Path):
    shop = root / "shop"
    found, missing = diagnostics.detect(shop, root)
    assert [(c.tool, c.parser) for c in found] == [("tsc", "text"), ("ruff", "ruff-json")]
    assert found[0].argv[1:] == ("--noEmit", "--pretty", "false", "-p", "tsconfig.json")
    assert "--no-fix" in found[1].argv and found[1].command(shop).startswith(".venv/bin/ruff check")
    assert [m.tool for m in missing] == ["mypy"] and "pip install mypy" in missing[0].why

    # A solution-style tsconfig is checked per reference — a bare `-p tsconfig.json` would check nothing.
    (shop / "tsconfig.json").write_text('{"files": [], "references": [{"path": "./tsconfig.app.json"}, '
                                        '{"path": "./tsconfig.node.json"}, {"path": "./gone.json"},]}')
    (shop / "tsconfig.app.json").write_text("{}")
    (shop / "tsconfig.node.json").write_text("{}")
    found, _ = diagnostics.detect(shop, root)
    assert [c.label for c in found if c.tool == "tsc"] == ["tsc · tsconfig.app.json", "tsc · tsconfig.node.json"]

    # A monorepo's node_modules above the folder is the project's own too; above the root it is not.
    (root / "mono" / "web").mkdir(parents=True)
    fake_tsc(root / "mono")
    (root / "mono" / "web" / "tsconfig.json").write_text("{}")
    assert [c.tool for c in diagnostics.detect(root / "mono" / "web", root)[0]] == ["tsc"]
    assert diagnostics.detect(root / "mono" / "web", root / "mono" / "web")[0] == []

    # Declared but not installed, each named with what installs it; nothing declared, nothing run.
    (root / "rs").mkdir()
    (root / "rs" / "Cargo.toml").write_text("[package]\nname='x'\n")
    (root / "rs" / ".eslintrc.json").write_text("{}")
    assert {m.tool for m in diagnostics.detect(root / "rs", root)[1]} == {"cargo check", "eslint"}
    (root / "empty").mkdir()
    assert diagnostics.detect(root / "empty", root) == ([], [])


# ── checks over HTTP ──────────────────────────────────────────────
async def test_a_check_runs_the_projects_own_checkers_and_reads_their_problems(
        client: AsyncClient, root: Path, catalogued: AsyncSession):
    shop = root / "shop"
    listed = await client.get("/diagnostics/checkers", params={"folder": str(shop)})
    assert listed.status_code == 200, listed.text
    assert [c["tool"] for c in listed.json()["checkers"]] == ["tsc", "ruff"]
    assert listed.json()["checkers"][0]["command"] == "node_modules/.bin/tsc --noEmit --pretty false -p tsconfig.json"
    assert listed.json()["missing"][0]["tool"] == "mypy"

    started = await client.post("/diagnostics/checks", json={"folder": str(shop)})
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "running" and started.json()["problems"] == []
    done = await finished(client, started.json()["id"])

    assert done["status"] == "done" and done["target"]["kind"] == "folder" and done["target"]["folder"] == str(shop)
    assert [(t["tool"], t["status"], t["exit"], t["problems"]) for t in done["tools"]] == [
        ("tsc", "problems", 2, 2), ("ruff", "problems", 1, 2)]
    assert all(t["output"] == [] for t in done["tools"])              # the problems speak for themselves
    assert (shop / ".tsc-args").read_text().strip() == "--noEmit --pretty false -p tsconfig.json"
    assert (shop / ".ruff-args").read_text() == "check --output-format json --no-fix ."
    assert done["counts"] == {"error": 3, "warning": 1, "info": 0} and done["total"] == 4 and not done["capped"]
    # Errors first, then by file and line; each file as the Workbench names it, and where it really is.
    assert [(p["file"], p["line"], p["col"], p["severity"], p["code"], p["tool"]) for p in done["problems"]] == [
        ("src/a.ts", 1, 7, "error", "TS2322", "tsc"), ("src/a.ts", 2, 41, "error", "TS2339", "tsc"),
        ("src/b.py", 3, 5, "error", None, "ruff"), ("src/b.py", 1, 8, "warning", "F401", "ruff")]
    assert done["problems"][0]["path"] == str(shop / "src" / "a.ts")
    assert done["problems"][3]["endCol"] == 10 and done["missing"] == [listed.json()["missing"][0]]

    # Filtered and paged on the server.
    only = await client.get(f"/diagnostics/checks/{done['id']}", params={"severity": "warning"})
    assert [p["code"] for p in only.json()["problems"]] == ["F401"] and only.json()["matching"] == 1
    page = await client.get(f"/diagnostics/checks/{done['id']}", params={"tool": "tsc", "offset": 1, "limit": 5})
    assert [p["code"] for p in page.json()["problems"]] == ["TS2339"] and page.json()["matching"] == 2
    huge = await client.get(f"/diagnostics/checks/{done['id']}", params={"limit": 100000})
    assert huge.json()["limit"] == diagnostics.PAGE_MAX

    # The editor's underlines for one file.
    here = await client.get("/diagnostics/file", params={"path": str(shop / "src" / "b.py")})
    assert here.json()["checkId"] == done["id"] and [p["line"] for p in here.json()["problems"]] == [1, 3]
    fresh = await client.get("/diagnostics/file", params={"path": str(root / "shop" / "tsconfig.json")})
    assert fresh.json()["checkId"] == done["id"] and fresh.json()["problems"] == []

    recent = await client.get("/diagnostics/checks", params={"folder": str(shop)})
    assert [c["id"] for c in recent.json()] == [done["id"]] and recent.json()[0]["problems"] == []

    audit = (await catalogued.execute(
        select(m.AuditEntry).where(m.AuditEntry.action == "diagnostics.check"))).scalars().all()
    assert len(audit) == 1 and audit[0].target == str(shop) and audit[0].detail["check"] == done["id"]
    assert audit[0].detail["commands"][0].startswith("node_modules/.bin/tsc")


async def test_a_project_check_names_files_the_way_the_workbench_does(
        client: AsyncClient, root: Path, catalogued: AsyncSession):
    api = root / "api"
    (api / "pkg").mkdir(parents=True)
    (api / "tsconfig.json").write_text("{}")
    fake_tsc(api, "printf 'pkg/x.ts(3,1): error TS1005: expected\\n'\nexit 2\n")
    catalogued.add(m.ProjectSource(project_id="shop", label="api", kind="local", repo=str(api), status="active"))
    await catalogued.flush()

    first = await client.post("/diagnostics/checks", json={"projectId": "shop", "tools": ["tsc"]})
    assert first.status_code == 202, first.text
    done = await finished(client, first.json()["id"])
    assert done["target"]["name"] == "Shop" and done["target"]["prefix"] == ""
    assert [t["tool"] for t in done["tools"]] == ["tsc"]              # only what was asked for
    assert {p["file"] for p in done["problems"]} == {"src/a.ts"} and done["problems"][0]["source"] == "shop"

    second = await client.post("/diagnostics/checks", json={"projectId": "shop", "source": "api"})
    done = await finished(client, second.json()["id"])
    assert [(p["file"], p["path"], p["source"]) for p in done["problems"]] == [
        ("api/pkg/x.ts", str(api / "pkg" / "x.ts"), "api")]

    both = await client.get("/diagnostics/checks", params={"projectId": "shop"})
    assert {c["target"]["source"] for c in both.json()} == {"shop", "api"}
    assert (await client.post("/diagnostics/checks", json={"projectId": "shop", "source": "nope"})).status_code == 404
    assert (await client.post("/diagnostics/checks", json={"projectId": "ghost"})).status_code == 404


async def test_a_tool_that_fails_without_problems_times_out_or_is_cancelled_says_so(
        client: AsyncClient, root: Path, monkeypatch: pytest.MonkeyPatch):
    odd = root / "odd"
    (odd / "tsconfig.json").parent.mkdir()
    (odd / "tsconfig.json").write_text("{}")
    fake_tsc(odd, "echo 'error: something the reader does not know' >&2\nexit 1\n")
    done = await finished(client, (await client.post("/diagnostics/checks", json={"folder": str(odd)})).json()["id"])
    [t] = done["tools"]
    assert (t["status"], t["exit"], t["problems"]) == ("failed", 1, 0)
    assert "without naming a problem" in t["note"] and t["output"] == ["error: something the reader does not know"]
    assert done["counts"] == {"error": 0, "warning": 0, "info": 0}

    fake_tsc(odd, "echo ok\nexit 0\n")
    done = await finished(client, (await client.post("/diagnostics/checks", json={"folder": str(odd)})).json()["id"])
    assert done["tools"][0]["status"] == "passed" and done["tools"][0]["output"] == []

    fake_tsc(odd, "printf 'src/a.ts(1,1): error TS1: early\\n'\nexec sleep 30\n")
    monkeypatch.setattr(diagnostics, "TOOL_SECONDS", 0.6)
    done = await finished(client, (await client.post("/diagnostics/checks", json={"folder": str(odd)})).json()["id"])
    assert done["tools"][0]["status"] == "timeout" and done["total"] == 1       # what it printed first is kept

    monkeypatch.setattr(diagnostics, "TOOL_SECONDS", 60.0)
    started = await client.post("/diagnostics/checks", json={"folder": str(odd)})
    again = await client.post("/diagnostics/checks", json={"folder": str(odd)})
    assert again.status_code == 409 and "being checked already" in again.json()["detail"]
    await asyncio.sleep(0.3)
    stopped = await client.post(f"/diagnostics/checks/{started.json()['id']}/cancel")
    assert stopped.status_code == 200
    assert stopped.json()["status"] == "cancelled" and stopped.json()["tools"][0]["status"] == "cancelled"


async def test_checks_are_refused_outside_the_roots_without_checkers_and_without_the_permission(
        client: AsyncClient, api: Any, root: Path, monkeypatch: pytest.MonkeyPatch):
    outside = root.parent / "outside"
    assert (await client.post("/diagnostics/checks", json={"folder": str(outside)})).status_code == 403
    assert (await client.get("/diagnostics/file", params={"path": "/etc/hosts"})).status_code == 403
    assert (await client.post("/diagnostics/checks", json={})).status_code == 422
    both = await client.post("/diagnostics/checks", json={"folder": str(root), "projectId": "shop"})
    assert both.status_code == 422
    (root / "empty").mkdir()
    empty = await client.post("/diagnostics/checks", json={"folder": str(root / "empty")})
    assert empty.status_code == 409 and "declares none" in empty.json()["detail"]
    (root / "rs").mkdir()
    (root / "rs" / "Cargo.toml").write_text("[package]\n")
    lacking = await client.post("/diagnostics/checks", json={"folder": str(root / "rs")})
    assert lacking.status_code == 409 and "https://rustup.rs" in lacking.json()["detail"]
    assert (await client.get("/diagnostics/checks/chk-nothing")).status_code == 404

    # An Admin lacks machine:access; another Owner's check is as absent as one that never was.
    mine = await client.post("/diagnostics/checks", json={"folder": str(root / "shop")})
    await finished(client, mine.json()["id"])
    assert (await client.post("/admin/users", json=ADMIN)).status_code in (200, 201)
    async with _client(api) as other:
        assert (await other.post("/auth/login", json={"email": ADMIN["email"],
                                                      "password": ADMIN["password"]})).status_code == 200
        refused = await other.get(f"/diagnostics/checks/{mine.json()['id']}")
        assert refused.status_code == 403 and "machine:access" in refused.json()["detail"]
        assert (await other.post("/lsp/hover", json={"path": str(root / "shop" / "src" / "b.py")})).status_code == 403
    second = {**ADMIN, "email": "second-owner@example.com", "roles": ["owner"]}
    assert (await client.post("/admin/users", json=second)).status_code in (200, 201)
    async with _client(api) as other:
        await other.post("/auth/login", json={"email": second["email"], "password": second["password"]})
        assert (await other.get(f"/diagnostics/checks/{mine.json()['id']}")).status_code == 404
        assert (await other.get("/diagnostics/checks")).json() == []
        assert (await other.get("/diagnostics/file", params={"path": str(root / "shop" / "src" / "b.py")})
                ).json()["checkId"] is None

    off = real_settings().model_copy(update={"machine_roots": str(root), "machine_access": False})
    monkeypatch.setattr(machine, "settings", lambda: off)
    assert (await client.get("/diagnostics/checkers", params={"folder": str(root / "shop")})).status_code == 404
    assert (await client.get("/lsp/servers")).status_code == 404


# ── language servers ──────────────────────────────────────────────
@pytest.fixture
def fake_server(monkeypatch: pytest.MonkeyPatch) -> None:
    """Python is served by the fake server; every other language keeps its real (absent) candidates."""
    specs = [lsp.Spec(s.id, s.language, s.extensions, ((sys.executable, str(FAKE_LSP)),), s.markers, s.install)
             if s.id == "python" else s for s in lsp.SPECS]
    monkeypatch.setattr(lsp, "SPECS", specs)


@pytest.fixture
def pyproject(root: Path) -> Path:
    shop = root / "shop"
    (shop / "pkg").mkdir()
    (shop / "pkg" / "util.py").write_text("def helper(x):\n    return x * 2\n")
    (shop / "pkg" / "main.py").write_text(
        "from pkg.util import helper\n\n\nclass Cart:\n    def total(self):\n        return helper(elsewhere)\n\n\n"
        "def linked():\n    return Cart()\n")
    return shop


async def test_hover_definition_and_symbols_come_from_the_language_server(
        client: AsyncClient, api: Any, pyproject: Path, fake_server: None):
    main = pyproject / "pkg" / "main.py"
    status = await client.get("/lsp/status", params={"path": str(main)})
    assert status.status_code == 200, status.text
    assert status.json()["state"] == "available" and status.json()["root"] == str(pyproject)
    assert status.json()["language"] == "Python"
    assert (await client.get("/lsp/servers")).json() == []                 # asking about it started nothing

    # Hover on `helper` (line 6, col 16): the server holds the file as it is on disk (version 1)…
    got = await client.post("/lsp/hover", json={"path": str(main), "line": 6, "col": 17})
    assert got.status_code == 200, got.text
    assert got.json()["markdown"].startswith("**helper** · version 1 · root shop")
    # …and it answered its own configuration request, which the client answered with nothing to say.
    got = await client.post("/lsp/hover", json={"path": str(main), "line": 1, "col": 7})
    assert got.json()["markdown"].endswith("configured [null]")
    # The editor's unsaved text is what the server reads.
    edited = main.read_text().replace("helper(elsewhere)", "renamed(elsewhere)")
    got = await client.post("/lsp/hover", json={"path": str(main), "line": 6, "col": 17, "text": edited})
    assert got.json()["markdown"].startswith("**renamed** · version 2")
    nothing = await client.post("/lsp/hover", json={"path": str(main), "line": 2, "col": 1, "text": edited})
    assert nothing.json()["markdown"] is None

    status = await client.get("/lsp/status", params={"path": str(main)})
    assert status.json()["state"] == "ready" and status.json()["message"].endswith("ready")

    # Go to definition: in another file, inside the roots and openable; one outside is named, not opened.
    to = await client.post("/lsp/definition", json={"path": str(main), "line": 6, "col": 17})
    assert to.json()["locations"] == [{"path": str(pyproject / "pkg" / "util.py"), "openable": True,
                                       "line": 1, "col": 5, "endLine": 1, "endCol": 11}]
    away = await client.post("/lsp/definition", json={"path": str(main), "line": 6, "col": 24})
    assert away.json()["locations"][0]["openable"] is False and away.json()["locations"][0]["line"] == 10
    link = await client.post("/lsp/definition", json={"path": str(main), "line": 9, "col": 6})
    assert link.json()["locations"][0]["path"] == str(pyproject / "pkg" / "util.py")

    outline = await client.post("/lsp/symbols", json={"path": str(main)})
    assert [(s["name"], s["kind"], s["depth"], s["line"]) for s in outline.json()["symbols"]] == [
        ("Cart", "class", 0, 4), ("total", "method", 1, 5), ("linked", "function", 0, 9)]

    [running] = (await client.get("/lsp/servers")).json()
    assert running["state"] == "ready" and running["root"] == str(pyproject) and running["openFiles"] == 1
    assert (await client.delete(f"/lsp/servers/{running['id']}")).json() == {"ok": True}
    assert (await client.get("/lsp/servers")).json() == []
    assert (await client.delete(f"/lsp/servers/{running['id']}")).status_code == 404

    # Stopped servers start again on the next question; an idle one is stopped by the reaper's rule.
    assert (await client.post("/lsp/hover", json={"path": str(main), "line": 6, "col": 17})).status_code == 200
    assert await servers_of(api).stop_idle(older_than=0) == 1
    assert (await client.get("/lsp/servers")).json() == []


async def test_language_servers_that_are_missing_or_do_not_apply_say_so(
        client: AsyncClient, root: Path, pyproject: Path, fake_server: None, monkeypatch: pytest.MonkeyPatch):
    (root / "shop" / "main.go").write_text("package main\n")
    status = await client.get("/lsp/status", params={"path": str(root / "shop" / "main.go")})
    assert status.json()["state"] == "missing"
    assert status.json()["message"] == ("No language server for Go on this machine — install gopls "
                                        "(go install golang.org/x/tools/gopls@latest).")
    hover = await client.post("/lsp/hover", json={"path": str(root / "shop" / "main.go")})
    assert hover.status_code == 409 and "install gopls" in hover.json()["detail"]

    readme = root / "shop" / "README.md"
    readme.write_text("# Shop\n")
    assert (await client.get("/lsp/status", params={"path": str(readme)})).json()["state"] == "none"
    assert (await client.post("/lsp/hover", json={"path": str(readme)})).status_code == 422
    assert (await client.post("/lsp/hover", json={"path": str(root / "shop" / "gone.py")})).status_code == 404
    assert (await client.post("/lsp/hover", json={"path": "/etc/hosts"})).status_code == 403

    # A server that dies at start says why, in its own words.
    broken = tool(root / "broken-lsp", f"#!{sys.executable}\nimport sys\nsys.stderr.write('no licence found\\n')\n"
                                       "sys.exit(3)\n")
    specs = [lsp.Spec(s.id, s.language, s.extensions, ((str(broken),),), s.markers, s.install)
             if s.id == "python" else s for s in lsp.SPECS]
    monkeypatch.setattr(lsp, "SPECS", specs)
    failed = await client.post("/lsp/hover", json={"path": str(pyproject / "pkg" / "main.py")})
    assert failed.status_code == 502 and "broken-lsp did not start" in failed.json()["detail"]
    status = await client.get("/lsp/status", params={"path": str(pyproject / "pkg" / "main.py")})
    assert status.json()["state"] == "failed" and "no licence found" in status.json()["message"]
