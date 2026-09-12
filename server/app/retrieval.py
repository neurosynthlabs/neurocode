"""Retrieval: finding the few pieces of this workspace that actually bear on a question.

A **chunk** is the smallest piece worth retrieving on its own — a symbol with the lines that follow
it, a section of a document, a remembered fact. Chunks keep their text, so an answer can quote them
and say where they came from.

Two searches run over the same rows and are fused by rank:

- **lexical** — FTS5 over the text. No key, no model, no waiting. It finds what you can name.
- **semantic** — cosine over embeddings, when a lane makes them. It finds what you mean but did not
  name: "where is tax split" reaching `apply_gst_breakup`.

Fusing them by reciprocal rank means neither has to win outright, and retrieval keeps working with no
model at all — it simply says it is lexical only, instead of quietly becoming worse.
"""
from __future__ import annotations

import math
import re
import time
from array import array
from pathlib import Path
from typing import Any

from . import onboarding
from .context import Ctx
from .db import Store, _fts_query, now_iso

GLOBAL = "global"                 # memory belongs to the workspace, not to one project
CHUNK_LINES = 60                  # a symbol and what follows it, at most
MAX_CHARS = 2_000                 # what one chunk may hold
MAX_CHUNKS = 4_000                # per project, so even a large repository indexes in seconds
BATCH = 24                        # texts per embedding call
CANDIDATES = 60                   # how many each search hands to the fusion
RRF_K = 60                        # the constant in reciprocal rank fusion
DOCS = (".md", ".mdx", ".rst", ".txt", ".adoc")
SKIP_DIRS = {".git", "node_modules", "dist", "build", ".venv", "venv", "__pycache__", ".next", "target"}
HEADING = re.compile(r"^#{1,3} ", re.M)


# ── vectors, without a numerical library ─────────────────────────
def pack(values: list[float]) -> bytes:
    """A normalised vector as float32 bytes. Normalised on the way in, so searching is a dot product."""
    length = math.sqrt(sum(v * v for v in values)) or 1.0
    return array("f", [v / length for v in values]).tobytes()


def unpack(blob: bytes) -> array:
    out = array("f")
    out.frombytes(blob)
    return out


def dot(a: array, b: array) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))


# ── making chunks ────────────────────────────────────────────────
def _clip(text: str) -> str:
    return text.strip()[:MAX_CHARS]


def _code_chunks(store: Store, pid: str, root: Path) -> list[dict[str, Any]]:
    """One chunk per symbol — its own lines, in the file it lives in — plus the head of files that
    declare nothing. The index already knows where everything is; this reads the lines themselves."""
    out: list[dict[str, Any]] = []
    files = store.rows("SELECT id, path, lang FROM code_files WHERE project_id = ? ORDER BY path", (pid,))
    for f in files:
        source = root / f["path"]
        try:
            lines = source.read_text(errors="replace").splitlines()
        except OSError:
            continue
        symbols = store.rows("SELECT name, kind, line FROM code_symbols WHERE file_id = ? ORDER BY line", (f["id"],))
        starts = [s["line"] for s in symbols]
        if not symbols:
            head = "\n".join(lines[:CHUNK_LINES])
            if head.strip():
                out.append({"kind": "code", "ref": f"{f['path']}#file", "path": f["path"], "line": 1,
                            "title": f["path"], "text": _clip(f"{f['path']} ({f['lang']})\n{head}")})
            continue
        for i, s in enumerate(symbols):
            stop = min(starts[i + 1] - 1 if i + 1 < len(starts) else len(lines), s["line"] - 1 + CHUNK_LINES)
            body = "\n".join(lines[s["line"] - 1:stop])
            if not body.strip():
                continue
            out.append({"kind": "code", "ref": f"{f['path']}#{s['name']}:{s['line']}", "path": f["path"],
                        "line": s["line"], "title": f"{s['name']} · {s['kind']}",
                        "text": _clip(f"{f['path']}:{s['line']} · {s['kind']} {s['name']}\n{body}")})
        if len(out) >= MAX_CHUNKS:
            break
    return out[:MAX_CHUNKS]


def _doc_chunks(root: Path, excluded: list[str]) -> list[dict[str, Any]]:
    """The project's own writing: README, docs, notes — split at its headings."""
    out: list[dict[str, Any]] = []
    skip = SKIP_DIRS | {e.strip("/") for e in excluded if e}
    for path in sorted(root.rglob("*")):
        if len(out) >= MAX_CHUNKS // 4:
            break
        if path.suffix.lower() not in DOCS or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in skip for part in rel.split("/")[:-1]):
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        pieces = [p for p in HEADING.split(text) if p.strip()] if HEADING.search(text) else [text]
        for n, piece in enumerate(pieces[:24]):
            title = piece.strip().splitlines()[0][:80] if piece.strip() else rel
            out.append({"kind": "doc", "ref": f"{rel}#{n}", "path": rel, "line": 1,
                        "title": f"{rel} · {title}", "text": _clip(piece)})
    return out


def _memory_chunks(store: Store) -> list[dict[str, Any]]:
    facts = store.memory("", None, None, False)
    return [{"kind": "memory", "ref": f["ref"], "path": f.get("category", ""), "line": 0,
             "title": f["title"], "text": _clip(f"{f['title']}\n{f.get('body', '')}\n{f.get('reason', '')}")}
            for f in facts]


# ── keeping them ─────────────────────────────────────────────────
def _replace(store: Store, scope: str, chunks: list[dict[str, Any]]) -> None:
    """A scope's chunks are replaced in one transaction, so a reader never sees half an index."""
    with store.lock, store.conn as conn:
        ids = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE project_id = ?", (scope,))]
        conn.executemany("DELETE FROM chunk_fts WHERE chunk_id = ?", [(i,) for i in ids])
        conn.execute("DELETE FROM chunks WHERE project_id = ?", (scope,))
        at = now_iso()
        for ch in chunks:
            cur = conn.execute(
                "INSERT INTO chunks(project_id, kind, ref, path, title, line, text, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (scope, ch["kind"], ch["ref"], ch["path"], ch["title"], ch["line"], ch["text"], at))
            ch["id"] = cur.lastrowid
            conn.execute("INSERT INTO chunk_fts(title, text, path, project_id, chunk_id) VALUES (?, ?, ?, ?, ?)",
                         (ch["title"], ch["text"], ch["path"], scope, ch["id"]))


def _embed(c: Ctx, chunks: list[dict[str, Any]], project: str) -> tuple[int, str, str, str]:
    """Vectors for chunks that have none. Returns how many were embedded, the model, the lane, a note."""
    lane = c.gateway.embed_lane()
    if lane is None:
        return 0, "", "", "No lane makes embeddings, so search here is lexical only. Add a Gemini or Mistral key, or pull nomic-embed-text in Ollama."
    done = 0
    for start in range(0, len(chunks), BATCH):
        batch = chunks[start:start + BATCH]
        try:
            vectors, model, lane_id = c.gateway.embed([ch["text"] for ch in batch], project=project, lane=lane)
        except Exception as e:                       # a lane that stops answering must not lose the index
            return done, lane.embed, lane.id, f"{type(e).__name__} after {done} chunks — the rest stay lexical."
        if not vectors:
            break
        with c.store.lock, c.store.conn as conn:
            for ch, vector in zip(batch, vectors, strict=False):
                conn.execute("UPDATE chunks SET vector = ?, dim = ?, model = ? WHERE id = ?",
                             (pack(vector), len(vector), model, ch["id"]))
        done += len(batch)
    return done, lane.embed, lane.id, ""


def build(c: Ctx, pid: str) -> dict[str, Any]:
    """Chunk a project's code and documents, and the workspace's memory, then embed what can be
    embedded. Blocking: call it from a worker thread or a background task."""
    t0 = time.monotonic()
    project = c.store.get("projects", pid)
    if project is None:
        raise ValueError(f"project {pid} is gone")
    root = onboarding.source_root(project)
    chunks: list[dict[str, Any]] = []
    if root is not None and root.is_dir():
        chunks = [*_code_chunks(c.store, pid, root), *_doc_chunks(root, project.get("excluded", []))][:MAX_CHUNKS]
    _replace(c.store, pid, chunks)
    memory = _memory_chunks(c.store)
    _replace(c.store, GLOBAL, memory)
    embedded, model, lane, note = _embed(c, [*chunks, *memory], pid)
    ms = round((time.monotonic() - t0) * 1000)
    c.store.execute(
        "INSERT INTO retrieval_runs(project_id, finished_at, ms, chunks, embedded, model, lane, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(project_id) DO UPDATE SET finished_at = excluded.finished_at, "
        "ms = excluded.ms, chunks = excluded.chunks, embedded = excluded.embedded, model = excluded.model, "
        "lane = excluded.lane, note = excluded.note",
        (pid, now_iso(), ms, len(chunks) + len(memory), embedded, model, lane, note))
    return summary(c.store, pid)


def summary(store: Store, pid: str) -> dict[str, Any]:
    row = store.row("SELECT finished_at, ms, chunks, embedded, model, lane, note FROM retrieval_runs WHERE project_id = ?", (pid,))
    counts = {r[0]: r[1] for r in store.rows(
        "SELECT kind, COUNT(*) FROM chunks WHERE project_id IN (?, ?) GROUP BY kind", (pid, GLOBAL))}
    out: dict[str, Any] = {"built": row is not None, "chunks": sum(counts.values()), "byKind": counts,
                           "semantic": bool(row and row["embedded"])}
    if row is not None:
        out.update({"at": row["finished_at"], "ms": row["ms"], "embedded": row["embedded"],
                    "model": row["model"], "lane": row["lane"], "note": row["note"]})
    return out


# ── searching ────────────────────────────────────────────────────
def _lexical(store: Store, pid: str, q: str, limit: int) -> list[int]:
    match = _fts_query(q, "any") or _fts_query(q)
    if not match:
        return []
    try:
        rows = store.rows("SELECT chunk_id FROM chunk_fts WHERE chunk_fts MATCH ? AND project_id IN (?, ?) "
                          "ORDER BY rank LIMIT ?", (match, pid, GLOBAL, limit))
    except Exception:                                 # a query FTS5 cannot parse is not an error worth raising
        return []
    return [r[0] for r in rows]


def _semantic(c: Ctx, pid: str, q: str, limit: int) -> list[int]:
    if c.gateway.embed_lane() is None:
        return []
    try:
        vectors, _model, _lane = c.gateway.embed([q], project=pid)
    except Exception:
        return []
    if not vectors:
        return []
    query = unpack(pack(vectors[0]))
    rows = c.store.rows("SELECT id, vector FROM chunks WHERE project_id IN (?, ?) AND vector IS NOT NULL "
                        "LIMIT ?", (pid, GLOBAL, MAX_CHUNKS))
    scored = [(dot(query, unpack(r[1])), r[0]) for r in rows]
    scored.sort(reverse=True)
    return [cid for _score, cid in scored[:limit]]


def search(c: Ctx, pid: str, q: str, limit: int = 8) -> list[dict[str, Any]]:
    """The chunks that bear on this question, best first, each saying how it was found."""
    if not q.strip():
        return []
    lex = _lexical(c.store, pid, q, CANDIDATES)
    sem = _semantic(c, pid, q, CANDIDATES)
    scores: dict[int, float] = {}
    how: dict[int, set[str]] = {}
    for ids, name in ((lex, "lexical"), (sem, "semantic")):
        for rank, cid in enumerate(ids):
            scores[cid] = scores.get(cid, 0.0) + 1 / (RRF_K + rank + 1)
            how.setdefault(cid, set()).add(name)
    if not scores:
        return []
    best = sorted(scores, key=lambda cid: scores[cid], reverse=True)[:limit]
    rows = {r["id"]: r for r in c.store.rows(
        f"SELECT id, kind, ref, path, title, line, text FROM chunks WHERE id IN ({','.join('?' * len(best))})",
        tuple(best))}
    out = []
    for cid in best:
        r = rows.get(cid)
        if r is None:
            continue
        out.append({"ref": r["ref"], "kind": r["kind"], "path": r["path"], "title": r["title"], "line": r["line"],
                    "text": r["text"], "score": round(scores[cid], 5),
                    "how": "both" if len(how[cid]) > 1 else next(iter(how[cid]))})
    return out


def grounding(c: Ctx, pid: str, question: str, limit: int = 4) -> tuple[str, int]:
    """What this workspace already holds about a question, as text a model can be handed — with the
    places it came from, so an answer cites them instead of inventing them."""
    found = search(c, pid, question, limit)
    if not found:
        return "", 0
    pieces = [f"[{x['kind']} · {x['ref']}]\n{x['text'][:900]}" for x in found]
    return ("What this repository already holds about the question — quote these refs when you use them, "
            "and read the files if you need more:\n\n" + "\n\n".join(pieces)), len(found)
