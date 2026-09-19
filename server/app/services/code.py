"""The code index as the screens ask for it, and the two slow jobs that rebuild it.

Nothing here reads a source file to work out what it means — `codeindex` did that when the index was
built, and `indexing` wrote what it found into Postgres. This is the layer above: it asks the
database the questions the Code Intelligence screens ask, and it assembles the answers.

Two things are said plainly rather than guessed. A project that was never indexed answers `indexed:
false` with the two flags the screen needs to decide what to offer — never an error, because "we have
not read this code yet" is an ordinary state. And the file view reads the checkout through the same
path check the agent runtime writes with, so a path that tries to leave is refused outright rather
than quietly rewritten.
"""
from __future__ import annotations

import asyncio
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .. import codeindex, onboarding
from ..agent.git import Refused as PathRefused
from ..agent.git import safe_path
from ..ai.gateway import Gateway
from ..data import roster
from ..data.engine import Database
from ..models import Project, ProjectSource
from ..repositories.base import NotFound
from ..repositories.code import CodeEdgeRepository, CodeFileRepository, CodeIndexRepository, MAX_ENTRIES
from ..repositories.retrieval import ChunkRepository, MAX_DOCS, MAX_DOC_PATHS
from ..repositories.sources import ProjectSourceRepository
from ..repositories.work import ActivityRepository, ProjectRepository
from ..schemas.code import file_json, run_json, symbol_json, tree_file_json
from .errors import Refused
from .indexing import INDEXING, build_project_index
from .retrieval import (
    DOCS,
    MAX_DOC_CHUNKS,
    RetrievalService,
    doc_json,
    doc_title,
    entity_tokens,
    pipeline,
    read_docs_on_disk,
)

#: How much of a file the file view hands back, and how many modules the graph draws.
MAX_TEXT = 400_000
GRAPH_MODULES = 36
TOP_OBJECTS = 12
DEPENDS_KINDS = ("imports", "uses")
DATA_KINDS = ("reads", "writes", "calls")


#: A source's label: what it is called inside the project and the folder its files appear under there.
LABEL = re.compile(r"^[a-z0-9][a-z0-9._-]{0,59}$")


@dataclass(frozen=True, slots=True)
class Source:
    """One checkout that belongs to a project, where it is on this machine.

    The first source is the project's own `source_*` columns: its paths inside the project are plain,
    as they always were, and its label is the project's id. Every further source is a `project_sources`
    row, and its paths inside the project carry its label — `api/app/main.py` is `app/main.py` in the
    `api` checkout. `ready` is false while a further source is still being cloned, or failed to be: its
    paths are known to be its own, but there is nothing on disk to read yet.
    """

    label: str
    root: Path
    kind: str
    primary: bool
    id: int | None = None
    ready: bool = True

    @property
    def prefix(self) -> str:
        return "" if self.primary else f"{self.label}/"


def checkout(project: Project) -> Path | None:
    """Where this project's first source is on this machine, or None for a project that was never
    onboarded from here. `roots` adds the rest; this is the one place the first one is worked out."""
    return onboarding.source_root({"id": project.id, "source": {
        "kind": project.source_kind, "repo": project.source_repo}} if project.source_kind else {})


def source_root(source: ProjectSource) -> Path | None:
    """Where one further source is on this machine."""
    return onboarding.source_path(source.project_id, source.id, source.kind, source.repo)


def sources_from(project: Project, rows: list[ProjectSource]) -> list[Source]:
    """The project's checkouts from its row and its source rows, first source first. Pure: no disk."""
    out: list[Source] = []
    first = checkout(project)
    if first is not None:
        out.append(Source(label=project.id, root=first, kind=project.source_kind or "local", primary=True))
    for row in rows:
        where = source_root(row)
        if where is not None:
            out.append(Source(label=row.label, root=where, kind=row.kind, primary=False, id=row.id,
                              ready=row.status == "active"))
    return out


async def roots(session: AsyncSession, project: Project) -> list[Source]:
    """Every checkout of this project on this machine, the first source first, then the rest in the
    order a person arranged them. Sources still onboarding are listed with `ready` false."""
    return sources_from(project, await ProjectSourceRepository(session).of(project.id))


def split(sources: list[Source], path: str) -> tuple[Source, str] | None:
    """Which source a project path belongs to, and the path inside that checkout.

    A path whose first folder is a further source's label is that source's; anything else is the first
    source's. None when that source is not on this machine — or is not ready to be read.
    """
    head, _, rest = path.strip().replace("\\", "/").partition("/")
    for source in sources:
        if not source.primary and source.label == head:
            return (source, rest) if source.ready else None
    first = next((x for x in sources if x.primary), None)
    return (first, path.strip().replace("\\", "/")) if first is not None else None


def resolve_in(sources: list[Source], path: str) -> Path | None:
    """A project path as a file on this machine — refused when it tries to leave its checkout, through
    `..`, an absolute path, `.git` or a symlink that points outside. Blocking: it follows links."""
    _inside(path)
    hit = split(sources, path)
    if hit is None:
        return None
    source, rel = hit
    if not rel:
        return source.root
    target = source.root / safe_path(rel)
    real, home = os.path.realpath(target), os.path.realpath(source.root)
    if real != home and not real.startswith(home + os.sep):
        raise Refused(f"{path} is outside this project.", status=403)
    return target


async def locate(session: AsyncSession, project: Project, path: str) -> Path | None:
    """A project path — label-prefixed for a further source, plain for the first — to an absolute path
    inside the right checkout, refusing an escape. None when that checkout is not on this machine."""
    sources = await roots(session, project)
    return await asyncio.to_thread(resolve_in, sources, path)


def _inside(path: str) -> Path:
    """The checkout is the boundary for reading too, so the runtime's own check is the one used."""
    try:
        return safe_path(path)
    except PathRefused as escaped:
        raise Refused(f"{path} is outside this project.", status=403) from escaped


def _reason(error: Exception) -> str:
    """What to tell the operator when a job fails — credentials stripped, never a stack trace."""
    return onboarding.redact(str(error))[:200] or type(error).__name__


class CodeService:
    """Every read the code screens do. It writes nothing; re-indexing is the job below."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.index = CodeIndexRepository(session)
        self.files = CodeFileRepository(session)
        self.edges = CodeEdgeRepository(session)

    async def project(self, project_id: str) -> Project:
        found = await self.projects.get(project_id)
        if found is None:
            raise NotFound(f"project {project_id}")
        return found

    # ── the index summary ────────────────────────────────────────
    async def summary(self, project_id: str) -> dict[str, Any]:
        """What the index holds. A project nobody has indexed answers honestly, not with an error."""
        project = await self.project(project_id)
        state = {"indexing": project_id in INDEXING,
                 "canIndex": any(x.ready for x in await roots(self.session, project))}
        run = await self.index.get(project_id)
        if run is None:
            return {"indexed": False, **state}
        return {"indexed": True, "run": run_json(run),
                "languages": await self.index.languages(project_id),
                "modules": await self._modules(project_id),
                "hotspots": [{"path": path, "lines": lines, "complexity": complexity, "churn": churn,
                              "fanIn": reached, "risk": codeindex.risk_of(reached, complexity)}
                             for path, lines, complexity, churn, reached
                             in await self.index.hotspots(project_id)],
                "database": await self._database(project_id), **state}

    async def _modules(self, project_id: str) -> list[dict[str, Any]]:
        symbols = await self.index.symbols_per_module(project_id)
        modules = {name: {"name": name, "files": files, "lines": lines, "complexity": complexity,
                          "symbols": symbols.get(name, 0), "fanIn": 0, "fanOut": 0}
                   for name, files, lines, complexity in await self.index.module_sizes(project_id)}
        for a, b, _weight in await self.index.module_edges(project_id):
            if a in modules:
                modules[a]["fanOut"] += 1
            if b in modules:
                modules[b]["fanIn"] += 1
        return sorted(modules.values(), key=lambda m: -m["lines"])

    async def _database(self, project_id: str) -> dict[str, Any]:
        """The database objects this repository declares, busiest first."""
        usage = await self.index.object_users(project_id)
        objects, seen = [], set()
        for name, kind, path in await self.index.declared_objects(project_id):
            if name.lower() in seen:
                continue
            seen.add(name.lower())
            used = usage.get(name.lower(), {})
            objects.append({"name": name, "kind": kind, "path": path,
                            "readers": used.get("reads", 0), "writers": used.get("writes", 0),
                            "callers": used.get("calls", 0)})
        objects.sort(key=lambda o: (-(o["readers"] + o["writers"] + o["callers"]), o["name"]))
        # Counted by the database, not by measuring the capped list this list came from: on a large
        # T-SQL repository that would have printed 500 objects however many there were.
        return {"objects": await self.index.declared_object_count(project_id),
                "top": objects[:TOP_OBJECTS]}

    # ── the tree, and one file ───────────────────────────────────
    async def tree(self, project_id: str, directory: str = "") -> dict[str, Any]:
        """One level of the file tree: its folders, with everything beneath them counted, and its files."""
        await self.project(project_id)
        prefix = f"{directory.strip('/')}/" if directory.strip("/") else ""
        found = await self.files.in_folder(project_id, prefix)
        # The repository asks for one more than the ceiling so that "there are exactly this many" and
        # "there are more than we will show" can be told apart, and the screen can say which it is.
        truncated = len(found) > MAX_ENTRIES
        found = found[:MAX_ENTRIES]
        fan = await self.files.fan_in([f.id for f in found])
        return {"dir": prefix.rstrip("/"),
                "dirs": await self.files.folders(project_id, prefix),
                "files": [tree_file_json(f, fan_in=fan.get(f.id, 0)) for f in found],
                "truncated": truncated}

    async def search(self, project_id: str, q: str) -> list[dict[str, Any]]:
        await self.project(project_id)
        return await self.files.search(project_id, q)

    async def file(self, project_id: str, path: str, *, source: bool = False) -> dict[str, Any]:
        """One file: what it declares, what it reaches, what reaches it — and the source itself."""
        project = await self.project(project_id)
        relative = _inside(path)
        found = await self.files.by_path(project_id, path)
        if found is None:
            raise NotFound(path)

        reaches = await self.files.depends_on(found.id)
        depends = [{"path": to_path, "target": target, "kind": kind}
                   for target, kind, to_path in reaches if kind in DEPENDS_KINDS]
        used: dict[str, dict[str, Any]] = {}
        for source, kind, target in await self.files.used_by(found.id):
            entry = used.setdefault(source, {"path": source, "kinds": [], "targets": []})
            if kind not in entry["kinds"]:
                entry["kinds"].append(kind)
            if target not in entry["targets"]:
                entry["targets"].append(target)

        text, truncated = await self._source(project, relative.as_posix()) if source else (None, False)
        return {
            "file": file_json(found, fan_in=len(used),
                              fan_out=len({d["path"] for d in depends if d["path"]})),
            "symbols": [symbol_json(s) for s in await self.files.symbols(found.id)],
            "dependsOn": depends,
            "database": [{"object": target, "kind": kind, "path": to_path}
                         for target, kind, to_path in reaches if kind in DATA_KINDS],
            "usedBy": list(used.values()),
            "impact": await self._impact(project_id, path=path),
            "text": text, "truncated": truncated,
        }

    async def _source(self, project: Project, relative: str) -> tuple[str | None, bool]:
        """The file as it is on disk right now, from whichever source holds it. None when that checkout
        is not on this machine."""
        target = await locate(self.session, project, relative)
        if target is None:
            return None, False

        def read() -> tuple[str | None, bool]:
            if not target.is_file():
                return None, False
            body = target.read_text(errors="replace")
            return body[:MAX_TEXT], len(body) > MAX_TEXT

        return await asyncio.to_thread(read)

    # ── the project's documents ──────────────────────────────────
    async def _on_disk(self, project: Project, only: list[str] | None = None) -> tuple[
            dict[str, tuple[int, float]], dict[str, tuple[str, str]], set[str], bool]:
        """What the checkouts say about the documents, and whether there was a checkout to ask. A
        further source's documents are named under its label, as the index names them."""
        sources = [x for x in await roots(self.session, project) if x.ready]
        excluded = list(project.excluded or [])

        def read() -> tuple[dict[str, tuple[int, float]], dict[str, tuple[str, str]], set[str], bool]:
            disk: dict[str, tuple[int, float]] = {}
            commits: dict[str, tuple[str, str]] = {}
            tracked: set[str] = set()
            known = False
            for source in sources:
                if not source.root.is_dir():
                    continue
                known = True
                mine = None
                if only is not None:
                    mine = [rel for hit in (split(sources, x) for x in only)
                            if hit is not None and hit[0] is source for rel in [hit[1]]]
                    if not mine:
                        continue
                d, c, t = read_docs_on_disk(source.root, excluded, mine)
                disk.update({source.prefix + k: v for k, v in d.items()})
                commits.update({source.prefix + k: v for k, v in c.items()})
                tracked.update(source.prefix + k for k in t)
            return disk, commits, tracked, known

        return await asyncio.to_thread(read)

    async def docs(self, project_id: str, gateway: Gateway) -> dict[str, Any]:
        """Every document retrieval holds for this project, and every one on disk that it does not.

        A document the index dropped — past the doc cap, or written since the last build — is listed as
        not indexed rather than left out, because "awaiting index" is only honest if it counts them.
        """
        project = await self.project(project_id)
        chunks = ChunkRepository(self.session)
        state = await RetrievalService(self.session, gateway).summary(project_id)
        built_at = datetime.fromisoformat(state["at"]) if state.get("at") else None
        rows = {row["path"]: row for row in await chunks.docs(project_id, limit=MAX_DOC_PATHS)}
        disk, commits, tracked, known = await self._on_disk(project)

        def one(path: str) -> dict[str, Any]:
            row = rows.get(path, {})
            return doc_json(path, chunks=row.get("chunks", 0), embedded=row.get("embedded", 0),
                            title=row.get("title") or "", head=row.get("head") or "", project_id=project_id,
                            disk=disk, commits=commits, tracked=tracked, disk_known=known, built_at=built_at,
                            indexed_at=row["at"].isoformat(timespec="seconds") if row.get("at") else state.get("at"))

        listed = [one(path) for path in sorted({*rows, *disk})]
        names, _paths, _refs = entity_tokens("\n".join(await chunks.doc_bodies(project_id)), cap=MAX_DOC_CHUNKS * 5)
        # Counted over everything found, then cut to what one list may show.
        return {"retrieval": state, "docs": listed[:MAX_DOCS], "onDisk": len(disk),
                "notIndexed": sum(1 for d in listed if not d["indexed"]),
                "stale": sum(1 for d in listed if d["stale"]),
                "entitiesLinked": await chunks.count_symbols_named(project_id, names),
                "formats": [suffix.lstrip(".") for suffix in DOCS], "pipeline": pipeline(state)}

    async def doc(self, project_id: str, path: str, gateway: Gateway) -> dict[str, Any]:
        """One document with what it links to: symbols the index declares, files, and refs that exist."""
        project = await self.project(project_id)
        relative = _inside(path).as_posix()
        chunks = ChunkRepository(self.session)
        state = await RetrievalService(self.session, gateway).summary(project_id)
        sections = await chunks.doc_sections(project_id, relative)
        disk, commits, tracked, known = await self._on_disk(
            project, [relative] if Path(relative).suffix.lower() in DOCS else [])
        if not sections and relative not in disk:
            raise NotFound(path)

        text = "\n".join(body for _ref, _title, body, _embedded in sections)
        names, ticked, refs = entity_tokens(text)
        declared = await chunks.symbols_named(project_id, names)
        files = await chunks.code_paths(project_id, ticked)
        real = await chunks.existing_refs(project_id, refs)
        first_title, first_body = (sections[0][1], sections[0][2]) if sections else ("", "")
        doc = doc_json(relative, chunks=len(sections), embedded=sum(1 for *_x, e in sections if e),
                       title=first_title, head=first_body[:600], project_id=project_id, disk=disk,
                       commits=commits, tracked=tracked, disk_known=known,
                       built_at=datetime.fromisoformat(state["at"]) if state.get("at") else None,
                       indexed_at=state.get("at"))
        doc["entities"] = list(dict.fromkeys(name for name, _path in declared))
        doc["linkedTo"] = list(dict.fromkeys([*(p for _name, p in declared), *(t for t in ticked if t in files),
                                              *(r for r in refs if r in real)]))
        doc["sections"] = [{"ref": ref, "title": doc_title(title, relative), "embedded": embedded}
                           for ref, title, _body, embedded in sections]
        return doc

    # ── what breaks if this changes ──────────────────────────────
    async def impact(self, project_id: str, *, path: str | None = None, module: str | None = None,
                     obj: str | None = None) -> dict[str, Any]:
        await self.project(project_id)
        if not (path or module or obj):
            raise Refused("Name a path, a module or a database object", status=422)
        found = await self._impact(project_id, path=path, module=module, obj=obj)
        if found is None:
            raise NotFound(path or module or obj or "")
        return found

    async def _impact(self, project_id: str, *, path: str | None = None, module: str | None = None,
                      obj: str | None = None) -> dict[str, Any] | None:
        """Everything that depends on this, directly or through others, and what that means.

        A database object is one step further out than a file: the files that read or write it are
        its *direct* dependents, so the whole walk is shifted a step away from it.
        """
        if path:
            seeds, label, shift = await self.files.ids_of(project_id, path=path), path, 0
        elif module:
            seeds, label, shift = await self.files.ids_of(project_id, module=module), module, 0
        elif obj:
            seeds, label, shift = await self.edges.users_of_object(project_id, obj), obj, 1
            if not seeds and not await self.edges.object_is_declared(project_id, obj):
                return None
        else:
            return None
        if not seeds and not obj:
            return None

        reached = await self.files.reached(seeds)
        for row in reached:
            row["depth"] += shift
        seed_ids = set(seeds)
        seed_rows = [x for x in reached if x["id"] in seed_ids]
        if module:
            dependents = [x for x in reached if x["module"] != module]
        elif obj:
            dependents = reached
        else:
            dependents = [x for x in reached if x["id"] not in seed_ids]

        direct = sorted(x["path"] for x in dependents if x["depth"] == 1)
        further = sorted(x["path"] for x in dependents if x["depth"] > 1)
        tests = sorted({x["path"] for x in dependents + seed_rows
                        if codeindex.TEST_FILE.search(x["path"])})
        modules = Counter(x["module"] for x in dependents)
        data = ([(label, kind) for kind in await self.edges.kinds_against(project_id, obj)] if obj
                else await self.edges.data_touched(seeds))
        writes = sorted({target for target, kind in data if kind == "writes"})

        complexity = max((x["complexity"] for x in seed_rows), default=0)
        churn = max((x["churn"] for x in seed_rows), default=0)
        risk = codeindex.risk_of(len(dependents), complexity, bool(writes))
        run = await self.index.get(project_id)
        unresolved = run.unresolved if run else 0
        groups = [{"label": "Uses it directly", "items": direct[:40]},
                  {"label": "Reached through others", "items": further[:40]},
                  {"label": "Tests that reach it", "items": tests[:40]},
                  {"label": "Data it touches", "items": [f"{kind} {target}" for target, kind in data][:40]}]
        return {
            "target": label, "kind": "file" if path else "module" if module else "object",
            "risk": risk,
            "confidence": _confidence({x["lang"] for x in seed_rows + dependents},
                                      unresolved, run.edges if run else 0),
            "counts": {"direct": len(direct), "dependents": len(dependents), "modules": len(modules),
                       "tests": len(tests), "data": len(data)},
            "blastRadius": [g for g in groups if g["items"]],
            "modules": [{"name": name, "files": n} for name, n in modules.most_common(12)],
            "warnings": _warnings(direct, bool(seed_rows or dependents), tests, writes, modules,
                                  churn, unresolved),
            "recommendation": _advice(risk),
        }

    # ── the dependency graph ─────────────────────────────────────
    async def graph(self, project_id: str, limit: int = GRAPH_MODULES) -> dict[str, Any]:
        """Modules and the database objects they touch, sized for one screen: the largest modules."""
        await self.project(project_id)
        sizes = (await self.index.module_sizes(project_id))[:limit]
        total = await self.index.module_count(project_id)
        keep = {name for name, *_ in sizes}
        crossings = [(a, b, n) for a, b, n in await self.index.module_edges(project_id)
                     if a in keep and b in keep]
        depended = Counter(b for _a, b, _n in crossings)
        nodes = [{"id": f"m:{name}", "label": name, "kind": "module", "files": files, "lines": lines,
                  "risk": codeindex.risk_of(depended[name] * 4)}
                 for name, files, lines, _complexity in sizes]
        edges = [{"from": f"m:{a}", "to": f"m:{b}", "kind": "depends", "weight": n}
                 for a, b, n in crossings]

        declared = {name.lower(): (name, kind)
                    for name, kind, _path in await self.index.declared_objects(project_id)}
        uses = [(target, kind, module, n)
                for target, kind, module, n in await self.index.object_usage(project_id)
                if module in keep]
        busiest = {name for name, _n in Counter(t.lower() for t, _k, _m, _n in uses).most_common(16)}
        written = {t.lower() for t, kind, _m, _n in uses if kind == "writes"}
        for lowered in sorted(busiest):
            name, kind = declared.get(lowered, (lowered, "table"))
            nodes.append({"id": f"d:{name}", "label": name, "kind": kind, "files": 0, "lines": 0,
                          "risk": "HIGH" if lowered in written else "MEDIUM"})
        edges += [{"from": f"m:{module}", "to": f"d:{declared.get(t.lower(), (t,))[0]}",
                   "kind": kind, "weight": n}
                  for t, kind, module, n in uses if t.lower() in busiest]
        return {"nodes": nodes, "edges": edges, "modules": total, "truncated": total > limit}


def _confidence(langs: set[str], unresolved: int, edges: int) -> int:
    """How far to trust the radius: the parsers that read it, less the imports nobody resolved.

    Python is read by its own syntax tree, so a radius made only of Python and SQL is worth more than
    one that leans on patterns — and every import the indexer could not place widens the true radius.
    """
    base = 90 if langs and langs <= {"Python", "T-SQL"} else 80 if "Python" in langs else 72
    return max(40, base - min(20, round(100 * unresolved / max(1, edges + unresolved))))


def _warnings(direct: list[str], reaches_anything: bool, tests: list[str], writes: list[str],
              modules: Counter[str], churn: int, unresolved: int) -> list[str]:
    """What a person should know before touching this, in the order it matters."""
    out: list[str] = []
    if len(direct) >= 10:
        out.append(f"{len(direct)} files use it directly. A change to its signature reaches every "
                   "one of them.")
    if reaches_anything and not tests:
        out.append("No test file reaches it. A change here is unguarded until one does.")
    if writes:
        out.append(f"It writes {', '.join(writes[:3])}{'…' if len(writes) > 3 else ''}. Check "
                   "migrations, constraints and anything that audits that data.")
    if len(modules) >= 3:
        out.append(f"The change crosses {len(modules)} modules.")
    if churn >= 10:
        out.append(f"It changed {churn} times in 90 days: a hotspot, where regressions cluster.")
    if unresolved:
        out.append(f"{unresolved} imports in this project could not be resolved, so the true radius "
                   "may be larger.")
    return out


def _advice(risk: str) -> str:
    if risk in ("HIGH", "CRITICAL"):
        return ("Change it behind a stable interface, land the tests first, and let the plan stop at "
                "your approval.")
    if risk == "MEDIUM":
        return "Change it together with its direct users in one plan, and run their tests."
    return "Safe to change in one step. Run the tests that already reach it."


def coverage_after(stored: Any, measured: dict[str, int]) -> list[dict[str, Any]]:
    """The project's coverage bars with the ones this index can speak for brought up to date.

    The bars the index knows nothing about — business rules, test mapping — keep whatever onboarding
    last measured, rather than being reset to zero by a job that never looked at them.
    """
    return [{**bar, "pct": measured.get(bar["label"], bar["pct"])} for bar in stored or []]


# ── the slow jobs ────────────────────────────────────────────────
async def _say(db: Database, project_id: str, action: str, detail: str, level: str = "ok") -> None:
    async with db.session() as open_session:
        await ActivityRepository(open_session).record(actor=roster.ARCHITECT, actor_kind="agent",
                                                      action=action, detail=detail, level=level,
                                                      project_id=project_id)


async def _name_of(db: Database, project_id: str) -> str:
    async with db.read() as open_session:
        found = await ProjectRepository(open_session).get(project_id)
        return found.name if found else project_id


async def reindex(db: Database, gateway: Gateway, project_id: str, root: Path,
                  excluded: list[str]) -> None:
    """Read the code again — every source of the project — then rebuild retrieval on top of it.

    `root` is the first source's checkout, as the route found it; the further sources are looked up
    here, inside the job, so one added a moment ago is read too.

    Sessions of its own: the request that asked for this returned long ago, and its transaction with
    it. A failure leaves the previous index in place — a project is better off with a stale answer
    than with none — and says why in the activity feed.
    """
    name = await _name_of(db, project_id)
    try:
        async with db.session() as open_session:
            project = await ProjectRepository(open_session).get(project_id)
            if project is None:
                return
            found = await roots(open_session, project)
            if not any(x.primary for x in found):
                found = [Source(label=project_id, root=root, kind="local", primary=True), *found]
            index = await build_project_index(open_session, project_id, found, excluded)
            project = await ProjectRepository(open_session).get(project_id)
            if project is not None:
                project.coverage = coverage_after(project.coverage, index.coverage())
    except Exception as e:
        await _say(db, project_id, "Indexing failed", f"{name} · {_reason(e)}", "err")
        return
    await _say(db, project_id, "Code indexed", f"{name} · {index.describe()}")
    # Retrieval rides on the index: the chunks are only worth having if they match the code as it is
    # now. It must never be able to lose an index that succeeded, so it fails quietly and says so.
    await build_retrieval(db, gateway, project_id, level="warn")


async def build_retrieval(db: Database, gateway: Gateway, project_id: str, *,
                          level: str = "err") -> None:
    """Chunk the project again and embed what a lane can. Slow enough to belong in the background."""
    name = await _name_of(db, project_id)
    try:
        async with db.session() as open_session:
            built = await RetrievalService(open_session, gateway).build(project_id)
    except Exception as e:
        await _say(db, project_id, "Retrieval failed", f"{name} · {_reason(e)}", level)
        return
    await _say(db, project_id, "Retrieval ready",
               f"{name} · {built['chunks']} chunks · "
               f"{'meaning and words' if built['semantic'] else 'words only, no embedding lane'}")
