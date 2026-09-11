"""The code index: every source file of an onboarded project, the symbols declared in it, and the
edges between files — imports, type uses, and reads, writes and calls into the database.

Nothing needs installing. Python is read by its own `ast`, so its symbols and imports are exact.
TypeScript/JavaScript, C#, Java, Go and T-SQL are read by patterns, and every index records which
parser read which language, so the screens can say how far to trust it. A syntax-tree parser can
replace one pattern set at a time without touching the schema.
"""
from __future__ import annotations

import ast
import bisect
import hashlib
import json
import os
import posixpath
import re
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import onboarding
from .db import Store, _fts_query, now_iso

PARSERS = {"Python": "python-ast", "TypeScript": "patterns", "JavaScript": "patterns", "C#": "patterns",
           "Java": "patterns", "Go": "patterns", "T-SQL": "patterns"}
MAX_PARSE = 1_000_000
DB_KINDS = ("table", "procedure", "view", "function", "trigger")
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|specs?)/|(^|/)test_[^/]+$|[._-](test|spec)s?\.\w+$|Tests?\.(cs|java|kt)$", re.I)
BRANCHES = re.compile(r"\b(?:if|elif|for|foreach|while|case|catch|except)\b|&&|\|\|")
NOT_METHODS = {"if", "for", "foreach", "while", "switch", "catch", "using", "lock", "return", "nameof", "typeof",
               "sizeof", "new", "base", "this", "throw", "await", "when", "fixed", "checked"}
TYPE_LINE = re.compile(r"\b(?:class|record|struct|interface|enum)\b")

TS_IMPORT = re.compile(
    r"""(?:^|[\s;])(?:import|export)\s+(?:type\s+)?(?:[\w*${}\s,]+?\s+from\s+)?['"]([^'"\n]+)['"]"""
    r"""|\bimport\(\s*['"]([^'"\n]+)['"]\s*\)|\brequire\(\s*['"]([^'"\n]+)['"]\s*\)""")
TS_DECL = re.compile(
    r"^(export[ \t]+)?(default[ \t]+)?(?:declare[ \t]+)?(?:abstract[ \t]+)?(?:async[ \t]+)?"
    r"(function\*?|class|interface|type|enum|const|let|var)[ \t]+([A-Za-z_$][\w$]*)([^\n]{0,120})", re.M)
TS_FN_VALUE = re.compile(
    r"[ \t]*(?::[^=\n]{0,80})?=[ \t]*(?:async[ \t]+)?(?:function\b|\([^)\n]{0,200}\)[ \t]*(?::[^=\n]{0,60})?=>|[\w$]+[ \t]*=>)")
CS_TYPE = re.compile(
    r"^[ \t]*(?:\[[^\]\n]*\][ \t]*)*((?:(?:public|internal|private|protected|static|sealed|abstract|partial|readonly|"
    r"unsafe|new|file)[ \t]+)*)(class|interface|record|struct|enum)[ \t]+([A-Za-z_]\w*)", re.M)
CS_METHOD = re.compile(
    r"[ \t]*((?:(?:public|internal|private|protected|static|virtual|override|abstract|async|sealed|extern|new|unsafe|"
    r"partial)[ \t]+)+)(?:[\w<>\[\],.?]+[ \t]+){0,4}?([A-Za-z_]\w*)[ \t]*(?:<[^>\n]{0,80}>)?[ \t]*\(")
JAVA_TYPE = re.compile(
    r"^[ \t]*(?:@\w+(?:\([^)\n]*\))?[ \t]*)*((?:(?:public|private|protected|static|final|abstract|sealed|strictfp)[ \t]+)*)"
    r"(class|interface|enum|record)[ \t]+([A-Za-z_]\w*)", re.M)
JAVA_METHOD = re.compile(
    r"[ \t]*((?:(?:public|private|protected|static|final|abstract|synchronized|native|default)[ \t]+)+)"
    r"(?:<[^>\n]{0,80}>[ \t]+)?(?:[\w<>\[\],.?]+[ \t]+){0,3}?([A-Za-z_]\w*)[ \t]*\(")
JAVA_IMPORT = re.compile(r"^[ \t]*import[ \t]+(?:static[ \t]+)?([\w.]+?)(?:\.\*)?[ \t]*;", re.M)
GO_FUNC = re.compile(r"^func[ \t]+(\([^)\n]*\)[ \t]*)?([A-Za-z_]\w*)", re.M)
GO_TYPE = re.compile(r"^type[ \t]+([A-Za-z_]\w*)[ \t]+(struct|interface)\b", re.M)
SQL_DECL = re.compile(
    r"\bcreate[ \t]+(?:or[ \t]+(?:alter|replace)[ \t]+)?(table|proc(?:edure)?|view|function|trigger)[ \t]+"
    r"(?:if[ \t]+not[ \t]+exists[ \t]+)?((?:\[?\w+\]?\.)?\[?\w+\]?)", re.I)
SQL_KIND = {"table": "table", "proc": "procedure", "procedure": "procedure", "view": "view", "function": "function",
            "trigger": "trigger"}
DB_REF = re.compile(
    r"\b(from|join|into|update|exec(?:ute)?|merge[ \t]+into|delete[ \t]+from)[ \t]+((?:\[?\w+\]?\.)?\[?[A-Za-z_]\w*\]?)", re.I)
WRITES = {"into", "update", "merge into", "delete from"}
CAPS = re.compile(r"\b[A-Z][A-Za-z0-9_]{2,}\b")
TS_EXTS = ("", ".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs", "/index.ts", "/index.tsx", "/index.js", "/index.jsx")


# ── parsing one file ─────────────────────────────────────────────
@dataclass
class Parsed:
    symbols: list[tuple[str, str, int, bool]] = field(default_factory=list)   # name, kind, line, exported
    imports: list[list[str]] = field(default_factory=list)                  # candidate specifiers, best first
    types: set[str] = field(default_factory=set)                              # capitalised names it uses (C#, Java)
    db: set[tuple[str, str]] = field(default_factory=set)                     # (object, reads | writes | calls)


class Lines:
    """Line numbers for character offsets, without rescanning the text for every match."""

    def __init__(self, text: str) -> None:
        self.breaks = [m.start() for m in re.finditer("\n", text)]

    def __call__(self, pos: int) -> int:
        return bisect.bisect_right(self.breaks, pos - 1) + 1


def bare(name: str) -> str:
    """[dbo].[TRANS_INVOICE] → TRANS_INVOICE."""
    return name.replace("[", "").replace("]", "").split(".")[-1]


def words(name: str) -> str:
    """InvoiceTaxService → Invoice Tax Service; calc_tax_line → calc tax line. What makes "tax" find it."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", name))
    return re.sub(r"[_.\-]+", " ", spaced)


def parse_python(text: str) -> Parsed:
    p = Parsed()
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return p
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            p.symbols.append((node.name, "class", node.lineno, not node.name.startswith("_")))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    p.symbols.append((f"{node.name}.{sub.name}", "method", sub.lineno, not sub.name.startswith("_")))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            p.symbols.append((node.name, "function", node.lineno, not node.name.startswith("_")))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            p.imports += [[a.name] for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            for a in node.names:
                sep = "" if not base or base.endswith(".") else "."
                p.imports.append([f"{base}{sep}{a.name}", base] if a.name != "*" else [base])
    return p


def parse_ts(text: str, jsx: bool) -> Parsed:
    p, line = Parsed(), Lines(text)
    for m in TS_IMPORT.finditer(text):
        spec = m.group(1) or m.group(2) or m.group(3)
        if spec:
            p.imports.append([spec])
    for m in TS_DECL.finditer(text):
        exported, word, name, rest = bool(m.group(1)), m.group(3), m.group(4), m.group(5)
        if word in ("const", "let", "var"):
            if TS_FN_VALUE.match(rest):
                kind = "function"
            elif exported:
                kind = "constant"
            else:
                continue
        else:
            kind = {"class": "class", "interface": "interface", "type": "type", "enum": "enum"}.get(word, "function")
        if jsx and kind in ("function", "constant") and name[:1].isupper():
            kind = "component"
        p.symbols.append((name, kind, line(m.start()), exported))
    return p


def _methods(text: str, pattern: re.Pattern[str], p: Parsed) -> None:
    # line by line, and only short lines with a parenthesis: bounded work however odd the file
    for n, raw in enumerate(text.split("\n"), 1):
        if "(" not in raw or len(raw) > 400 or TYPE_LINE.search(raw):
            continue
        m = pattern.match(raw)
        if m and m.group(2) not in NOT_METHODS:
            p.symbols.append((m.group(2), "method", n, "public" in m.group(1)))


def parse_cs(text: str) -> Parsed:
    p, line = Parsed(), Lines(text)
    for m in CS_TYPE.finditer(text):
        kind = {"interface": "interface", "enum": "enum"}.get(m.group(2), "class")
        p.symbols.append((m.group(3), kind, line(m.start()), "public" in m.group(1) or "internal" in m.group(1)))
    _methods(text, CS_METHOD, p)
    p.types = set(CAPS.findall(text))
    return p


def parse_java(text: str) -> Parsed:
    p, line = Parsed(), Lines(text)
    for m in JAVA_TYPE.finditer(text):
        kind = {"interface": "interface", "enum": "enum"}.get(m.group(2), "class")
        p.symbols.append((m.group(3), kind, line(m.start()), "public" in m.group(1)))
    _methods(text, JAVA_METHOD, p)
    p.types = set(CAPS.findall(text)) | {imp.rsplit(".", 1)[-1] for imp in JAVA_IMPORT.findall(text)}
    return p


def parse_go(text: str) -> Parsed:
    p, line = Parsed(), Lines(text)
    for m in GO_FUNC.finditer(text):
        name = m.group(2)
        p.symbols.append((name, "method" if m.group(1) else "function", line(m.start()), name[:1].isupper()))
    for m in GO_TYPE.finditer(text):
        name = m.group(1)
        p.symbols.append((name, "interface" if m.group(2) == "interface" else "class", line(m.start()), name[:1].isupper()))
    return p


def parse_sql(text: str) -> Parsed:
    p, line = Parsed(), Lines(text)
    for m in SQL_DECL.finditer(text):
        p.symbols.append((bare(m.group(2)), SQL_KIND[m.group(1).lower()], line(m.start()), True))
    return p


def db_refs(text: str) -> set[tuple[str, str]]:
    """Every name that follows FROM, JOIN, INTO, UPDATE or EXEC. Only names the schema declares become edges."""
    out: set[tuple[str, str]] = set()
    for m in DB_REF.finditer(text):
        keyword = " ".join(m.group(1).lower().split())
        kind = "calls" if keyword.startswith("exec") else "writes" if keyword in WRITES else "reads"
        out.add((bare(m.group(2)), kind))
    return out


def parse(lang: str, path: str, text: str) -> Parsed:
    if lang == "Python":
        p = parse_python(text)
    elif lang in ("TypeScript", "JavaScript"):
        p = parse_ts(text, jsx=path.endswith("x"))
    elif lang == "C#":
        p = parse_cs(text)
    elif lang == "Java":
        p = parse_java(text)
    elif lang == "Go":
        p = parse_go(text)
    else:
        p = parse_sql(text)
    p.db = db_refs(text)
    return p


# ── resolving imports into edges ─────────────────────────────────
def _python_modules(paths: set[str]) -> dict[str, list[str]]:
    """Every dotted name a Python file can be imported by: server/app/db.py → server.app.db, app.db, db."""
    out: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        if not path.endswith(".py"):
            continue
        parts = path[:-3].split("/")
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for i in range(len(parts)):
            out[".".join(parts[i:])].append(path)
    return out


def _shared(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a.split("/"), b.split("/")):
        if x != y:
            break
        n += 1
    return n


def _resolve_py(cands: list[str], path: str, modules: dict[str, list[str]], paths: set[str]) -> str | None:
    for spec in cands:
        if spec.startswith("."):
            level = len(spec) - len(spec.lstrip("."))
            base = posixpath.dirname(path)
            for _ in range(level - 1):
                base = posixpath.dirname(base)
            rest = spec[level:]
            target = posixpath.join(base, *rest.split(".")) if rest else base
            for candidate in (f"{target}.py", f"{target}/__init__.py"):
                if candidate.lstrip("/") in paths:
                    return candidate.lstrip("/")
        elif homes := modules.get(spec):
            return max(homes, key=lambda h: _shared(h, path))
    return None


def _resolve_ts(spec: str, path: str, paths: set[str]) -> tuple[str | None, bool]:
    """(the file a specifier names, whether it names a file of this repository at all)."""
    if spec.startswith("."):
        base = posixpath.normpath(posixpath.join(posixpath.dirname(path), spec))
    elif spec.startswith(("@/", "~/")):
        i = path.find("src/")
        root = path[: i + 4] if i >= 0 and (i == 0 or path[i - 1] == "/") else "src/"
        base = root + spec[2:]
    elif spec.startswith("/"):
        base = spec[1:]
    else:
        return None, False
    for ext in TS_EXTS:
        if base + ext in paths:
            return base + ext, True
    stem, ext = posixpath.splitext(base)
    if ext in (".js", ".jsx", ".mjs"):  # an ESM-style ".js" specifier that points at the TypeScript source
        for alt in (".ts", ".tsx"):
            if stem + alt in paths:
                return stem + alt, True
    return None, True


def _package(spec: str, lang: str) -> str:
    """The package an outside import comes from: react-dom/client → react-dom, os.path → os."""
    if lang == "Python":
        return spec.lstrip(".").split(".")[0] or spec
    parts = spec.split("/")
    return "/".join(parts[:2]) if spec.startswith("@") and len(parts) > 1 else parts[0]


def _edges(parsed: dict[str, Parsed], lang_of: dict[str, str], paths: set[str]) -> tuple[set[tuple[str, str | None, str, str]], int, int]:
    edges: set[tuple[str, str | None, str, str]] = set()
    resolved = unresolved = 0
    modules = _python_modules(paths)

    for path, p in parsed.items():
        lang = lang_of[path]
        for cands in p.imports:
            if lang == "Python":
                spec = cands[-1]
                target = _resolve_py(cands, path, modules, paths)
                internal = spec.startswith(".") or spec.split(".")[0] in modules
            elif lang in ("TypeScript", "JavaScript"):
                spec = cands[0]
                target, internal = _resolve_ts(spec, path, paths)
            else:
                continue
            if target:
                if target != path:
                    edges.add((path, target, spec, "imports"))
                    resolved += 1
            elif internal:
                unresolved += 1
            else:
                edges.add((path, None, _package(spec, lang), "imports"))

    # C# and Java: a file that names a type declared in another file depends on that file
    declared: dict[str, set[str]] = defaultdict(set)
    for path, p in parsed.items():
        if lang_of[path] in ("C#", "Java"):
            for name, kind, _, _ in p.symbols:
                if kind in ("class", "interface", "enum"):
                    declared[name].add(path)
    for path, p in parsed.items():
        if lang_of[path] not in ("C#", "Java"):
            continue
        own = {name for name, _, _, _ in p.symbols}
        for t in p.types - own:
            homes = declared.get(t)
            if homes and len(homes) <= 3:  # a name declared everywhere tells us nothing
                for home in homes:
                    if home != path:
                        edges.add((path, home, t, "uses"))
                        resolved += 1

    # the database: names the schema declares, found after FROM, JOIN, INTO, UPDATE or EXEC anywhere
    homes: dict[str, str] = {}
    names: dict[str, str] = {}
    for path, p in parsed.items():
        if lang_of[path] == "T-SQL":
            for name, kind, _, _ in p.symbols:
                if kind in DB_KINDS:
                    homes.setdefault(name.lower(), path)
                    names.setdefault(name.lower(), name)
    for path, p in parsed.items():
        for obj, kind in p.db:
            home = homes.get(obj.lower())
            if home and home != path:
                edges.add((path, home, names[obj.lower()], kind))
    return edges, resolved, unresolved


def _history(root: Path) -> dict[str, tuple[int, str]]:
    """Commits per file over the last 90 days, and each file's last commit, when root is in a git repository."""
    try:
        out = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.quotePath=false", "-C", str(root), "log",
             "--since=90.days", "--relative", "--no-renames", "--name-only", "--format=%x00%cI"],
            capture_output=True, text=True, timeout=30, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    except (OSError, subprocess.SubprocessError):
        return {}
    if out.returncode != 0:
        return {}
    seen: dict[str, tuple[int, str]] = {}
    for block in out.stdout.split("\x00")[1:]:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        for name in lines[1:]:
            count, last = seen.get(name, (0, ""))
            seen[name] = (count + 1, max(last, lines[0]))
    return seen


# ── building and saving an index ─────────────────────────────────
@dataclass
class Index:
    files: list[dict[str, Any]]
    symbols: list[tuple[str, str, str, int, bool]]      # path, name, kind, line, exported
    edges: list[tuple[str, str | None, str, str]]       # from path, to path (None: outside the repo), target, kind
    parsed: int
    resolved: int
    unresolved: int
    db_objects: int
    db_referenced: int
    parsers: dict[str, str]
    ms: int = 0

    def coverage(self) -> dict[str, int]:
        code = sum(1 for f in self.files if f["lang"] in PARSERS)
        links = self.resolved + self.unresolved
        return {
            "Syntax & symbols": round(100 * self.parsed / code) if code else 0,
            "Dependency graph": round(100 * self.resolved / links) if links else (100 if self.parsed else 0),
            "Database links": round(100 * self.db_referenced / self.db_objects) if self.db_objects else 0,
        }

    def stats(self) -> dict[str, Any]:
        return {"files": len(self.files), "symbols": len(self.symbols), "edges": len(self.edges),
                "unresolved": self.unresolved, "ms": self.ms, "at": now_iso()}

    def describe(self) -> str:
        unresolved = f" · {self.unresolved} unresolved imports" if self.unresolved else ""
        return (f"{len(self.files):,} files · {len(self.symbols):,} symbols · {len(self.edges):,} dependencies"
                f"{unresolved} · {self.ms / 1000:.1f}s")


def build(root: Path, excluded: list[str]) -> Index:
    """Read every source file under root. Blocking and CPU-bound: run it on a worker thread."""
    t0 = time.monotonic()
    files: list[dict[str, Any]] = []
    parsed: dict[str, Parsed] = {}
    lang_of: dict[str, str] = {}
    for rel, lang, data in onboarding.walk(root, excluded):
        rec = {"path": rel, "lang": lang, "module": onboarding.module_of(rel), "bytes": len(data),
               "lines": data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0),
               "sha1": hashlib.sha1(data).hexdigest(), "complexity": 0, "churn": 0, "changed_at": None}
        files.append(rec)
        lang_of[rel] = lang
        if lang in PARSERS and len(data) <= MAX_PARSE:
            text = data.decode("utf-8", errors="replace")
            rec["complexity"] = len(BRANCHES.findall(text))
            parsed[rel] = parse(lang, rel, text)
    history = _history(root)
    for rec in files:
        if rec["path"] in history:
            rec["churn"], rec["changed_at"] = history[rec["path"]]
    edges, resolved, unresolved = _edges(parsed, lang_of, set(lang_of))
    symbols = [(path, name, kind, line, exported) for path, p in parsed.items() for name, kind, line, exported in p.symbols]
    declared = {name.lower() for path, p in parsed.items() if lang_of[path] == "T-SQL"
                for name, kind, _, _ in p.symbols if kind in DB_KINDS}
    referenced = {target.lower() for _, _, target, kind in edges if kind in ("reads", "writes", "calls")}
    return Index(files, symbols, sorted(edges, key=lambda e: (e[0], e[1] or "", e[2], e[3])), len(parsed), resolved,
                 unresolved, len(declared), len(declared & referenced),
                 {lang: PARSERS[lang] for lang in sorted({lang_of[p] for p in parsed})},
                 round((time.monotonic() - t0) * 1000))


def save(store: Store, pid: str, root: str, idx: Index) -> None:
    """Replace the project's index in one transaction: readers see the old one or the new one, never half."""
    with store.tx() as c:
        c.execute("DELETE FROM code_files WHERE project_id = ?", (pid,))  # symbols and edges go with their files
        c.execute("DELETE FROM code_fts WHERE project_id = ?", (pid,))
        ids: dict[str, int] = {}
        for f in idx.files:
            cur = c.execute(
                "INSERT INTO code_files(project_id, path, lang, module, lines, bytes, sha1, complexity, churn, changed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pid, f["path"], f["lang"], f["module"], f["lines"], f["bytes"], f["sha1"], f["complexity"], f["churn"],
                 f["changed_at"]))
            ids[f["path"]] = cur.lastrowid
        c.executemany("INSERT INTO code_symbols(project_id, file_id, name, kind, line, exported) VALUES (?, ?, ?, ?, ?, ?)",
                      [(pid, ids[path], name, kind, line, int(exported)) for path, name, kind, line, exported in idx.symbols])
        c.executemany("INSERT INTO code_edges(project_id, from_file, to_file, target, kind) VALUES (?, ?, ?, ?, ?)",
                      [(pid, ids[a], ids[b] if b else None, target, kind) for a, b, target, kind in idx.edges])
        c.executemany(
            "INSERT INTO code_fts(name, words, path, kind, project_id, file_id, line) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(posixpath.basename(f["path"]), words(posixpath.splitext(posixpath.basename(f["path"]))[0]), f["path"], "file",
              pid, ids[f["path"]], 0) for f in idx.files]
            + [(name, words(name), path, kind, pid, ids[path], line) for path, name, kind, line, _ in idx.symbols])
        c.execute(
            "INSERT OR REPLACE INTO code_index_runs(project_id, root, finished_at, ms, files, symbols, edges, unresolved, parsers) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pid, root, now_iso(), idx.ms, len(idx.files), len(idx.symbols), len(idx.edges), idx.unresolved,
             json.dumps(idx.parsers)))


def project_fields(idx: Index, doc: dict[str, Any], *, found: dict[str, Any] | None = None,
                   steps: int | None = None) -> dict[str, Any]:
    """What an index changes on the project record."""
    cov = idx.coverage()
    fields: dict[str, Any] = {"codeIndex": idx.stats()}
    if found is not None and steps:
        fields.update(onboarding.measured(found, steps, cov, idx.stats()))
    else:
        fields["coverage"] = [{**item, "pct": cov.get(item["label"], item["pct"])} for item in doc.get("coverage", [])]
    return fields


# ── reading an index ─────────────────────────────────────────────
def risk_of(dependents: int, complexity: int = 0, writes: bool = False) -> str:
    if dependents >= 60 or (writes and dependents >= 20):
        return "CRITICAL"
    if dependents >= 15 or writes or complexity >= 80:
        return "HIGH"
    if dependents >= 4 or complexity >= 30:
        return "MEDIUM"
    return "LOW"


def _chunks(ids: list[int], size: int = 500):
    for i in range(0, len(ids), size):
        yield ids[i:i + size]


def _fan_in(store: Store, ids: list[int]) -> dict[int, int]:
    out: dict[int, int] = {}
    for part in _chunks(ids):
        marks = ",".join("?" * len(part))
        for r in store.rows(f"SELECT to_file, COUNT(DISTINCT from_file) FROM code_edges WHERE to_file IN ({marks}) "
                            "AND from_file != to_file GROUP BY to_file", tuple(part)):
            out[r[0]] = r[1]
    return out


def _module_edges(store: Store, pid: str) -> list[tuple[str, str, int]]:
    return [(r[0], r[1], r[2]) for r in store.rows(
        "SELECT fa.module, fb.module, COUNT(*) FROM code_edges e JOIN code_files fa ON fa.id = e.from_file "
        "JOIN code_files fb ON fb.id = e.to_file WHERE e.project_id = ? AND fa.module != fb.module "
        "GROUP BY fa.module, fb.module", (pid,))]


def _db_objects(store: Store, pid: str) -> list[dict[str, Any]]:
    use: dict[str, dict[str, set[int]]] = {}
    for target, kind, src in store.rows("SELECT target, kind, from_file FROM code_edges WHERE project_id = ? "
                                        "AND kind IN ('reads', 'writes', 'calls')", (pid,)):
        use.setdefault(target.lower(), {"reads": set(), "writes": set(), "calls": set()})[kind].add(src)
    out, seen = [], set()
    for name, kind, path in store.rows(
            "SELECT s.name, s.kind, f.path FROM code_symbols s JOIN code_files f ON f.id = s.file_id WHERE s.project_id = ? "
            "AND f.lang = 'T-SQL' AND s.kind IN ('table', 'procedure', 'view', 'function', 'trigger') ORDER BY s.name", (pid,)):
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        u = use.get(name.lower(), {"reads": set(), "writes": set(), "calls": set()})
        out.append({"name": name, "kind": kind, "path": path, "readers": len(u["reads"]), "writers": len(u["writes"]),
                    "callers": len(u["calls"])})
    return sorted(out, key=lambda o: (-(o["readers"] + o["writers"] + o["callers"]), o["name"]))


def summary(store: Store, pid: str) -> dict[str, Any] | None:
    run = store.row("SELECT * FROM code_index_runs WHERE project_id = ?", (pid,))
    if run is None:
        return None
    languages = [{"name": r[0], "files": r[1], "lines": r[2] or 0} for r in store.rows(
        "SELECT lang, COUNT(*), SUM(lines) FROM code_files WHERE project_id = ? GROUP BY lang ORDER BY SUM(lines) DESC", (pid,))]
    mods = {r[0]: {"name": r[0], "files": r[1], "lines": r[2] or 0, "complexity": r[3] or 0, "symbols": 0, "fanIn": 0, "fanOut": 0}
            for r in store.rows("SELECT module, COUNT(*), SUM(lines), SUM(complexity) FROM code_files WHERE project_id = ? "
                                "GROUP BY module", (pid,))}
    for module, n in store.rows("SELECT f.module, COUNT(*) FROM code_symbols s JOIN code_files f ON f.id = s.file_id "
                                "WHERE s.project_id = ? GROUP BY f.module", (pid,)):
        mods[module]["symbols"] = n
    for a, b, _ in _module_edges(store, pid):
        mods[a]["fanOut"] += 1
        mods[b]["fanIn"] += 1
    hot = store.rows(
        "SELECT f.path, f.lines, f.complexity, f.churn, COUNT(DISTINCT e.from_file) AS n FROM code_edges e "
        "JOIN code_files f ON f.id = e.to_file WHERE e.project_id = ? AND e.from_file != e.to_file "
        "GROUP BY e.to_file ORDER BY n DESC, f.complexity DESC LIMIT 10", (pid,))
    objects = _db_objects(store, pid)
    return {
        "indexed": True,
        "run": {"files": run["files"], "symbols": run["symbols"], "edges": run["edges"], "unresolved": run["unresolved"],
                "ms": run["ms"], "finishedAt": run["finished_at"], "parsers": json.loads(run["parsers"])},
        "languages": languages,
        "modules": sorted(mods.values(), key=lambda m: -m["lines"]),
        "hotspots": [{"path": r[0], "lines": r[1], "complexity": r[2], "churn": r[3], "fanIn": r[4], "risk": risk_of(r[4], r[2])}
                     for r in hot],
        "database": {"objects": len(objects), "top": objects[:12]},
    }


def children(store: Store, pid: str, directory: str = "") -> dict[str, Any]:
    """One level of the file tree: its folders, with counts, and its files."""
    prefix = f"{directory.strip('/')}/" if directory.strip("/") else ""
    rows = store.rows("SELECT id, path, lang, lines, complexity FROM code_files WHERE project_id = ? AND path > ? AND path < ?",
                      (pid, prefix, prefix + "\U0010ffff"))
    dirs: dict[str, dict[str, Any]] = {}
    files: list[dict[str, Any]] = []
    for r in rows:
        rest = r["path"][len(prefix):]
        if "/" in rest:
            name = rest.split("/", 1)[0]
            d = dirs.setdefault(name, {"name": name, "path": prefix + name, "files": 0, "lines": 0})
            d["files"] += 1
            d["lines"] += r["lines"]
        else:
            files.append({"id": r["id"], "name": rest, "path": r["path"], "lang": r["lang"], "lines": r["lines"],
                          "complexity": r["complexity"], "fanIn": 0})
    fan = _fan_in(store, [f["id"] for f in files])
    for f in files:
        f["fanIn"] = fan.get(f["id"], 0)
    return {"dir": prefix.rstrip("/"), "dirs": sorted(dirs.values(), key=lambda d: d["name"].lower()),
            "files": sorted(files, key=lambda f: f["name"].lower())}


def search(store: Store, pid: str, q: str, limit: int = 40) -> list[dict[str, Any]]:
    match = _fts_query(q)
    if not match:
        return []
    rows = store.rows("SELECT name, path, kind, line FROM code_fts WHERE code_fts MATCH ? AND project_id = ? "
                      "ORDER BY rank LIMIT ?", (match, pid, limit))
    return [{"name": r[0], "path": r[1], "kind": r[2], "line": r[3]} for r in rows]


def impact(store: Store, pid: str, *, path: str | None = None, module: str | None = None,
           obj: str | None = None) -> dict[str, Any] | None:
    """What moves if this changes: everything that depends on it, directly or through others, found by
    walking the dependency edges backwards in the database (a recursive query, eight steps deep)."""
    if path:
        seeds = [r[0] for r in store.rows("SELECT id FROM code_files WHERE project_id = ? AND path = ?", (pid, path))]
        label, shift = path, 0
    elif module:
        seeds = [r[0] for r in store.rows("SELECT id FROM code_files WHERE project_id = ? AND module = ?", (pid, module))]
        label, shift = module, 0
    elif obj:
        seeds = [r[0] for r in store.rows("SELECT DISTINCT from_file FROM code_edges WHERE project_id = ? AND target = ? "
                                          "COLLATE NOCASE AND kind IN ('reads', 'writes', 'calls')", (pid, obj))]
        label, shift = obj, 1  # the files that use the object are its direct dependents
        if not seeds and not store.row("SELECT 1 FROM code_symbols WHERE project_id = ? AND name = ? COLLATE NOCASE", (pid, obj)):
            return None
    else:
        return None
    if not seeds and not obj:
        return None
    rows = store.rows(
        "WITH RECURSIVE dep(id, depth) AS (SELECT value, 0 FROM json_each(?) UNION "
        "SELECT e.from_file, dep.depth + 1 FROM code_edges e JOIN dep ON e.to_file = dep.id "
        "WHERE dep.depth < 8 AND e.from_file != e.to_file) "
        "SELECT f.id, f.path, f.module, f.lang, f.complexity, f.churn, MIN(dep.depth) FROM dep "
        "JOIN code_files f ON f.id = dep.id GROUP BY f.id", (json.dumps(seeds),))
    seed_set = set(seeds)
    reached = [{"id": r[0], "path": r[1], "module": r[2], "lang": r[3], "complexity": r[4], "churn": r[5],
                "depth": r[6] + shift} for r in rows]
    seed_rows = [x for x in reached if x["id"] in seed_set]
    if module:
        dependents = [x for x in reached if x["module"] != module]
    elif obj:
        dependents = reached
    else:
        dependents = [x for x in reached if x["id"] not in seed_set]
    direct = sorted(x["path"] for x in dependents if x["depth"] == 1)
    further = sorted(x["path"] for x in dependents if x["depth"] > 1)
    tests = sorted({x["path"] for x in dependents + seed_rows if TEST_FILE.search(x["path"])})
    mods = Counter(x["module"] for x in dependents)

    if obj:
        data = [(label, k) for (k,) in store.rows("SELECT DISTINCT kind FROM code_edges WHERE project_id = ? AND target = ? "
                                                  "COLLATE NOCASE", (pid, obj))]
    else:
        data = []
        for part in _chunks(seeds):
            marks = ",".join("?" * len(part))
            data += [(r[0], r[1]) for r in store.rows(
                f"SELECT DISTINCT target, kind FROM code_edges WHERE from_file IN ({marks}) "
                "AND kind IN ('reads', 'writes', 'calls') ORDER BY target", tuple(part))]
    writes = sorted({t for t, k in data if k == "writes"})
    complexity = max((x["complexity"] for x in seed_rows), default=0)
    churn = max((x["churn"] for x in seed_rows), default=0)
    risk = risk_of(len(dependents), complexity, bool(writes))

    run = store.row("SELECT edges, unresolved FROM code_index_runs WHERE project_id = ?", (pid,))
    langs = {x["lang"] for x in seed_rows + dependents}
    base = 90 if langs and langs <= {"Python", "T-SQL"} else 80 if "Python" in langs else 72
    penalty = min(20, round(100 * run["unresolved"] / max(1, run["edges"] + run["unresolved"]))) if run else 0

    warnings = []
    if len(direct) >= 10:
        warnings.append(f"{len(direct)} files use it directly. A change to its signature reaches every one of them.")
    if (dependents or seed_rows) and not tests:
        warnings.append("No test file reaches it. A change here is unguarded until one does.")
    if writes:
        warnings.append(f"It writes {', '.join(writes[:3])}{'…' if len(writes) > 3 else ''}. Check migrations, "
                        "constraints and anything that audits that data.")
    if len(mods) >= 3:
        warnings.append(f"The change crosses {len(mods)} modules.")
    if churn >= 10:
        warnings.append(f"It changed {churn} times in 90 days: a hotspot, where regressions cluster.")
    if run and run["unresolved"]:
        warnings.append(f"{run['unresolved']} imports in this project could not be resolved, so the true radius may be larger.")
    recommendation = (
        "Change it behind a stable interface, land the tests first, and let the plan stop at your approval."
        if risk in ("HIGH", "CRITICAL") else
        "Change it together with its direct users in one plan, and run their tests." if risk == "MEDIUM" else
        "Safe to change in one step. Run the tests that already reach it.")
    groups = [
        {"label": "Uses it directly", "items": direct[:40]},
        {"label": "Reached through others", "items": further[:40]},
        {"label": "Tests that reach it", "items": tests[:40]},
        {"label": "Data it touches", "items": [f"{k} {t}" for t, k in data][:40]},
    ]
    return {
        "target": label, "kind": "file" if path else "module" if module else "object",
        "risk": risk, "confidence": max(40, base - penalty),
        "counts": {"direct": len(direct), "dependents": len(dependents), "modules": len(mods), "tests": len(tests),
                   "data": len(data)},
        "blastRadius": [g for g in groups if g["items"]],
        "modules": [{"name": m, "files": n} for m, n in mods.most_common(12)],
        "warnings": warnings,
        "recommendation": recommendation,
    }


def file_detail(store: Store, pid: str, path: str) -> dict[str, Any] | None:
    f = store.row("SELECT * FROM code_files WHERE project_id = ? AND path = ?", (pid, path))
    if f is None:
        return None
    symbols = [{"name": r[0], "kind": r[1], "line": r[2], "exported": bool(r[3])} for r in store.rows(
        "SELECT name, kind, line, exported FROM code_symbols WHERE file_id = ? ORDER BY line", (f["id"],))]
    out = store.rows("SELECT e.target, e.kind, t.path FROM code_edges e LEFT JOIN code_files t ON t.id = e.to_file "
                     "WHERE e.from_file = ? ORDER BY t.path IS NULL, t.path, e.target", (f["id"],))
    used: dict[str, dict[str, Any]] = {}
    for src, kind, target in store.rows(
            "SELECT s.path, e.kind, e.target FROM code_edges e JOIN code_files s ON s.id = e.from_file "
            "WHERE e.to_file = ? AND e.from_file != e.to_file ORDER BY s.path", (f["id"],)):
        u = used.setdefault(src, {"path": src, "kinds": [], "targets": []})
        if kind not in u["kinds"]:
            u["kinds"].append(kind)
        if target not in u["targets"]:
            u["targets"].append(target)
    depends = [{"path": r[2], "target": r[0], "kind": r[1]} for r in out if r[1] in ("imports", "uses")]
    return {
        "file": {"path": f["path"], "lang": f["lang"], "module": f["module"], "lines": f["lines"], "bytes": f["bytes"],
                 "complexity": f["complexity"], "churn": f["churn"], "changedAt": f["changed_at"], "fanIn": len(used),
                 "fanOut": len({d["path"] for d in depends if d["path"]}), "test": bool(TEST_FILE.search(f["path"]))},
        "symbols": symbols,
        "dependsOn": depends,
        "database": [{"object": r[0], "kind": r[1], "path": r[2]} for r in out if r[1] in ("reads", "writes", "calls")],
        "usedBy": list(used.values()),
        "impact": impact(store, pid, path=path),
    }


def graph(store: Store, pid: str, limit: int = 36) -> dict[str, Any]:
    """Modules and the database objects they touch, sized for one screen: the largest `limit` modules."""
    mods = store.rows("SELECT module, COUNT(*), SUM(lines) FROM code_files WHERE project_id = ? GROUP BY module "
                      "ORDER BY SUM(lines) DESC", (pid,))
    keep = {r[0] for r in mods[:limit]}
    medges = [(a, b, n) for a, b, n in _module_edges(store, pid) if a in keep and b in keep]
    fan = Counter(b for _, b, _ in medges)
    nodes = [{"id": f"m:{r[0]}", "label": r[0], "kind": "module", "files": r[1], "lines": r[2] or 0,
              "risk": risk_of(fan[r[0]] * 4)} for r in mods[:limit]]
    edges = [{"from": f"m:{a}", "to": f"m:{b}", "kind": "depends", "weight": n} for a, b, n in medges]

    declared = {r[0].lower(): (r[0], r[1]) for r in store.rows(
        "SELECT s.name, s.kind FROM code_symbols s JOIN code_files f ON f.id = s.file_id WHERE s.project_id = ? "
        "AND f.lang = 'T-SQL' AND s.kind IN ('table', 'procedure', 'view', 'function', 'trigger')", (pid,))}
    uses = [(r[0], r[1], r[2], r[3]) for r in store.rows(
        "SELECT e.target, e.kind, f.module, COUNT(*) FROM code_edges e JOIN code_files f ON f.id = e.from_file "
        "WHERE e.project_id = ? AND e.kind IN ('reads', 'writes', 'calls') GROUP BY e.target, e.kind, f.module", (pid,))]
    reach = Counter(t.lower() for t, _, m, _ in uses if m in keep)
    top = {o for o, _ in reach.most_common(16)}
    written = {t.lower() for t, k, m, _ in uses if k == "writes" and m in keep}
    for o in sorted(top):
        name, kind = declared.get(o, (o, "table"))
        nodes.append({"id": f"d:{name}", "label": name, "kind": kind, "files": 0, "lines": 0,
                      "risk": "HIGH" if o in written else "MEDIUM"})
    edges += [{"from": f"m:{m}", "to": f"d:{declared.get(t.lower(), (t,))[0]}", "kind": k, "weight": n}
              for t, k, m, n in uses if m in keep and t.lower() in top]
    return {"nodes": nodes, "edges": edges, "modules": len(mods), "truncated": len(mods) > limit}
