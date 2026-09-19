"""The code index, in the shape the Code Intelligence screens read.

Almost everything here is a straight copy of a row. The two that are not are the ones the old index
kept on the file and let drift: how many files depend on it, and how many it depends on. They are
counted from the edges each time they are asked for, so a file that lost its last dependent stops
claiming to have one.
"""
from __future__ import annotations

from typing import Any

from .. import codeindex
from ..models import CodeFile, CodeIndexRun, CodeSymbol
from .work import when


def run_json(run: CodeIndexRun) -> dict[str, Any]:
    """The last index of a project: what it found, how long it took, and when it finished."""
    return {"files": run.files, "symbols": run.symbols, "edges": run.edges,
            "unresolved": run.unresolved, "ms": run.ms, "finishedAt": when(run.finished_at),
            "parsers": run.parsers or {}}


def symbol_json(symbol: CodeSymbol) -> dict[str, Any]:
    return {"name": symbol.name, "kind": symbol.kind, "line": symbol.line, "endLine": symbol.end_line,
            "exported": bool(symbol.exported)}


def tree_file_json(file: CodeFile, *, fan_in: int) -> dict[str, Any]:
    """One row of the file tree. The id is what the screen keys on, so it is handed out."""
    return {"id": file.id, "name": file.path.rsplit("/", 1)[-1], "path": file.path, "lang": file.lang,
            "lines": file.lines, "complexity": file.complexity, "fanIn": fan_in}


def file_json(file: CodeFile, *, fan_in: int, fan_out: int) -> dict[str, Any]:
    """The header of the file view. `test` is decided by the path, the way the indexer decides it."""
    return {"path": file.path, "lang": file.lang, "module": file.module, "lines": file.lines,
            "bytes": file.bytes, "complexity": file.complexity, "churn": file.churn,
            "changedAt": when(file.changed_at), "fanIn": fan_in, "fanOut": fan_out,
            "test": bool(codeindex.TEST_FILE.search(file.path))}
