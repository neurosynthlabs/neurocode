"""Filling the schema: the catalogue every workspace starts with, and documents carried in from elsewhere.

Two jobs, and they are kept apart on purpose. `sync_roles` and `sync_agents` write the product's own
catalogue — the built-in roles and the agent roster — on every start, and are the only rows a new
workspace holds. `load_seed` turns documents in the old document store's shapes into rows: the SQLite
importer hands it a running workspace, and the tests hand it their fixture. It is never given anything
by default, so nothing reaches a real workspace that someone did not bring.

It is also where the document store's shapes meet the relational one, so it is the honest test of the
schema — a field with nowhere to go shows up here rather than in a screen that quietly renders nothing.

Loading replaces: the tables it writes are emptied and written again inside one transaction, so a
half-written workspace is never visible. What people made themselves — accounts, keys, audit — and
the catalogue are never touched.
"""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import (
    ActivityEvent,
    Agent,
    Approval,
    ChecklistItem,
    McpServer,
    McpTool,
    MemoryConflict,
    MemoryFact,
    MemoryTag,
    Plan,
    PlanQuestion,
    PlanStep,
    Project,
    Role,
    RolePermission,
    Task,
    TaskAgent,
)
from .catalogue import AGENTS, ROLES

#: Emptied and rewritten by a load, child rows first. Accounts, sessions, audit, keys and the catalogue
#: are not here.
LOADED = (MemoryTag, MemoryFact, MemoryConflict, ChecklistItem, TaskAgent, Task, PlanStep, PlanQuestion,
          Plan, Approval, ActivityEvent, McpTool, McpServer, Project)

SIZES = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


def count(value: Any) -> int:
    """"412K" and "1.2M" are how the screens show a size; the database keeps the number."""
    if isinstance(value, int | float):
        return int(value)
    if not isinstance(value, str) or not value.strip():
        return 0
    m = re.match(r"^\s*([\d.,]+)\s*([kKmMbB])?", value)
    if not m:
        return 0
    try:
        n = float(m.group(1).replace(",", ""))
    except ValueError:
        return 0
    return int(n * SIZES.get((m.group(2) or "").lower(), 1))


#: "2 min ago", "3 h ago", "6 d ago" — how the old document store wrote times, back when these were
#: display strings kept in a document and never compared to anything.
AGO = re.compile(r"^\s*(\d+)\s*(min|minute|minutes|h|hour|hours|d|day|days|w|week|weeks)\s+ago\s*$", re.I)
AGO_UNITS = {"min": "minutes", "minute": "minutes", "minutes": "minutes", "h": "hours", "hour": "hours",
             "hours": "hours", "d": "days", "day": "days", "days": "days", "w": "weeks",
             "week": "weeks", "weeks": "weeks"}


def when(value: Any) -> datetime | None:
    """A real timestamp, or nothing.

    An old workspace still says "2 min ago", because in the old store these were strings a screen
    printed and nothing ever sorted or filtered by. Dropping them — which is what returning None did —
    left every imported project with no last-active date and every fact with no last-used date, so the
    columns existed and were empty. They are read relative to now instead: approximate, and true enough
    to sort by, which is the whole reason the column is there.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.lower() in ("just now", "now"):
        return datetime.now(UTC)
    ago = AGO.match(text)
    if ago:
        return datetime.now(UTC) - timedelta(**{AGO_UNITS[ago.group(2).lower()]: int(ago.group(1))})
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def clear(session: AsyncSession) -> None:
    for model in LOADED:
        await session.execute(delete(model))


async def load_seed(session: AsyncSession, data: dict[str, Any]) -> dict[str, int]:
    """Write these documents as the workspace. Returns what it wrote, so a caller can say so out loud.

    There is no default. It used to fall back to the sample file when handed nothing, so an old SQLite
    file with none of the document tables in it imported the sample as though it were that workspace.
    """
    await clear(session)
    written: dict[str, int] = {}

    for row in data.get("projects", []):
        source = row.get("source") or {}
        session.add(Project(
            id=row["id"], name=row["name"], codename=row.get("codename", ""), kind=row.get("kind", "platform"),
            status=row.get("status", "active"), description=row.get("description", ""), repo=row.get("repo", ""),
            lines_count=count(row.get("lines")), files_count=count(row.get("files")),
            modules=count(row.get("modules")), db_tables=count(row.get("dbTables")),
            stored_procs=count(row.get("storedProcs")), last_active_at=when(row.get("lastActive")),
            source_kind=source.get("kind"), source_repo=source.get("repo", ""),
            source_branch=source.get("branch", ""),
            languages=row.get("languages", []), coverage=row.get("coverage", []), rules=row.get("rules", []),
            excluded=row.get("excluded", []), stack=row.get("stack", []),
        ))
    written["projects"] = len(data.get("projects", []))
    # Parents before children, group by group: almost everything below points at a project, and a
    # flush per level puts any failure on the row that caused it instead of at the end of the load.
    await session.flush()

    #: A plan names its task by reference, not by id; the tasks are loaded first, so this is the map.
    task_ids = {row["ref"]: row["id"] for row in data.get("tasks", []) if row.get("ref")}
    for row in data.get("tasks", []):
        session.add(Task(
            id=row["id"], ref=row["ref"], title=row["title"], project_id=row["projectId"],
            status=row.get("status", "backlog"), priority=row.get("priority", "NORMAL"),
            risk=row.get("risk", "LOW"), epic=row.get("epic", "") or "", requirement=row.get("requirement", ""),
            worktree=row.get("worktree", "") or "", blocked_reason=row.get("blockedReason", "") or "",
            files=count(row.get("files")), tests=count(row.get("tests")), progress=count(row.get("progress")),
            layers=row.get("layers", []),
        ))
        for n, item in enumerate(row.get("checklist", [])):
            # c1, c2, c3 repeat from task to task in the documents: they are positions within a task, not
            # keys. Namespacing with the task makes them the keys the schema needs.
            session.add(ChecklistItem(id=f"{row['id']}-{item.get('id') or n}", task_id=row["id"], n=n,
                                      label=item.get("label", ""), done=bool(item.get("done"))))
        for agent in dict.fromkeys(row.get("agents", [])):
            session.add(TaskAgent(task_id=row["id"], agent=agent))
    written["tasks"] = len(data.get("tasks", []))
    await session.flush()

    for row in data.get("plans", []):
        answered = {a.get("q"): a.get("a", "") for a in row.get("answered", []) if isinstance(a, dict)}
        deferred = set(row.get("deferred", []))
        session.add(Plan(
            id=row["id"], ref=row["ref"], project_id=row["projectId"],
            # Every plan document names the task it came from, and the column for it was once simply
            # never filled — so the relationship resolved to None and a plan looked unattached to its work.
            task_id=task_ids.get(row.get("taskRef", "")),
            status=row.get("status", "draft"), risk=row.get("risk", "LOW"),
            confidence=row.get("confidence"), raw_requirement=row.get("rawRequirement", ""),
            business_requirement=row.get("businessRequirement", ""),
            technical_requirement=row.get("technicalRequirement", ""),
            architecture_impact=row.get("architectureImpact", ""),
            requested_by=row.get("requestedBy", ""),
            affected_modules=row.get("affectedModules", []), affected_files=row.get("affectedFiles", []),
            affected_db=row.get("affectedDb", []), test_plan=row.get("testPlan", []),
            cited=row.get("cited", []), compiler=row.get("compiler", {}),
        ))
        for n, step in enumerate(row.get("steps", [])):
            session.add(PlanStep(id=f"{row['id']}-{step.get('id') or n}", plan_id=row["id"],
                                 n=step.get("n", n + 1), label=step.get("label", ""),
                                 agent=step.get("agent", ""), state=step.get("state", "todo"),
                                 detail=step.get("detail", ""), duration_s=step.get("durationS")))
        for n, question in enumerate(row.get("openQuestions", [])):
            session.add(PlanQuestion(id=f"{row['id']}-q{n}", plan_id=row["id"], n=n, question=question,
                                     answer=answered.get(question, ""), deferred=question in deferred))
    written["plans"] = len(data.get("plans", []))
    await session.flush()

    for row in data.get("approvals", []):
        session.add(Approval(
            id=row["id"], ref=row["ref"], title=row["title"], agent=row.get("agent", ""),
            tool=row.get("tool", ""), risk=row.get("risk", "LOW"), status=row.get("status", "pending"),
            project_id=row.get("projectId"), payload=row.get("payload", ""), reason=row.get("reason", ""),
            run_ref=row.get("runRef"), step=row.get("step"),
        ))
    written["approvals"] = len(data.get("approvals", []))

    for row in data.get("memory", []):
        project = row.get("projectId")
        session.add(MemoryFact(
            id=row["id"], ref=row["ref"], category=row.get("category", "project"), title=row["title"],
            body=row.get("body", ""), reason=row.get("reason", ""), source=row.get("source", ""),
            project_id=None if project in (None, "", "global") else project,
            confidence=row.get("confidence", "MEDIUM"), last_used_at=when(row.get("lastUsed")),
            pinned=bool(row.get("pinned")), archived=bool(row.get("archived")),
            archived_at=when(row.get("archivedAt")),
            evidence=row.get("evidence", []),
        ))
        for tag in dict.fromkeys(row.get("tags", [])):
            session.add(MemoryTag(fact_id=row["id"], tag=tag))
    written["memory"] = len(data.get("memory", []))
    # A conflict points at the two facts by key now, so the facts have to exist before it is written.
    await session.flush()

    for row in data.get("conflicts", []):
        session.add(MemoryConflict(
            id=row["id"], topic=row.get("topic", ""), status=row.get("status", "open"),
            severity=row.get("severity", "MEDIUM"), detail=row.get("detail", ""),
            suggestion=row.get("suggestion", ""), a=row["a"], b=row["b"],
        ))
    written["conflicts"] = len(data.get("conflicts", []))

    for row in data.get("mcp", []):
        session.add(McpServer(
            id=row["id"], name=row["name"], transport=row.get("transport", "stdio"),
            status=row.get("status", "disconnected"), scope=row.get("scope", "global"),
            command=row.get("command", ""), untrusted=bool(row.get("untrusted")), default_effect=row.get("defaultEffect", "ask"),
            config=row.get("config", "") or "",
        ))
        for tool in row.get("tools", []):
            session.add(McpTool(server_id=row["id"], name=tool.get("name", ""),
                                description=tool.get("description", ""), risk=tool.get("risk", "LOW")))
    written["mcp"] = len(data.get("mcp", []))

    for row in data.get("activity", []):
        session.add(ActivityEvent(
            actor=row.get("actor", "System"), actor_kind=row.get("actorKind", "system"),
            action=row.get("action", ""), detail=row.get("detail", ""), level=row.get("level", "info"),
            project_id=row.get("projectId") or None, task_ref=row.get("taskRef"),
        ))
    written["activity"] = len(data.get("activity", []))

    await session.flush()
    return written


async def sync_roles(session: AsyncSession) -> int:
    """Built-in roles, re-read from the catalogue on every start, so a new permission lands on upgrade.
    A custom role someone made is left exactly as it is."""
    for n, row in enumerate(ROLES):
        role = await session.get(Role, row.id)
        if role is None:
            role = Role(id=row.id)
            session.add(role)
        role.name, role.description, role.builtin = row.name, row.description, True
        # The catalogue's order is the ladder the access screen shows, so it is carried rather than
        # re-derived: written in one transaction, these rows all share a timestamp and cannot be
        # ordered by when they arrived.
        role.rank = n
        await session.flush()
        await session.execute(delete(RolePermission).where(RolePermission.role_id == role.id))
        for permission in dict.fromkeys(row.permissions):
            session.add(RolePermission(role_id=role.id, permission=permission))
    await session.flush()
    return len(ROLES)


async def sync_agents(session: AsyncSession) -> int:
    """The roster, re-read from the catalogue on every start, the way the built-in roles are.

    Only what the catalogue declares is written: who the agent is and what it says of itself. Its
    status is left alone, because `disabled` is the one a person sets; everything else about an agent
    — its record, its spend, the lane it works through — is derived and never stored on the row.
    Nothing is deleted: a run or a task that names an agent still names it after an upgrade.
    """
    for row in AGENTS:
        agent = await session.get(Agent, row.id)
        if agent is None:
            agent = Agent(id=row.id)
            session.add(agent)
        agent.name, agent.role, agent.icon = row.name, row.role, row.icon
        agent.autonomy, agent.system_prompt = row.autonomy, row.system_prompt
        agent.tools, agent.skills, agent.guardrails = list(row.tools), list(row.skills), list(row.guardrails)
    await session.flush()
    return len(AGENTS)
