"""Problems in a project's code, found by the project's own checkers and read into one shape.

NeuroCode never judges code by itself here. A check runs the tools the repository already declares and
that are really installed — its TypeScript compiler, its ESLint or oxlint, its ruff, mypy or pyright, go
vet, cargo check, Gradle or Maven, dotnet build — each in the way that prints machine-readable output
where the tool has one (`--pretty false`, `-f json`, `--output-format json`, `--outputjson`,
`--message-format json`). What they print is read into problems: a file, a line and column, a
severity, the tool's own code and words. A tool that is configured but not installed is named as
missing, with what installs it, rather than skipped in silence; one that exits non-zero while printing
nothing that reads as a problem says so, with its last lines, rather than "no problems".

Where each tool comes from is the project's own first: `node_modules/.bin` (in the folder or a folder
above it, for a monorepo), the project's `.venv`/`venv`, then the PATH. Nothing is installed.

A check is a person running commands on this machine, so it needs `machine:access` and stays inside the
machine's roots (`machine.inside`); it is in the audit log. Checks live in this process, like terminals
and debug sessions: each has a time limit per tool, a ceiling on the output read and on the problems
kept, and can be cancelled, which kills the tool's whole process group.

The parsers are plain functions of text, so a run's own check step (services/runs.py) reads the output
of `npm run lint` or `make typecheck` with the same rules — `parse_text` knows the text shapes of tsc,
dotnet, mypy, ruff, eslint's stylish report, pyright, cargo/rustc, javac, Kotlin, Maven, go vet and the
generic `file:line:col: message`.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .errors import Refused

#: How long one tool may run before it is stopped, and how much of its output is read.
TOOL_SECONDS = 300.0
OUTPUT_BYTES = 16 * 1024 * 1024
#: Problems one check keeps; a codebase with more is told how many there were.
MAX_PROBLEMS = 5000
#: A message longer than this is cut: a type error that prints a whole type is still read in a line.
MESSAGE_CHARS = 2000
#: One person's checks running at once, and finished ones remembered (per person) to be read again.
MAX_RUNNING = 2
KEEP_DONE = 20
#: The most problems one page of a check carries.
PAGE_MAX = 1000
PAGE_DEFAULT = 500
#: How far above a folder `node_modules/.bin` and a virtual environment are looked for.
UP_LEVELS = 6
#: A tsconfig that only lists references is checked per reference, at most this many.
MAX_REFERENCES = 6
SEVERITIES = ("error", "warning", "info")
#: Lines kept of what a tool said besides its problems, to show when it failed without any.
NOTE_LINES = 12


# ── what a checker found ──────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Found:
    """One problem as a tool printed it: the file as the tool named it (relative to where it ran, or
    absolute), 1-based line and column."""

    file: str
    line: int
    col: int
    severity: str
    message: str
    code: str | None = None
    end_line: int | None = None
    end_col: int | None = None


@dataclass(frozen=True, slots=True)
class Problem:
    """A problem placed on this machine: `path` is absolute and real; `file` is how the Workbench names
    it — label-prefixed for a project's further source, plain for its first, relative for a folder."""

    source: str
    file: str
    path: str
    line: int
    col: int
    severity: str
    message: str
    tool: str
    code: str | None = None
    end_line: int | None = None
    end_col: int | None = None

    def json(self) -> dict[str, Any]:
        return {"source": self.source, "file": self.file, "path": self.path, "line": self.line, "col": self.col,
                "endLine": self.end_line, "endCol": self.end_col, "severity": self.severity, "code": self.code,
                "message": self.message, "tool": self.tool}


def _severity(word: str | int | None, default: str = "error") -> str:
    text = str(word or "").strip().lower()
    if text in ("error", "fatal", "e", "2", "err", "failure"):
        return "error"
    if text in ("warning", "warn", "w", "1"):
        return "warning"
    if text in ("info", "information", "note", "hint", "help", "message", "suggestion", "i", "0"):
        return "info"
    return default


def _cut(message: str) -> str:
    text = message.strip()
    return text if len(text) <= MESSAGE_CHARS else text[:MESSAGE_CHARS - 1] + "…"


def _int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


# ── the JSON reports ──────────────────────────────────────────────
def _json(text: str) -> Any:
    """The JSON document in a tool's output. Some print a line before it (npm, a banner), so the first
    `[` or `{` that starts a line is where it is looked for when the whole text is not JSON."""
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    for match in re.finditer(r"^[\[{]", text, re.M):
        with contextlib.suppress(ValueError):
            return json.loads(text[match.start():])
    return None


def parse_eslint_json(text: str) -> list[Found]:
    """`eslint -f json`: a list of files, each with its messages. Severity 2 is an error, 1 a warning."""
    data = _json(text)
    out: list[Found] = []
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict):
            continue
        for m in entry.get("messages") or []:
            if not isinstance(m, dict):
                continue
            out.append(Found(file=str(entry.get("filePath") or ""), line=_int(m.get("line"), 1) or 1,
                             col=_int(m.get("column"), 1) or 1, end_line=_int(m.get("endLine")),
                             end_col=_int(m.get("endColumn")), severity=_severity(m.get("severity"), "warning"),
                             code=m.get("ruleId") or None, message=_cut(str(m.get("message") or ""))))
    return out


def parse_oxlint_json(text: str) -> list[Found]:
    """`oxlint -f json`: `{"diagnostics": [...]}`, each with a filename and labelled spans."""
    data = _json(text)
    out: list[Found] = []
    for d in (data.get("diagnostics") or []) if isinstance(data, dict) else []:
        if not isinstance(d, dict):
            continue
        spans = [x.get("span") or {} for x in d.get("labels") or [] if isinstance(x, dict)]
        span = spans[0] if spans else {}
        message = str(d.get("message") or "")
        if d.get("help"):
            message = f"{message} {d['help']}"
        out.append(Found(file=str(d.get("filename") or ""), line=_int(span.get("line"), 1) or 1,
                         col=_int(span.get("column"), 1) or 1, severity=_severity(d.get("severity"), "warning"),
                         code=d.get("code") or None, message=_cut(message)))
    return out


def parse_ruff_json(text: str) -> list[Found]:
    """`ruff check --output-format json`. A rule broken is a warning; a file that does not parse (no
    code, or ruff's syntax-error codes) is an error."""
    data = _json(text)
    out: list[Found] = []
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict):
            continue
        at, end = d.get("location") or {}, d.get("end_location") or {}
        code = d.get("code") or None
        syntax = code is None or code in ("E999", "invalid-syntax") or str(d.get("message", "")).startswith(
            "SyntaxError")
        out.append(Found(file=str(d.get("filename") or ""), line=_int(at.get("row"), 1) or 1,
                         col=_int(at.get("column"), 1) or 1, end_line=_int(end.get("row")),
                         end_col=_int(end.get("column")), severity="error" if syntax else "warning", code=code,
                         message=_cut(str(d.get("message") or ""))))
    return out


def parse_pyright_json(text: str) -> list[Found]:
    """`pyright --outputjson`: ranges are 0-based, as in the language server protocol."""
    data = _json(text)
    out: list[Found] = []
    for d in (data.get("generalDiagnostics") or []) if isinstance(data, dict) else []:
        if not isinstance(d, dict):
            continue
        r = d.get("range") or {}
        start, end = r.get("start") or {}, r.get("end") or {}
        out.append(Found(file=str(d.get("file") or ""), line=(_int(start.get("line"), 0) or 0) + 1,
                         col=(_int(start.get("character"), 0) or 0) + 1,
                         end_line=(_int(end.get("line"), 0) or 0) + 1 if end else None,
                         end_col=(_int(end.get("character"), 0) or 0) + 1 if end else None,
                         severity=_severity(d.get("severity")), code=d.get("rule") or None,
                         message=_cut(str(d.get("message") or ""))))
    return out


def parse_cargo_json(text: str) -> list[Found]:
    """`cargo check --message-format json`: one JSON object a line; a compiler message's primary span is
    where the problem is. A message with no span ("aborting due to 2 previous errors") is a summary."""
    out: list[Found] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict) or d.get("reason") != "compiler-message":
            continue
        m = d.get("message") or {}
        spans = [s for s in m.get("spans") or [] if isinstance(s, dict)]
        primary = next((s for s in spans if s.get("is_primary")), spans[0] if spans else None)
        level = str(m.get("level") or "")
        if primary is None or level in ("failure-note",):
            continue
        code = (m.get("code") or {}).get("code") if isinstance(m.get("code"), dict) else None
        message = str(m.get("message") or "")
        if primary.get("label"):
            message = f"{message}: {primary['label']}"
        out.append(Found(file=str(primary.get("file_name") or ""), line=_int(primary.get("line_start"), 1) or 1,
                         col=_int(primary.get("column_start"), 1) or 1, end_line=_int(primary.get("line_end")),
                         end_col=_int(primary.get("column_end")), severity=_severity(level), code=code,
                         message=_cut(message)))
    return out


# ── the text reports ──────────────────────────────────────────────
# tsc --pretty false, and dotnet/MSBuild: `file(line,col): error TS2322: message` (MSBuild may give a
# range, `(l,c,el,ec)`, and ends with the project in brackets).
_PAREN = re.compile(r"^\s*(?P<file>[^\s(][^(]*?)\((?P<line>\d+),(?P<col>\d+)(?:,(?P<eline>\d+),(?P<ecol>\d+))?\)"
                    r"\s*:\s*(?P<sev>error|warning|info|message|hidden)\s*(?P<code>[A-Za-z]+\d+)?\s*:\s*(?P<msg>.*)$",
                    re.I)
_MSBUILD_PROJECT = re.compile(r"\s+\[[^\]]+\.(?:csproj|fsproj|vbproj|sln|proj)\]$")
# mypy (and many others): `file:line[:col][:eline:ecol]: error: message  [code]`.
_MYPY = re.compile(r"^(?P<file>[^\s:][^:]*?):(?P<line>\d+):(?:(?P<col>\d+):)?(?:(?P<eline>\d+):(?P<ecol>\d+):)?"
                   r"\s*(?P<sev>error|warning|note|info)\s*:\s*(?P<msg>.*?)(?:\s{2}\[(?P<code>[\w.-]+)\])?$", re.I)
# pyright's text report: `  /abs/file.py:12:5 - error: message (rule)`.
_PYRIGHT = re.compile(r"^\s*(?P<file>\S.*?):(?P<line>\d+):(?P<col>\d+) - (?P<sev>error|warning|information)\s*:"
                      r"\s*(?P<msg>.*?)(?:\s\((?P<code>report\w+)\))?$")
# rustc/cargo and ruff's "full" format: a header, then `  --> file:line:col`.
_ARROW = re.compile(r"^\s*-->\s*(?P<file>[^\s:][^:]*?):(?P<line>\d+):(?P<col>\d+)\s*$")
_RUST_HEAD = re.compile(r"^(?P<sev>error|warning|note|help)(?:\[(?P<code>[\w-]+)\])?:\s*(?P<msg>.+)$")
_RUFF_HEAD = re.compile(r"^(?P<code>[A-Z]{1,4}\d{2,4})\s+(?:\[\*\]\s+)?(?P<msg>.+)$")
# eslint's stylish report: a file on its own line, then `  12:5  error  message  rule`.
_STYLISH_FILE = re.compile(r"^(?P<file>(?:/|[A-Za-z]:\\|\.{0,2}/?)\S[^:]*\.\w{1,8})$")
_STYLISH_ROW = re.compile(r"^\s+(?P<line>\d+):(?P<col>\d+)\s+(?P<sev>error|warning)\s+(?P<msg>.+?)"
                          r"(?:\s{2,}(?P<code>[@\w/.-]+))?$")
# Kotlin through Gradle: `e: file:///path/File.kt:12:5 message` (older: `e: /path/File.kt: (12, 5): message`).
_KOTLIN = re.compile(r"^(?P<sev>[ew]):\s+(?:file://)?(?P<file>[^\s:][^:]*?\.kts?):(?:\s*\()?(?P<line>\d+)[:,]\s*"
                     r"(?P<col>\d+)\)?:?\s+(?P<msg>.*)$")
# Maven: `[ERROR] /path/File.java:[12,5] message`.
_MAVEN = re.compile(r"^\[(?P<sev>ERROR|WARNING|INFO)\]\s+(?P<file>\S.*?\.\w+):\[(?P<line>\d+),(?P<col>\d+)\]\s+"
                    r"(?P<msg>.*)$")
# The generic `file:line[:col]: message` — go vet, javac, gcc/clang, ruff's concise format, flake8,
# shellcheck -f gcc and most compilers. The file must look like a file (an extension) and not a URL.
_GENERIC = re.compile(r"^(?:vet:\s+)?(?P<file>[^\s:\"'][^:\"']*?\.[A-Za-z0-9_+-]{1,10}):(?P<line>\d+):"
                      r"(?:(?P<col>\d+):?)?\s*(?P<rest>.+)$")
_GENERIC_SEV = re.compile(r"^(?:(?:fatal\s+)?(?P<sev>error|warning|note|info|hint)\s*(?:\[(?P<code1>[\w-]+)\])?"
                          r"\s*:\s*)?"
                          r"(?:(?P<code2>[A-Z]{1,4}\d{2,4})\s+(?:\[\*\]\s+)?)?(?P<msg>.+)$", re.I)


def parse_text(text: str | Iterable[str], default: str = "error") -> list[Found]:
    """Every problem a tool's text output names, whatever the tool: the shapes are tried in order from
    the most particular to the generic `file:line:col: message`. Lines that are none of them — a
    banner, a summary, a source excerpt — are left alone. A line indented under a tsc or mypy problem
    continues its message (tsc explains a type mismatch over several lines)."""
    lines = text.splitlines() if isinstance(text, str) else [ln.rstrip("\n") for ln in text]
    out: list[Found] = []
    stylish_file: str | None = None
    header: tuple[str, str | None, str] | None = None     # a rustc/ruff header waiting for its `-->`
    last_continues = False
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            stylish_file, last_continues = None, False
            continue
        m = _PAREN.match(line)
        if m:
            message = _MSBUILD_PROJECT.sub("", m["msg"])
            out.append(Found(file=m["file"].strip(), line=int(m["line"]), col=int(m["col"]),
                             end_line=_int(m["eline"]), end_col=_int(m["ecol"]),
                             severity=_severity(m["sev"]), code=m["code"], message=_cut(message)))
            last_continues, header = True, None
            continue
        m = _PYRIGHT.match(line)
        if m:
            out.append(Found(file=m["file"].strip(), line=int(m["line"]), col=int(m["col"]),
                             severity=_severity(m["sev"]), code=m["code"], message=_cut(m["msg"])))
            last_continues, header = False, None
            continue
        m = _ARROW.match(line)
        if m and header is not None:
            sev, code, msg = header
            out.append(Found(file=m["file"], line=int(m["line"]), col=int(m["col"]), severity=sev, code=code,
                             message=_cut(msg)))
            header, last_continues = None, False
            continue
        m = _RUST_HEAD.match(line)
        if m and not _GENERIC.match(line):
            header = (_severity(m["sev"]), m["code"], m["msg"])
            last_continues = False
            continue
        m = _RUFF_HEAD.match(line)
        if m and not line.startswith(" "):
            header = ("warning", m["code"], m["msg"])
            last_continues = False
            continue
        m = _MAVEN.match(line)
        if m:
            out.append(Found(file=m["file"], line=int(m["line"]), col=int(m["col"]), severity=_severity(m["sev"]),
                             message=_cut(m["msg"])))
            last_continues = False
            continue
        m = _KOTLIN.match(line)
        if m:
            out.append(Found(file=m["file"], line=int(m["line"]), col=int(m["col"]), severity=_severity(m["sev"]),
                             message=_cut(m["msg"])))
            last_continues = False
            continue
        m = _MYPY.match(line)
        if m:
            out.append(Found(file=m["file"], line=int(m["line"]), col=_int(m["col"], 1) or 1,
                             end_line=_int(m["eline"]), end_col=_int(m["ecol"]), severity=_severity(m["sev"]),
                             code=m["code"], message=_cut(m["msg"])))
            last_continues = False
            continue
        if stylish_file is not None:
            m = _STYLISH_ROW.match(line)
            if m:
                out.append(Found(file=stylish_file, line=int(m["line"]), col=int(m["col"]),
                                 severity=_severity(m["sev"]), code=m["code"], message=_cut(m["msg"])))
                continue
        m = _GENERIC.match(line)
        if m and "://" not in m["file"]:
            rest = _GENERIC_SEV.match(m["rest"].strip())
            sev = _severity(rest["sev"], default) if rest and rest["sev"] else default
            code = (rest["code1"] or rest["code2"]) if rest else None
            if rest and rest["code2"] and not rest["sev"]:
                sev = "warning"                     # a lint rule's code (ruff, flake8) with no severity word
            msg = rest["msg"] if rest else m["rest"]
            out.append(Found(file=m["file"], line=int(m["line"]), col=_int(m["col"], 1) or 1, severity=sev,
                             code=code, message=_cut(msg)))
            last_continues, stylish_file = False, None
            continue
        if _STYLISH_FILE.match(line.strip()) and not line.startswith(" "):
            stylish_file, header, last_continues = line.strip(), None, False
            continue
        if last_continues and out and raw.startswith(" "):
            # tsc prints the rest of a long message indented under it.
            prev = out[-1]
            out[-1] = replace(prev, message=_cut(f"{prev.message}\n{line.strip()}"))
            continue
        last_continues = False
    return out


PARSERS: dict[str, Callable[[str], list[Found]]] = {
    "eslint-json": parse_eslint_json,
    "oxlint-json": parse_oxlint_json,
    "ruff-json": parse_ruff_json,
    "pyright-json": parse_pyright_json,
    "cargo-json": parse_cargo_json,
    "text": parse_text,
}


def place(found: Iterable[Found], *, cwd: Path, base: Path, prefix: str, source: str, tool: str,
          limit: int = MAX_PROBLEMS) -> tuple[list[Problem], int]:
    """Problems on this machine: each file made absolute (relative ones are relative to where the tool
    ran) and real, then named as the Workbench names it — relative to `base` with `prefix` in front — or
    left absolute when it lies outside `base` (a library's own file, say). Returns the problems kept and
    how many there were."""
    home = os.path.realpath(base)
    out: list[Problem] = []
    seen: set[tuple[str, int, int, str]] = set()
    total = 0
    for f in found:
        name = f.file.strip()
        if name.startswith("file://"):
            name = unquote(urlparse(name).path)
        if not name:
            continue
        absolute = os.path.realpath(name if os.path.isabs(name) else os.path.join(cwd, name))
        key = (absolute, f.line, f.col, f.message)
        if key in seen:
            continue
        seen.add(key)
        total += 1
        if len(out) >= limit:
            continue
        if absolute == home or absolute.startswith(home + os.sep):
            shown = prefix + os.path.relpath(absolute, home).replace(os.sep, "/")
        else:
            shown = absolute
        out.append(Problem(source=source, file=shown, path=absolute, line=max(1, f.line), col=max(1, f.col),
                           end_line=f.end_line, end_col=f.end_col, severity=f.severity, code=f.code,
                           message=f.message, tool=tool))
    return out, total


# ── finding the checkers a folder has ─────────────────────────────
@dataclass(frozen=True, slots=True)
class Checker:
    """One command a check runs: the tool, its words, how its output is read, and why it was chosen."""

    tool: str
    label: str
    argv: tuple[str, ...]
    parser: str
    why: str
    default: str = "error"

    def command(self, cwd: Path) -> str:
        """The command as a person reads it: a tool inside the folder is shown relative to it."""
        words = list(self.argv)
        with contextlib.suppress(ValueError):
            if os.path.isabs(words[0]):
                rel = os.path.relpath(words[0], cwd)
                words[0] = rel if not rel.startswith("../../..") else words[0]
        return shlex.join(words)

    def json(self, cwd: Path) -> dict[str, Any]:
        return {"tool": self.tool, "label": self.label, "command": self.command(cwd), "why": self.why}


@dataclass(frozen=True, slots=True)
class Missing:
    """A checker the project declares whose tool is not on this machine."""

    tool: str
    why: str

    def json(self) -> dict[str, Any]:
        return {"tool": self.tool, "why": self.why}


def _text(file: Path, limit: int = 512 * 1024) -> str:
    try:
        if file.is_file() and not file.is_symlink() and file.stat().st_size <= limit:
            return file.read_text(errors="replace")
    except OSError:
        pass
    return ""


def _executable(file: Path) -> bool:
    return file.is_file() and os.access(file, os.X_OK)


def _upwards(folder: Path, bound: Path | None) -> list[Path]:
    """The folder and those above it, stopping at `bound` (the root it is inside) or after a few."""
    out = [folder]
    here = folder
    for _ in range(UP_LEVELS):
        if bound is not None and here == bound:
            break
        if here.parent == here:
            break
        here = here.parent
        if bound is not None and not (here == bound or here.is_relative_to(bound)):
            break
        out.append(here)
    return out


def node_tool(folder: Path, name: str, bound: Path | None, *, path_too: bool = True) -> str | None:
    """A tool the project installed with npm (its own `node_modules/.bin`, here or above), else PATH's."""
    for here in _upwards(folder, bound):
        found = here / "node_modules" / ".bin" / name
        if _executable(found):
            return str(found)
    return shutil.which(name) if path_too else None


def python_tool(folder: Path, name: str, bound: Path | None) -> str | None:
    """A tool in the project's own virtual environment (`.venv`, `venv`, here or above), else PATH's."""
    for here in _upwards(folder, bound):
        for env in (".venv", "venv", "env"):
            found = here / env / ("Scripts" if os.name == "nt" else "bin") / name
            if _executable(found):
                return str(found)
    return shutil.which(name)


def _tsconfig_references(text: str) -> list[str] | None:
    """The configs a solution-style tsconfig points at (`"files": []` plus `"references"`), or None
    when it is an ordinary config. tsconfig allows comments and trailing commas, so they go first."""
    clean = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    clean = re.sub(r"(^|[^:\"\\])//[^\n]*", r"\1", clean)
    clean = re.sub(r",(\s*[}\]])", r"\1", clean)
    try:
        data = json.loads(clean)
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("files") != [] or not data.get("references"):
        return None
    refs = [str(r.get("path")) for r in data["references"] if isinstance(r, dict) and r.get("path")]
    return refs[:MAX_REFERENCES] or None


def _ref_config(folder: Path, ref: str) -> str | None:
    target = folder / ref
    if target.is_dir():
        target = target / "tsconfig.json"
    return os.path.relpath(target, folder) if target.is_file() else None


ESLINT_CONFIGS = ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts",
                  "eslint.config.mts", "eslint.config.cts", ".eslintrc", ".eslintrc.js", ".eslintrc.cjs",
                  ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml")


def detect(folder: Path, bound: Path | None = None) -> tuple[list[Checker], list[Missing]]:
    """The checkers this folder declares and this machine has, and those it declares but lacks.

    Declared means the project's own configuration says so: a tsconfig, an ESLint config, oxlint
    installed in the project, ruff/mypy/pyright configured, a go.mod, a Cargo.toml, a Gradle or Maven
    build, a .NET solution or project. Nothing is guessed from file extensions alone. Blocking."""
    found: list[Checker] = []
    missing: list[Missing] = []
    package = _text(folder / "package.json")
    pyproject = _text(folder / "pyproject.toml")
    setup_cfg = _text(folder / "setup.cfg")

    # TypeScript
    tsconfig = folder / "tsconfig.json"
    if tsconfig.is_file():
        tsc = node_tool(folder, "tsc", bound)
        if tsc is None:
            missing.append(Missing("tsc", "tsconfig.json is here, but TypeScript is not installed — run npm "
                                          "install (or add typescript to the devDependencies)."))
        else:
            refs = _tsconfig_references(_text(tsconfig))
            configs = [c for c in (_ref_config(folder, r) for r in refs or []) if c] if refs else []
            for config in configs or ["tsconfig.json"]:
                found.append(Checker("tsc", f"tsc · {config}" if configs else "tsc",
                                     (tsc, "--noEmit", "--pretty", "false", "-p", config), "text",
                                     f"{config} and TypeScript's compiler"))
    # ESLint
    eslint_config = next((n for n in ESLINT_CONFIGS if (folder / n).is_file()), None)
    if eslint_config is None and '"eslintConfig"' in package:
        eslint_config = "package.json (eslintConfig)"
    if eslint_config:
        eslint = node_tool(folder, "eslint", bound)
        if eslint is None:
            missing.append(Missing("eslint", f"{eslint_config} is here, but ESLint is not installed — run npm "
                                             "install."))
        else:
            found.append(Checker("eslint", "eslint", (eslint, "-f", "json", "."), "eslint-json",
                                 f"{eslint_config} and ESLint", default="warning"))
    # oxlint: installed by the project, or configured with it on PATH.
    oxlint = node_tool(folder, "oxlint", bound, path_too=False)
    if oxlint is None and ((folder / ".oxlintrc.json").is_file() or "oxlint" in package):
        oxlint = shutil.which("oxlint")
        if oxlint is None:
            missing.append(Missing("oxlint", "The project uses oxlint, but it is not installed — run npm install."))
    if oxlint:
        found.append(Checker("oxlint", "oxlint", (oxlint, "-f", "json"), "oxlint-json",
                             "oxlint installed in the project", default="warning"))
    # ruff
    if "[tool.ruff" in pyproject or (folder / "ruff.toml").is_file() or (folder / ".ruff.toml").is_file():
        ruff = python_tool(folder, "ruff", bound)
        if ruff is None:
            missing.append(Missing("ruff", "ruff is configured, but not installed — pip install ruff (or uv add "
                                           "--dev ruff)."))
        else:
            # --no-fix: a project's `fix = true` would otherwise let a check rewrite files.
            found.append(Checker("ruff", "ruff", (ruff, "check", "--output-format", "json", "--no-fix", "."),
                                 "ruff-json", "ruff's configuration", default="warning"))
    # mypy
    mypy_ini = next((n for n in ("mypy.ini", ".mypy.ini") if (folder / n).is_file()), None)
    mypy_config = "[tool.mypy" in pyproject or mypy_ini is not None or "[mypy" in setup_cfg
    if mypy_config:
        mypy = python_tool(folder, "mypy", bound)
        if mypy is None:
            missing.append(Missing("mypy", "mypy is configured, but not installed — pip install mypy."))
        else:
            config_text = pyproject + _text(folder / (mypy_ini or "mypy.ini")) + setup_cfg
            names_files = re.search(r"^\s*files\s*=", config_text, re.M) is not None
            found.append(Checker("mypy", "mypy", (mypy, "--show-column-numbers", "--no-error-summary",
                                                  "--no-color-output", "--show-error-codes", "--no-pretty",
                                                  *([] if names_files else ["."])),
                                 "text", "mypy's configuration"))
    # pyright / basedpyright
    if (folder / "pyrightconfig.json").is_file() or "[tool.pyright" in pyproject or "[tool.basedpyright" in pyproject:
        pyright = (python_tool(folder, "basedpyright", bound) if "[tool.basedpyright" in pyproject else None) \
            or python_tool(folder, "pyright", bound) or node_tool(folder, "pyright", bound)
        if pyright is None:
            missing.append(Missing("pyright", "pyright is configured, but not installed — pip install pyright "
                                              "(or npm install -D pyright)."))
        else:
            found.append(Checker("pyright", Path(pyright).name, (pyright, "--outputjson"), "pyright-json",
                                 "pyright's configuration"))
    # Go
    if (folder / "go.mod").is_file() or (folder / "go.work").is_file():
        go = shutil.which("go")
        if go is None:
            missing.append(Missing("go vet", "go.mod is here, but Go is not installed — see https://go.dev/dl."))
        else:
            found.append(Checker("go vet", "go vet", (go, "vet", "./..."), "text", "go.mod"))
    # Rust
    if (folder / "Cargo.toml").is_file():
        cargo = shutil.which("cargo") or next((str(p) for p in [Path.home() / ".cargo" / "bin" / "cargo"]
                                               if _executable(p)), None)
        if cargo is None:
            missing.append(Missing("cargo check", "Cargo.toml is here, but Rust is not installed — see "
                                                  "https://rustup.rs."))
        else:
            found.append(Checker("cargo check", "cargo check", (cargo, "check", "--message-format", "json"),
                                 "cargo-json", "Cargo.toml"))
    # Java and Kotlin: Gradle (the wrapper first), else Maven.
    gradle_file = next((n for n in ("build.gradle.kts", "build.gradle") if (folder / n).is_file()), None)
    if gradle_file:
        wrapper = folder / "gradlew"
        gradle = str(wrapper) if _executable(wrapper) else shutil.which("gradle")
        if gradle is None:
            missing.append(Missing("gradle", f"{gradle_file} is here, but neither ./gradlew nor Gradle is here."))
        else:
            found.append(Checker("gradle", "gradle classes", (gradle, "classes", "--console=plain", "-q"), "text",
                                 gradle_file))
    elif (folder / "pom.xml").is_file():
        wrapper = folder / "mvnw"
        mvn = str(wrapper) if _executable(wrapper) else shutil.which("mvn")
        if mvn is None:
            missing.append(Missing("maven", "pom.xml is here, but neither ./mvnw nor Maven is installed."))
        else:
            found.append(Checker("maven", "mvn compile", (mvn, "-q", "-B", "compile"), "text", "pom.xml"))
    # .NET
    dotnet_file = next(folder.glob("*.sln"), None) or next(folder.glob("*.csproj"), None) or \
        next(folder.glob("*.fsproj"), None)
    if dotnet_file is not None:
        dotnet = shutil.which("dotnet")
        if dotnet is None:
            missing.append(Missing("dotnet build", f"{dotnet_file.name} is here, but the .NET SDK is not installed."))
        else:
            found.append(Checker("dotnet build", "dotnet build",
                                 (dotnet, "build", dotnet_file.name, "-nologo", "-clp:NoSummary", "-v", "q"), "text",
                                 dotnet_file.name))
    return found, missing


# ── a check ───────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(at: datetime | None) -> str | None:
    return at.isoformat(timespec="seconds") if at else None


@dataclass(slots=True)
class ToolRun:
    checker: Checker
    status: str = "waiting"        # waiting · running · passed · problems · failed · timeout · cancelled · error
    exit: int | None = None
    ms: int | None = None
    problems: int = 0
    note: str = ""
    said: list[str] = field(default_factory=list)

    def json(self, cwd: Path) -> dict[str, Any]:
        return {**self.checker.json(cwd), "status": self.status, "exit": self.exit, "ms": self.ms,
                "problems": self.problems, "note": self.note, "output": self.said[-NOTE_LINES:]}


@dataclass(frozen=True, slots=True)
class Target:
    """Where a check runs: a project's source (`prefix` names its files the Workbench's way) or a folder."""

    kind: str                      # "project" | "folder"
    folder: Path
    name: str
    prefix: str = ""
    project_id: str | None = None
    source: str | None = None

    def json(self) -> dict[str, Any]:
        return {"kind": self.kind, "folder": str(self.folder), "name": self.name, "projectId": self.project_id,
                "source": self.source, "prefix": self.prefix}


class Check:
    """One press of "Check": the checkers of one folder, one after another, and what they found."""

    def __init__(self, *, owner: str, target: Target, checkers: list[Checker], missing: list[Missing]) -> None:
        self.id = f"chk-{uuid.uuid4().hex[:12]}"
        self.owner = owner
        self.target = target
        self.tools = [ToolRun(c) for c in checkers]
        self.missing = missing
        self.status = "running"
        self.started_at = _now()
        self.ended_at: datetime | None = None
        self.problems: list[Problem] = []
        self.total = 0
        self._cancelled = False
        self._proc: asyncio.subprocess.Process | None = None
        self.task: asyncio.Task[None] | None = None

    # ── running ──────────────────────────────────────────────────
    async def run(self) -> None:
        try:
            for tool in self.tools:
                if self._cancelled:
                    tool.status = "cancelled"
                    continue
                await self._one(tool)
            self.status = "cancelled" if self._cancelled else "done"
        except Exception as failed:                    # noqa: BLE001 — a check that breaks says so, never hangs
            self.status = "failed"
            for tool in self.tools:
                if tool.status in ("waiting", "running"):
                    tool.status, tool.note = "error", str(failed)[:300]
        finally:
            self.ended_at = _now()
            self._proc = None

    async def _one(self, tool: ToolRun) -> None:
        folder = self.target.folder
        tool.status = "running"
        started = time.monotonic()
        env = {**os.environ, "CI": "1", "NO_COLOR": "1", "FORCE_COLOR": "0", "TERM": "dumb"}
        try:
            proc = await asyncio.create_subprocess_exec(
                *tool.checker.argv, cwd=folder, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, env=env, start_new_session=True)
        except OSError as failed:
            tool.status = "error"
            tool.note = f"{tool.checker.argv[0]} could not be started: {failed.strerror or failed}"
            tool.ms = int((time.monotonic() - started) * 1000)
            return
        self._proc = proc
        out = bytearray()
        err: deque[str] = deque(maxlen=400)
        over = False

        async def read_out() -> None:
            nonlocal over
            assert proc.stdout is not None
            while chunk := await proc.stdout.read(65536):
                if len(out) + len(chunk) > OUTPUT_BYTES:
                    out.extend(chunk[:OUTPUT_BYTES - len(out)])
                    over = True
                    _kill(proc)
                    return
                out.extend(chunk)

        async def read_err() -> None:
            assert proc.stderr is not None
            size = 0
            while line := await proc.stderr.readline():
                size += len(line)
                if size <= OUTPUT_BYTES:
                    err.append(line.decode("utf-8", "replace").rstrip())

        timed_out = False
        try:
            await asyncio.wait_for(asyncio.gather(read_out(), read_err(), proc.wait()), timeout=TOOL_SECONDS)
        except TimeoutError:
            timed_out = True
            _kill(proc)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=5)
        tool.ms = int((time.monotonic() - started) * 1000)
        tool.exit = proc.returncode
        stdout = out.decode("utf-8", "replace")
        errors = "\n".join(err)
        if self._cancelled:
            tool.status, tool.note = "cancelled", "Stopped by you before it finished."
            return
        parser = PARSERS[tool.checker.parser]
        # A text tool may write its problems to either stream (go vet uses stderr); a JSON tool to stdout.
        found = parse_text(stdout + "\n" + errors, tool.checker.default) if parser is parse_text \
            else parser(stdout)
        placed, total = place(found, cwd=folder, base=folder, prefix=self.target.prefix,
                              source=self.target.source or self.target.name, tool=tool.checker.tool,
                              limit=max(0, MAX_PROBLEMS - len(self.problems)))
        self.problems.extend(placed)
        self.total += total
        tool.problems = total
        # What it said is kept only to explain a run that named no problem; the problems speak for themselves.
        tool.said = [] if total else [ln[:400] for ln in (errors or stdout).splitlines() if ln.strip()][-NOTE_LINES:]
        if timed_out:
            tool.status = "timeout"
            tool.note = f"Stopped after {int(TOOL_SECONDS)} seconds; what it printed before then is read."
        elif total:
            tool.status = "problems"
            if over:
                tool.note = f"It printed more than {OUTPUT_BYTES // (1024 * 1024)} MB; the rest was not read."
        elif proc.returncode == 0:
            tool.status = "passed"
            tool.said = []
        else:
            tool.status = "failed"
            tool.note = (f"Exited with code {proc.returncode} without naming a problem this reader recognises — "
                         "its last lines are below.")

    def cancel(self) -> None:
        if self.status != "running":
            return
        self._cancelled = True
        if self._proc is not None:
            _kill(self._proc)

    # ── reading ──────────────────────────────────────────────────
    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(SEVERITIES, 0)
        for p in self.problems:
            out[p.severity] = out.get(p.severity, 0) + 1
        return out

    def json(self, *, offset: int = 0, limit: int = PAGE_DEFAULT, severity: str | None = None,
             tool: str | None = None) -> dict[str, Any]:
        ended = self.ended_at
        shown = [p for p in self.problems if (severity is None or p.severity == severity)
                 and (tool is None or p.tool == tool)]
        # Errors first, then by file and position, so the first page is the one worth reading.
        rank = {s: i for i, s in enumerate(SEVERITIES)}
        shown.sort(key=lambda p: (rank.get(p.severity, 3), p.file, p.line, p.col))
        page = shown[offset:offset + limit]
        return {"id": self.id, "target": self.target.json(), "status": self.status,
                "startedAt": _stamp(self.started_at), "endedAt": _stamp(ended),
                "ms": int((ended - self.started_at).total_seconds() * 1000) if ended else None,
                "tools": [t.json(self.target.folder) for t in self.tools],
                "missing": [m.json() for m in self.missing],
                "counts": self.counts(), "total": self.total, "kept": len(self.problems),
                "capped": self.total > len(self.problems), "matching": len(shown),
                "offset": offset, "limit": limit, "problems": [p.json() for p in page]}

    def of_file(self, path: str) -> list[Problem]:
        return [p for p in self.problems if p.path == path]


def _kill(proc: asyncio.subprocess.Process) -> None:
    """The tool and everything it started (tsc's node, gradle's workers): its own process group."""
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        with contextlib.suppress(ProcessLookupError):
            proc.kill()


class Checks:
    """The checks this API holds, per person, like its terminals: in this process, closed with it."""

    def __init__(self) -> None:
        self._all: dict[str, Check] = {}

    def mine(self, owner: str) -> list[Check]:
        return sorted((c for c in self._all.values() if c.owner == owner), key=lambda c: c.started_at,
                      reverse=True)

    def get(self, check_id: str, owner: str) -> Check:
        from ..repositories import NotFound

        found = self._all.get(check_id)
        if found is None or found.owner != owner:
            raise NotFound(f"check {check_id}")
        return found

    def start(self, *, owner: str, target: Target, checkers: list[Checker], missing: list[Missing]) -> Check:
        running = [c for c in self.mine(owner) if c.status == "running"]
        if len(running) >= MAX_RUNNING:
            raise Refused(f"You have {MAX_RUNNING} checks running. Wait for one, or cancel it.", status=429)
        same = next((c for c in running if c.target.folder == target.folder), None)
        if same is not None:
            raise Refused(f"{target.name} is being checked already.", status=409)
        for old in [c for c in self.mine(owner) if c.status != "running"][KEEP_DONE:]:
            self._all.pop(old.id, None)
        check = Check(owner=owner, target=target, checkers=checkers, missing=missing)
        self._all[check.id] = check
        check.task = asyncio.get_running_loop().create_task(check.run())
        return check

    def latest(self, owner: str, *, folder: Path | None = None, project_id: str | None = None) -> list[Check]:
        """The newest check of each folder, newest first — of one folder, or of one project's sources."""
        out: dict[Path, Check] = {}
        for c in self.mine(owner):
            if folder is not None and c.target.folder != folder:
                continue
            if project_id is not None and c.target.project_id != project_id:
                continue
            out.setdefault(c.target.folder, c)
        return list(out.values())

    def of_file(self, owner: str, path: str) -> tuple[Check | None, list[Problem]]:
        """The problems the newest finished check holding this file found in it."""
        for c in self.mine(owner):
            if c.status == "running":
                continue
            real = Path(path)
            if real == c.target.folder or real.is_relative_to(c.target.folder):
                return c, c.of_file(path)
        return None, []

    async def close_all(self) -> None:
        for check in list(self._all.values()):
            check.cancel()
            if check.task is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(asyncio.shield(check.task), timeout=5)
        self._all.clear()


def run_problems(lines: Iterable[str], *, work: Path, prefix: str, source: str, tool: str,
                 limit: int = 200) -> tuple[list[dict[str, Any]], dict[str, int], int]:
    """A run's check step: the problems in what its command printed, named as the project names its
    files — relative to the worktree, with the source's label in front. Returns the problems (at most
    `limit`), counts by severity, and how many there were."""
    placed, total = place(parse_text(list(lines)), cwd=work, base=work, prefix=prefix, source=source, tool=tool,
                          limit=MAX_PROBLEMS)
    counts = dict.fromkeys(SEVERITIES, 0)
    for p in placed:
        counts[p.severity] = counts.get(p.severity, 0) + 1
    rank = {s: i for i, s in enumerate(SEVERITIES)}
    placed.sort(key=lambda p: (rank.get(p.severity, 3), p.file, p.line))
    # Inside the worktree only: a path outside it is the worktree's absolute path of nothing a person has.
    kept = [p.json() for p in placed if not os.path.isabs(p.file)][:limit]
    for p in kept:
        p.pop("path", None)
    return kept, counts, total
