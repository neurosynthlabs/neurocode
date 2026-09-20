"""The code index against files that are not well-behaved, and against a project whose sources meet.

The polyglot tests next door prove the index reads real code correctly. These prove it survives the
rest of a repository: a file that is one long error, a name wider than the column that holds it, a
symlink, a declaration whose end nobody knows, and two sources that arrive at the same path. Each of
these cost a whole index once — twenty minutes of a pinned worker, a rolled-back write, a file read
from outside the checkout, an invented end line, a database error in the feed.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import codeindex, treesitter
from app.models import CodeFile, CodeSymbol, Project
from app.services.indexing import build_index, merge

#: A file at the parse cap whose brackets never close: a fuzzing corpus, a template saved as .js, a
#: binary named .ts. Asking the tree about it used to take time in the square of its length.
UNCLOSED = b"const x = " + b"(" * 200_000 + b"1;"


# ── a file that came back wrecked ───────────────────────────────

def test_a_file_of_unclosed_brackets_is_left_unread_instead_of_holding_the_index():
    """One such file used to be about twenty minutes of one CPU — 3 s at 50 000 brackets, 50 s at
    200 000, and the cap allows a million — with the project stuck on "being read" the whole time."""
    started = time.monotonic()
    assert treesitter.outline("typescript", UNCLOSED) is None
    assert time.monotonic() - started < 5, "reading it must be refused, not survived"


def test_a_file_with_an_ordinary_syntax_error_is_still_read():
    """The guard is about how wide one error is, not whether there is one: a file the grammar trips
    over in one place — three in a hundred real .ts files do — must still give up its symbols."""
    source = b"export function ok() { return 1 }\nclass ? {}\nexport function alsoOk() { return 2 }\n"
    found = treesitter.outline("typescript", source)
    assert found is not None and [s[0] for s in found.symbols] == ["ok", "alsoOk"]


def test_the_rest_of_a_project_is_indexed_around_a_wrecked_file(tmp_path: Path):
    """The index goes on without it, and says so honestly: the file is there, with no symbols, and
    TypeScript is not claimed to have been read."""
    (tmp_path / "broken.ts").write_bytes(UNCLOSED)
    (tmp_path / "good.py").write_text("def ok():\n    return 1\n")
    started = time.monotonic()
    idx = codeindex.build(tmp_path, [])
    assert time.monotonic() - started < 5
    assert sorted(f["path"] for f in idx.files) == ["broken.ts", "good.py"]
    assert [(path, name) for path, name, *_ in idx.symbols] == [("good.py", "ok")]
    assert idx.parsers == {"Python": "python-ast"}


# ── what a body declares is not the file's ──────────────────────

def test_a_declaration_inside_a_function_body_is_not_a_symbol_of_the_file():
    """A closure, a nested helper or a class declared inside a function belongs to that function, not
    to the file. All five of these used to be published as the file's own, un-namespaced, and a
    private helper's name then drew "uses" edges from every other file that happened to mention it."""
    source = (b"function outer() {\n"
              b"  function helperInsideOuter() { return 1; }\n"
              b"  const LocalThing = () => 2;\n"
              b"  class LocalClass { method() { return 3; } }\n"
              b"}\n")
    found = treesitter.outline("typescript", source)
    assert found is not None and [s[0] for s in found.symbols] == ["outer"]


def test_a_method_body_hides_its_declarations_too_without_hiding_the_method():
    source = b"export class Cart { total() { function round(n) { return n; } return round(1); } }\n"
    found = treesitter.outline("typescript", source)
    assert found is not None and [s[0] for s in found.symbols] == ["Cart", "Cart.total"]
    go = treesitter.outline("go", b"package a\n\nfunc Outer() {\n\ttype LocalT struct{ A int }\n\t_ = LocalT{}\n}\n")
    assert go is not None and [s[0] for s in go.symbols] == ["Outer"]


# ── the checkout is the boundary ────────────────────────────────

def test_the_walk_does_not_follow_a_link_out_of_the_checkout_or_read_one_file_twice(tmp_path: Path):
    """`services.code.resolve_in` refuses a link that leaves the checkout for every read by path; the
    index has to keep the same boundary, or it lists names the Workbench then refuses to open. A link
    inside the tree is not followed either: it would be the same file under two paths, with its
    symbols and its fan-in counted twice."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("SECRET_TOKEN_NAME = 'x'\n")
    project = tmp_path / "project"
    project.mkdir()
    (project / "real.py").write_text("def a():\n    return 1\n")
    os.symlink(outside / "secret.py", project / "vendored.py")
    os.symlink(project / "real.py", project / "alias.py")

    idx = codeindex.build(project, [])
    assert [f["path"] for f in idx.files] == ["real.py"]
    assert [(path, name) for path, name, *_ in idx.symbols] == [("real.py", "a")]


# ── figures nobody measured ─────────────────────────────────────

def test_a_t_sql_declaration_has_no_end_line_because_the_patterns_cannot_know_it():
    """T-SQL is the one language read by patterns. A regex over CREATE PROCEDURE cannot see the END,
    so the end line is unknown — and unknown is null, not the line it started on."""
    parsed = codeindex.parse_sql("CREATE PROCEDURE sp_Long AS\nBEGIN\n  SELECT 1\nEND\n")
    assert parsed.symbols == [("sp_Long", "procedure", 1, True, 0)]


async def test_a_t_sql_procedure_is_published_with_no_end_line(session: AsyncSession, tmp_path: Path):
    (tmp_path / "schema.sql").write_text("CREATE PROCEDURE sp_Long @id INT AS\nBEGIN\n  SELECT 1\nEND\n")
    session.add(Project(id="sqlproj", name="SQL", source_kind="local", source_repo=str(tmp_path)))
    await session.flush()
    await build_index(session, "sqlproj", tmp_path, [])
    rows = (await session.execute(select(CodeSymbol.name, CodeSymbol.line, CodeSymbol.end_line)
                                  .where(CodeSymbol.project_id == "sqlproj"))).all()
    assert rows == [("sp_Long", 1, None)]


# ── a name wider than its column ────────────────────────────────

async def test_one_over_long_name_is_cut_to_fit_instead_of_losing_the_whole_index(
        session: AsyncSession, tmp_path: Path):
    """`code_symbols.name` holds 200 characters. Only tree-sitter used to cut its names to fit, so a
    Python class and method whose names together run past 200 made Postgres refuse the statement,
    rolled the whole write back, and left the project on its old index with a database error in the
    feed. Every reader ends at the same write, so that is where the column's width is honoured."""
    long = "A" * 120
    (tmp_path / "wide.py").write_text(f"class {long}:\n    def {long}(self):\n        pass\n")
    (tmp_path / "wide.sql").write_text(f"CREATE PROCEDURE {'B' * 250} AS BEGIN\nSELECT 1\nEND\n")
    session.add(Project(id="wideproj", name="Wide", source_kind="local", source_repo=str(tmp_path)))
    await session.flush()

    await build_index(session, "wideproj", tmp_path, [])
    names = sorted((await session.execute(select(CodeSymbol.name)
                                          .where(CodeSymbol.project_id == "wideproj"))).scalars())
    assert names == [long, f"{long}.{long}"[:200], "B" * 200]
    assert (await session.execute(select(CodeFile.path).where(CodeFile.project_id == "wideproj")
                                  .order_by(CodeFile.path))).scalars().all() == ["wide.py", "wide.sql"]


# ── two sources at one path ─────────────────────────────────────

def test_two_sources_at_one_path_are_refused_in_words(tmp_path: Path):
    """A label may not name a top-level entry of the first checkout, but that is checked only when
    the label is chosen: the checkout can grow an `api/` folder afterwards. The write would then fail
    on the unique constraint over (project, path) and post a database error to the feed, on every
    re-index, until someone guessed why."""
    first = tmp_path / "web"
    (first / "api").mkdir(parents=True)
    (first / "api" / "main.py").write_text("def handler():\n    return 1\n")
    second = tmp_path / "api"
    second.mkdir()
    (second / "main.py").write_text("def handler():\n    return 1\n")

    parts = [("", codeindex.build(first, [])), ("api/", codeindex.build(second, []))]
    try:
        merge(parts)
    except RuntimeError as refused:
        assert "api/main.py" in str(refused) and "api" in str(refused)
        assert "Rename" in str(refused)
    else:
        raise AssertionError("two sources holding api/main.py must be refused, not written")


def test_sources_that_do_not_meet_are_merged_as_before(tmp_path: Path):
    first = tmp_path / "web"
    first.mkdir()
    (first / "app.py").write_text("def handler():\n    return 1\n")
    second = tmp_path / "api"
    second.mkdir()
    (second / "app.py").write_text("def handler():\n    return 2\n")

    merged = merge([("", codeindex.build(first, [])), ("api/", codeindex.build(second, []))])
    assert sorted(f["path"] for f in merged.files) == ["api/app.py", "app.py"]
