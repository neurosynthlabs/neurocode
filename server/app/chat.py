"""A conversation that can act.

You ask; a model answers, or it reaches for a tool; the tool runs *here*, and what it found goes back
into the conversation. The model never runs anything itself — it names a tool from a fixed catalogue
and its arguments are checked before anything happens, exactly like the runtime, where a model may
only propose whole files. Every tool in this catalogue reads; none of them writes, so a session can
be left to think without anyone watching it.

Each turn is written to the database the moment it happens — your question, the tool calls with what
they returned, the answer. A session survives a reload, a restart and a crash, because losing work is
the thing this product exists to stop.
"""
from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, Field

from . import codeindex, onboarding, retrieval
from .ai.gateway import CHAT, NoModel, extract_json
from .context import Ctx
from .db import now_iso

MAX_STEPS = 6                 # tool calls in one answer, then it must answer with what it has
MAX_FILE_LINES = 400
MAX_OBSERVATION = 6_000       # what one tool may put back into the conversation
MAX_HISTORY = 24              # turns replayed to the model
MAX_QUESTION = 4_000


class Refused(RuntimeError):
    """A tool would not do what was asked, with the reason a person should read."""


# ── the tools ────────────────────────────────────────────────────
@dataclass(frozen=True)
class Tool:
    name: str
    takes: str                                            # the arguments, for the prompt
    what: str                                             # one line, for the prompt and the screen
    run: Callable[[Ctx, dict[str, Any], dict[str, Any]], tuple[str, str]]


def _project(c: Ctx, doc: dict[str, Any]) -> dict[str, Any]:
    project = c.store.get("projects", doc["projectId"])
    if project is None:
        raise Refused("This session's project is gone.")
    return project


def _root(c: Ctx, doc: dict[str, Any]) -> Path:
    root = onboarding.source_root(_project(c, doc))
    if root is None or not root.is_dir():
        raise Refused("This project's code is not on this machine, so its files cannot be read.")
    return root


def _safe(root: Path, rel: str) -> Path:
    """A path inside the project, never upwards and never into .git. Refused, never quietly rewritten."""
    p = PurePosixPath(str(rel).strip().replace("\\", "/"))
    parts = [part for part in p.parts if part != "."]
    if not parts or p.is_absolute() or any(part in ("..", ".git") for part in parts):
        raise Refused(f"{rel} is outside the project.")
    return root.joinpath(*parts)


def _text(args: dict[str, Any], key: str, *names: str) -> str:
    """Models name arguments loosely; take the first key that is actually there."""
    for k in (key, *names):
        value = args.get(k)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _read_file(c: Ctx, doc: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    path = _text(args, "path", "file", "filename")
    if not path:
        raise Refused("read_file needs a path.")
    start = max(1, int(args.get("start") or 1))
    want = min(MAX_FILE_LINES, max(1, int(args.get("lines") or 200)))
    f = _safe(_root(c, doc), path)
    if not f.is_file():
        raise Refused(f"{path} is not a file in this project.")
    lines = f.read_text(errors="replace").splitlines()
    shown = lines[start - 1:start - 1 + want]
    body = "\n".join(f"{start + i:>5}  {line}" for i, line in enumerate(shown))
    head = f"{path} · lines {start}-{start + len(shown) - 1} of {len(lines)}"
    return f"{head}\n{body}", f"{path} · {len(shown)} lines"


def _find(c: Ctx, doc: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    """Retrieval: the pieces of this workspace that bear on a question, by meaning and by words."""
    q = _text(args, "query", "q", "question")
    if not q:
        raise Refused("find needs a query.")
    found = retrieval.search(c, doc["projectId"], q, 6)
    if not found:
        return (f"Retrieval holds nothing about {q!r}. The project may not be indexed yet.",
                f"{q} · nothing found")
    body = "\n\n".join(f"[{x['kind']} · {x['ref']}]\n{x['text'][:700]}" for x in found)
    ways = ", ".join(sorted({x["how"] for x in found}))
    return f"{len(found)} pieces about {q!r}:\n\n{body}", f"{q} · {len(found)} pieces ({ways})"


def _search_code(c: Ctx, doc: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    q = _text(args, "query", "q", "name", "symbol")
    if not q:
        raise Refused("search_code needs a query.")
    found = codeindex.search(c.store, doc["projectId"], q, limit=25)
    if not found:
        return f"Nothing in the index matches {q!r}.", f"{q} · nothing found"
    body = "\n".join(f"{x['kind']:<10} {x['name']:<28} {x['path']}:{x['line']}" for x in found)
    return f"{len(found)} matches for {q!r}:\n{body}", f"{q} · {len(found)} matches"


def _list_files(c: Ctx, doc: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    where = _text(args, "directory", "dir", "path")
    tree = codeindex.children(c.store, doc["projectId"], where)
    dirs = ", ".join(f"{d['name']}/ ({d['files']})" for d in tree.get("dirs", [])) or "—"
    files = ", ".join(f"{f['path'].rsplit('/', 1)[-1]} ({f['lines']})" for f in tree.get("files", [])) or "—"
    return f"In {where or 'the project root'}:\nfolders: {dirs}\nfiles: {files}", f"{where or '/'} · listed"


def _impact(c: Ctx, doc: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    path = _text(args, "path", "file")
    if not path:
        raise Refused("impact needs a path.")
    out = codeindex.impact(c.store, doc["projectId"], path=path)
    if out is None:
        raise Refused(f"{path} is not in the code index.")
    return f"What moves if {path} changes:\n{json.dumps(out)[:MAX_OBSERVATION]}", f"{path} · blast radius"


def _search_memory(c: Ctx, doc: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    q = _text(args, "query", "q", "question")
    facts = c.store.memory(q, None, None, False)[:12]
    if not facts:
        return f"Memory holds nothing about {q!r}.", f"{q} · nothing remembered"
    body = "\n".join(f"{f['ref']} · {f['title']} — {(f.get('body') or '')[:200]}" for f in facts)
    return f"{len(facts)} facts:\n{body}", f"{q} · {len(facts)} facts"


def _project_summary(c: Ctx, doc: dict[str, Any], args: dict[str, Any]) -> tuple[str, str]:
    project = _project(c, doc)
    index = codeindex.summary(c.store, doc["projectId"])
    if index is None:
        return f"{project['name']} has no code index yet. Onboard it to read its code.", "not indexed"
    return f"{project['name']}:\n{json.dumps(index)[:MAX_OBSERVATION]}", f"{project['name']} · indexed"


TOOLS: tuple[Tool, ...] = (
    Tool("find", '{"query": "where is the interstate tax split"}',
         "search this project's code, its documents and the workspace's memory by meaning as well as by "
         "words — start here when you do not know the name of the thing", _find),
    Tool("search_code", '{"query": "TaxService"}', "find where a name is defined in this project's code", _search_code),
    Tool("read_file", '{"path": "pkg/tax.py", "start": 1, "lines": 200}', "read part of a file, with line numbers", _read_file),
    Tool("list_files", '{"directory": "pkg"}', "list one level of the file tree", _list_files),
    Tool("impact", '{"path": "pkg/tax.py"}', "what depends on a file, directly and through others", _impact),
    Tool("search_memory", '{"query": "gst"}', "search the workspace's remembered facts and decisions", _search_memory),
    Tool("project_summary", "{}", "the project's languages, modules and size", _project_summary),
)
BY_NAME = {t.name: t for t in TOOLS}


# ── what the model may answer ────────────────────────────────────
class Turn(BaseModel):
    tool: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    why: str = ""
    answer: str = ""


def _system(c: Ctx, doc: dict[str, Any]) -> str:
    catalogue = "\n".join(f'- {t.name} {t.takes} — {t.what}' for t in TOOLS)
    project = c.store.get("projects", doc["projectId"]) or {}
    return (
        "You are NeuroCode, working inside an engineering workspace. You answer questions about one "
        f"project: {project.get('name', 'this project')}.\n\n"
        "You cannot see the code until you read it. Use the tools, one at a time, until you know enough, "
        "then answer from what they returned. Never invent a file, a symbol, a line number or a fact — if "
        "the tools do not show it, say so plainly.\n\n"
        f"Tools:\n{catalogue}\n\n"
        "Answer with one JSON object and nothing else.\n"
        'To use a tool: {"tool": "<name>", "arguments": {…}, "why": "<a short line for the person watching>"}\n'
        'To answer: {"answer": "<your answer, in the language the person used>"}\n'
        f"You may call at most {MAX_STEPS} tools before you must answer with what you have."
    )


def _wire(c: Ctx, doc: dict[str, Any]) -> list[dict[str, str]]:
    """The conversation as the model sees it: the system prompt, then the turns, oldest last kept."""
    out: list[dict[str, str]] = [{"role": "system", "content": _system(c, doc)}]
    for m in c.store.messages(doc["id"])[-MAX_HISTORY:]:
        if m["role"] == "you":
            out.append({"role": "user", "content": m["text"][:MAX_QUESTION]})
        elif m["role"] == "assistant":
            out.append({"role": "assistant", "content": json.dumps({"answer": m["text"]})})
        elif m["role"] == "tool":
            out.append({"role": "assistant", "content": json.dumps({"tool": m["tool"], "arguments": m.get("arguments", {})})})
            out.append({"role": "user", "content": f"Result of {m['tool']}:\n{m['text'][:MAX_OBSERVATION]}"})
    return out


# ── keeping the conversation ─────────────────────────────────────
def say(c: Ctx, doc: dict[str, Any], role: str, text: str, **extra: Any) -> dict[str, Any]:
    """One turn, stored and streamed to every open tab in the same breath."""
    message = {"at": now_iso(), "role": role, "text": text, **extra}
    saved = c.store.add_message(doc["id"], message)
    c.bus.publish("chat", {"sessionRef": doc["ref"], **saved})
    return saved


def _save(c: Ctx, doc: dict[str, Any]) -> dict[str, Any]:
    doc["lastAt"] = now_iso()
    return c.store.save_chat(doc)


def start(c: Ctx, project: dict[str, Any], by: str, title: str = "") -> dict[str, Any]:
    n = c.store.next_chat_number()
    doc = {
        "id": f"s{n}-{int(time.time())}", "ref": f"CHAT-{n}", "projectId": project["id"],
        "projectName": project["name"], "title": title.strip()[:80] or "New session", "status": "idle",
        "startedAt": now_iso(), "lastAt": now_iso(), "startedBy": by, "turns": 0, "toolCalls": 0,
        "model": None, "lane": None, "note": "",
    }
    return c.store.insert_chat(doc)


def _cancelled(c: Ctx, ref: str) -> bool:
    return bool(c.runtime.get(f"chat:{ref}", {}).get("cancel", threading.Event()).is_set())


def cancel(c: Ctx, ref: str) -> None:
    c.runtime.setdefault(f"chat:{ref}", {}).setdefault("cancel", threading.Event()).set()


def think(c: Ctx, ref: str, by: str) -> None:
    """Answer the last question: read with the tools until the model has enough, then say what it found.
    Blocking — FastAPI runs it as a background task, exactly like a run."""
    doc = c.store.one("chats", ref)
    if doc is None:
        return
    c.runtime.setdefault(f"chat:{ref}", {})["cancel"] = threading.Event()
    doc["status"] = "thinking"
    _save(c, doc)
    # Before the model is asked anything, retrieval answers the cheapest question: what does this
    # workspace already hold about this? Grounding is a turn like any other, so the model replays it
    # and the person can see exactly what it was given.
    asked = next((m["text"] for m in reversed(c.store.messages(doc["id"])) if m["role"] == "you"), "")
    if asked:
        try:
            ground, pieces = retrieval.grounding(c, doc["projectId"], asked)
        except Exception:                                   # retrieval must never stop a conversation
            ground, pieces = "", 0
        if ground:
            say(c, doc, "tool", ground, tool="grounding", detail=f"{pieces} pieces from the index", ok=True)
    try:
        for step in range(MAX_STEPS + 1):
            if _cancelled(c, ref):
                say(c, doc, "note", "You stopped this answer.")
                break
            last = step == MAX_STEPS
            messages = _wire(c, doc)
            if last:
                messages.append({"role": "user", "content":
                                 "You have used every tool call. Answer now with what you already have, "
                                 'as {"answer": "…"}.'})
            try:
                result = c.gateway.ask(messages, lambda raw: Turn.model_validate(extract_json(raw, trim=False)),
                                       feature="chat", actor=by, project=doc["projectId"], role=CHAT)
            except NoModel as e:
                say(c, doc, "note", str(e))
                doc["note"] = str(e)
                break
            except Exception as e:                       # every lane failed, or none could be parsed
                say(c, doc, "note", f"No lane could answer: {type(e).__name__}. Try again in a moment.")
                doc["note"] = str(e)[:200]
                break
            doc["model"], doc["lane"] = result.provider.model, result.provider.id
            turn = result.data
            if turn.answer or not turn.tool or last:
                # On the last step whatever comes back is the answer: a session always ends with words.
                say(c, doc, "assistant",
                    turn.answer or f"I read what I could in {MAX_STEPS} tool calls without reaching an answer.",
                    model=result.provider.model, lane=result.provider.id, ms=result.ms)
                doc["turns"] += 1
                break
            tool = BY_NAME.get(turn.tool.strip())
            if tool is None:
                say(c, doc, "tool", f"There is no tool called {turn.tool!r}. The tools are: "
                                    f"{', '.join(BY_NAME)}.", tool=turn.tool.strip()[:40] or "?",
                    arguments=turn.arguments, detail="refused", ok=False)
                continue
            try:
                observation, detail = tool.run(c, doc, turn.arguments or {})
            except Refused as e:
                observation, detail, ok = str(e), "refused", False
            except Exception as e:                        # a tool that breaks must not end the session
                observation, detail, ok = f"{type(e).__name__}: {e}", "failed", False
            else:
                ok = True
            doc["toolCalls"] += 1
            say(c, doc, "tool", observation[:MAX_OBSERVATION], tool=tool.name, arguments=turn.arguments or {},
                why=turn.why[:160], detail=detail, ok=ok)
    finally:
        doc["status"] = "idle"
        _save(c, doc)
        c.runtime.pop(f"chat:{ref}", None)


def ask(c: Ctx, doc: dict[str, Any], question: str, by: str) -> dict[str, Any]:
    """Your question, kept before anything else happens — so it is never lost if the model is not."""
    text = question.strip()[:MAX_QUESTION]
    if not text:
        raise Refused("There is nothing to ask.")
    if doc["turns"] == 0 and doc["title"] == "New session":
        doc["title"] = text.splitlines()[0][:80]
    message = say(c, doc, "you", text, by=by)
    doc["status"] = "thinking"
    _save(c, doc)
    return message
