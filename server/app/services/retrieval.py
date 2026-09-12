"""Retrieval: building the chunks, and finding the few that bear on a question.

Chunking is unchanged in spirit — a symbol with the lines that follow it, a section of a document,
a remembered fact — but everything after it is different. Vectors go into a `vector(1536)` column
behind an HNSW index instead of a blob, searching is one statement instead of a Python loop over
every row, and the whole build happens inside one transaction, so a reader never sees half an index.

Two rules keep it honest. A model that returns fewer numbers is **padded with zeros**, which changes
neither the dot product nor the norm, so one index can serve every lane. And with no embedding lane
at all, retrieval stays lexical and says so instead of quietly getting worse.
"""
from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import onboarding
from ..ai.gateway import Gateway
from ..data.base import utcnow
from ..models import EMBED_DIM, Chunk, CodeFile, CodeSymbol, MemoryFact, Project, RetrievalRun
from ..repositories.retrieval import ChunkRepository
from ..repositories.work import ProjectRepository

CHUNK_LINES = 60          # a symbol and what follows it, at most
MAX_CHARS = 2_000         # what one chunk may hold
MAX_CHUNKS = 4_000        # per project, so even a large repository indexes in seconds
BATCH = 24                # texts per embedding call
DOCS = (".md", ".mdx", ".rst", ".txt", ".adoc")
SKIP = {".git", "node_modules", "dist", "build", ".venv", "venv", "__pycache__", ".next", "target"}
HEADING = re.compile(r"^#{1,3} ", re.M)

NO_LANE = ("No lane makes embeddings, so search here is by words only. Add a Gemini or Mistral key, "
           "or pull nomic-embed-text in Ollama.")


def _clip(text: str) -> str:
    return text.strip()[:MAX_CHARS]


def _pad(vector: list[float]) -> list[float]:
    """Every embedding is stored at one width, so one index covers them all. Zeros change nothing."""
    return list(vector[:EMBED_DIM]) + [0.0] * max(0, EMBED_DIM - len(vector))


def _read_code(root: Path, files: list[tuple[str, str, list[tuple[str, str, int]]]]) -> list[dict[str, Any]]:
    """Blocking: opens files. Called in a thread, never on the event loop."""
    out: list[dict[str, Any]] = []
    for path, lang, symbols in files:
        try:
            lines = (root / path).read_text(errors="replace").splitlines()
        except OSError:
            continue
        if not symbols:
            head = "\n".join(lines[:CHUNK_LINES])
            if head.strip():
                out.append({"kind": "code", "ref": f"{path}#file", "path": path, "line": 1,
                            "title": path, "body": _clip(f"{path} ({lang})\n{head}")})
            continue
        starts = [line for _n, _k, line in symbols]
        for i, (name, kind, line) in enumerate(symbols):
            stop = min(starts[i + 1] - 1 if i + 1 < len(starts) else len(lines), line - 1 + CHUNK_LINES)
            body = "\n".join(lines[line - 1:stop])
            if body.strip():
                out.append({"kind": "code", "ref": f"{path}#{name}:{line}", "path": path, "line": line,
                            "title": f"{name} · {kind}",
                            "body": _clip(f"{path}:{line} · {kind} {name}\n{body}")})
        if len(out) >= MAX_CHUNKS:
            break
    return out[:MAX_CHUNKS]


def _read_docs(root: Path, excluded: list[str]) -> list[dict[str, Any]]:
    """The project's own writing: README, docs, notes — split at its headings. Blocking."""
    out: list[dict[str, Any]] = []
    skip = SKIP | {e.strip("/") for e in excluded if e}
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
                        "title": f"{rel} · {title}", "body": _clip(piece)})
    return out


class RetrievalService:
    def __init__(self, session: AsyncSession, gateway: Gateway) -> None:
        self.session = session
        self.gateway = gateway
        self.chunks = ChunkRepository(session)
        self.projects = ProjectRepository(session)

    # ── reading ──────────────────────────────────────────────────
    async def summary(self, project_id: str) -> dict[str, Any]:
        run = await self.session.get(RetrievalRun, project_id)
        counts = await self.chunks.counts(project_id)
        out: dict[str, Any] = {"built": run is not None, "chunks": sum(counts.values()),
                               "byKind": counts, "semantic": bool(run and run.embedded)}
        if run is not None:
            out.update({"at": run.finished_at.isoformat(timespec="seconds"), "ms": run.ms,
                        "embedded": run.embedded, "model": run.model, "lane": run.lane,
                        "note": run.note})
        return out

    async def _embed_query(self, q: str, project_id: str) -> list[float] | None:
        if self.gateway.embed_lane() is None:
            return None
        try:
            vectors, _model, _lane = await asyncio.to_thread(self.gateway.embed, [q], project=project_id)
        except Exception:            # a lane that stops answering must not stop the search
            return None
        return _pad(vectors[0]) if vectors else None

    async def search(self, project_id: str, q: str, limit: int = 8) -> list[dict[str, Any]]:
        if not q.strip():
            return []
        return await self.chunks.search(project_id, q, vector=await self._embed_query(q, project_id),
                                        limit=limit)

    async def grounding(self, project_id: str, question: str, limit: int = 4) -> tuple[str, int]:
        """What this workspace already holds about a question, as text a model can be handed."""
        found = await self.search(project_id, question, limit)
        if not found:
            return "", 0
        pieces = [f"[{x['kind']} · {x['ref']}]\n{x['text'][:900]}" for x in found]
        return ("What this repository already holds about the question — quote these refs when you use "
                "them, and read the files if you need more:\n\n" + "\n\n".join(pieces)), len(found)

    # ── building ─────────────────────────────────────────────────
    async def _code_chunks(self, project: Project, root: Path) -> list[dict[str, Any]]:
        files = (await self.session.execute(
            select(CodeFile).where(CodeFile.project_id == project.id).order_by(CodeFile.path))).scalars()
        symbols_by_file: dict[int, list[tuple[str, str, int]]] = {}
        for name, kind, line, file_id in (await self.session.execute(
                select(CodeSymbol.name, CodeSymbol.kind, CodeSymbol.line, CodeSymbol.file_id)
                .where(CodeSymbol.project_id == project.id).order_by(CodeSymbol.line))).all():
            symbols_by_file.setdefault(file_id, []).append((name, kind, line))
        spec = [(f.path, f.lang, symbols_by_file.get(f.id, [])) for f in files]
        return await asyncio.to_thread(_read_code, root, spec)

    async def _memory_chunks(self) -> list[dict[str, Any]]:
        facts = (await self.session.execute(
            select(MemoryFact).where(MemoryFact.archived.is_(False)))).scalars().unique()
        return [{"kind": "memory", "ref": f.ref, "path": f.category, "line": 0, "title": f.title,
                 "body": _clip(f"{f.title}\n{f.body}\n{f.reason}")} for f in facts]

    async def _replace(self, project_id: str | None, rows: list[dict[str, Any]]) -> list[Chunk]:
        """A scope's chunks are replaced inside the caller's transaction: no half-built index."""
        await self.session.execute(delete(Chunk).where(
            Chunk.project_id == project_id if project_id else Chunk.project_id.is_(None)))
        made = [Chunk(project_id=project_id, **row) for row in rows]
        self.session.add_all(made)
        await self.session.flush()
        return made

    async def _embed(self, chunks: list[Chunk], project_id: str) -> tuple[int, str, str, str]:
        lane = self.gateway.embed_lane()
        if lane is None:
            return 0, "", "", NO_LANE
        done = 0
        for start in range(0, len(chunks), BATCH):
            batch = chunks[start:start + BATCH]
            try:
                vectors, model, lane_id = await asyncio.to_thread(
                    self.gateway.embed, [c.body for c in batch], project=project_id, lane=lane)
            except Exception as e:   # a lane that stops answering must not lose the index
                return done, lane.embed, lane.id, f"{type(e).__name__} after {done} chunks — the rest stay lexical."
            if not vectors:
                break
            for chunk, vector in zip(batch, vectors, strict=False):
                chunk.embedding, chunk.dim, chunk.model = _pad(vector), len(vector), model
            await self.session.flush()
            done += len(batch)
        return done, lane.embed, lane.id, ""

    async def build(self, project_id: str) -> dict[str, Any]:
        """Chunk a project's code and documents, and the workspace's memory, then embed what it can."""
        t0 = time.monotonic()
        project = await self.projects.get(project_id)
        if project is None:
            raise ValueError(f"project {project_id} is gone")
        root = await asyncio.to_thread(onboarding.source_root, {
            "id": project.id, "source": {"kind": project.source_kind, "repo": project.source_repo}
            if project.source_kind else None})

        rows: list[dict[str, Any]] = []
        if root is not None and await asyncio.to_thread(root.is_dir):
            rows = [*await self._code_chunks(project, root),
                    *await asyncio.to_thread(_read_docs, root, project.excluded or [])][:MAX_CHUNKS]
        made = await self._replace(project_id, rows)
        remembered = await self._replace(None, await self._memory_chunks())

        embedded, model, lane, note = await self._embed([*made, *remembered], project_id)
        ms = round((time.monotonic() - t0) * 1000)
        run = await self.session.get(RetrievalRun, project_id) or RetrievalRun(project_id=project_id)
        # Set every time, not left to the column default: a default only fires on the first insert,
        # so a rebuilt index would keep telling the screen it was built days ago.
        run.finished_at = utcnow()
        run.ms, run.chunks, run.embedded = ms, len(made) + len(remembered), embedded
        run.model, run.lane, run.note = model, lane, note
        self.session.add(run)
        await self.session.flush()
        return await self.summary(project_id)
