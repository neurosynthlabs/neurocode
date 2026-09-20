"""Every language in the code index, read by its syntax tree.

`tests/fixtures/polyglot` is a small repository with a file or two per language, each written so the
right answer is known in advance: what it declares, where each declaration ends, whether it is part of
what the file offers, and which other file of the repository it imports. The index is built from it
for real — the bundled grammars, the resolvers, the uses pass — and the answers are compared exactly.

The last tests go through HTTP: the fixture is indexed as a project, and the summary, the file view
and search are read back the way the Code Intelligence screen reads them.
"""
from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app import codeindex, treesitter
from app.api import deps
from app.api.app import create_api
from app.models import Project
from app.services.indexing import build_index
from tests.fixtures.workspace import load_workspace

FIXTURE = Path(__file__).parent / "fixtures" / "polyglot"
OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}
PID = "polyglot"

#: Every language the fixture holds, and the parser that must have read it.
READ_BY = {
    "Python": "python-ast", "Jupyter Notebook": "python-ast", "T-SQL": "patterns",
    **{lang: "tree-sitter" for lang in (
        "TypeScript", "JavaScript", "Go", "Rust", "Java", "Kotlin", "C#", "C", "C++", "Ruby", "PHP", "Swift",
        "Scala", "Dart", "Lua", "R", "Julia", "Elixir", "Haskell", "OCaml", "Zig", "Shell", "Vue", "Svelte")},
}

#: path → the symbols it declares: (name, kind, line, exported, end line).
SYMBOLS = {
    "py/app/tax.py": [("RATE", "constant", 1, True, 1), ("rate", "function", 4, True, 7)],
    "py/app/billing.py": [("Invoice", "class", 5, True, 7), ("Invoice.total", "method", 6, True, 7),
                          ("export", "function", 10, True, 11)],
    "ts/src/util.ts": [("formatMoney", "function", 1, True, 3), ("Money", "interface", 5, True, 7),
                       ("Currency", "type", 9, True, 9), ("Status", "enum", 11, True, 14),
                       ("TAX_RATE", "constant", 16, True, 16), ("DOUBLE_RATE", "constant", 19, True, 19)],
    "ts/src/api.ts": [("ApiClient", "class", 4, True, 11), ("ApiClient.get", "method", 5, True, 10),
                      ("load", "function", 13, True, 15), ("reactVersion", "constant", 17, True, 17)],
    "ts/src/View.tsx": [("CartView", "component", 3, True, 5), ("Badge", "component", 7, True, 7)],
    "js/lib/helper.js": [("double", "function", 1, False, 3)],
    "go/cart/cart.go": [("Cart", "struct", 4, True, 6), ("Priced", "interface", 8, True, 10),
                        ("MaxItems", "constant", 12, True, 12), ("Add", "function", 14, True, 18),
                        ("Cart.Count", "method", 20, True, 22), ("helper", "function", 24, False, 24)],
    "rust/src/billing.rs": [("Invoice", "struct", 1, True, 3), ("Priced", "trait", 5, True, 7),
                            ("Priced.price", "method", 6, True, 6), ("Status", "enum", 9, True, 12),
                            ("Invoice.total", "method", 15, True, 17), ("private_helper", "function", 20, False, 20),
                            ("LIMIT", "constant", 22, True, 22)],
    "java/src/main/java/com/acme/billing/Invoice.java": [
        ("Invoice", "class", 3, True, 16), ("Invoice.total", "method", 6, True, 11),
        ("Invoice.reset", "method", 13, False, 15), ("Priced", "interface", 18, False, 20),
        ("Priced.price", "method", 19, True, 19), ("Status", "enum", 22, False, 22)],
    "kotlin/src/main/kotlin/com/acme/Cart.kt": [
        ("Cart", "class", 3, True, 5), ("Cart.count", "method", 4, True, 4), ("Priced", "interface", 7, True, 9),
        ("Priced.price", "method", 8, True, 8), ("Registry", "object", 11, True, 13),
        ("Registry.register", "method", 12, True, 12), ("total", "function", 15, True, 17),
        ("hidden", "function", 19, False, 19)],
    "csharp/Billing/Invoice.cs": [
        ("Invoice", "class", 3, True, 17), ("Invoice.Total", "method", 7, True, 14),
        ("Invoice.Reset", "method", 16, False, 16), ("IPriced", "interface", 19, True, 22),
        ("IPriced.Price", "method", 21, True, 21), ("Status", "enum", 24, True, 24), ("Line", "struct", 26, True, 26),
        ("Receipt", "class", 28, True, 28)],
    "c/src/util.c": [("add", "function", 3, True, 8), ("hidden", "function", 10, False, 10)],
    "c/src/util.h": [("point", "struct", 4, True, 7)],
    "cpp/src/shape.hpp": [("Shape", "class", 5, True, 8), ("Point", "struct", 10, True, 10), ("Kind", "enum", 12, True, 12)],
    "cpp/src/shape.cpp": [("Shape.area", "method", 6, True, 8), ("scale", "function", 10, True, 12)],
    "ruby/lib/cart.rb": [("Shop", "module", 1, True, 11), ("Shop.Cart", "class", 2, True, 10),
                         ("Cart.add", "method", 3, True, 5), ("Cart.build", "method", 7, True, 9)],
    "php/src/Models/User.php": [("User", "class", 5, True, 11), ("User.name", "method", 7, True, 10),
                                ("Named", "interface", 13, True, 13), ("HasName", "trait", 15, True, 15),
                                ("helper", "function", 17, True, 17)],
    "swift/Sources/App/Cart.swift": [
        ("Cart", "struct", 3, True, 9), ("Cart.count", "method", 6, True, 8), ("Store", "class", 11, True, 13),
        ("Store.open", "method", 12, True, 12), ("Priced", "interface", 15, True, 17),
        ("Priced.price", "method", 16, True, 16), ("Status", "enum", 19, True, 21), ("total", "function", 23, True, 26),
        ("hidden", "function", 28, False, 28)],
    "scala/src/main/scala/com/acme/Cart.scala": [
        ("Cart", "class", 3, True, 5), ("Cart.count", "method", 4, True, 4), ("Priced", "trait", 7, True, 9),
        ("Priced.price", "method", 8, True, 8), ("Cart", "object", 11, True, 13), ("Cart.empty", "method", 12, True, 12)],
    "dart/lib/cart.dart": [("Cart", "class", 1, True, 7), ("Cart.count", "method", 4, True, 6),
                           ("Status", "enum", 9, True, 9), ("total", "function", 11, True, 11),
                           ("_hidden", "function", 13, False, 13)],
    "lua/shop/cart.lua": [("M.add", "function", 3, True, 7), ("hidden", "function", 9, False, 10),
                          ("count", "function", 12, True, 14)],
    "r/helpers.R": [("clean_names", "function", 1, True, 4)],
    "julia/src/cart.jl": [("Cart", "struct", 1, True, 3), ("add!", "function", 5, True, 9)],
    "julia/src/Shop.jl": [("Shop", "module", 1, True, 8), ("Shop.total", "function", 6, True, 6)],
    "elixir/lib/shop/cart.ex": [("Shop.Cart", "module", 1, True, 7), ("Shop.Cart.add", "function", 2, True, 4),
                                ("Shop.Cart.hidden", "function", 6, False, 6)],
    "haskell/src/Shop/Cart.hs": [("Cart", "type", 3, True, 3), ("Priced", "interface", 5, True, 6),
                                 ("add", "function", 9, True, 9)],
    "ocaml/lib/cart.ml": [("t", "type", 1, True, 1), ("add", "function", 3, True, 3), ("Store", "module", 5, True, 7),
                          ("Store.open_", "function", 6, True, 6)],
    "zig/src/cart.zig": [("Cart", "struct", 3, True, 9), ("Cart.add", "method", 6, True, 8),
                         ("hidden", "function", 11, False, 11)],
    "bash/scripts/lib.sh": [("log", "function", 1, True, 3)],
    # T-SQL is read by patterns, which see the CREATE and not the END: sp_AddOrder really runs to
    # line 6, and no reader here knows that, so the end line is 0 — NULL once written, not a guess.
    "sql/schema.sql": [("Orders", "table", 1, True, 0), ("sp_AddOrder", "procedure", 3, True, 0)],
    # The file is the component; its <script> is read with its lines counted from the file's top.
    "vue/src/Cart.vue": [("Cart", "component", 1, True, 12), ("checkout", "function", 8, False, 10)],
    "svelte/src/Cart.svelte": [("Cart", "component", 1, True, 12), ("items", "constant", 4, True, 4),
                               ("add", "function", 6, False, 8)],
    # The code cells, joined in order; the markdown cell's "def not_code()" is not code.
    "notebooks/analysis.ipynb": [("load", "function", 5, True, 7), ("Report", "class", 9, True, 11),
                                 ("Report.render", "method", 10, True, 11)],
}

#: (from, to, what the import or use named, kind) — every link inside the repository.
LINKS = {
    ("py/app/billing.py", "py/app/tax.py", ".tax", "imports"),
    ("notebooks/analysis.ipynb", "py/app/tax.py", "py.app.tax", "imports"),
    ("ts/src/api.ts", "ts/src/util.ts", "./util", "imports"),
    ("ts/src/View.tsx", "ts/src/api.ts", "./api", "imports"),
    ("js/lib/index.js", "js/lib/helper.js", "./helper", "imports"),
    ("vue/src/Cart.vue", "vue/src/cart.ts", "./cart", "imports"),
    ("go/main.go", "go/cart/cart.go", "example.com/shop/cart", "imports"),        # through go.mod's module path
    ("rust/src/main.rs", "rust/src/billing.rs", "billing", "imports"),             # mod billing;
    ("rust/src/main.rs", "rust/src/billing.rs", "crate::billing::Invoice", "imports"),
    ("rust/src/main.rs", "rust/src/billing.rs", "Invoice", "uses"),
    ("java/src/main/java/com/acme/App.java", "java/src/main/java/com/acme/billing/Invoice.java",
     "com.acme.billing.Invoice", "imports"),
    ("java/src/main/java/com/acme/App.java", "java/src/main/java/com/acme/billing/Invoice.java", "Invoice", "uses"),
    ("kotlin/src/main/kotlin/com/acme/Main.kt", "kotlin/src/main/kotlin/com/acme/Cart.kt", "com.acme.Cart", "imports"),
    ("kotlin/src/main/kotlin/com/acme/Main.kt", "kotlin/src/main/kotlin/com/acme/Cart.kt", "Cart", "uses"),
    ("scala/src/main/scala/com/acme/Main.scala", "scala/src/main/scala/com/acme/Cart.scala", "com.acme.Cart", "imports"),
    ("scala/src/main/scala/com/acme/Main.scala", "scala/src/main/scala/com/acme/Cart.scala", "Cart", "uses"),
    ("csharp/Program.cs", "csharp/Billing/Invoice.cs", "Invoice", "uses"),        # `using Acme.Billing` names a namespace
    ("c/src/main.c", "c/src/util.h", "util.h", "imports"),
    ("c/src/util.c", "c/src/util.h", "util.h", "imports"),
    ("c/src/main.c", "c/src/util.c", "add", "uses"),
    ("cpp/src/shape.cpp", "cpp/src/shape.hpp", "shape.hpp", "imports"),
    ("ruby/app.rb", "ruby/lib/cart.rb", "lib/cart", "imports"),
    ("ruby/app.rb", "ruby/lib/cart.rb", "Cart", "uses"),
    ("ruby/app.rb", "ruby/lib/cart.rb", "Shop", "uses"),
    ("php/src/Http/Controller.php", "php/src/Models/User.php", "App\\Models\\User", "imports"),
    ("php/src/Http/Controller.php", "php/src/Models/User.php", "User", "uses"),
    ("dart/lib/main.dart", "dart/lib/cart.dart", "cart.dart", "imports"),
    ("dart/lib/main.dart", "dart/lib/cart.dart", "Cart", "uses"),
    ("dart/lib/main.dart", "dart/lib/cart.dart", "total", "uses"),
    ("lua/main.lua", "lua/shop/cart.lua", "shop.cart", "imports"),
    ("lua/main.lua", "lua/shop/cart.lua", "add", "uses"),
    ("r/analysis.R", "r/helpers.R", "helpers.R", "imports"),
    ("r/analysis.R", "r/helpers.R", "clean_names", "uses"),
    ("julia/src/Shop.jl", "julia/src/cart.jl", "cart.jl", "imports"),
    ("elixir/lib/shop.ex", "elixir/lib/shop/cart.ex", "Shop.Cart", "imports"),
    ("elixir/lib/shop.ex", "elixir/lib/shop/cart.ex", "Cart", "uses"),
    ("elixir/lib/shop.ex", "elixir/lib/shop/cart.ex", "add", "uses"),
    ("haskell/app/Main.hs", "haskell/src/Shop/Cart.hs", "Shop.Cart", "imports"),
    ("haskell/app/Main.hs", "haskell/src/Shop/Cart.hs", "add", "uses"),
    ("ocaml/app/main.ml", "ocaml/lib/cart.ml", "Cart", "imports"),
    ("ocaml/app/main.ml", "ocaml/lib/cart.ml", "add", "uses"),
    ("zig/src/main.zig", "zig/src/cart.zig", "cart.zig", "imports"),
    ("bash/scripts/deploy.sh", "bash/scripts/lib.sh", "./lib.sh", "imports"),
    ("bash/scripts/deploy.sh", "bash/scripts/lib.sh", "log", "uses"),
}

#: (from, the package it named) — imports that leave the repository, named the way the ecosystem does.
PACKAGES = {
    ("py/app/billing.py", "json"), ("notebooks/analysis.ipynb", "pandas"), ("ts/src/api.ts", "react"),
    ("js/lib/index.js", "express"), ("svelte/src/Cart.svelte", "svelte"), ("go/main.go", "fmt"),
    ("rust/src/main.rs", "std"), ("java/src/main/java/com/acme/App.java", "java.util"),
    ("kotlin/src/main/kotlin/com/acme/Main.kt", "kotlinx.coroutines"),
    ("scala/src/main/scala/com/acme/Main.scala", "scala.util"), ("csharp/Program.cs", "System"),
    ("c/src/main.c", "stdio.h"), ("cpp/src/shape.cpp", "vector"), ("ruby/app.rb", "json"),
    ("php/src/Http/Controller.php", "Illuminate"), ("swift/Sources/App/Cart.swift", "Foundation"),
    ("dart/lib/main.dart", "flutter"), ("lua/main.lua", "dkjson"), ("r/analysis.R", "dplyr"),
    ("julia/src/Shop.jl", "LinearAlgebra"), ("elixir/lib/shop.ex", "Logger"), ("haskell/app/Main.hs", "Data"),
    ("zig/src/cart.zig", "std"), ("zig/src/main.zig", "std"),
}


@pytest.fixture(scope="module")
def index() -> codeindex.Index:
    return codeindex.build(FIXTURE, [])


def test_every_language_is_read_and_says_by_what(index: codeindex.Index):
    assert index.parsers == dict(sorted(READ_BY.items()))
    assert index.parsed == len(index.files) == sum(1 for p in FIXTURE.rglob("*") if p.is_file()
                                                   and p.suffix.lower() in codeindex.LANGUAGES)
    assert index.coverage()["Syntax & symbols"] == 100


def test_each_file_declares_exactly_what_it_should_with_where_it_ends(index: codeindex.Index):
    found: dict[str, list[tuple[str, str, int, bool, int]]] = {}
    for path, name, kind, line, exported, end in index.symbols:
        found.setdefault(path, []).append((name, kind, line, exported, end))
    for path, expected in SYMBOLS.items():
        assert found.get(path) == expected, path


def test_imports_resolve_to_the_files_they_name_in_every_language(index: codeindex.Index):
    inside = {e for e in index.edges if e[1] is not None}
    assert inside == LINKS
    outside = {(a, target) for a, b, target, kind in index.edges if b is None and kind == "imports"}
    assert outside == PACKAGES
    assert index.unresolved == 0


def test_a_java_method_call_is_not_mistaken_for_a_kotlin_function(index: codeindex.Index):
    """App.java calls `.total()` on an Invoice; Cart.kt declares a top-level `total`. Types cross between
    the JVM languages, functions do not."""
    assert not any(a.endswith("App.java") and b and b.endswith(".kt") for a, b, _t, _k in index.edges)


def test_complexity_is_the_branches_the_tree_holds(index: codeindex.Index):
    complexity = {f["path"]: f["complexity"] for f in index.files}
    assert complexity["csharp/Billing/Invoice.cs"] == 2        # if, &&
    assert complexity["ts/src/api.ts"] == 2                    # if, &&
    assert complexity["c/src/util.c"] == 2
    assert complexity["go/cart/cart.go"] == 1
    assert complexity["notebooks/analysis.ipynb"] == 1
    assert complexity["py/app/billing.py"] == 0
    # A comment that says "if" is not a branch: the old text count said 1 here.
    assert codeindex.parse("Python", "x.py", b"# if this, for that\nx = 1\n").complexity == 0


def test_a_notebook_is_read_as_the_language_its_kernel_speaks():
    julia = (b'{"nbformat": 4, "metadata": {"kernelspec": {"language": "julia"}}, "cells": ['
             b'{"cell_type": "code", "source": ["function area(r)\\n", "  r * r\\n", "end\\n"]}]}')
    parsed = codeindex.parse("Jupyter Notebook", "a.ipynb", julia)
    assert parsed is not None and parsed.reads_as == "Julia"
    assert parsed.symbols == [("area", "function", 1, True, 3)]
    assert codeindex.parse("Jupyter Notebook", "b.ipynb", b"not json") is None


def test_generated_constant_tables_are_capped_per_file():
    table = "package gen\n\nconst (\n" + "".join(f"\tE{i} = {i}\n" for i in range(treesitter.MAX_CONSTANTS + 50)) + ")\n"
    found = treesitter.outline("go", table.encode())
    assert found is not None and sum(1 for s in found.symbols if s[1] == "constant") == treesitter.MAX_CONSTANTS


def test_parsing_in_worker_processes_gives_the_same_index(monkeypatch: pytest.MonkeyPatch, index: codeindex.Index):
    """Past POOL_FROM files the parsing moves into worker processes; the index must not notice."""
    monkeypatch.setattr(codeindex, "POOL_FROM", 1)
    monkeypatch.setattr(codeindex, "POOL_CHUNK", 7)
    pooled = codeindex.build(FIXTURE, [])
    assert pooled.symbols == index.symbols and pooled.edges == index.edges and pooled.parsers == index.parsers
    assert [f["complexity"] for f in pooled.files] == [f["complexity"] for f in index.files]


def test_a_grammar_that_is_missing_leaves_its_language_seen_but_not_read(monkeypatch: pytest.MonkeyPatch,
                                                                        tmp_path: Path):
    (tmp_path / "a.go").write_text("package a\n\nfunc A() {}\n")
    (tmp_path / "b.py").write_text("def b():\n    pass\n")
    monkeypatch.setattr(treesitter, "outline", lambda *args, **kwargs: None)
    idx = codeindex.build(tmp_path, [])
    assert idx.parsers == {"Python": "python-ast"}
    assert {f["lang"] for f in idx.files} == {"Go", "Python"} and idx.parsed == 1


# ── over HTTP, the way the screen reads it ──────────────────────

@pytest_asyncio.fixture
async def client(session: AsyncSession) -> AsyncIterator[AsyncClient]:
    await load_workspace(session)
    app: FastAPI = create_api(db=None)

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[deps.session] = use_the_test_session
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://api", headers=HEADERS) as c:
        await c.post("/auth/setup", json=OWNER)
        yield c


@pytest_asyncio.fixture
async def indexed(client: AsyncClient, session: AsyncSession, tmp_path: Path) -> Path:
    """The fixture, as a project on this machine, indexed for real — plus two files nothing reads."""
    repo = tmp_path / "polyglot"
    shutil.copytree(FIXTURE, repo)
    (repo / "deploy.yml").write_text("name: deploy\n")
    (repo / "theme.css").write_text("body { color: red; }\n")
    session.add(Project(id=PID, name="Polyglot", source_kind="local", source_repo=str(repo)))
    await session.flush()
    await build_index(session, PID, repo, [])
    return repo


async def test_the_summary_reports_every_language_with_its_parser_and_what_was_not_read(
        client: AsyncClient, indexed: Path):
    body = (await client.get(f"/projects/{PID}/code")).json()
    assert body["run"]["parsers"] == dict(sorted(READ_BY.items()))
    parsing = {p["language"]: p for p in body["parsing"]}
    assert set(parsing) == set(READ_BY) | {"YAML", "CSS"}
    assert parsing["Go"] == {"language": "Go", "files": 2, "symbols": 7, "parser": "tree-sitter"}
    assert parsing["Rust"] == {"language": "Rust", "files": 2, "symbols": 8, "parser": "tree-sitter"}
    assert parsing["Jupyter Notebook"] == {"language": "Jupyter Notebook", "files": 1, "symbols": 3,
                                           "parser": "python-ast"}
    assert parsing["YAML"] == {"language": "YAML", "files": 1, "symbols": 0, "parser": None}
    assert body["unparsed"] == ["CSS", "YAML"]
    # most files first, then by name
    assert [p["files"] for p in body["parsing"]] == sorted((p["files"] for p in body["parsing"]), reverse=True)


async def test_a_go_file_opens_with_its_symbols_and_the_file_it_imports(client: AsyncClient, indexed: Path):
    body = (await client.get(f"/projects/{PID}/code/file", params={"path": "go/main.go"})).json()
    assert body["file"]["lang"] == "Go"
    assert [s["name"] for s in body["symbols"]] == ["main"]
    assert {"path": "go/cart/cart.go", "target": "example.com/shop/cart", "kind": "imports"} in body["dependsOn"]
    cart = (await client.get(f"/projects/{PID}/code/file", params={"path": "go/cart/cart.go"})).json()
    assert [u["path"] for u in cart["usedBy"]] == ["go/main.go"]
    assert [(s["name"], s["kind"]) for s in cart["symbols"]][:2] == [("Cart", "struct"), ("Priced", "interface")]
    # Where each declaration ends is kept too: Cart spans lines 4-6, Add (with its if) lines 14-18.
    spans = {s["name"]: (s["line"], s["endLine"]) for s in cart["symbols"]}
    assert spans["Cart"] == (4, 6) and spans["Add"] == (14, 18)


async def test_search_finds_a_rust_trait_and_an_elixir_function_by_their_words(client: AsyncClient,
                                                                              indexed: Path):
    trait = (await client.get(f"/projects/{PID}/code/search", params={"q": "priced"})).json()
    assert {"name": "Priced", "path": "rust/src/billing.rs", "kind": "trait", "line": 5} in trait
    add = (await client.get(f"/projects/{PID}/code/search", params={"q": "shop cart add"})).json()
    assert {"name": "Shop.Cart.add", "path": "elixir/lib/shop/cart.ex", "kind": "function", "line": 2} in add


async def test_impact_reaches_across_a_language_s_own_imports(client: AsyncClient, indexed: Path):
    body = (await client.get(f"/projects/{PID}/code/impact", params={"path": "rust/src/billing.rs"})).json()
    assert body["counts"]["direct"] == 1
    assert body["blastRadius"][0] == {"label": "Uses it directly", "items": ["rust/src/main.rs"]}
    assert body["confidence"] == 80                        # read by a syntax tree, every import placed
