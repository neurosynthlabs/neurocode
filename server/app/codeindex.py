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
from datetime import UTC, datetime
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import onboarding

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
                "unresolved": self.unresolved, "ms": self.ms, "at": datetime.now(UTC).isoformat(timespec="seconds")}

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


# ── reading an index ─────────────────────────────────────────────
def risk_of(dependents: int, complexity: int = 0, writes: bool = False) -> str:
    if dependents >= 60 or (writes and dependents >= 20):
        return "CRITICAL"
    if dependents >= 15 or writes or complexity >= 80:
        return "HIGH"
    if dependents >= 4 or complexity >= 30:
        return "MEDIUM"
    return "LOW"
