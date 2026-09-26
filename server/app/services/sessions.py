"""Shaping a session: forking it, carrying it out and back in, turning it into a plan, and what the
composer offers — files, symbols, facts and plans to mention, and files dropped into it.

The answering loop lives in `services/chat.py`; everything here works on turns already written. None of
it calls a model except "Make this a plan", which is a compile like any other and goes through the
compiler and the gateway exactly as the Plans screen's would.

**Fork** copies the active line up to a turn into a new session that remembers where it came from
(`parent_id`, `forked_at`). What is copied is what the person saw: the turns, the folded ones folded,
the summary, the attachments (and the uploads they name). Grants are not copied — "Allow for this
session" was said about that session. A card still waiting is copied as lapsed, so the fork does not
start out waiting on a question nobody asked in it.

**Export** is every turn, replaced ones included and linked, as JSON (`neurocode.session`, version 1),
or the active line as Markdown. The screen builds the download itself. **Import** reads that JSON back
into a new session on a project the person names: turns keep their words, times and authors; uploads are
not in an export, so an imported picture is a name without its bytes, and says so.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import mimetypes
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway
from ..data.base import utcnow
from ..models import Chat, ChatFile, ChatMessage, CodeFile, CodeSymbol, MemoryFact, Plan, Task
from ..repositories import ActivityRepository, ChatRepository, NotFound, ProjectRepository
from .chat import CONTEXT, IMAGE_TYPES, MAX_QUESTION, PERMISSION, ChatService, active
from .code import reference_labels, roots
from .errors import Refused
from .identity import Person
from .plans import PlanService
from .references import referenced

#: What an export says it is, so an import can refuse anything else in words.
FORMAT = "neurocode.session"
VERSION = 1
#: An import's ceilings: turns, and characters in any one turn.
MAX_IMPORT_TURNS = 2_000
MAX_IMPORT_TEXT = 200_000
#: What an export reads at most — ten pages of the repository's own ceiling.
MAX_EXPORT_TURNS = 10_000
#: Uploads: text a model can read, and pictures for a lane that reads images.
MAX_TEXT_UPLOAD = 1024 * 1024
MAX_IMAGE_UPLOAD = 5 * 1024 * 1024
MAX_UPLOADS = 40
TEXT_TYPES = ("application/json", "application/xml", "application/x-yaml", "application/yaml",
              "application/javascript", "application/typescript", "application/sql", "application/toml",
              "application/x-sh", "application/x-python", "application/csv")
#: Mentions: how many of each kind one lookup answers.
MENTION_EACH = 8
#: Files offered from each referenced project, after the project's own.
MENTION_REFERENCED = 4
ROLES = ("you", "assistant", "tool", "note", "summary")
#: Tool turns that are not a tool the model called: they are left out of the tool-call count of a copy.
NOT_CALLS = ("command", CONTEXT, PERMISSION, "grounding")


async def _chat(session: AsyncSession, ref: str) -> Chat:
    chat = await ChatRepository(session).by_ref(ref)
    if chat is None:
        raise NotFound(f"session {ref}")
    return chat


async def _every_turn(session: AsyncSession, chat_id: str) -> list[ChatMessage]:
    """Every turn of a session, paged past the repository's per-call ceiling, up to MAX_EXPORT_TURNS."""
    repo, out, after = ChatRepository(session), [], 0
    while len(out) < MAX_EXPORT_TURNS:
        page = await repo.messages(chat_id, after, limit=1000)
        if not page:
            break
        out += page
        after = page[-1].id
        if len(page) < 1000:
            break
    return out[:MAX_EXPORT_TURNS]


def _lapsed(arguments: dict[str, Any]) -> dict[str, Any]:
    """A permission card copied out of the session it was asked in: nobody will answer it there."""
    if arguments.get("state") == "pending":
        return {**arguments, "state": "lapsed"}
    return arguments


class SessionShapes:
    def __init__(self, session: AsyncSession, gateway: Gateway) -> None:
        self.session = session
        self.gateway = gateway
        self.chats = ChatRepository(session)
        self.activity = ActivityRepository(session)

    # ── fork ─────────────────────────────────────────────────────
    async def fork(self, ref: str, at: int, who: Person) -> Chat:
        parent = await _chat(self.session, ref)
        line = active(await _every_turn(self.session, parent.id))
        if not any(m.id == at for m in line):
            raise NotFound(f"turn {at} on the current line of {ref}")
        kept = [m for m in line if m.id <= at]
        made = await ChatService(self.session, self.gateway).start(parent.project_id, who.name,
                                                                   f"{parent.title[:72]} (fork)")
        made.parent_id, made.forked_at = parent.id, at
        made.model, made.lane = parent.model, parent.lane
        await self._copy(made, kept, source_chat=parent.id)
        await self.activity.record(actor=who.name, actor_kind="human", action="Session forked",
                                   detail=f"{made.ref} from {parent.ref} at turn {at} · {len(kept)} turns",
                                   project_id=parent.project_id)
        return made

    async def _copy(self, into: Chat, turns: list[ChatMessage] | list[dict[str, Any]], *,
                    source_chat: str | None = None) -> None:
        """Write turns into a session in order, keeping replaced-by links and a summary's folded range
        pointing at the copies. From another session's rows (fork) or from an export's dicts (import)."""
        ids: dict[int, int] = {}
        files: dict[int, int] = {}
        rows: list[tuple[ChatMessage, int | None]] = []
        for t in turns:
            fields = _fields(t)
            old_id, replaced = fields.pop("_id"), fields.pop("_superseded_by")
            fields["arguments"] = _lapsed(fields["arguments"]) if fields.get("tool") == PERMISSION else fields["arguments"]
            if source_chat is not None:
                fields["attachments"] = await self._uploads(fields["attachments"], source_chat, into, files)
            row = ChatMessage(chat_id=into.id, **fields)
            self.session.add(row)
            await self.session.flush()
            if old_id is not None:
                ids[old_id] = row.id
            rows.append((row, replaced))
        for row, replaced in rows:
            if replaced is not None and replaced in ids:
                row.superseded_by = ids[replaced]
            if row.role == "summary":
                folded = dict(row.arguments or {})
                for key in ("from", "to"):
                    if isinstance(folded.get(key), int) and folded[key] in ids:
                        folded[key] = ids[folded[key]]
                row.arguments = folded
        into.turns = sum(1 for r, _ in rows if r.role == "assistant")
        into.tool_calls = sum(1 for r, _ in rows if r.role == "tool" and r.tool not in NOT_CALLS)
        into.last_at = utcnow()
        await self.session.flush()

    async def _uploads(self, attachments: list[dict[str, Any]], source_chat: str, into: Chat,
                       copied: dict[int, int]) -> list[dict[str, Any]]:
        """A forked turn's uploads are copied with it, so its chips still open and a picture can be sent."""
        out = []
        for a in attachments:
            ref = str(a.get("ref", ""))
            if a.get("kind") == "upload" and ref.isdigit():
                old = int(ref)
                if old not in copied:
                    found = (await self.session.execute(select(ChatFile).where(
                        ChatFile.chat_id == source_chat, ChatFile.id == old))).scalar_one_or_none()
                    if found is not None:
                        twin = ChatFile(chat_id=into.id, name=found.name, mime=found.mime, bytes=found.bytes,
                                        sha1=found.sha1, data=found.data, by=found.by)
                        self.session.add(twin)
                        await self.session.flush()
                        copied[old] = twin.id
                if old in copied:
                    a = {**a, "ref": str(copied[old])}
            out.append(a)
        return out

    # ── export and import ────────────────────────────────────────
    async def export(self, ref: str, fmt: str) -> dict[str, Any]:
        """`{filename, mime, text}` — the screen turns it into a download without another request."""
        chat = await _chat(self.session, ref)
        turns = await _every_turn(self.session, chat.id)
        project = await ProjectRepository(self.session).get(chat.project_id)
        stem = f"{chat.ref.lower()}-{''.join(c if c.isalnum() else '-' for c in chat.title.lower())[:40].strip('-')}"
        if fmt == "json":
            doc = {"format": FORMAT, "version": VERSION, "exportedAt": utcnow().isoformat(),
                   "session": {"ref": chat.ref, "title": chat.title, "projectId": chat.project_id,
                               "projectName": project.name if project else chat.project_id,
                               "startedBy": chat.started_by, "startedAt": chat.created_at.isoformat(),
                               "model": chat.model, "lane": chat.lane},
                   "messages": [_exported(m) for m in turns]}
            return {"filename": f"{stem}.json", "mime": "application/json",
                    "text": json.dumps(doc, ensure_ascii=False, indent=2)}
        if fmt != "md":
            raise Refused("Export as md or json.", status=422)
        return {"filename": f"{stem}.md", "mime": "text/markdown",
                "text": _markdown(chat, project.name if project else chat.project_id, turns)}

    async def import_(self, project_id: str, doc: Any, who: Person) -> Chat:
        if await ProjectRepository(self.session).get(project_id) is None:
            raise NotFound(f"project {project_id}")
        if not isinstance(doc, dict) or doc.get("format") != FORMAT:
            raise Refused("That is not a NeuroCode session export (its format is not neurocode.session).",
                          status=422)
        if doc.get("version") != VERSION:
            raise Refused(f"This server reads session exports of version {VERSION}; that one is version "
                          f"{doc.get('version')!r}.", status=422)
        messages = doc.get("messages")
        if not isinstance(messages, list) or not messages:
            raise Refused("The export holds no turns.", status=422)
        if len(messages) > MAX_IMPORT_TURNS:
            raise Refused(f"An import holds at most {MAX_IMPORT_TURNS} turns; this one has {len(messages)}.",
                          status=422)
        turns = [_imported(n, m) for n, m in enumerate(messages, 1)]
        head = doc.get("session") if isinstance(doc.get("session"), dict) else {}
        title = str(head.get("title") or "Imported session")[:70]
        made = await ChatService(self.session, self.gateway).start(project_id, who.name, f"{title} (imported)")
        await self._copy(made, turns)
        await self.activity.record(actor=who.name, actor_kind="human", action="Session imported",
                                   detail=f"{made.ref} · {len(turns)} turns · from {str(head.get('ref') or 'an export')[:40]}",
                                   project_id=project_id)
        return made

    # ── make this a plan ─────────────────────────────────────────
    async def to_plan(self, ref: str, who: Person) -> tuple[Plan, Task]:
        """Compile a plan from the session's last question and what its answer rests on: the files the
        tools read, the facts and plans attached, the pages fetched. The session is left as it is."""
        chat = await _chat(self.session, ref)
        if chat.status == "thinking":
            raise Refused(f"{ref} is still answering. Make it a plan once the answer is in.")
        line = active(await _every_turn(self.session, chat.id))
        at = max((i for i, m in enumerate(line) if m.role == "you"), default=None)
        if at is None:
            raise Refused(f"{ref} has no question to make a plan of yet.", status=409)
        question, after = line[at], line[at + 1:]
        if not any(m.role == "assistant" for m in after):
            raise Refused(f"{ref} has not answered its last question yet. Make it a plan once it has.", status=409)
        cited = _cited(question, after)
        answer = next((m.body for m in reversed(after) if m.role == "assistant"), "")
        requirement = question.body.strip()
        extra = ""
        if answer:
            extra += f"\n\nWhat the session {chat.ref} answered, for context:\n{answer.strip()[:1500]}"
        if cited:
            extra += "\n\nWhat that answer rests on:\n" + "\n".join(f"- {c}" for c in cited)
        text = (requirement + extra)[:MAX_QUESTION]
        return await PlanService(self.session, self.gateway).compile(chat.project_id, text, by=who.name,
                                                                     by_id=who.id)

    # ── the composer: mentions and uploads ───────────────────────
    async def mentions(self, project_id: str, q: str, *, hidden: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
        """What `@` offers: files and symbols from the code index, memory facts for this project or the
        whole workspace, and this project's plans — the closest names first (pg_trgm), a few of each.

        Files of a reference source say "reference"; files of a project this one references come after
        the project's own, named `<project id>:<path>` — the prefix the session's tools read them with —
        and say whose they are. Both are read only. `hidden` is the projects the person asking may not see
        (`unseen_by`): a project this one references that is among them offers nothing, not even its name."""
        project = await ProjectRepository(self.session).get(project_id)
        if project is None:
            raise NotFound(f"project {project_id}")
        words = q.strip()[:120]
        like = f"%{words}%"
        out: list[dict[str, Any]] = []
        labels = reference_labels(await roots(self.session, project))

        def found_files(owner: str, limit: int) -> Any:
            files = select(CodeFile.path, CodeFile.lines).where(CodeFile.project_id == owner)
            files = (files.where(CodeFile.path.ilike(like)).order_by(func.similarity(CodeFile.path, words).desc(),
                                                                     CodeFile.path)
                     if words else files.order_by(CodeFile.churn.desc(), CodeFile.path))
            return files.limit(limit)

        for path, lines in (await self.session.execute(found_files(project_id, MENTION_EACH))).all():
            head, cut, _ = path.partition("/")
            read_only = " · reference, read only" if cut and head in labels else ""
            out.append({"kind": "file", "ref": path, "name": path, "detail": f"{lines} lines{read_only}"})
        for other, name in await referenced(self.session, project_id):
            if other in hidden:
                continue
            for path, lines in (await self.session.execute(found_files(other, MENTION_REFERENCED))).all():
                out.append({"kind": "file", "ref": f"{other}:{path}", "name": f"{other}:{path}",
                            "detail": f"{name} · reference, read only · {lines} lines"})

        if words:
            symbols = (select(CodeSymbol.id, CodeSymbol.name, CodeSymbol.kind, CodeSymbol.line, CodeFile.path)
                       .join(CodeFile, CodeFile.id == CodeSymbol.file_id)
                       .where(CodeSymbol.project_id == project_id, CodeSymbol.name.ilike(like))
                       .order_by(func.similarity(CodeSymbol.name, words).desc(), CodeSymbol.name)
                       .limit(MENTION_EACH))
            for sid, name, kind, line, path in (await self.session.execute(symbols)).all():
                out.append({"kind": "symbol", "ref": str(sid), "name": name, "detail": f"{kind} · {path}:{line}"})

        facts = (select(MemoryFact.ref, MemoryFact.title, MemoryFact.category)
                 .where(MemoryFact.archived.is_(False),
                        or_(MemoryFact.project_id == project_id, MemoryFact.project_id.is_(None))))
        facts = (facts.where(or_(MemoryFact.title.ilike(like), MemoryFact.ref.ilike(like), MemoryFact.body.ilike(like)))
                 .order_by(func.similarity(MemoryFact.title, words).desc())
                 if words else facts.order_by(MemoryFact.pinned.desc(), MemoryFact.updated_at.desc()))
        for fref, title, category in (await self.session.execute(facts.limit(MENTION_EACH))).all():
            out.append({"kind": "fact", "ref": fref, "name": title, "detail": f"{fref} · {category}"})

        plans = select(Plan.ref, Plan.business_requirement, Plan.raw_requirement, Plan.status).where(
            Plan.project_id == project_id)
        if words:
            plans = plans.where(or_(Plan.ref.ilike(like), Plan.business_requirement.ilike(like),
                                    Plan.raw_requirement.ilike(like)))
        for pref, business, raw, status in (await self.session.execute(
                plans.order_by(Plan.created_at.desc()).limit(MENTION_EACH))).all():
            name = (business or raw or pref).strip().splitlines()[0][:120] if (business or raw) else pref
            out.append({"kind": "plan", "ref": pref, "name": name, "detail": f"{pref} · {status}"})
        return out

    async def upload(self, ref: str, name: str, mime: str, data: str, who: Person) -> ChatFile:
        """A file dropped into the composer: text a model reads (up to 1 MB) or a picture (up to 5 MB) for a
        lane that reads images. Anything else is refused in words — nothing is kept that cannot be used."""
        chat = await _chat(self.session, ref)
        try:
            raw = base64.b64decode(data, validate=True)
        except (binascii.Error, ValueError) as e:
            raise Refused("The file did not arrive whole (its base64 did not decode).", status=422) from e
        clean = name.strip().replace("/", "_").replace("\\", "_")[:200] or "upload"
        kind = (mime or "").split(";")[0].strip().lower() or (mimetypes.guess_type(clean)[0] or "")
        if kind in IMAGE_TYPES:
            if len(raw) > MAX_IMAGE_UPLOAD:
                raise Refused(f"{clean} is {len(raw) // 1024} KB; a picture may be at most 5 MB.", status=413)
        else:
            if len(raw) > MAX_TEXT_UPLOAD:
                raise Refused(f"{clean} is {len(raw) // 1024} KB; a text file may be at most 1 MB.", status=413)
            if not (kind.startswith("text/") or kind in TEXT_TYPES or not kind):
                raise Refused(f"{clean} is {kind}. A session takes text files and PNG, JPEG, GIF or WebP "
                              "pictures.", status=415)
            try:
                raw.decode("utf-8")
            except UnicodeDecodeError as e:
                raise Refused(f"{clean} is not text a model can read (it is not UTF-8).", status=415) from e
            kind = kind or "text/plain"
        count = (await self.session.execute(select(func.count(ChatFile.id)).where(ChatFile.chat_id == chat.id))
                 ).scalar_one()
        if count >= MAX_UPLOADS:
            raise Refused(f"A session holds at most {MAX_UPLOADS} uploads. Start a new one, or fork it.")
        row = ChatFile(chat_id=chat.id, name=clean, mime=kind, bytes=len(raw), sha1=hashlib.sha1(raw).hexdigest(),
                       data=raw, by=who.name)
        self.session.add(row)
        await self.session.flush()
        return row

    async def file(self, ref: str, file_id: int) -> ChatFile:
        chat = await _chat(self.session, ref)
        found = (await self.session.execute(select(ChatFile).where(ChatFile.chat_id == chat.id,
                                                                   ChatFile.id == file_id))).scalar_one_or_none()
        if found is None:
            raise NotFound(f"file {file_id} in {ref}")
        return found


def upload_json(f: ChatFile) -> dict[str, Any]:
    return {"id": f.id, "name": f.name, "mime": f.mime, "bytes": f.bytes, "sha1": f.sha1,
            "image": f.mime in IMAGE_TYPES, "by": f.by, "at": f.at.isoformat() if f.at else None}


# ── the shapes a turn takes on the way out and back in ─────────────
def _fields(t: ChatMessage | dict[str, Any]) -> dict[str, Any]:
    if isinstance(t, dict):
        return dict(t)
    return {"_id": t.id, "_superseded_by": t.superseded_by, "role": t.role, "body": t.body, "by": t.by,
            "at": t.at, "model": t.model, "lane": t.lane, "ms": t.ms, "tool": t.tool,
            "arguments": dict(t.arguments or {}), "why": t.why, "detail": t.detail, "ok": t.ok,
            "reasoning": t.reasoning, "compacted": t.compacted, "attachments": list(t.attachments or [])}


def _exported(m: ChatMessage) -> dict[str, Any]:
    return {"id": m.id, "at": m.at.isoformat() if m.at else None, "role": m.role, "text": m.body, "by": m.by,
            "model": m.model, "lane": m.lane, "ms": m.ms, "tool": m.tool, "arguments": m.arguments or {},
            "why": m.why, "detail": m.detail, "ok": m.ok, "reasoning": m.reasoning, "compacted": m.compacted,
            "attachments": m.attachments or [], "supersededBy": m.superseded_by}


def _when(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else utcnow()
    except (TypeError, ValueError):
        return utcnow()


def _imported(n: int, m: Any) -> dict[str, Any]:
    """One exported turn, checked field by field. Refused in words, naming the turn, when it is not one."""
    if not isinstance(m, dict):
        raise Refused(f"Turn {n} is not an object.", status=422)
    role = m.get("role")
    if role not in ROLES:
        raise Refused(f"Turn {n} has no known role ({role!r}).", status=422)
    text = m.get("text")
    if not isinstance(text, str) or len(text) > MAX_IMPORT_TEXT:
        raise Refused(f"Turn {n}'s text is missing or longer than {MAX_IMPORT_TEXT} characters.", status=422)
    tool = m.get("tool")
    if tool is not None and (not isinstance(tool, str) or len(tool) > 60):
        raise Refused(f"Turn {n}'s tool is not a short name.", status=422)

    def short(key: str, cap: int) -> str:
        value = m.get(key)
        return value[:cap] if isinstance(value, str) else ""

    attachments = [
        # An export carries no uploaded bytes: a picture comes back as its name, marked missing.
        {**{k: a[k] for k in ("kind", "ref", "name", "chars", "cut", "path", "line", "mime") if k in a},
         **({"missing": True} if a.get("kind") == "upload" else {})}
        for a in (m.get("attachments") if isinstance(m.get("attachments"), list) else []) if isinstance(a, dict)]
    lane = short("lane", 40) or None
    ms = m.get("ms") if isinstance(m.get("ms"), int) else None
    replaced = m.get("supersededBy") if isinstance(m.get("supersededBy"), int) else None
    return {"_id": m.get("id") if isinstance(m.get("id"), int) else None, "_superseded_by": replaced,
            "role": role, "body": text, "by": short("by", 120), "at": _when(m.get("at")),
            "model": short("model", 120) or None, "lane": lane, "ms": ms, "tool": tool,
            "arguments": m.get("arguments") if isinstance(m.get("arguments"), dict) else {},
            "why": short("why", 2000), "detail": short("detail", 2000),
            "ok": m.get("ok") if isinstance(m.get("ok"), bool) else None,
            "reasoning": short("reasoning", MAX_IMPORT_TEXT), "compacted": m.get("compacted") is True,
            "attachments": attachments}


def _markdown(chat: Chat, project_name: str, turns: list[ChatMessage]) -> str:
    line = active(turns)
    replaced = len(turns) - len(line)
    out = [f"# {chat.title}", "",
           f"{chat.ref} · {project_name} · started by {chat.started_by or 'someone'} on "
           f"{chat.created_at:%Y-%m-%d %H:%M} UTC", ""]
    if replaced:
        out += [f"_{replaced} replaced turns (edited questions, regenerated answers) are in the JSON export._", ""]
    for m in line:
        stamp = f"{m.at:%H:%M}" if m.at else ""
        if m.role == "you":
            out += [f"## {m.by or 'You'} · {stamp}", "", m.body, ""]
            chips = [f"`{a.get('kind')}: {a.get('name')}`" for a in (m.attachments or []) if isinstance(a, dict)]
            if chips:
                out += ["Attached: " + ", ".join(chips), ""]
        elif m.role == "assistant":
            by = " · ".join(x for x in (m.lane, m.model) if x)
            out += [f"## NeuroCode{f' ({by})' if by else ''} · {stamp}", "", m.body, ""]
        elif m.role == "tool" and m.tool == PERMISSION:
            asked = m.arguments or {}
            out += [f"> Permission: {asked.get('tool')} on `{asked.get('subject')}` — {asked.get('state')}"
                    + (f" by {asked.get('decidedBy')}" if asked.get("decidedBy") else ""), ""]
        elif m.role == "tool":
            out += [f"<details><summary>{m.tool} · {m.detail}</summary>", "", "```", m.body, "```", "",
                    "</details>", ""]
        elif m.role == "summary":
            out += ["> **Summary of earlier turns**", ">", *[f"> {x}" for x in m.body.splitlines()], ""]
        elif m.role == "note":
            out += [f"_{m.body}_", ""]
    return "\n".join(out).rstrip() + "\n"


def _cited(question: ChatMessage, after: list[ChatMessage]) -> list[str]:
    """The refs an answer rests on, in the order they were read, each once: files the tools read or
    traced, what the person attached, pages fetched, and MCP tools called."""
    seen: dict[str, None] = {}
    for a in question.attachments or []:
        if isinstance(a, dict) and a.get("kind") in ("file", "symbol", "fact", "plan"):
            where = f" ({a.get('path')}:{a.get('line')})" if a.get("path") else ""
            seen[f"{a.get('kind')} {a.get('name')}{where}"] = None
    for m in after:
        if m.role != "tool" or not m.ok:
            continue
        args = m.arguments or {}
        if m.tool == "read_file" and args.get("path"):
            seen[f"file {args['path']}" + (f" from line {args['start']}" if args.get("start") else "")] = None
        elif m.tool == "impact" and args.get("path"):
            seen[f"what depends on {args['path']}"] = None
        elif m.tool == "web_fetch" and args.get("url"):
            seen[f"web page {args['url']}"] = None
        elif m.tool == "mcp":
            seen[f"MCP {args.get('server')}/{args.get('tool')}"] = None
    return list(seen)[:24]
