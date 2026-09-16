"""A conversation that can act, on Postgres.

Unchanged in what it promises: the model never runs anything, it names a tool from a fixed catalogue
and the arguments are checked first; the catalogue only reads; every turn is written down the moment
it happens, your question **before** the model is ever called.

What changed is where the turns live and where the tools look. Turns are rows in `chat_messages`,
written inside a transaction of their own so a crash mid-answer loses nothing that was already said.
The tools read the code index and memory through repositories, and `find` is now Postgres hybrid
search — words and meaning in one statement.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import onboarding
from ..ai.gateway import CHAT, Gateway, NoModel, extract_json
from ..data.base import utcnow
from ..data.engine import Database
from ..models import Chat, CodeEdge, CodeFile, CodeSymbol, Project
from ..repositories import ChatRepository, MemoryRepository, NotFound, ProjectRepository
from . import extensions
from .errors import Refused
from .extensions import SkillFile, Snapshot
from .retrieval import RetrievalService

MAX_STEPS = 6                 # tool calls in one answer, then it must answer with what it has
MAX_FILE_LINES = 400
MAX_OBSERVATION = 6_000       # what one tool may put back into the conversation
MAX_HISTORY = 24              # turns replayed to the model
MAX_QUESTION = 4_000
MAX_SKILLS_LISTED = 60        # one line each in the system prompt; beyond this the prompt is the cost


# ── the tools ────────────────────────────────────────────────────
@dataclass(frozen=True)
class Tool:
    name: str
    takes: str
    what: str
    run: Callable[["Tools", dict[str, Any]], Awaitable[tuple[str, str]]]


def _text(args: dict[str, Any], *names: str) -> str:
    """Models name arguments loosely; take the first key that is actually there."""
    for name in names:
        value = args.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _safe(root: Path, rel: str) -> Path:
    """A path inside the project, never upwards and never into .git. Refused, never rewritten."""
    p = PurePosixPath(str(rel).strip().replace("\\", "/"))
    parts = [part for part in p.parts if part != "."]
    if not parts or p.is_absolute() or any(part in ("..", ".git") for part in parts):
        raise Refused(f"{rel} is outside the project.", status=403)
    return root.joinpath(*parts)


class Tools:
    """What a session may do. Everything here reads; nothing writes and nothing runs."""

    def __init__(self, session: AsyncSession, gateway: Gateway, project: Project,
                 skills: Sequence[SkillFile] = ()) -> None:
        self.session = session
        self.project = project
        # The skills this answer may load, discovered once before it started — never globbed per call.
        self.skills = skills
        self.retrieval = RetrievalService(session, gateway)
        self.memory = MemoryRepository(session)

    def root(self) -> Path:
        found = onboarding.source_root({"id": self.project.id, "source": {
            "kind": self.project.source_kind, "repo": self.project.source_repo}} if self.project.source_kind else {})
        if found is None or not found.is_dir():
            raise Refused("This project's code is not on this machine, so its files cannot be read.")
        return found

    async def find(self, args: dict[str, Any]) -> tuple[str, str]:
        q = _text(args, "query", "q", "question")
        if not q:
            raise Refused("find needs a query.", status=422)
        found = await self.retrieval.search(self.project.id, q, limit=6)
        if not found:
            return (f"Retrieval holds nothing about {q!r}. The project may not be indexed yet.",
                    f"{q} · nothing found")
        body = "\n\n".join(f"[{x['kind']} · {x['ref']}]\n{x['text'][:700]}" for x in found)
        ways = ", ".join(sorted({x["how"] for x in found}))
        return f"{len(found)} pieces about {q!r}:\n\n{body}", f"{q} · {len(found)} pieces ({ways})"

    async def search_code(self, args: dict[str, Any]) -> tuple[str, str]:
        q = _text(args, "query", "q", "name", "symbol")
        if not q:
            raise Refused("search_code needs a query.", status=422)
        words = func.plainto_tsquery("simple", q)
        stmt = (select(CodeSymbol.name, CodeSymbol.kind, CodeSymbol.line, CodeFile.path)
                .join(CodeFile, CodeFile.id == CodeSymbol.file_id)
                .where(CodeSymbol.project_id == self.project.id,
                       CodeSymbol.search.op("@@")(words) | CodeSymbol.name.ilike(f"%{q}%"))
                .order_by(CodeSymbol.name).limit(25))
        rows = (await self.session.execute(stmt)).all()
        if not rows:
            return f"Nothing in the index matches {q!r}.", f"{q} · nothing found"
        body = "\n".join(f"{kind:<10} {name:<28} {path}:{line}" for name, kind, line, path in rows)
        return f"{len(rows)} matches for {q!r}:\n{body}", f"{q} · {len(rows)} matches"

    async def read_file(self, args: dict[str, Any]) -> tuple[str, str]:
        path = _text(args, "path", "file", "filename")
        if not path:
            raise Refused("read_file needs a path.", status=422)
        start = max(1, int(args.get("start") or 1))
        want = min(MAX_FILE_LINES, max(1, int(args.get("lines") or 200)))
        target = _safe(self.root(), path)

        def read() -> list[str]:
            if not target.is_file():
                raise Refused(f"{path} is not a file in this project.", status=404)
            return target.read_text(errors="replace").splitlines()

        lines = await asyncio.to_thread(read)
        shown = lines[start - 1:start - 1 + want]
        body = "\n".join(f"{start + i:>5}  {line}" for i, line in enumerate(shown))
        head = f"{path} · lines {start}-{start + len(shown) - 1} of {len(lines)}"
        return f"{head}\n{body}", f"{path} · {len(shown)} lines"

    async def list_files(self, args: dict[str, Any]) -> tuple[str, str]:
        where = _text(args, "directory", "dir", "path")
        prefix = f"{where.strip('/')}/" if where.strip("/") else ""
        stmt = (select(CodeFile.path, CodeFile.lines).where(CodeFile.project_id == self.project.id,
                                                            CodeFile.path.startswith(prefix))
                .order_by(CodeFile.path).limit(200))
        rows = (await self.session.execute(stmt)).all()
        if not rows:
            return f"Nothing indexed under {where or 'the project root'}.", f"{where or '/'} · empty"
        body = ", ".join(f"{p[len(prefix):]} ({n})" for p, n in rows[:120])
        return f"In {where or 'the project root'}:\n{body}", f"{where or '/'} · {len(rows)} files"

    async def impact(self, args: dict[str, Any]) -> tuple[str, str]:
        """What moves if this changes — the dependency edges walked backwards, in the database."""
        path = _text(args, "path", "file")
        if not path:
            raise Refused("impact needs a path.", status=422)
        seed = (await self.session.execute(select(CodeFile.id).where(
            CodeFile.project_id == self.project.id, CodeFile.path == path))).scalar_one_or_none()
        if seed is None:
            raise Refused(f"{path} is not in the code index.", status=404)

        depth = select(CodeEdge.from_file).where(CodeEdge.to_file == seed).cte("dependents", recursive=True)
        deeper = select(CodeEdge.from_file).join(depth, CodeEdge.to_file == depth.c.from_file)
        walked = depth.union(deeper)
        rows = (await self.session.execute(
            select(CodeFile.path).join(walked, walked.c.from_file == CodeFile.id).distinct().limit(60))).scalars()
        dependents = sorted(set(rows))
        if not dependents:
            return f"Nothing in this repository depends on {path}.", f"{path} · nothing depends on it"
        listed = "\n".join(f"  {p}" for p in dependents[:40])
        return (f"{len(dependents)} files depend on {path}, directly or through others:\n{listed}",
                f"{path} · {len(dependents)} dependents")

    async def search_memory(self, args: dict[str, Any]) -> tuple[str, str]:
        q = _text(args, "query", "q", "question")
        facts = await self.memory.search(q, limit=12)
        if not facts:
            return f"Memory holds nothing about {q!r}.", f"{q} · nothing remembered"
        body = "\n".join(f"{f.ref} · {f.title} — {f.body[:200]}" for f in facts)
        return f"{len(facts)} facts:\n{body}", f"{q} · {len(facts)} facts"

    async def load_skill(self, args: dict[str, Any]) -> tuple[str, str]:
        """A listed skill's full instructions. The detail is the skill's key: that row is the load count."""
        name = _text(args, "name", "skill", "slug", "key")
        if not name:
            raise Refused("load_skill needs a name.", status=422)
        found = extensions.resolve_skill(self.skills, name)
        if found is None:
            listed = ", ".join(s.slug for s in self.skills[:40]) or "none"
            raise Refused(f"There is no enabled skill called {name!r}. The skills are: {listed}.", status=404)
        more = "\n\n[The rest of this skill was cut off.]" if found.truncated else ""
        return f"Skill {found.name} ({found.source}):\n\n{found.body}{more}", found.key

    async def project_summary(self, args: dict[str, Any]) -> tuple[str, str]:
        files, lines = (await self.session.execute(select(
            func.count(CodeFile.id), func.coalesce(func.sum(CodeFile.lines), 0)
        ).where(CodeFile.project_id == self.project.id))).one()
        if not files:
            return (f"{self.project.name} has no code index yet. Onboard it to read its code.",
                    "not indexed")
        langs = (await self.session.execute(
            select(CodeFile.lang, func.count()).where(CodeFile.project_id == self.project.id)
            .group_by(CodeFile.lang).order_by(func.count().desc()).limit(6))).all()
        spread = ", ".join(f"{lang} ({n})" for lang, n in langs)
        return (f"{self.project.name}: {files} files, {lines} lines · {spread}",
                f"{self.project.name} · indexed")


CATALOGUE: tuple[Tool, ...] = (
    Tool("find", '{"query": "where is the interstate tax split"}',
         "search this project's code, its documents and the workspace's memory by meaning as well as "
         "by words — start here when you do not know the name of the thing", Tools.find),
    Tool("search_code", '{"query": "TaxService"}',
         "find where a name is defined in this project's code", Tools.search_code),
    Tool("read_file", '{"path": "pkg/tax.py", "start": 1, "lines": 200}',
         "read part of a file, with line numbers", Tools.read_file),
    Tool("list_files", '{"directory": "pkg"}', "list what is indexed under a folder", Tools.list_files),
    Tool("impact", '{"path": "pkg/tax.py"}',
         "what depends on a file, directly and through others", Tools.impact),
    Tool("search_memory", '{"query": "gst"}',
         "search the workspace's remembered facts and decisions", Tools.search_memory),
    Tool("project_summary", "{}", "the project's languages and size", Tools.project_summary),
    Tool("load_skill", '{"name": "impact-analysis"}',
         "read the full instructions of one of the skills listed below, when its description fits the "
         "question", Tools.load_skill),
)
BY_NAME = {t.name: t for t in CATALOGUE}


class Turn(BaseModel):
    tool: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    why: str = ""
    answer: str = ""


def system_prompt(project_name: str, skills: Sequence[SkillFile] = ()) -> str:
    """The standing instructions. Each enabled skill costs one line here; its body is loaded only on ask."""
    offered = [t for t in CATALOGUE if t.name != "load_skill" or skills]
    catalogue = "\n".join(f"- {t.name} {t.takes} — {t.what}" for t in offered)
    listed = "".join(f"\n- {s.slug} — {s.description[:200]}" for s in skills[:MAX_SKILLS_LISTED])
    skill_section = f"Skills (load one with load_skill when it fits):{listed}\n\n" if skills else ""
    return (
        "You are NeuroCode, working inside an engineering workspace. You answer questions about one "
        f"project: {project_name}.\n\n"
        "You cannot see the code until you read it. Use the tools, one at a time, until you know "
        "enough, then answer from what they returned. Never invent a file, a symbol, a line number or "
        "a fact — if the tools do not show it, say so plainly.\n\n"
        f"Tools:\n{catalogue}\n\n"
        f"{skill_section}"
        "Answer with one JSON object and nothing else.\n"
        'To use a tool: {"tool": "<name>", "arguments": {…}, "why": "<a short line for the person watching>"}\n'
        'To answer: {"answer": "<your answer, in the language the person used>"}\n'
        f"You may call at most {MAX_STEPS} tools before you must answer with what you have."
    )


class ChatService:
    """Starting a session, asking it something, and reading it back."""

    def __init__(self, session: AsyncSession, gateway: Gateway) -> None:
        self.session = session
        self.gateway = gateway
        self.chats = ChatRepository(session)
        self.projects = ProjectRepository(session)

    async def start(self, project_id: str, by: str, title: str = "") -> Chat:
        project = await self.projects.get(project_id)
        if project is None:
            raise NotFound(f"project {project_id}")
        ref = await self.chats.next_ref()
        return await self.chats.add(Chat(
            id=f"s{ref.split('-')[-1]}", ref=ref, project_id=project_id,
            title=title.strip()[:80] or "New session", started_by=by))

    async def ask(self, ref: str, question: str, by: str) -> dict[str, Any]:
        """Your question, kept before anything else happens — so it is never lost if the model is."""
        chat = await self.chats.by_ref(ref)
        if chat is None:
            raise NotFound(f"session {ref}")
        if chat.status == "thinking":
            raise Refused(f"{ref} is still answering. Wait for it, or stop it first.")
        text = question.strip()[:MAX_QUESTION]
        if not text:
            raise Refused("There is nothing to ask.", status=422)
        command = await self._command(chat, text)
        if chat.turns == 0 and chat.title == "New session":
            chat.title = text.splitlines()[0][:80]
        message = await self.chats.say(chat.id, role="you", body=text, by=by)
        if command is not None:
            # The expansion is its own turn, so the person sees exactly the prompt the model will be given
            # and the row counts one run of the command. It is replayed to the model as your words.
            found, args = command
            await self.chats.say(chat.id, role="tool", body=extensions.expand(found, args)[:MAX_OBSERVATION],
                                 tool="command", arguments={"name": found.name, "args": args},
                                 detail=found.key, ok=True)
        chat.status, chat.last_at = "thinking", utcnow()
        await self.session.flush()
        return {"message": message, "chat": chat}


    async def _command(self, chat: Chat, text: str) -> tuple[extensions.CommandFile, str] | None:
        """The command a `/name args` question names, if one is on disk. A question that only looks like
        one (`/etc kya hai`) is asked as it is; only a command a person switched off is refused."""
        parsed = extensions.parse_command(text)
        if parsed is None:
            return None
        project = await self.projects.get(chat.project_id)
        found_on_disk = await extensions.snapshot(self.session, project)
        name, args = parsed
        found = extensions.resolve_command(found_on_disk.commands, name)
        if found is None:
            return None
        if not extensions.switched_on(found_on_disk.commands_on, found.key):
            raise extensions.refuse_disabled(found)
        return found, args


# ── answering, in the background ─────────────────────────────────
#: Sessions a person stopped. In this process only — a stop is a request, not a promise across restarts.
_STOPPED: set[str] = set()


def stop(ref: str) -> None:
    _STOPPED.add(ref)


async def _wire(session: AsyncSession, chat: Chat, project_name: str,
                skills: Sequence[SkillFile] = ()) -> list[dict[str, str]]:
    """The conversation as the model sees it: the system prompt, then the turns, newest last."""
    out: list[dict[str, str]] = [{"role": "system", "content": system_prompt(project_name, skills)}]
    turns = await ChatRepository(session).messages(chat.id)
    for m in turns[-MAX_HISTORY:]:
        if m.role == "you":
            out.append({"role": "user", "content": m.body[:MAX_QUESTION]})
        elif m.role == "assistant":
            out.append({"role": "assistant", "content": json.dumps({"answer": m.body})})
        elif m.role == "tool" and m.tool == "command":
            # A command is the person's own prompt, not a tool the model called: replaying it as a call
            # would teach the model to call a tool named "command" that does not exist.
            name = (m.arguments or {}).get("name", "the command")
            out.append({"role": "user",
                        "content": f"{name} expands to this prompt. Follow it:\n{m.body[:MAX_OBSERVATION]}"})
        elif m.role == "tool":
            out.append({"role": "assistant",
                        "content": json.dumps({"tool": m.tool, "arguments": m.arguments or {}})})
            out.append({"role": "user", "content": f"Result of {m.tool}:\n{m.body[:MAX_OBSERVATION]}"})
    return out


async def _tool_turn(session: AsyncSession, gateway: Gateway, chat: Chat, project: Project,
                     turn: Turn, skills: Sequence[SkillFile] = ()) -> None:
    """Run one tool and write what it found — a refusal is reported into the conversation, not raised."""
    tool = BY_NAME.get(turn.tool.strip())
    if tool is None:
        await ChatRepository(session).say(
            chat.id, role="tool", body=f"There is no tool called {turn.tool!r}. The tools are: "
                                       f"{', '.join(BY_NAME)}.",
            tool=turn.tool.strip()[:40] or "?", arguments=turn.arguments or {}, detail="refused", ok=False)
        return
    try:
        observation, detail = await tool.run(Tools(session, gateway, project, skills), turn.arguments or {})
        ok = True
    except Refused as refused:
        observation, detail, ok = str(refused), "refused", False
    except Exception as e:                       # a tool that breaks must not end the session
        observation, detail, ok = f"{type(e).__name__}: {e}", "failed", False
    chat.tool_calls += 1
    await ChatRepository(session).say(chat.id, role="tool", body=observation[:MAX_OBSERVATION],
                                      tool=tool.name, arguments=turn.arguments or {},
                                      why=turn.why[:160], detail=detail, ok=ok)


def _grounding_question(turns: Sequence[Any]) -> str:
    """What retrieval should look for: the last question — or, when it was a command, its arguments,
    since `/plan invoice tax` is about the invoice tax and not about the word plan."""
    for i in range(len(turns) - 1, -1, -1):
        if turns[i].role != "you":
            continue
        command = next((m for m in turns[i + 1:] if m.role == "tool" and m.tool == "command"), None)
        if command is not None:
            return str((command.arguments or {}).get("args") or "") or command.body[:MAX_QUESTION]
        return turns[i].body
    return ""


async def think(db: Database, gateway: Gateway, ref: str, by: str) -> None:
    """Answer the last question: ground it, read with the tools, then say what was found.

    Each turn is written in a transaction of its own, so a crash halfway through loses only the turn
    that was in flight — everything already said is already saved.
    """
    _STOPPED.discard(ref)
    async with db.session() as s:
        chat = await ChatRepository(s).by_ref(ref)
        if chat is None:
            return
        project = await ProjectRepository(s).get(chat.project_id)
        project_name = project.name if project else chat.project_id
        asked = _grounding_question(await ChatRepository(s).messages(chat.id))
        chat.status = "thinking"

    # Skills are discovered once per answer, in a thread, with the switches read once: every step's
    # prompt and every load_skill call works from this snapshot instead of reading the disk again.
    async with db.read() as s:
        found: Snapshot = await extensions.snapshot(s, project)

    # Before the model is asked anything, retrieval answers the cheapest question: what do we already
    # hold about this? It is a turn like any other, so the model replays it and the person sees it.
    if asked:
        async with db.session() as s:
            try:
                ground, pieces = await RetrievalService(s, gateway).grounding(chat.project_id, asked)
            except Exception:
                ground, pieces = "", 0
            if ground:
                await ChatRepository(s).say(chat.id, role="tool", body=ground, tool="grounding",
                                            detail=f"{pieces} pieces from the index", ok=True)

    try:
        for step in range(MAX_STEPS + 1):
            if ref in _STOPPED:
                async with db.session() as s:
                    await ChatRepository(s).say(chat.id, role="note", body="You stopped this answer.")
                break
            last = step == MAX_STEPS

            async with db.read() as s:
                messages = await _wire(s, chat, project_name, found.skills)
            if last:
                messages.append({"role": "user", "content":
                                 "You have used every tool call. Answer now with what you already "
                                 'have, as {"answer": "…"}.'})

            try:
                result = await asyncio.to_thread(
                    gateway.ask, messages,
                    lambda raw: Turn.model_validate(extract_json(raw, trim=False)),
                    feature="chat", actor=by, project=chat.project_id, role=CHAT)
            except NoModel as e:
                async with db.session() as s:
                    await ChatRepository(s).say(chat.id, role="note", body=str(e))
                break
            except Exception as e:                       # every lane failed, or none could be parsed
                async with db.session() as s:
                    await ChatRepository(s).say(
                        chat.id, role="note",
                        body=f"No lane could answer: {type(e).__name__}. Try again in a moment.")
                break

            turn = result.data
            if turn.answer or not turn.tool or last:
                # On the last step whatever comes back is the answer: a session always ends in words.
                async with db.session() as s:
                    fresh = await ChatRepository(s).by_ref(ref)
                    await ChatRepository(s).say(
                        chat.id, role="assistant",
                        body=turn.answer or f"I read what I could in {MAX_STEPS} tool calls without "
                                            "reaching an answer.",
                        model=result.provider.model, lane=result.provider.id, ms=result.ms)
                    if fresh is not None:
                        fresh.turns += 1
                        fresh.model, fresh.lane = result.provider.model, result.provider.id
                break

            async with db.session() as s:
                fresh = await ChatRepository(s).by_ref(ref)
                if fresh is None:
                    break
                await _tool_turn(s, gateway, fresh, project, turn, found.skills)
    finally:
        async with db.session() as s:
            fresh = await ChatRepository(s).by_ref(ref)
            if fresh is not None:
                fresh.status, fresh.last_at = "idle", utcnow()
        _STOPPED.discard(ref)
