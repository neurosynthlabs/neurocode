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
import logging
import os
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import onboarding
from ..agent.git import git, repo_of
from ..ai.gateway import Gateway
from ..data.base import utcnow
from ..models import EMBED_DIM, Chunk, CodeFile, CodeSymbol, MemoryFact, Project, RetrievalRun
from ..repositories.retrieval import CANDIDATES, ChunkRepository, LEXICAL_COUNT_CAP, RRF_K
from ..repositories.work import ProjectRepository

log = logging.getLogger(__name__)

CHUNK_LINES = 60          # a symbol and what follows it, at most
MAX_CHARS = 2_000         # what one chunk may hold
MAX_CHUNKS = 4_000        # per project, so even a large repository indexes in seconds
BATCH = 24                # texts per embedding call
DOCS = (".md", ".mdx", ".rst", ".txt", ".adoc")
SKIP = {".git", "node_modules", "dist", "build", ".venv", "venv", "__pycache__", ".next", "target"}
HEADING = re.compile(r"^#{1,3} ", re.M)
SECTIONS = 24             # pieces one document may be split into
MAX_DOC_CHUNKS = MAX_CHUNKS // 4
MAX_DOC_FILES = 2_000     # documents the Knowledge screen walks the checkout for
HISTORY_COMMITS = 1_000   # how far back one `git log` pass looks for a document's last commit
GIT_TIMEOUT = 20

# What links a document to the rest of the workspace, and nothing more: a name in backticks, a word
# shaped like code (camelCase or snake_case), and a reference like TASK-492. A word that matches none
# of the index is simply not shown — nothing here is guessed.
TICKED = re.compile(r"`([^`\n]{1,200})`")
CODE_WORD = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:[a-z][A-Z]|_)[A-Za-z0-9_]*\b")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REF = re.compile(r"\b[A-Z]+-\d+\b")
MAX_TOKENS = 400

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


def doc_files(root: Path, excluded: list[str]) -> list[Path]:
    """Every document the chunker reads, in the order it reads them. Blocking.

    A folder named in SKIP or in the project's exclusions is never entered, at any depth — the same
    rule as testing each parent folder's name, without walking node_modules to throw it away.
    """
    skip = SKIP | {e.strip("/") for e in excluded if e}
    found: list[Path] = []
    for folder, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip]
        found += [Path(folder) / name for name in names if Path(name).suffix.lower() in DOCS]
    return sorted(p for p in found if p.is_file())


def _read_docs(root: Path, excluded: list[str]) -> list[dict[str, Any]]:
    """The project's own writing: README, docs, notes — split at its headings. Blocking."""
    out: list[dict[str, Any]] = []
    for path in doc_files(root, excluded):
        if len(out) >= MAX_DOC_CHUNKS:
            break
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        pieces = [p for p in HEADING.split(text) if p.strip()] if HEADING.search(text) else [text]
        for n, piece in enumerate(pieces[:SECTIONS]):
            title = piece.strip().splitlines()[0][:80] if piece.strip() else rel
            out.append({"kind": "doc", "ref": f"{rel}#{n}", "path": rel, "line": 1,
                        "title": f"{rel} · {title}", "body": _clip(piece)})
    return out


# ── documents, as the Knowledge screen reads them ────────────────
def doc_title(title: str, path: str) -> str:
    """A piece is titled "<path> · <its first line>". The document is called by that line, or its file name."""
    lead = f"{path} · "
    return (title[len(lead):] if title.startswith(lead) else "") or path.rsplit("/", 1)[-1]


def doc_summary(head: str, title: str, path: str) -> str:
    """The document's own first paragraph, never a written one.

    The chunker splits at headings and drops the `#` markers, so a piece starts with its heading's bare
    text — which is also what its title holds. That first line is skipped when it is the title, or every
    document that opens with `# Title` would summarise itself as its title again.
    """
    lines = head.strip().splitlines()
    lead = f"{path} · "
    if lines and title.startswith(lead) and lines[0][:80] == title[len(lead):]:
        lines = lines[1:]
    for paragraph in re.split(r"\n\s*\n", "\n".join(lines)):
        text = " ".join(line.strip() for line in paragraph.splitlines()).strip()
        if text:
            return text[:280]
    return ""


def entity_tokens(text: str, cap: int = MAX_TOKENS) -> tuple[list[str], list[str], list[str]]:
    """Names that might be symbols, backticked strings that might be paths, and references. Deduplicated."""
    ticked = [t.strip() for t in TICKED.findall(text)]
    names = [t for t in ticked if IDENTIFIER.match(t)] + CODE_WORD.findall(text)
    return (list(dict.fromkeys(n for n in names if len(n) >= 4))[:cap],
            list(dict.fromkeys(t for t in ticked if "/" in t or "." in t))[:cap],
            list(dict.fromkeys(REF.findall(text)))[:cap])


def pipeline(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """The stages a document really goes through, written from the constants that govern them."""
    embedding = (f"In batches of {BATCH} by {summary.get('model') or 'the embedding model'}"
                 f"{' via ' + summary['lane'] if summary.get('lane') else ''}, padded to one width."
                 if summary.get("semantic") else "No embedding lane, so documents are found by their words only.")
    return [
        {"n": 1, "step": "Onboarding scans the checkout",
         "detail": "The files are read where they are on this machine. Nothing is uploaded."},
        {"n": 2, "step": "Split at headings",
         "detail": f"Files ending {', '.join(DOCS)} are split at #, ## and ### headings: up to {SECTIONS} "
                   f"sections a file, {MAX_CHARS:,} characters each."},
        {"n": 3, "step": "Code first, then documents",
         "detail": f"At most {MAX_DOC_CHUNKS:,} document pieces, inside {MAX_CHUNKS:,} pieces in all, with "
                   "code placed first. A document past either cap is listed here as awaiting index."},
        {"n": 4, "step": "Indexed by words", "detail": "Postgres generates a full-text vector for every piece."},
        {"n": 5, "step": "Embedded by meaning", "detail": embedding},
        {"n": 6, "step": "Rebuilt on every re-index",
         "detail": "The project's pieces are replaced in one transaction, so a search never sees half an index."},
    ]


def read_docs_on_disk(root: Path, excluded: list[str],
                      only: list[str] | None = None) -> tuple[dict[str, tuple[int, float]], dict[str, tuple[str, str]], set[str]]:
    """Blocking. Size and modified time of each document, its last commit, and which ones git tracks.

    The last commits come from one `git log` pass over the project, not one call per file. A document
    older than that pass is still known to be committed, through `ls-files`, and is said to be so.
    """
    if only is None:
        files = {p.relative_to(root).as_posix(): p for p in doc_files(root, excluded)[:MAX_DOC_FILES]}
    else:
        files = {rel: root / rel for rel in only if (root / rel).is_file()}
    disk: dict[str, tuple[int, float]] = {}
    for rel, path in files.items():
        try:
            stat = path.stat()
        except OSError:
            continue
        disk[rel] = (stat.st_size, stat.st_mtime)

    found = repo_of(root)
    if found is None or not disk:
        return disk, {}, set()
    repo, prefix = found
    lead = f"{prefix}/" if prefix else ""
    specs = [f"{lead}{rel}" for rel in only] if only else [prefix or "."]
    commits: dict[str, tuple[str, str]] = {}
    tracked: set[str] = set()
    try:
        logged = git(["--no-optional-locks", "-c", "core.quotePath=false", "log", "-n", str(HISTORY_COMMITS),
                      "--no-renames", "--format=%x1e%an%x1f%cI", "--name-only", "--", *specs], repo,
                     timeout=GIT_TIMEOUT).stdout
        for record in logged.split("\x1e"):
            header, _, names = record.partition("\n")
            author, _, at = header.partition("\x1f")
            for name in names.splitlines():
                rel = name[len(lead):] if name.startswith(lead) else ""
                if rel in disk and rel not in commits:
                    commits[rel] = (author, at)
        listed = git(["--no-optional-locks", "-c", "core.quotePath=false", "ls-files", "-z", "--", *specs], repo,
                     timeout=GIT_TIMEOUT).stdout
        tracked = {name[len(lead):] for name in listed.split("\0") if name.startswith(lead)}
    except (OSError, subprocess.SubprocessError) as failed:
        log.warning("git history for the documents in %s did not answer: %s", root, failed)
    return disk, commits, tracked


def doc_json(path: str, *, chunks: int, embedded: int, title: str, head: str, project_id: str,
             disk: dict[str, tuple[int, float]], commits: dict[str, tuple[str, str]], tracked: set[str],
             disk_known: bool, built_at: datetime | None, indexed_at: str | None) -> dict[str, Any]:
    """One document as the Knowledge list shows it. Where it came from is git's answer, or the disk's."""
    size, mtime = disk.get(path, (0, 0.0))
    modified = datetime.fromtimestamp(mtime, UTC).isoformat(timespec="seconds") if path in disk else None
    if path in commits:
        source, added = f"repository file · last commit by {commits[path][0]}", commits[path][1]
    elif path in disk and path in tracked:
        source, added = f"repository file · committed before the newest {HISTORY_COMMITS:,} commits", modified
    elif path in disk:
        source, added = "file on disk, not committed", modified
    elif disk_known:
        source, added = "no longer on disk", indexed_at
    else:
        source, added = "indexed from the repository", indexed_at
    gone = disk_known and path not in disk
    changed = bool(built_at and path in disk and mtime > built_at.timestamp())
    return {"id": path, "ref": path, "kind": Path(path).suffix.lstrip(".").lower(), "title": doc_title(title, path),
            "projectId": project_id, "source": source, "addedAt": added or indexed_at, "bytes": size,
            "chunks": chunks, "embedded": embedded, "indexed": chunks > 0, "stale": chunks > 0 and (gone or changed),
            "summary": doc_summary(head, title, path), "entities": [], "linkedTo": [], "sections": []}


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

    async def search_counted(self, project_id: str, q: str, limit: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """The search, and what each half had to work with: the chunks the words matched (to a cap), the
        neighbours the vector was compared against — none when no lane embedded the question — and how
        many survived the fusion."""
        vector = await self._embed_query(q, project_id)
        # The search box: what a person typed there is a narrowing, so every word counts.
        found = await self.chunks.search(project_id, q, vector=vector, limit=limit, mode="all")
        semantic = min(CANDIDATES, await self.chunks.embedded(project_id)) if vector is not None else 0
        # The count's ceiling and the fusion constant travel with the counts, so the screen reads them
        # instead of keeping its own copies that would go quietly wrong the day either changes.
        return found, {"lexical": await self.chunks.lexical_count(project_id, q, mode="all"), "semantic": semantic,
                       "fused": len(found), "lexicalCap": LEXICAL_COUNT_CAP, "k": RRF_K}

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
