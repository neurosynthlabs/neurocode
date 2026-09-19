"""The code index: every source file of an onboarded project, the symbols declared in it, and the
edges between files — imports, type and function uses, and reads, writes and calls into the database.

Nothing needs installing. Python is read by its own `ast`, so its symbols and imports are exact. Every
other language a bundled tree-sitter grammar covers — TypeScript and JavaScript, Go, Rust, Java,
Kotlin, C#, C and C++, Ruby, PHP, Swift, Scala, Dart, Lua, R, Julia, Elixir, Haskell, OCaml, Zig,
shell, the script blocks of Vue and Svelte files — is read by its syntax tree (`treesitter.py`).
T-SQL keeps its patterns: the generic SQL grammar does not know T-SQL's procedures, and the patterns
do. Notebooks are read cell by cell, as the language their kernel speaks.

Every index records which parser really read which language, so the screens can say how far to
trust it — and a language that was seen but that nothing could read is listed as exactly that.
"""
from __future__ import annotations

import ast
import bisect
import fnmatch
import hashlib
import json
import os
import posixpath
import re
import subprocess
import time
from collections import defaultdict
import multiprocessing
from collections.abc import Iterator
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import onboarding, treesitter

#: The file extensions the index reads: everything the onboarding scan counts, and the languages it
#: does not count yet but a grammar can read.
LANGUAGES = {
    **onboarding.LANGS,
    ".cjs": "JavaScript", ".mts": "TypeScript", ".cts": "TypeScript", ".kts": "Kotlin", ".scala": "Scala",
    ".sc": "Scala", ".hpp": "C++", ".hh": "C++", ".hxx": "C++", ".cxx": "C++", ".lua": "Lua", ".r": "R",
    ".jl": "Julia", ".ex": "Elixir", ".exs": "Elixir", ".hs": "Haskell", ".ml": "OCaml", ".mli": "OCaml",
    ".zig": "Zig", ".bash": "Shell", ".vue": "Vue", ".svelte": "Svelte", ".ipynb": "Jupyter Notebook",
}
#: The grammar each language is read with. A C header is read as C++: the C++ grammar reads C headers
#: too, and a header in a C++ project is C++ far more often than the extension admits.
GRAMMARS = {
    "TypeScript": "typescript", "JavaScript": "javascript", "Go": "go", "Rust": "rust", "Java": "java",
    "Kotlin": "kotlin", "C#": "csharp", "C": "c", "C++": "cpp", "Ruby": "ruby", "PHP": "php", "Swift": "swift",
    "Scala": "scala", "Dart": "dart", "Lua": "lua", "R": "r", "Julia": "julia", "Elixir": "elixir",
    "Haskell": "haskell", "OCaml": "ocaml", "Zig": "zig", "Shell": "bash", "PowerShell": "powershell",
}
#: The parser that reads each language when everything it needs is on the machine. What really read
#: a language in a given index is `Index.parsers`: a grammar that failed to load leaves it out.
PARSERS = {"Python": "python-ast", "Jupyter Notebook": "python-ast", "T-SQL": "patterns",
           "Vue": "tree-sitter", "Svelte": "tree-sitter", **{lang: "tree-sitter" for lang in GRAMMARS}}
#: A notebook kernel's language, as its metadata spells it, to the language the index reads it as.
KERNELS = {"python": "Python", **{lang.lower(): lang for lang in GRAMMARS}, "c++": "C++", "c#": "C#",
           "csharp": "C#", "bash": "Shell", "sh": "Shell"}
#: Languages whose imports name files the way TypeScript does.
SCRIPT_LANGS = ("TypeScript", "JavaScript", "Vue", "Svelte")
#: Languages that are one family when a name declared in one is used in another.
FAMILY = {"C": "c", "C++": "c", "Java": "jvm", "Kotlin": "jvm", "Scala": "jvm"}
#: Kinds that name a type, which one language of a family can use from another.
TYPE_KINDS = {"class", "struct", "interface", "trait", "enum", "object", "type"}
#: Kinds a name can be used by from another file.
USABLE = {"class", "struct", "interface", "trait", "enum", "object", "type", "module", "function", "constant"}
MAX_PARSE = 1_000_000
#: Past this many files to read, parsing is spread over worker processes. A parser holds Python's
#: lock while it works, so threads would take turns; processes really run side by side. Below it,
#: starting the workers would cost more than it saves.
POOL_FROM = 400
POOL_WORKERS = 4
POOL_CHUNK = 48
DB_KINDS = ("table", "procedure", "view", "function", "trigger")
TEST_FILE = re.compile(r"(^|/)(tests?|__tests__|specs?)/|(^|/)test_[^/]+$|[._-](test|spec)s?\.\w+$|Tests?\.(cs|java|kt)$", re.I)
BRANCHES = re.compile(r"\b(?:if|elif|for|foreach|while|case|catch|except)\b|&&|\|\|")
SQL_DECL = re.compile(
    r"\bcreate[ \t]+(?:or[ \t]+(?:alter|replace)[ \t]+)?(table|proc(?:edure)?|view|function|trigger)[ \t]+"
    r"(?:if[ \t]+not[ \t]+exists[ \t]+)?((?:\[?\w+\]?\.)?\[?\w+\]?)", re.I)
SQL_KIND = {"table": "table", "proc": "procedure", "procedure": "procedure", "view": "view", "function": "function",
            "trigger": "trigger"}
DB_REF = re.compile(
    r"\b(from|join|into|update|exec(?:ute)?|merge[ \t]+into|delete[ \t]+from)[ \t]+((?:\[?\w+\]?\.)?\[?[A-Za-z_]\w*\]?)", re.I)
WRITES = {"into", "update", "merge into", "delete from"}
TS_EXTS = ("", ".ts", ".tsx", ".d.ts", ".js", ".jsx", ".mjs", ".cjs", "/index.ts", "/index.tsx", "/index.js", "/index.jsx")
SCRIPT_BLOCK = re.compile(r"<script\b([^>]*)>(.*?)</script>", re.S | re.I)
MAGIC = re.compile(r"^\s*[%!]")
PY_UPPER = re.compile(r"^[A-Z][A-Z0-9_]*$")
#: The Python nodes the index looks at: imports, and the decision points (if, loops, except, match
#: cases, conditional expressions, and/or chains, comprehension clauses).
PY_COUNTED = {ast.Import, ast.ImportFrom, ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.IfExp,
              ast.match_case, ast.BoolOp, ast.comprehension}
#: The first segment of a dotted package that names an organisation, so the second one is kept too:
#: org.springframework, com.google — never just `org`.
DOMAINS = {"com", "org", "net", "io", "dev", "edu", "gov", "co", "me", "java", "javax", "jakarta", "kotlin",
           "kotlinx", "scala", "android", "androidx"}


# ── parsing one file ─────────────────────────────────────────────
@dataclass
class Parsed:
    symbols: list[tuple[str, str, int, bool, int]] = field(default_factory=list)  # name, kind, line, exported, end
    imports: list[list[str]] = field(default_factory=list)              # Python/TS: candidate specifiers, best first
    links: list[tuple[str, str]] = field(default_factory=list)          # every other language: (how, specifier)
    refs: set[str] = field(default_factory=set)                         # names it calls or uses
    package: str = ""                                                   # the package or namespace it declares
    db: set[tuple[str, str]] = field(default_factory=set)               # (object, reads | writes | calls)
    complexity: int = 0
    #: the language whose import rules apply: a notebook reads as its kernel's, a Vue file as TypeScript
    reads_as: str = ""


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
    p = Parsed(reads_as="Python")
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return p
    for node in tree.body:
        end = getattr(node, "end_lineno", None) or node.lineno
        if isinstance(node, ast.ClassDef):
            p.symbols.append((node.name, "class", node.lineno, not node.name.startswith("_"), end))
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    p.symbols.append((f"{node.name}.{sub.name}", "method", sub.lineno, not sub.name.startswith("_"),
                                      sub.end_lineno or sub.lineno))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            p.symbols.append((node.name, "function", node.lineno, not node.name.startswith("_"), end))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            # A module-level name in capitals is a constant by Python's own convention (PEP 8).
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and PY_UPPER.match(target.id):
                    p.symbols.append((target.id, "constant", node.lineno, True, end))
    # One walk for the imports and the decision points, counted on the tree rather than on the text,
    # so a comment saying "if" is not one.
    # Node types are looked up, not tested one isinstance at a time: this loop meets every node of
    # every Python file, and it is most of what indexing a Python repository costs.
    for node in ast.walk(tree):
        kind = type(node)
        if kind not in PY_COUNTED:
            continue
        if kind is ast.Import:
            p.imports += [[a.name] for a in node.names]  # type: ignore[attr-defined]
        elif kind is ast.ImportFrom:
            base = "." * node.level + (node.module or "")  # type: ignore[attr-defined]
            for a in node.names:  # type: ignore[attr-defined]
                sep = "" if not base or base.endswith(".") else "."
                p.imports.append([f"{base}{sep}{a.name}", base] if a.name != "*" else [base])
        elif kind is ast.BoolOp:
            p.complexity += len(node.values) - 1  # type: ignore[attr-defined]
        elif kind is ast.comprehension:
            p.complexity += 1 + len(node.ifs)  # type: ignore[attr-defined]
        else:
            p.complexity += 1
    return p


def parse_sql(text: str) -> Parsed:
    p, line = Parsed(reads_as="T-SQL"), Lines(text)
    for m in SQL_DECL.finditer(text):
        at = line(m.start())
        p.symbols.append((bare(m.group(2)), SQL_KIND[m.group(1).lower()], at, True, at))
    p.complexity = len(BRANCHES.findall(text))
    return p


def _from_outline(lang: str, found: treesitter.Outline) -> Parsed:
    p = Parsed(symbols=found.symbols, refs=found.refs, package=found.package, complexity=found.branches,
               reads_as=lang)
    if lang in SCRIPT_LANGS:
        p.imports = [[spec] for how, spec in found.imports]
    else:
        p.links = found.imports
    return p


def parse_tree(lang: str, path: str, data: bytes) -> Parsed | None:
    """A file its language's grammar reads. None when no grammar for it loads on this machine."""
    grammar = GRAMMARS[lang]
    if lang == "TypeScript" and path.endswith("x"):
        grammar = "tsx"
    elif lang == "C" and path.endswith(".h"):
        grammar = "cpp"
    found = treesitter.outline(grammar, data, component_names=path.endswith(("x", ".jsx")))
    return None if found is None else _from_outline(lang, found)


def parse_component(lang: str, path: str, text: str) -> Parsed | None:
    """A Vue or Svelte file: its <script> blocks, read as the TypeScript or JavaScript they are, with
    their lines counted from where each block starts — and the file itself, which is a component."""
    p = Parsed(reads_as="TypeScript")
    stem = posixpath.splitext(posixpath.basename(path))[0]
    p.symbols.append((stem, "component", 1, True, text.count("\n") + 1))
    for m in SCRIPT_BLOCK.finditer(text):
        typed = re.search(r"""\blang\s*=\s*["']?(ts|tsx|typescript)\b""", m.group(1), re.I)
        found = treesitter.outline("typescript" if typed else "javascript", m.group(2).encode(),
                                   line_offset=text.count("\n", 0, m.start(2)))
        if found is None:
            return None
        p.symbols += found.symbols
        p.imports += [[spec] for _how, spec in found.imports]
        p.refs |= found.refs
        p.complexity += found.branches
    return p


def parse_notebook(path: str, data: bytes) -> Parsed | None:
    """A Jupyter notebook: its code cells, joined in order, read as the language its kernel speaks.
    Magics and shell escapes (`%timeit`, `!pip`) are blanked, not dropped, so the lines still count.
    Lines are the lines of that joined code — the notebook's program, as the kernel would run it."""
    try:
        book = json.loads(data)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(book, dict) or not isinstance(book.get("cells"), list):
        return None
    meta = book.get("metadata") if isinstance(book.get("metadata"), dict) else {}
    spoken = ((meta.get("kernelspec") or {}).get("language") or (meta.get("language_info") or {}).get("name")
              or "python")
    code: list[str] = []
    for cell in book["cells"]:
        if not isinstance(cell, dict) or cell.get("cell_type") != "code":
            continue
        source = cell.get("source", "")
        lines = ("".join(source) if isinstance(source, list) else str(source)).split("\n")
        code += ["" if MAGIC.match(line) else line for line in lines]
    text = "\n".join(code)
    language = KERNELS.get(str(spoken).lower())
    if language == "Python":
        return parse_python(text)
    if language in GRAMMARS:
        found = treesitter.outline(GRAMMARS[language], text.encode())
        return None if found is None else _from_outline(language, found)
    return None


def db_refs(text: str) -> set[tuple[str, str]]:
    """Every name that follows FROM, JOIN, INTO, UPDATE or EXEC. Only names the schema declares become edges."""
    out: set[tuple[str, str]] = set()
    for m in DB_REF.finditer(text):
        keyword = " ".join(m.group(1).lower().split())
        kind = "calls" if keyword.startswith("exec") else "writes" if keyword in WRITES else "reads"
        out.add((bare(m.group(2)), kind))
    return out


def parse(lang: str, path: str, data: bytes) -> Parsed | None:
    """One file, by the best reader its language has. None when nothing on this machine reads it."""
    text = data.decode("utf-8", errors="replace")
    if lang == "Python":
        p: Parsed | None = parse_python(text)
    elif lang == "T-SQL":
        p = parse_sql(text)
    elif lang == "Jupyter Notebook":
        p = parse_notebook(path, data)
    elif lang in ("Vue", "Svelte"):
        p = parse_component(lang, path, text)
    elif lang in GRAMMARS:
        p = parse_tree(lang, path, data)
    else:
        return None
    if p is not None:
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
    for ext in TS_EXTS + (".vue", ".svelte"):
        if base + ext in paths:
            return base + ext, True
    stem, ext = posixpath.splitext(base)
    if ext in (".js", ".jsx", ".mjs"):  # an ESM-style ".js" specifier that points at the TypeScript source
        for alt in (".ts", ".tsx"):
            if stem + alt in paths:
                return stem + alt, True
    return None, True


def _package(spec: str, lang: str) -> str:
    """The package an outside import comes from: react-dom/client → react-dom, os.path → os,
    org.springframework.web.Controller → org.springframework, github.com/x/y/z → github.com/x/y."""
    if lang == "Python":
        return spec.lstrip(".").split(".")[0] or spec
    if lang == "Go":
        parts = spec.split("/")
        return "/".join(parts[:3]) if "." in parts[0] else parts[0]
    if lang == "Rust":
        return spec.lstrip(":").split("::")[0]
    if lang == "PHP":
        return spec.lstrip("\\").split("\\")[0]
    if lang == "Dart":
        return spec.removeprefix("package:").split("/")[0]
    if lang in SCRIPT_LANGS or lang in ("Ruby", "Lua", "Zig", "C", "C++"):
        parts = spec.split("/")
        return "/".join(parts[:2]) if spec.startswith("@") and len(parts) > 1 else parts[0]
    parts = spec.split(".")
    return ".".join(parts[:2]) if parts[0] in DOMAINS and len(parts) > 1 else parts[0]


class Layout:
    """What the whole repository says about where names live, worked out once per index: the packages,
    namespaces and modules its files declare, its go.mod files, and its files by name."""

    def __init__(self, root: Path | None, parsed: dict[str, Parsed], lang_of: dict[str, str], paths: set[str]) -> None:
        self.paths = paths
        self.by_name: dict[str, list[str]] = defaultdict(list)       # file name → paths
        for path in paths:
            self.by_name[posixpath.basename(path)].append(path)
        # Keyed by language as well as by name: a Kotlin and a Scala file may both say com.acme.Cart,
        # and an Elixir and a Haskell module may both be Shop.Cart, without being each other.
        self.qualified: dict[tuple[str, str], str] = {}               # (lang, com.acme.Cart) → file
        self.namespaces: set[tuple[str, str]] = set()                 # (lang, a declared package or namespace)
        self.modules: dict[tuple[str, str], str] = {}                 # (lang, an Elixir/Haskell/Julia module) → file
        for path, p in parsed.items():
            lang = lang_of[path]
            if p.package:
                self.namespaces.add((lang, p.package))
                if lang == "Haskell":
                    self.modules[(lang, p.package)] = path
            for name, kind, _line, _exported, _end in p.symbols:
                if "." not in name and kind in USABLE:
                    self.qualified.setdefault((lang, f"{p.package}.{name}" if p.package else name), path)
                if kind == "module":
                    self.modules.setdefault((lang, name), path)
        self.go = self._go_modules(root, [p for p in paths if lang_of[p] == "Go"])
        #: Swift Package Manager targets: Sources/<Module>/, imported by the module's name
        self.swift_modules = {m.group(1) for p in paths if lang_of[p] == "Swift"
                              and (m := re.search(r"(?:^|/)Sources/([^/]+)/", p))}

    @staticmethod
    def _go_modules(root: Path | None, go_files: list[str]) -> dict[str, str]:
        """module path → the folder its go.mod is in, for every go.mod above a Go file."""
        found: dict[str, str] = {}
        if root is None:
            return found
        seen: set[str] = set()
        for path in go_files:
            folder = posixpath.dirname(path)
            while folder not in seen:
                seen.add(folder)
                try:
                    text = (root / folder / "go.mod").read_text(errors="replace") if folder else \
                        (root / "go.mod").read_text(errors="replace")
                except OSError:
                    text = ""
                m = re.search(r"^module\s+(\S+)", text, re.M)
                if m:
                    found[m.group(1)] = folder
                if not folder:
                    break
                folder = posixpath.dirname(folder)
        return found

    def nearest(self, path: str, suffix: str) -> str | None:
        """The file whose path ends with `suffix`, closest to `path` when several do."""
        homes = [h for h in self.by_name.get(posixpath.basename(suffix), [])
                 if h == suffix or h.endswith("/" + suffix)]
        return max(homes, key=lambda h: (_shared(h, path), -len(h))) if homes else None

    def beside(self, path: str, spec: str, exts: tuple[str, ...] = ("",)) -> str | None:
        """A path named relative to the file's own folder, then to the repository's root."""
        spec = spec.removeprefix("./")
        for base in (posixpath.dirname(path), ""):
            target = posixpath.normpath(posixpath.join(base, spec)) if base else posixpath.normpath(spec)
            for ext in exts:
                if target + ext in self.paths:
                    return target + ext
        return None


def _rust_module_dir(path: str) -> str:
    """The folder a Rust file's own submodules live in: src/main.rs → src, src/billing.rs → src/billing."""
    folder, name = posixpath.split(path)
    stem = posixpath.splitext(name)[0]
    return folder if stem in ("main", "lib", "mod") else posixpath.join(folder, stem)


def _rust_crate_root(path: str, paths: set[str]) -> str:
    folder = posixpath.dirname(path)
    while True:
        if f"{folder}/lib.rs".lstrip("/") in paths or f"{folder}/main.rs".lstrip("/") in paths:
            return folder
        if not folder:
            return posixpath.dirname(path)
        folder = posixpath.dirname(folder)


def _rust_uses(spec: str) -> list[str]:
    """crate::a::{b, c::D} → crate::a::b, crate::a::c::D. `self` inside braces is the path itself."""
    spec = re.sub(r"\s+", "", re.sub(r"\s+as\s+\w+", "", spec))
    m = re.match(r"^(.*?)::\{(.*)\}$", spec)
    if not m:
        return [spec] if spec else []
    head, body = m.groups()
    out, depth, part = [], 0, ""
    for ch in body + ",":
        if ch == "," and depth == 0:
            if part:
                out += [head if part == "self" else x for x in _rust_uses(f"{head}::{part}")]
            part = ""
            continue
        depth += ch == "{"
        depth -= ch == "}"
        part += ch
    return out


def _resolve_rust(how: str, spec: str, path: str, layout: Layout) -> tuple[list[str], str]:
    if how == "mod":
        base = posixpath.join(_rust_module_dir(path), spec)
        hit = layout.beside("", base, (".rs", "/mod.rs"))
        return ([hit], "resolved") if hit else ([], "unresolved")
    targets: list[str] = []
    for one in _rust_uses(spec):
        parts = one.split("::")
        if parts[0] == "crate":
            base, rest = _rust_crate_root(path, layout.paths), parts[1:]
        elif parts[0] in ("self", "super"):
            base, rest = _rust_module_dir(path), parts
            while rest and rest[0] in ("self", "super"):
                if rest[0] == "super":
                    base = posixpath.dirname(base)
                rest = rest[1:]
        else:
            return [], "external"
        for n in range(len(rest), 0, -1):
            hit = layout.beside("", posixpath.join(base, *rest[:n]), (".rs", "/mod.rs"))
            if hit:
                targets.append(hit)
                break
    if targets:
        return targets, "resolved"
    return [], "covered"          # a name of the crate's own root file, or one the uses pass will find


def _resolve_link(lang: str, how: str, spec: str, path: str, p: Parsed, layout: Layout,
                  go_files: dict[str, list[str]], declares: dict[str, set[str]]) -> tuple[list[str], str]:
    """Where one import of a non-Python, non-script file leads: (files, state). State is resolved,
    unresolved (it names this repository but no file was found), external (a package), or covered
    (it names a namespace of this repository; the files are found by what they use, not by it)."""
    if not spec:
        return [], "covered"
    if how == "relative":
        exts = {"Ruby": ("", ".rb"), "Lua": ("", ".lua"), "Zig": ("",), "Shell": ("", ".sh")}.get(lang, ("",))
        if lang == "Zig" and not spec.endswith(".zig"):
            return [], "external"
        hit = layout.beside(path, spec, exts) or (layout.nearest(path, spec) if lang in ("C", "C++") else None)
        return ([hit], "resolved") if hit else ([], "unresolved")
    if how == "system":
        hit = layout.nearest(path, spec)
        return ([hit], "resolved") if hit else ([], "external")
    if how == "package":
        home = layout.modules.get((lang, spec))
        return ([home], "resolved") if home else ([], "external")
    if lang == "Rust":
        return _resolve_rust(how, spec, path, layout)
    if lang == "Go":
        for module, folder in sorted(layout.go.items(), key=lambda m: -len(m[0])):
            if spec == module or spec.startswith(module + "/"):
                target = posixpath.join(folder, spec[len(module):].lstrip("/")).strip("/")
                files = [f for f in go_files.get(target, []) if not f.endswith("_test.go")]
                if not files:
                    return [], "unresolved"
                used = [f for f in files if declares.get(f, set()) & p.refs]
                return (used or sorted(files)[:1]), "resolved"
        return [], "external"
    if lang in ("Java", "Kotlin", "Scala", "C#", "PHP", "Elixir", "Haskell", "Swift", "Julia"):
        name = spec.replace("\\", ".").lstrip(".").removesuffix(".*").removesuffix("._")
        if lang in ("Elixir", "Haskell", "Julia"):
            home = layout.modules.get((lang, name))
            return ([home], "resolved") if home and home != path else ([], "covered" if home else "external")
        # Java and Kotlin import each other's classes; a language's own declaration wins.
        kin = [lang] + [other for other, family in FAMILY.items() if family == FAMILY.get(lang) and other != lang]
        for key in (name, name.rsplit(".", 1)[0]):
            home = next((layout.qualified[(k, key)] for k in kin if (k, key) in layout.qualified), None)
            if home:
                return [home], "resolved"
        if any(k == lang and (ns == name or ns.startswith(name + ".")) for k, ns in layout.namespaces):
            return [], "covered"
        if lang == "Swift" and name in layout.swift_modules:
            return [], "covered"
        return [], "external"
    if lang == "Dart":
        if spec.startswith("dart:"):
            return [], "external"
        if spec.startswith("package:"):
            rest = spec[len("package:"):].split("/", 1)
            hit = layout.nearest(path, f"lib/{rest[1]}") if len(rest) == 2 else None
            return ([hit], "resolved") if hit else ([], "external")
        hit = layout.beside(path, spec)
        return ([hit], "resolved") if hit else ([], "unresolved")
    if lang == "Ruby":
        hit = layout.nearest(path, f"{spec}.rb")
        return ([hit], "resolved") if hit else ([], "external")
    if lang == "Lua":
        stem = spec.replace(".", "/")
        hit = layout.nearest(path, f"{stem}.lua") or layout.nearest(path, f"{stem}/init.lua")
        return ([hit], "resolved") if hit else ([], "external")
    if lang == "OCaml":
        module = spec.split(".")[0]
        hit = layout.nearest(path, f"{module[:1].lower()}{module[1:]}.ml")
        return ([hit], "resolved") if hit and hit != path else ([], "external")
    return [], "external"


def _edges(parsed: dict[str, Parsed], lang_of: dict[str, str], paths: set[str],
           root: Path | None = None) -> tuple[set[tuple[str, str | None, str, str]], int, int]:
    edges: set[tuple[str, str | None, str, str]] = set()
    resolved = unresolved = 0
    modules = _python_modules(paths)
    layout = Layout(root, parsed, lang_of, paths)
    go_files: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        if lang_of[path] == "Go":
            go_files[posixpath.dirname(path)].append(path)
    declares = {path: {name for name, kind, *_ in p.symbols if "." not in name} for path, p in parsed.items()}

    for path, p in parsed.items():
        lang = p.reads_as or lang_of[path]
        for cands in p.imports:
            if lang == "Python":
                spec = cands[-1]
                target = _resolve_py(cands, path, modules, paths)
                internal = spec.startswith(".") or spec.split(".")[0] in modules
            else:
                spec = cands[0]
                target, internal = _resolve_ts(spec, path, paths)
            if target:
                if target != path:
                    edges.add((path, target, spec, "imports"))
                    resolved += 1
            elif internal:
                unresolved += 1
            else:
                edges.add((path, None, _package(spec, lang), "imports"))
        for how, spec in p.links:
            targets, state = _resolve_link(lang, how, spec, path, p, layout, go_files, declares)
            if state == "resolved":
                for target in targets:
                    if target != path:
                        edges.add((path, target, spec, "imports"))
                        resolved += 1
            elif state == "unresolved":
                unresolved += 1
            elif state == "external":
                edges.add((path, None, _package(spec, lang), "imports"))

    # A file that calls a function or names a type declared in another file of its language depends on
    # that file — how C#, Java and every language whose imports name namespaces rather than files are
    # linked. Go's files of one package see each other without imports, so there it stays in the folder.
    # A type is found across one family (Java and Kotlin, C and C++); a function only in its own
    # language, since `x.total()` in Java is a method call, never Kotlin's top-level `total`.
    declared: dict[tuple[str, str], set[str]] = defaultdict(set)
    for path, p in parsed.items():
        lang = lang_of[path]
        if lang in PARSERS and lang not in SCRIPT_LANGS and lang not in ("Python", "Jupyter Notebook", "T-SQL"):
            for name, kind, *_ in p.symbols:
                if kind in USABLE and len(name) >= 3:
                    declared[(lang, name.rsplit(".", 1)[-1])].add(path)
    for path, p in parsed.items():
        lang = lang_of[path]
        kin = [other for other, family in FAMILY.items() if family == FAMILY.get(lang) and other != lang]
        own = declares.get(path, set())
        for ref in p.refs - own:
            homes = declared.get((lang, ref)) or {
                home for other in kin for home in declared.get((other, ref), ())
                if any(n.rsplit(".", 1)[-1] == ref and k in TYPE_KINDS for n, k, *_ in parsed[home].symbols)}
            if not homes or len(homes) > 3:  # a name declared everywhere tells us nothing
                continue
            for home in homes:
                if home != path and (lang != "Go" or posixpath.dirname(home) == posixpath.dirname(path)):
                    edges.add((path, home, ref, "uses"))
                    resolved += 1

    # the database: names the schema declares, found after FROM, JOIN, INTO, UPDATE or EXEC anywhere
    homes: dict[str, str] = {}
    names: dict[str, str] = {}
    for path, p in parsed.items():
        if lang_of[path] == "T-SQL":
            for name, kind, *_ in p.symbols:
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


def walk(root: Path, excluded: list[str]) -> Iterator[tuple[str, str, bytes]]:
    """Every file under root the index can say something about, as (path, language, contents).

    The onboarding scan's walk, with the same folders never entered, the same exclusions and the same
    caps, over the wider set of extensions the grammars read. It is its own function only because the
    scan's language table is narrower; see LANGUAGES.
    """
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        dirnames[:] = [d for d in dirnames if d not in onboarding.SKIP_DIRS and not d.startswith(".")
                       and not _excluded(os.path.normpath(os.path.join(rel_dir, d)), d, excluded)]
        for name in filenames:
            lang = LANGUAGES.get(os.path.splitext(name)[1].lower())
            rel = os.path.normpath(os.path.join(rel_dir, name))
            if not lang or _excluded(rel, name, excluded):
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(path) > onboarding.MAX_BYTES:
                    continue
                with open(path, "rb") as fh:
                    data = fh.read()
            except OSError:
                continue
            yield rel.replace(os.sep, "/"), lang, data
            count += 1
            if count >= onboarding.MAX_FILES:
                return


def _excluded(rel: str, name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(name, p.removeprefix("**/")) for p in patterns)


# ── building and saving an index ─────────────────────────────────
@dataclass
class Index:
    files: list[dict[str, Any]]
    symbols: list[tuple[str, str, str, int, bool, int]]  # path, name, kind, line, exported, end line
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


def _parse_chunk(chunk: list[tuple[str, str, bytes]]) -> list[tuple[str, Parsed | None]]:
    """One worker's share: (path, what it holds). Top-level so a worker process can be handed it."""
    return [(rel, parse(lang, rel, data)) for rel, lang, data in chunk]


def parse_all(jobs: list[tuple[str, str, bytes]]) -> list[tuple[str, Parsed | None]]:
    """Parse every file, in worker processes when there are enough of them to be worth it. A pool that
    cannot start or breaks — a sandbox without process spawning, a worker killed for memory — costs
    time, never the index: the files are then read here, one after another, with the same result."""
    workers = min(POOL_WORKERS, max(1, (os.cpu_count() or 1) - 1))
    if len(jobs) < POOL_FROM or workers < 2:
        return _parse_chunk(jobs)
    chunks = [jobs[i:i + POOL_CHUNK] for i in range(0, len(jobs), POOL_CHUNK)]
    try:
        with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("spawn")) as pool:
            return [pair for part in pool.map(_parse_chunk, chunks) for pair in part]
    except (OSError, BrokenProcessPool):
        return _parse_chunk(jobs)


def build(root: Path, excluded: list[str]) -> Index:
    """Read every source file under root. Blocking and CPU-bound: run it on a worker thread."""
    t0 = time.monotonic()
    files: list[dict[str, Any]] = []
    parsed: dict[str, Parsed] = {}
    lang_of: dict[str, str] = {}
    jobs: list[tuple[str, str, bytes]] = []
    for rel, lang, data in walk(root, excluded):
        rec = {"path": rel, "lang": lang, "module": onboarding.module_of(rel), "bytes": len(data),
               "lines": data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0),
               "sha1": hashlib.sha1(data).hexdigest(), "complexity": 0, "churn": 0, "changed_at": None}
        files.append(rec)
        lang_of[rel] = lang
        if lang in PARSERS and len(data) <= MAX_PARSE:
            jobs.append((rel, lang, data))
    by_path = {rec["path"]: rec for rec in files}
    for rel, p in parse_all(jobs):
        if p is not None:
            by_path[rel]["complexity"] = p.complexity
            parsed[rel] = p
    history = _history(root)
    for rec in files:
        if rec["path"] in history:
            rec["churn"], rec["changed_at"] = history[rec["path"]]
    edges, resolved, unresolved = _edges(parsed, lang_of, set(lang_of), root)
    symbols = [(path, name, kind, line, exported, end) for path, p in parsed.items()
               for name, kind, line, exported, end in p.symbols]
    declared = {name.lower() for path, p in parsed.items() if lang_of[path] == "T-SQL"
                for name, kind, *_ in p.symbols if kind in DB_KINDS}
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
