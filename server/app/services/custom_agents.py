"""Custom agents: the ones a person writes here, and the ones a repository declares as files.

Three places an agent comes from, one shape once read (`AgentSpec`):

- **stored** — a row in `custom_agents`, for the whole workspace or one project, written on the Agents
  screen by someone with `agents:manage`;
- **files** — `.neurocode/agents/*.md` and `.claude/agents/*.md` in the project's checkout, front matter
  (`name`, `description`, `tools`, `model`, `mode`, `maxSteps`) and a body that is the prompt. Read from
  disk every time, like skills, and never written: the repository is their truth;
- **built in** — the roster's own agents, which a session may be asked through with their declared prompt.

An agent is instructions, a lane it prefers and a list of tools it may ask for. It is never more than
that: every tool it calls still goes through the tool rules a person wrote, so an agent that lists
`edit` or `web_fetch` is only asking to be offered them — a rule that denies or asks still does. It
cannot grant itself anything, and a tool the product does not have (`Bash`, `Task`) is listed as
ignored rather than quietly mapped onto something it is not.

Names resolve the way commands do, nearest first: a project's stored agent, then the project's files
(.neurocode before .claude), then the workspace's stored agents. One that loses to another of the same
name is listed as shadowed; one named like a roster agent is never used, because a plan step naming
"Backend Engineer" must always mean the roster's.
"""
from __future__ import annotations

import asyncio
import math
import re
import secrets as token
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai import lanes
from ..ai.gateway import CHAT, Gateway, Result
from ..data import roster
from ..data.catalogue import AGENTS as ROSTER
from ..models import CustomAgent, Project
from ..repositories import ActivityRepository, AuditRepository, NotFound, ProjectRepository
from ..repositories.custom_agents import MAX_AGENTS, CustomAgentRepository
from ..schemas.work import when
from .code import checkout
from .errors import Refused, needs_a_model
from .extensions import _inside, _read, _words, front_matter
from .identity import Person

MANAGE = "agents:manage"
#: The tools an agent may name, where each one is offered, and what it does — the session's catalogue and
#: its acting tools, and the one thing a run's agent does. The Agents screen draws its checkboxes from this.
TOOLS: tuple[tuple[str, str, str], ...] = (
    ("find", "session", "search the code, documents and memory by meaning and by words"),
    ("search_code", "session", "find where a name is defined"),
    ("read_file", "session", "read part of a file"),
    ("list_files", "session", "list what is indexed under a folder"),
    ("impact", "session", "what depends on a file"),
    ("search_memory", "session", "search remembered facts and decisions"),
    ("project_summary", "session", "the project's languages and size"),
    ("load_skill", "session", "read a skill's instructions"),
    ("web_fetch", "session", "read a public web page — asks first unless a tool rule allows it"),
    ("web_search", "session", "search the web — asks first unless a tool rule allows it"),
    ("mcp", "session", "call a connected MCP server's tool — asks first unless a rule or the server allows it"),
    ("edit", "run", "write files in the run's own worktree — still weighed by the edit rules"),
)
TOOL_NAMES = tuple(t[0] for t in TOOLS)
#: Claude Code's and OpenCode's tool names, and the tools here that do the same thing. A name with no
#: counterpart — Bash, Task, TodoWrite — is kept as ignored, never mapped onto something it is not.
ALIASES: dict[str, tuple[str, ...]] = {
    "read": ("read_file",), "grep": ("search_code", "find"), "glob": ("list_files",), "ls": ("list_files",),
    "list": ("list_files",), "edit": ("edit",), "write": ("edit",), "multiedit": ("edit",), "patch": ("edit",),
    "notebookedit": ("edit",), "webfetch": ("web_fetch",), "websearch": ("web_search",), "mcp": ("mcp",),
}
MODES = ("primary", "subagent")
#: Where a repository declares agents, nearest first.
FOLDERS = (".neurocode/agents", ".claude/agents")
MAX_NAME, MAX_ROLE, MAX_PROMPT = 80, 160, 20_000
#: Tool calls one answer of an agent may make. A person may narrow a session's six or widen them to this.
MAX_AGENT_STEPS, DEFAULT_STEPS = 12, 8
#: Files read from one agents folder, at most.
MAX_FILES = 200
#: A dry run's question and the answer it may take, in characters.
MAX_TRY = 4_000
NAME = re.compile(r"^[\w][\w .+()&'-]{0,79}$")
RESERVED = {n.casefold() for n in (*roster.NAMES, roster.YOU, roster.ORCHESTRATOR, roster.UNNAMED, roster.APPROVER,
                                   "NeuroCode", "Completion check")}


@dataclass(frozen=True)
class AgentSpec:
    """One agent, wherever it came from, as the runtime and a session use it."""

    key: str                     # custom:<id> | file:<name> | a roster agent's id
    name: str
    role: str
    prompt: str
    lane: str | None             # the lane it prefers; the router still decides when that one cannot answer
    tools: tuple[str, ...]       # empty: every tool the product has, each still under the tool rules
    max_steps: int
    mode: str
    source: str                  # workspace | project | .neurocode/agents | .claude/agents | built-in
    project_id: str | None = None
    id: str | None = None
    path: str = ""
    model: str = ""              # what a file's front matter said, as written
    ignored: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    shadowed_by: str | None = None
    truncated: bool = False
    created_by: str | None = None
    created_at: Any = None
    updated_at: Any = None

    def may(self, tool: str) -> bool:
        return not self.tools or tool in self.tools

    @property
    def stored(self) -> bool:
        return self.id is not None

    def as_prompt(self) -> str:
        """What the model is told about whom it answers as. The runtime's own rules come after it and win."""
        who = f"You are {self.name}" + (f", {self.role}" if self.role else "") + "."
        return f"{who}\n{self.prompt.strip()}" if self.prompt.strip() else who


# ── reading agents from a repository ─────────────────────────────
def lane_of(model: str) -> tuple[str | None, str]:
    """A front matter `model` as a lane here: a lane's id, `provider/model` whose provider is a lane, or a
    lane's own model name. Anything else — `sonnet`, `inherit` — leaves the router to choose, and says so."""
    text = model.strip()
    low = text.lower()
    if not low or low == "inherit":
        return None, ""
    if low in lanes.BY_ID:
        return low, ""
    head = low.split("/", 1)[0]
    if "/" in low and head in lanes.BY_ID:
        return head, ""
    for lane in lanes.LANES:
        if lane.model.lower() == low:
            return lane.id, ""
    return None, f"{text} is not a lane here, so the router chooses."


def tools_of(value: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """A front matter `tools` as the tools here and the names that have none. Claude Code writes a list
    (`Read, Grep, Bash(git:*)`); OpenCode a map of switches (`write: false`), which turns tools off from all."""
    if isinstance(value, str) and re.search(r"\b\w+\s*:\s*(true|false)\b", value, re.I):
        off = {m.group(1).lower() for m in re.finditer(r"\b(\w+)\s*:\s*false\b", value, re.I)}
        banned = {t for name in off for t in ALIASES.get(name, (name,) if name in TOOL_NAMES else ())}
        kept = tuple(t for t in TOOL_NAMES if t not in banned)
        return (kept if banned else ()), ()
    kept: list[str] = []
    ignored: list[str] = []
    for raw in _words(value):
        bare = raw.split("(", 1)[0].strip()
        low = bare.lower()
        if low.startswith("mcp__"):
            low = "mcp"
        mapped = (low,) if low in TOOL_NAMES else ALIASES.get(low)
        if mapped:
            kept += [t for t in mapped if t not in kept]
        elif bare and bare not in ignored:
            ignored.append(bare)
    return tuple(kept), tuple(ignored)


def _steps(value: Any) -> int:
    try:
        return max(1, min(int(str(value).strip()), MAX_AGENT_STEPS))
    except (TypeError, ValueError):
        return DEFAULT_STEPS


def read_folder(root: Path, folder: str, project_id: str) -> tuple[list[AgentSpec], list[str]]:
    """Every agent file in one folder of a checkout, and the files whose front matter could not be read.
    Blocking. A file is read only if it is still inside the checkout once links are followed."""
    where = root / folder
    if not where.is_dir() or not _inside(root, where):
        return [], []
    found: list[AgentSpec] = []
    unreadable: list[str] = []
    for path in sorted(where.glob("*.md"))[:MAX_FILES]:
        if not _inside(root, path):
            continue
        text = _read(path)
        if text is None:
            continue
        meta, body, readable = front_matter(text)
        shown = f"{folder}/{path.name}"
        if not readable:
            unreadable.append(shown)
            continue
        name = " ".join(str(meta.get("name") or path.stem).split())[:MAX_NAME]
        tools, ignored = tools_of(meta.get("tools"))
        model = str(meta.get("model") or "")
        lane, note = lane_of(model)
        mode = str(meta.get("mode") or "subagent").strip().lower()
        notes = [note] if note else []
        if mode not in MODES:
            # OpenCode's "all" is both: offered to plans and to sessions, which is what a subagent is here.
            notes += [] if mode == "all" else [f"mode {mode} is not one NeuroCode knows; read as subagent."]
            mode = "subagent"
        prompt = body.strip()
        found.append(AgentSpec(
            key=f"file:{name}", name=name, role=str(meta.get("description") or "")[:MAX_ROLE].strip(),
            prompt=prompt[:MAX_PROMPT], lane=lane, tools=tools, ignored=ignored,
            max_steps=_steps(meta.get("maxSteps") or meta.get("max_steps") or meta.get("steps")),
            mode=mode, source=folder, project_id=project_id, path=shown, model=model, notes=tuple(notes),
            truncated=len(prompt) > MAX_PROMPT))
    return found, unreadable


def from_files(root: Path | None, project_id: str) -> tuple[list[AgentSpec], list[str]]:
    """The project's own agent files, .neurocode first. Blocking."""
    if root is None or not root.is_dir():
        return [], []
    specs: list[AgentSpec] = []
    unreadable: list[str] = []
    for folder in FOLDERS:
        got, bad = read_folder(root, folder, project_id)
        specs += got
        unreadable += bad
    return specs, unreadable


def _stored(row: CustomAgent) -> AgentSpec:
    return AgentSpec(key=f"custom:{row.id}", name=row.name, role=row.role, prompt=row.prompt, lane=row.lane,
                     tools=tuple(t for t in (row.tools or []) if t in TOOL_NAMES), max_steps=row.max_steps,
                     mode=row.mode, source="project" if row.project_id else "workspace",
                     project_id=row.project_id, id=row.id, created_by=row.created_by, created_at=row.created_at,
                     updated_at=row.updated_at)


def _built_in(agent_id: str) -> AgentSpec | None:
    found = next((a for a in ROSTER if a.id == agent_id), None)
    if found is None:
        return None
    return AgentSpec(key=found.id, name=found.name, role=found.role, prompt=found.system_prompt, lane=None,
                     tools=(), max_steps=DEFAULT_STEPS, mode="primary", source="built-in")


def ranked(project: list[AgentSpec], files: list[AgentSpec], workspace: list[AgentSpec]) -> list[AgentSpec]:
    """Every agent in resolution order, each that loses its name to a nearer one marked with who won — and
    one named like a roster agent marked as shadowed by the roster."""
    out: list[AgentSpec] = []
    taken: dict[str, str] = {}
    for spec in [*project, *files, *workspace]:
        low = spec.name.casefold()
        if low in RESERVED:
            out.append(replace(spec, shadowed_by="built-in"))
        elif low in taken:
            out.append(replace(spec, shadowed_by=taken[low]))
        else:
            taken[low] = spec.key
            out.append(spec)
    return out


def spec_json(spec: AgentSpec, *, sessions: tuple[int, Any] | None = None,
              steps: tuple[int, int] | None = None, names: dict[str, str] | None = None) -> dict[str, Any]:
    """An agent as the Agents screen reads it. Usage is counted, never estimated: sessions asked through it
    and writing steps it owned in runs, with how many of those finished done."""
    asked, last = sessions or (0, None)
    owned, done = steps or (0, 0)
    return {
        "key": spec.key, "id": spec.id, "name": spec.name, "role": spec.role, "prompt": spec.prompt,
        "lane": spec.lane, "model": spec.model or None, "tools": list(spec.tools), "ignored": list(spec.ignored),
        "maxSteps": spec.max_steps, "mode": spec.mode, "source": spec.source, "projectId": spec.project_id,
        "path": spec.path or None, "editable": spec.stored, "notes": list(spec.notes),
        "shadowedBy": spec.shadowed_by, "truncated": spec.truncated,
        "tokens": math.ceil(len(spec.prompt) / 4),
        "createdBy": (names or {}).get(spec.created_by or "", None) if spec.created_by else None,
        "createdAt": when(spec.created_at), "updatedAt": when(spec.updated_at),
        "usage": {"sessions": asked, "lastSession": when(last), "steps": owned, "stepsDone": done},
    }


# ── the service ──────────────────────────────────────────────────
@dataclass
class Draft:
    """What a person writes on the "New agent" form."""

    name: str
    role: str = ""
    prompt: str = ""
    lane: str | None = None
    tools: list[str] = field(default_factory=list)
    max_steps: int = DEFAULT_STEPS
    mode: str = "subagent"
    project_id: str | None = None


class Answer(BaseModel):
    answer: str


class CustomAgentService:
    """The Agents screen's custom agents, and resolving an agent for a run step or a session."""

    def __init__(self, session: AsyncSession, gateway: Gateway | None = None) -> None:
        self.session = session
        self.gateway = gateway
        self.rows = CustomAgentRepository(session)
        self.activity = ActivityRepository(session)
        self.audit = AuditRepository(session)

    async def _project(self, project_id: str | None) -> Project | None:
        if project_id is None:
            return None
        project = await ProjectRepository(self.session).get(project_id)
        if project is None:
            raise NotFound(f"project {project_id}")
        return project

    async def everyone(self, project: Project | None) -> tuple[list[AgentSpec], list[str], bool]:
        """Every agent in resolution order, the agent files that could not be read, and whether the
        project's checkout was there to read files from."""
        rows = await self.rows.visible(project.id if project else None)
        mine = [_stored(r) for r in rows if project is not None and r.project_id == project.id]
        shared = [_stored(r) for r in rows if r.project_id is None]
        root = checkout(project) if project is not None else None
        on_disk = root is not None and root.is_dir()
        files, unreadable = (await asyncio.to_thread(from_files, root, project.id)
                             if project is not None and on_disk else ([], []))
        return ranked(mine, files, shared), unreadable, on_disk

    # ── reading ──────────────────────────────────────────────────
    async def catalogue(self, project_id: str | None) -> dict[str, Any]:
        project = await self._project(project_id)
        specs, unreadable, on_disk = await self.everyone(project)
        sessions = await self.rows.sessions_asked([s.key for s in specs], project_id)
        steps = await self.rows.steps_owned(list({s.name for s in specs if not s.shadowed_by}), project_id)
        people = await self.rows.authors([s.created_by for s in specs if s.created_by])
        return {
            "agents": [spec_json(s, sessions=sessions.get(s.key), steps=None if s.shadowed_by else steps.get(s.name),
                                 names=people) for s in specs],
            "folders": [{"path": f, "read": on_disk} for f in FOLDERS] if project is not None else [],
            "checkout": on_disk if project is not None else None,
            "unreadable": unreadable[:50],
            "tools": [{"name": n, "where": w, "what": what} for n, w, what in TOOLS],
            "lanes": [{"id": x.id, "label": x.label, "model": x.model} for x in lanes.LANES],
            "limits": {"name": MAX_NAME, "role": MAX_ROLE, "prompt": MAX_PROMPT, "maxSteps": MAX_AGENT_STEPS,
                       "perScope": MAX_AGENTS},
        }

    async def resolve(self, project: Project | None, key: str) -> AgentSpec | None:
        """The agent a key names, as it is now — None when it is gone, belongs to another project, or has
        lost its name to a nearer one."""
        kind, _, rest = key.partition(":")
        if kind == "custom" and rest:
            row = await self.rows.get(rest)
            if row is None or (row.project_id is not None and (project is None or row.project_id != project.id)):
                return None
            spec = _stored(row)
            if spec.name.casefold() in RESERVED:
                return None
            return spec
        if kind == "file" and rest:
            if project is None:
                return None
            specs, _, _ = await self.everyone(project)
            return next((s for s in specs if s.key == key and not s.shadowed_by), None)
        if not rest:
            return _built_in(key)
        return None

    async def by_name(self, project: Project | None, name: str) -> AgentSpec | None:
        """The custom agent a plan step names, nearest first. None for a roster agent — the runtime's own —
        and for a name nobody defined."""
        if not name or name.casefold() in RESERVED:
            return None
        specs, _, _ = await self.everyone(project)
        return next((s for s in specs if not s.shadowed_by and s.name.casefold() == name.casefold()), None)

    async def for_compiler(self, project: Project | None) -> list[dict[str, str]]:
        """What the compiler may hand a step to beyond the roster: the subagents that are not shadowed.
        A primary agent is one a person talks to in a session; it is not given plan steps."""
        specs, _, _ = await self.everyone(project)
        return [{"name": s.name, "role": s.role or s.prompt[:120]} for s in specs
                if not s.shadowed_by and s.mode == "subagent" and s.may("edit")]

    # ── writing ──────────────────────────────────────────────────
    def _checked(self, draft: Draft) -> Draft:
        name = " ".join(draft.name.split())
        if not name or len(name) > MAX_NAME or not NAME.match(name):
            raise Refused(f"A name is 1 to {MAX_NAME} characters of letters, digits, spaces and . + ( ) & ' -.",
                          status=422)
        if name.casefold() in RESERVED:
            raise Refused(f"{name} is a built-in agent's name. Pick another, so a plan step that names it is never "
                          "ambiguous.", status=422)
        role = " ".join(draft.role.split())
        if len(role) > MAX_ROLE:
            raise Refused(f"A role is at most {MAX_ROLE} characters.", status=422)
        prompt = draft.prompt.strip()
        if not prompt:
            raise Refused("An agent needs instructions: what it is for and how it works.", status=422)
        if len(prompt) > MAX_PROMPT:
            raise Refused(f"Instructions are at most {MAX_PROMPT:,} characters.", status=422)
        if draft.lane is not None and draft.lane not in lanes.BY_ID:
            raise Refused(f"There is no lane called {draft.lane}. Lanes are: {', '.join(lanes.BY_ID)}.", status=422)
        unknown = [t for t in draft.tools if t not in TOOL_NAMES]
        if unknown:
            raise Refused(f"There is no tool called {unknown[0]}. Tools are: {', '.join(TOOL_NAMES)}.", status=422)
        if draft.mode not in MODES:
            raise Refused("An agent is primary (you talk to it) or a subagent (plans may hand it steps).", status=422)
        if not 1 <= draft.max_steps <= MAX_AGENT_STEPS:
            raise Refused(f"Tool calls per answer are 1 to {MAX_AGENT_STEPS}.", status=422)
        return replace(draft, name=name, role=role, prompt=prompt,
                       tools=[t for t in TOOL_NAMES if t in set(draft.tools)])

    async def create(self, draft: Draft, who: Person, ip: str = "") -> dict[str, Any]:
        who.must(MANAGE, "write a custom agent")
        clean = self._checked(draft)
        await self._project(clean.project_id)
        if await self.rows.named(clean.project_id, clean.name) is not None:
            raise Refused(f"There is already an agent called {clean.name} "
                          f"{'in this project' if clean.project_id else 'in the workspace'}. Edit that one instead.")
        if await self.rows.in_scope(clean.project_id) >= MAX_AGENTS:
            raise Refused(f"A scope holds at most {MAX_AGENTS} agents. Remove one you no longer use first.")
        row = await self.rows.add(CustomAgent(
            id=f"ag-{token.token_hex(5)}", project_id=clean.project_id, name=clean.name, role=clean.role,
            prompt=clean.prompt, lane=clean.lane, tools=list(clean.tools), max_steps=clean.max_steps,
            mode=clean.mode, created_by=who.id))
        await self._record(who, "Custom agent added", "custom_agent.create", row, ip,
                           {"name": row.name, "projectId": row.project_id, "lane": row.lane, "tools": row.tools,
                            "mode": row.mode, "maxSteps": row.max_steps})
        return await self.one(row.id)

    async def update(self, agent_id: str, draft: Draft, who: Person, ip: str = "") -> dict[str, Any]:
        """Change what an agent is. Its scope stays: an agent moved to another project is a different
        agent, deleted and written again."""
        who.must(MANAGE, "change a custom agent")
        row = await self.rows.get(agent_id)
        if row is None:
            raise NotFound(f"agent {agent_id}")
        clean = self._checked(replace(draft, project_id=row.project_id))
        other = await self.rows.named(row.project_id, clean.name)
        if other is not None and other.id != row.id:
            raise Refused(f"There is already an agent called {clean.name} in the same scope.")
        before = {"name": row.name, "role": row.role, "prompt": row.prompt, "lane": row.lane,
                  "tools": list(row.tools or []), "maxSteps": row.max_steps, "mode": row.mode}
        row.name, row.role, row.prompt, row.lane = clean.name, clean.role, clean.prompt, clean.lane
        row.tools, row.max_steps, row.mode = list(clean.tools), clean.max_steps, clean.mode
        after = {"name": row.name, "role": row.role, "prompt": row.prompt, "lane": row.lane,
                 "tools": list(row.tools), "maxSteps": row.max_steps, "mode": row.mode}
        changed = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
        if changed:
            await self.session.flush()
            # The prompt can be long; the audit keeps that it changed and how long it became.
            shown = {k: ({"from": len(v["from"]), "to": len(v["to"]), "chars": True} if k == "prompt" else v)
                     for k, v in changed.items()}
            await self._record(who, "Custom agent changed", "custom_agent.update", row, ip, shown)
        return await self.one(row.id)

    async def delete(self, agent_id: str, who: Person, ip: str = "") -> dict[str, Any]:
        """Gone for new work. Runs and sessions it already did keep its name, because that is what they
        were written with; a session asked through it can no longer be asked again."""
        who.must(MANAGE, "delete a custom agent")
        row = await self.rows.get(agent_id)
        if row is None:
            raise NotFound(f"agent {agent_id}")
        await self._record(who, "Custom agent removed", "custom_agent.delete", row, ip,
                           {"name": row.name, "projectId": row.project_id})
        await self.rows.remove(row)
        return {"ok": True, "id": agent_id}

    async def one(self, agent_id: str) -> dict[str, Any]:
        row = await self.rows.get(agent_id)
        if row is None:
            raise NotFound(f"agent {agent_id}")
        spec = _stored(row)
        sessions = await self.rows.sessions_asked([spec.key], row.project_id)
        steps = await self.rows.steps_owned([spec.name], row.project_id)
        people = await self.rows.authors([row.created_by] if row.created_by else [])
        return spec_json(spec, sessions=sessions.get(spec.key), steps=steps.get(spec.name), names=people)

    async def _record(self, who: Person, what: str, audited: str, row: CustomAgent, ip: str,
                      detail: dict[str, Any]) -> None:
        """Both logs: the team's feed, and the audit log — an agent's instructions are sent to a model on
        work that reaches a person's code, which is what the audit log is for."""
        await self.activity.record(actor=who.name, actor_kind="human", action=what,
                                   detail=f"{row.name} · {row.project_id or 'workspace'}", project_id=row.project_id)
        await self.audit.record(action=audited, user_id=who.id, target=f"custom agent {row.id}", detail=detail, ip=ip)

    # ── trying one ───────────────────────────────────────────────
    async def dry_run(self, key: str, project_id: str | None, question: str, who: Person) -> dict[str, Any]:
        """Ask the agent one question with no tools and nothing written but the usage line: what it would
        say, on the lane it prefers, so its instructions can be judged before any work is handed to it."""
        if self.gateway is None:
            raise Refused("No model gateway is available here.", status=409)
        text = question.strip()
        if not text:
            raise Refused("There is nothing to ask.", status=422)
        if len(text) > MAX_TRY:
            raise Refused(f"A question is at most {MAX_TRY:,} characters.", status=422)
        project = await self._project(project_id)
        spec = await self.resolve(project, key)
        if spec is None:
            raise NotFound(f"agent {key}")
        system = (f"{spec.as_prompt()}\n\nThis is a dry run: someone is trying these instructions out. You have no "
                  "tools and cannot read the code, so answer from the instructions and the question alone, and "
                  "say plainly what you would need to read to answer properly. Reply with one JSON object and "
                  'nothing else: {"answer": "…"}')
        messages = [{"role": "system", "content": system}, {"role": "user", "content": text}]
        gw = self.gateway

        def ask() -> Result[Answer]:
            from ..ai.gateway import extract_json
            return gw.ask(messages, lambda raw: Answer.model_validate(extract_json(raw, trim=False)),
                          feature="chat", actor=who.id, project=project_id, role=CHAT, lane=spec.lane,
                          agent=spec.name)

        result = await asyncio.to_thread(needs_a_model, ask)
        await self.activity.record(actor=who.name, actor_kind="human", action="Agent tried",
                                   detail=f"{spec.name} · answered by {result.provider.id} · {result.provider.model}",
                                   project_id=project_id)
        return {"agent": spec.key, "name": spec.name, "answer": result.data.answer, "lane": result.provider.id,
                "model": result.provider.model, "ms": result.ms, "reasoning": result.reasoning or None,
                "preferred": spec.lane, "onPreferred": spec.lane is None or result.provider.id == spec.lane,
                "tokens": {"in": int(result.usage.get("in") or 0), "out": int(result.usage.get("out") or 0)}}
