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
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.git import git, repo_of
from ..ai.gateway import Gateway
from ..data.base import utcnow
from ..models import EMBED_DIM, Chunk, CodeFile, CodeSymbol, MemoryFact, Project, RetrievalRun
from ..repositories.retrieval import CANDIDATES, ChunkRepository, LEXICAL_COUNT_CAP, RRF_K
from ..repositories.words import terms
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
DELETE_BATCH = 2_000      # ids per DELETE when a build finds chunks nothing builds any more


@dataclass(slots=True)
class Replaced:
    """What bringing one scope's chunks up to what was just built came to.

    `fresh` is the only thing the embedding model ever sees: a chunk whose text did not change keeps
    the vector it already had, so a rebuild of a project nobody edited costs nothing at all.
    """

    total: int                              #: chunks the scope holds now
    fresh: list[Chunk] = field(default_factory=list)   #: new or changed, and therefore not embedded yet
    gone: int = 0                           #: deleted, because nothing built them this time
    embedded: int = 0                       #: left untouched, and already carrying a vector


# What links a document to the rest of the workspace, and nothing more: a name in backticks, a word
# shaped like code (camelCase or snake_case), and a reference like TASK-492. A word that matches none
# of the index is simply not shown — nothing here is guessed.
TICKED = re.compile(r"`([^`\n]{1,200})`")
CODE_WORD = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*(?:[a-z][A-Z]|_)[A-Za-z0-9_]*\b")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
REF = re.compile(r"\b[A-Z]+-\d+\b")
MAX_TOKENS = 400

#: Projects whose retrieval chunks are being built right now, in this process. The screen polls the
#: summary's `building` until it is false, the way the code summary reports `indexing` — a fixed timer
#: guessed, and a build longer than it showed the old count as if it were the new one.
BUILDING: set[str] = set()

#: A referenced project's pieces take at most 1/REFERENCE_SHARE of a search's slots.
REFERENCE_SHARE = 3

#: The relevance floor, below which a piece is not handed to a model as an answer to the question.
#:
#: Postgres scores one occurrence of one word at exactly 0.0607927, and `ts_rank` over an `a | b | c`
#: query returns the mean of each word's own rank (measured: `step` 0.08275, `run` 0.07599,
#: `step | run` 0.07937). So a piece's evidence is its rank times the number of words the question
#: was split into, and this floor states the rule in one line: **a single mention of a single word of
#: the question is not evidence about the question.** Measured against it, "how does the Kubernetes
#: operator reconcile a custom resource" over a billing document lands on 0.0608 exactly — one word,
#: once — and is floored, while every question here that its pieces really did answer scored three to
#: seven times the floor. Without it, one shared word — file, run, step, code — filled all four
#: grounding slots for a question this repository holds no answer to, under a heading telling the
#: model they were what the repository holds about it.
#:
#: The value is that single hit, 0.0607927106, rounded up to the next ten-thousandth, so a piece with
#: exactly one hit can never scrape over the line on a floating-point rounding error.
TS_RANK_FLOOR = 0.0608
#: The same idea for the half that finds by meaning: a nearest neighbour is still the nearest thing in
#: the index even when the index holds nothing near. This ceiling is NOT measured — no lane on this
#: machine makes embeddings, so there are no real distances to read — and it is deliberately far out,
#: where only an unrelated piece lands. Every search now records its `distance` in the trace, so the
#: first workspace with a lane can tighten this from its own numbers instead of from an opinion.
DISTANCE_CEILING = 0.75
#: How many hits one trace keeps, which is also the most a search returns (repositories.retrieval).
TRACE_HITS = 50


def label(piece: dict[str, Any]) -> str:
    """How a piece is named to a model: its kind and ref, and "reference" when it is one."""
    return " · ".join(x for x in (piece["kind"], piece["ref"], piece.get("reference")) if x)


def near_enough(piece: dict[str, Any], words: int) -> bool:
    """Whether one piece is close enough to the question to be handed to a model as an answer to it.

    Found both ways, it stays: the two halves agreeing is the strongest signal either can give. Found
    by words alone, its rank times `words` — the number of words the question was split into, because
    the rank is their mean — must clear the floor. Found by meaning alone, its cosine distance must be
    inside the ceiling. A piece carrying neither number is kept: a missing figure is not evidence
    against it, and inventing one to refuse with would be worse than keeping it.
    """
    if piece.get("how") == "both":
        return True
    if piece.get("how") == "semantic":
        distance = piece.get("distance")
        return distance is None or float(distance) <= DISTANCE_CEILING
    ts_rank = piece.get("tsRank")
    return ts_rank is None or float(ts_rank) * max(words, 1) > TS_RANK_FLOOR


def trace(q: str, pieces: list[dict[str, Any]], *, lane: str | None, ms: int, floored: int = 0,
          k: int | None = None) -> dict[str, Any]:
    """What one search did, in the shape every caller records it in.

    Which pieces answered a question was nowhere on record: the session wrote prose, a run wrote the
    text it handed on, and nothing kept the query, the ranks or the scores. So "were those the right
    eight" could not be asked of anything that had already happened, and no golden set could be
    harvested from real work. This goes into JSONB that already exists on the turn and on the step —
    no new table, and nothing here is guessed: every figure comes back out of the statement.
    """
    return {
        "q": q[:2_000], "k": k if k is not None else len(pieces), "lane": lane, "ms": ms,
        "floored": floored,
        "hits": [{"ref": x["ref"], "kind": x["kind"], "path": x["path"], "how": x["how"],
                  "score": x.get("score"), "tsRank": x.get("tsRank"), "distance": x.get("distance"),
                  "lexicalRank": (x.get("rank") or {}).get("lexical"),
                  "semanticRank": (x.get("rank") or {}).get("semantic")}
                 for x in pieces[:TRACE_HITS]],
    }


NO_LANE = ("No lane makes embeddings, so search here is by words only. Add a Cloudflare, Gemini or Mistral "
           "key — Cloudflare's free allowance is the largest of the three — or pull nomic-embed-text in Ollama.")


def _clip(text: str) -> str:
    return text.strip()[:MAX_CHARS]


def embedded_text(title: str, body: str) -> str:
    """What the embedding model is actually given for a chunk: its title, then its body.

    A document chunk's body is the bare section — its file path and its heading live only in the
    title, which the full-text vector reads and the embedding did not. So a question naming the
    document by name could be matched by the words and never by meaning, for a header that was
    already written, already true and free to send. A code chunk repeats a little of its own header
    here, which costs ten or twenty tokens and puts the symbol's name in the vector twice over.
    """
    head = title.strip()
    return f"{head}\n{body}" if head and not body.startswith(head) else body


def _pad(vector: list[float]) -> list[float]:
    """Every embedding is stored at one width, so one index covers them all. Zeros change nothing."""
    return list(vector[:EMBED_DIM]) + [0.0] * max(0, EMBED_DIM - len(vector))


def _read_code(root: Path, files: list[tuple[str, str, list[tuple[str, str, int]]]],
               prefix: str = "") -> list[dict[str, Any]]:
    """Blocking: opens files. Called in a thread, never on the event loop. The paths are the index's —
    a further source's carry its label — and `prefix` is that label, taken off to find the file in its
    own checkout."""
    out: list[dict[str, Any]] = []
    for path, lang, symbols in files:
        try:
            lines = (root / path.removeprefix(prefix)).read_text(errors="replace").splitlines()
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


def _read_docs(root: Path, excluded: list[str], prefix: str = "") -> list[dict[str, Any]]:
    """The project's own writing: README, docs, notes — split at its headings. Blocking. A further
    source's documents are named under its label (`prefix`), as its code is."""
    out: list[dict[str, Any]] = []
    for path in doc_files(root, excluded):
        if len(out) >= MAX_DOC_CHUNKS:
            break
        rel = prefix + path.relative_to(root).as_posix()
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
                 f"{' via ' + summary['lane'] if summary.get('lane') else ''}, padded to one width. Each "
                 "piece is embedded with its title — its file path and heading — not the section alone."
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
                               "byKind": counts, "semantic": bool(run and run.embedded),
                               "building": project_id in BUILDING}
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

    async def search(self, project_id: str, q: str, limit: int = 8, *,
                     references: bool = True) -> list[dict[str, Any]]:
        """The pieces that bear on a question: the project's own first, then — when it references other
        projects — the best of theirs in the slots kept for them.

        Everything read from somewhere agents may not write is labelled so, in `reference`: a piece of a
        reference source ("design · reference"), and a piece of a referenced project ("Payments ·
        reference"), whose `ref` and `path` also carry that project's id (`payments:app/charge.py`) —
        the prefix a session's tools read it back with. `references=False` is the project alone.
        """
        if not q.strip():
            return []
        vector = await self._embed_query(q, project_id)
        own = await self.chunks.search(project_id, q, vector=vector, limit=limit)
        if not references:
            return own
        return await self._with_references(project_id, q, vector, own, limit)

    async def _with_references(self, project_id: str, q: str, vector: list[float] | None,
                               own: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        from .code import reference_labels, roots
        from .references import referenced
        project = await self.projects.get(project_id)
        if project is None:
            return own
        labels = reference_labels(await roots(self.session, project))
        for piece in own:
            head, cut, _ = piece["path"].partition("/")
            if cut and head in labels and piece["kind"] in ("code", "doc"):
                piece["reference"] = f"{head} · reference"
        others = dict(await referenced(self.session, project_id))
        if not others:
            return own
        # A share of the slots, never all of them: the project's own pieces lead, and a referenced
        # project fills at most a third of what the caller asked for.
        share = max(1, limit // REFERENCE_SHARE)
        found = await self.chunks.search(project_id, q, vector=vector, limit=share, among=list(others))
        for piece in found:
            pid = piece["project"]
            piece["ref"], piece["path"] = f"{pid}:{piece['ref']}", f"{pid}:{piece['path']}"
            piece["reference"] = f"{others.get(pid, pid)} · reference"
        return own[:max(0, limit - len(found))] + found

    async def search_counted(self, project_id: str, q: str, limit: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """The search, and what each half had to work with: the chunks the words matched (to a cap), the
        neighbours the vector was compared against — none when no lane embedded the question — how many
        survived the fusion, how many of those a near-duplicate rule skipped, and how many of the ones
        shown would not be handed to a model because they are below the relevance floor.

        The floor does not cut this list. Someone typing in the search box is narrowing on purpose and
        wants to see what matched at all; the screen says which of the results grounding would refuse.
        """
        vector = await self._embed_query(q, project_id)
        # The search box: what a person typed there is a narrowing, so every word counts.
        found, dropped = await self.chunks.fused(project_id, q, vector=vector, limit=limit, mode="all")
        semantic = min(CANDIDATES, await self.chunks.embedded(project_id)) if vector is not None else 0
        # The count's ceiling and the fusion constant travel with the counts, so the screen reads them
        # instead of keeping its own copies that would go quietly wrong the day either changes.
        return found, {"lexical": await self.chunks.lexical_count(project_id, q, mode="all"), "semantic": semantic,
                       "fused": len(found), "lexicalCap": LEXICAL_COUNT_CAP, "k": RRF_K, "dropped": dropped,
                       "floored": sum(1 for x in found if not near_enough(x, len(terms(q))))}

    async def grounded(self, project_id: str, question: str,
                       limit: int = 4) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        """Grounding, and the trace of the search behind it, for a caller that records what answered.

        Nothing below the floor is handed on. Retrieval used to be the one feature here that never
        refused: `search` ORs every meaningful word of the question, reciprocal rank gives any single
        lexical hit a score, and so an unanswerable question came back with four pieces under a heading
        saying they were what the repository holds about it. Now the two numbers the statement already
        computed decide, and when nothing clears the floor the caller is told plainly that there is
        nothing — which costs about 900 tokens less than four pieces that answer nothing.
        """
        t0 = time.monotonic()
        found = await self.search(project_id, question, limit)
        near = [x for x in found if near_enough(x, len(terms(question)))]
        lane = self.gateway.embed_lane()
        record = trace(question, near, lane=lane.id if lane is not None else None,
                       ms=round((time.monotonic() - t0) * 1000), floored=len(found) - len(near), k=limit)
        if not near:
            return "", [], record
        return self._as_text(near), near, record

    async def grounding(self, project_id: str, question: str, limit: int = 4) -> tuple[str, list[dict[str, Any]]]:
        """What this workspace already holds about a question, as text a model can be handed — and the
        pieces themselves, so the caller can record which remembered facts it was handed."""
        ground, found, _record = await self.grounded(project_id, question, limit)
        return ground, found

    @staticmethod
    def _as_text(found: list[dict[str, Any]]) -> str:
        pieces = [f"[{label(x)}]\n{x['text'][:900]}" for x in found]
        read_only = ("\n\nA piece marked \"reference\" is read only: it comes from a reference source or from "
                     "another project this one reads from (its refs start with that project's id and a colon — "
                     "read its files with that prefix). Nothing there is changed from here."
                     if any(x.get("reference") for x in found) else "")
        return ("What this repository already holds about the question — quote these refs when you use "
                "them, and read the files if you need more:\n\n" + "\n\n".join(pieces) + read_only)

    # ── building ─────────────────────────────────────────────────
    async def _code_chunks(self, project: Project, sources: list[Any]) -> list[dict[str, Any]]:
        """A piece per symbol, read from whichever checkout holds the file."""
        from .code import split
        files = (await self.session.execute(
            select(CodeFile).where(CodeFile.project_id == project.id).order_by(CodeFile.path))).scalars()
        symbols_by_file: dict[int, list[tuple[str, str, int]]] = {}
        for name, kind, line, file_id in (await self.session.execute(
                select(CodeSymbol.name, CodeSymbol.kind, CodeSymbol.line, CodeSymbol.file_id)
                .where(CodeSymbol.project_id == project.id).order_by(CodeSymbol.line))).all():
            symbols_by_file.setdefault(file_id, []).append((name, kind, line))
        by_source: dict[str, tuple[Any, list[tuple[str, str, list[tuple[str, str, int]]]]]] = {}
        for f in files:
            hit = split(sources, f.path)
            if hit is not None:
                by_source.setdefault(hit[0].label, (hit[0], []))[1].append(
                    (f.path, f.lang, symbols_by_file.get(f.id, [])))

        def read() -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for source, spec in by_source.values():
                out += _read_code(source.root, spec, source.prefix)
            return out[:MAX_CHUNKS]

        return await asyncio.to_thread(read)

    async def _memory_chunks(self) -> list[dict[str, Any]]:
        facts = (await self.session.execute(
            select(MemoryFact).where(MemoryFact.archived.is_(False)))).scalars().unique()
        return [{"kind": "memory", "ref": f.ref, "path": f.category, "line": 0, "title": f.title,
                 "body": _clip(f"{f.title}\n{f.body}\n{f.reason}")} for f in facts]

    async def _replace(self, project_id: str | None, rows: list[dict[str, Any]]) -> Replaced:
        """Bring a scope's chunks to what was just built, inside the caller's transaction.

        It used to delete the scope and write every chunk again. That is the same answer, and it costs
        the whole of it every time: a file nobody touched had its chunk deleted, written again, entered
        again into the full-text index and again into the HNSW vector index, and — because a new row has
        no embedding — sent to the embedding model again and paid for again. The indexes never gave the
        space back either: eight rebuilds of the same three thousand chunks took `ix_chunks_embedding`
        from 2.4 MB to about 6.9 MB, where it stayed.

        So the built rows are compared with the stored ones by `(kind, ref)`, which is the pair the
        table is already unique on, and only what actually differs is written: what is gone is deleted,
        what is new is inserted, and a chunk whose text changed is updated and loses its embedding,
        because that embedding described text that is no longer there. A chunk that only moved — same
        text, a different line or title — is corrected without being re-embedded.
        """
        scope = Chunk.project_id == project_id if project_id else Chunk.project_id.is_(None)
        stored = {(c.kind, c.ref): c for c in (await self.session.execute(select(Chunk).where(scope))).scalars()}
        fresh: list[Chunk] = []
        seen: set[tuple[str, str]] = set()
        still_embedded = 0
        for row in rows:
            key = (row["kind"], row["ref"])
            if key in seen:
                continue                    # the same ref built twice: the table's own unique rule, kept early
            seen.add(key)
            held = stored.get(key)
            if held is None:
                made = Chunk(project_id=project_id, **row)
                self.session.add(made)
                fresh.append(made)
            elif embedded_text(held.title, held.body) != embedded_text(row["title"], row["body"]):
                # The title is embedded with the body, so a piece whose heading or path changed no
                # longer has a vector that describes what it says, even when its text is untouched.
                for field, value in row.items():
                    setattr(held, field, value)
                held.embedding, held.dim, held.model = None, 0, ""
                fresh.append(held)
            else:
                for field in ("path", "title", "line"):
                    if getattr(held, field) != row[field]:
                        setattr(held, field, row[field])
                if held.embedding is not None:
                    still_embedded += 1
        gone = [c.id for key, c in stored.items() if key not in seen]
        for start in range(0, len(gone), DELETE_BATCH):
            await self.session.execute(delete(Chunk).where(Chunk.id.in_(gone[start:start + DELETE_BATCH])))
        await self.session.flush()
        return Replaced(total=len(seen), fresh=fresh, gone=len(gone), embedded=still_embedded)

    async def _embed(self, chunks: list[Chunk], project_id: str) -> tuple[int, str, str, str]:
        lane = self.gateway.embed_lane()
        if lane is None:
            return 0, "", "", NO_LANE
        done = 0
        for start in range(0, len(chunks), BATCH):
            batch = chunks[start:start + BATCH]
            try:
                vectors, model, lane_id = await asyncio.to_thread(
                    self.gateway.embed, [embedded_text(c.title, c.body) for c in batch],
                    project=project_id, lane=lane)
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
        BUILDING.add(project_id)
        try:
            built = await self._build(project_id)
        finally:
            BUILDING.discard(project_id)
        return {**built, "building": project_id in BUILDING}

    async def _build(self, project_id: str) -> dict[str, Any]:
        t0 = time.monotonic()
        project = await self.projects.get(project_id)
        if project is None:
            raise ValueError(f"project {project_id} is gone")
        from .code import roots
        # Every source of the project on this machine: its code pieces, then its documents, each named
        # under its source's label — so a question about the API finds the API's handler even when it
        # was asked from the web app's side.
        sources = [x for x in await roots(self.session, project) if x.ready]
        here = [x for x in sources if await asyncio.to_thread(x.root.is_dir)]
        excluded = list(project.excluded or [])

        def docs() -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for source in here:
                out += _read_docs(source.root, excluded, source.prefix)
            return out[:MAX_DOC_CHUNKS]

        rows: list[dict[str, Any]] = []
        if here:
            rows = [*await self._code_chunks(project, here), *await asyncio.to_thread(docs)][:MAX_CHUNKS]
        made = await self._replace(project_id, rows)
        remembered = await self._replace(None, await self._memory_chunks())

        # Only what is new or has changed goes to the embedding model; what was already embedded and
        # still says the same thing is counted, not sent again.
        embedded, model, lane, note = await self._embed([*made.fresh, *remembered.fresh], project_id)
        embedded += made.embedded + remembered.embedded
        ms = round((time.monotonic() - t0) * 1000)
        run = await self.session.get(RetrievalRun, project_id) or RetrievalRun(project_id=project_id)
        # Set every time, not left to the column default: a default only fires on the first insert,
        # so a rebuilt index would keep telling the screen it was built days ago.
        run.finished_at = utcnow()
        run.ms, run.chunks, run.embedded = ms, made.total + remembered.total, embedded
        run.model, run.lane, run.note = model, lane, note
        self.session.add(run)
        await self.session.flush()
        return await self.summary(project_id)
