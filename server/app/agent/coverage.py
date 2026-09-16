"""Line coverage, read from the report a project's own test command wrote.

NeuroCode collects no coverage of its own: it would have to instrument someone else's code, in a
language it may not speak, with a tool the project never chose. What it can do honestly is read what
the project's runner already produced — Cobertura XML (coverage.py, most JVM and .NET tools), lcov
(c8, nyc, jest, vitest), istanbul's json-summary, and a Go cover profile — and sum it per top-level
directory, so a screen can say which part of the repository its tests actually execute.

Pure: text in, numbers out. Which files to read, and whether they are fresh, is the caller's decision.
Every path comes back relative to the project's root; anything that would land outside it is dropped.
"""
from __future__ import annotations

import json
import posixpath
from xml.etree import ElementTree

#: Where runners write their reports by default, and the reader each one needs.
REPORTS: tuple[tuple[str, str], ...] = (
    ("coverage.xml", "cobertura"),
    ("lcov.info", "lcov"),
    ("coverage/lcov.info", "lcov"),
    ("coverage/coverage-summary.json", "istanbul"),
    ("coverage.out", "go"),
)

#: path relative to the project root → (lines covered, lines measured)
FileLines = dict[str, tuple[int, int]]


def _inside(path: str, roots: tuple[str, ...]) -> str | None:
    """The path relative to the project root, or None when it points anywhere else."""
    path = path.replace("\\", "/")
    if path.startswith("/"):
        for root in roots:
            stem = root.rstrip("/") + "/"
            if root and path.startswith(stem):
                path = path[len(stem):]
                break
        else:
            return None
    clean = posixpath.normpath(path)
    if clean in ("", ".") or clean.startswith("../") or clean == ".." or clean.startswith("/"):
        return None
    return clean


def cobertura(text: str, roots: tuple[str, ...]) -> FileLines:
    """Cobertura XML. A file listed under several classes (inner classes do that) is counted once."""
    if "<!ENTITY" in text or "<!DOCTYPE" in text:
        return {}                                   # no report needs entities; refusing them is cheap
    try:
        tree = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return {}
    sources = [s.text.strip() for s in tree.iter("source") if s.text and s.text.strip()]
    hits: dict[str, dict[int, int]] = {}
    for cls in tree.iter("class"):
        filename = cls.get("filename") or ""
        candidates = [filename] if filename.startswith("/") or not sources else \
            [posixpath.join(source, filename) for source in sources]
        rel = next((found for c in candidates if (found := _inside(c, roots)) is not None), None)
        if rel is None:
            continue
        lines = hits.setdefault(rel, {})
        for line in cls.iter("line"):
            try:
                number, count = int(line.get("number", "")), int(line.get("hits", "0"))
            except ValueError:
                continue
            lines[number] = max(lines.get(number, 0), count)
    return {path: (sum(1 for c in lines.values() if c > 0), len(lines)) for path, lines in hits.items()}


def lcov(text: str, roots: tuple[str, ...]) -> FileLines:
    """lcov tracefiles: `SF:` opens a file, `LF`/`LH` total it, `DA` lines when the totals are absent."""
    out: FileLines = {}
    path: str | None = None
    found = hit = 0
    lines: dict[int, int] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            path, found, hit, lines = _inside(line[3:], roots), 0, 0, {}
        elif line.startswith("DA:"):
            parts = line[3:].split(",")
            if len(parts) >= 2 and parts[0].isdigit() and parts[1].lstrip("-").isdigit():
                lines[int(parts[0])] = max(lines.get(int(parts[0]), 0), int(parts[1]))
        elif line.startswith("LF:") and line[3:].isdigit():
            found = int(line[3:])
        elif line.startswith("LH:") and line[3:].isdigit():
            hit = int(line[3:])
        elif line == "end_of_record" and path is not None:
            if not found and lines:
                found, hit = len(lines), sum(1 for c in lines.values() if c > 0)
            if found and hit <= found:
                covered, total = out.get(path, (0, 0))
                out[path] = (covered + hit, total + found)
            path = None
    return out


def istanbul(text: str, roots: tuple[str, ...]) -> FileLines:
    """istanbul's coverage-summary.json: a `lines` total per file, plus a `total` key that is skipped."""
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    out: FileLines = {}
    for key, value in data.items():
        if key == "total" or not isinstance(value, dict):
            continue
        lines = value.get("lines")
        rel = _inside(key, roots)
        if rel is None or not isinstance(lines, dict):
            continue
        total, covered = lines.get("total"), lines.get("covered")
        if isinstance(total, int) and isinstance(covered, int) and 0 <= covered <= total:
            out[rel] = (covered, total)
    return out


def go_profile(text: str, module: str) -> FileLines:
    """A Go cover profile. Its paths are import paths, placed in the repository through the module path;
    its unit is the statement, which is what Go measures, and a block listed twice counts once."""
    blocks: dict[tuple[str, str], tuple[int, int]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("mode:"):
            continue
        where, _, rest = line.rpartition(":")
        parts = rest.split()
        if not where or len(parts) != 3 or not all(p.isdigit() for p in parts[1:]):
            continue
        file, block = where, parts[0]
        statements, count = int(parts[1]), int(parts[2])
        seen = blocks.get((file, block))
        blocks[(file, block)] = (statements, max(count, seen[1] if seen else 0))
    out: FileLines = {}
    for (file, _), (statements, count) in blocks.items():
        if module and file.startswith(module + "/"):
            rel = _inside(file[len(module) + 1:], ())
        else:
            rel = None if module else _inside(file, ())
        if rel is None:
            continue
        covered, total = out.get(rel, (0, 0))
        out[rel] = (covered + (statements if count > 0 else 0), total + statements)
    return out


def parse(kind: str, text: str, *, roots: tuple[str, ...], module: str = "") -> FileLines:
    if kind == "cobertura":
        return cobertura(text, roots)
    if kind == "lcov":
        return lcov(text, roots)
    if kind == "istanbul":
        return istanbul(text, roots)
    if kind == "go":
        return go_profile(text, module)
    return {}


def by_directory(files: dict[str, tuple[int, int, str]]) -> list[tuple[str, int, int, str]]:
    """Summed per top-level directory: (path, covered, total, source), '' for files at the root.

    Each file carries the report it came from; a directory takes the source of its first file.
    """
    out: dict[str, list[int | str]] = {}
    for path, (covered, total, source) in sorted(files.items()):
        top = path.split("/", 1)[0] if "/" in path else ""
        row = out.setdefault(top, [0, 0, source])
        row[0] = int(row[0]) + covered
        row[1] = int(row[1]) + total
    return [(path, int(c), int(t), str(s)) for path, (c, t, s) in sorted(out.items()) if int(t) > 0]
