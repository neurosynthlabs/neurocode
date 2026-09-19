"""The platform's own rows: the ideas this workspace has argued with itself, and the MCP servers it knows.

A brainstorm used to be one JSON document with the whole brief inside it, so "the newest ideas" was a
sort in Python and a new reference was a count of the rows. Here it is a row whose stages, case
against, MVP and roadmap are columns of their own: a list is a query, and the next reference is one
statement the database answers, so two people brainstorming in the same second cannot claim the same
one.
"""
from __future__ import annotations

from sqlalchemy import ColumnElement, Integer, cast, func, or_, select

from ..models import Brainstorm, McpServer, Project, ToolRule, User
from .base import Page, Repository, bounded


class BrainstormRepository(Repository[Brainstorm]):
    model = Brainstorm

    async def by_ref(self, ref: str) -> Brainstorm | None:
        return await self.one(Brainstorm.ref == ref)

    async def newest(self, project_id: str | None = None, *, limit: int | None = None,
                     offset: int = 0) -> Page[Brainstorm]:
        where: list[ColumnElement[bool]] = [Brainstorm.project_id == project_id] if project_id else []
        return await self.page(*where, order_by=Brainstorm.created_at.desc(), limit=limit, offset=offset)

    async def next_ref(self, prefix: str = "IDEA-") -> str:
        """The next reference: the highest number any ref carries, plus one — worked out in SQL rather
        than by counting rows in Python, which is what let two of them collide before."""
        digits = func.nullif(func.regexp_replace(Brainstorm.ref, r"\D", "", "g"), "")
        stmt = select(func.coalesce(func.max(cast(digits, Integer)), 0))
        return f"{prefix}{int((await self.session.execute(stmt)).scalar_one()) + 1}"


class McpRepository(Repository[McpServer]):
    model = McpServer

    async def all_ordered(self) -> list[McpServer]:
        return await self.list(order_by=McpServer.name, limit=200)


#: The most rules `decide` reads for one tool. A workspace with more rules than this for a single tool
#: has stopped writing rules and started writing a list; the ceiling keeps a decision one small query.
RULES_PER_DECISION = 500


class ToolRuleRepository(Repository[ToolRule]):
    model = ToolRule

    async def applicable(self, tool: str, project_id: str | None) -> list[ToolRule]:
        """Every rule that could apply to this tool here: the workspace's, and this project's own."""
        scope = (or_(ToolRule.project_id.is_(None), ToolRule.project_id == project_id) if project_id
                 else ToolRule.project_id.is_(None))
        stmt = select(ToolRule).where(ToolRule.tool == tool, scope).order_by(ToolRule.id).limit(RULES_PER_DECISION)
        return list((await self.session.execute(stmt)).scalars())

    async def listed(self, *, project: str | None = None, tool: str | None = None, limit: int | None = None,
                     offset: int = 0) -> list[tuple[ToolRule, str | None, str | None]]:
        """Rules with the names a screen shows beside them: the project's and the author's.

        `project` is a project id, or `workspace` for the rules that hold everywhere; None is both.
        Workspace rules come first, then each project's, then by tool and pattern, so a list reads in
        the order a decision weighs them."""
        where: list[ColumnElement[bool]] = []
        if project == "workspace":
            where.append(ToolRule.project_id.is_(None))
        elif project:
            where.append(ToolRule.project_id == project)
        if tool:
            where.append(ToolRule.tool == tool)
        stmt = (select(ToolRule, Project.name, User.name)
                .outerjoin(Project, Project.id == ToolRule.project_id)
                .outerjoin(User, User.id == ToolRule.created_by)
                .where(*where)
                .order_by(ToolRule.project_id.is_not(None), Project.name, ToolRule.tool, ToolRule.pattern,
                          ToolRule.id)
                .limit(bounded(limit)).offset(max(0, offset)))
        return [(rule, project_name, author) for rule, project_name, author in await self.session.execute(stmt)]

    async def named(self, rule_id: int) -> tuple[ToolRule, str | None, str | None] | None:
        stmt = (select(ToolRule, Project.name, User.name)
                .outerjoin(Project, Project.id == ToolRule.project_id)
                .outerjoin(User, User.id == ToolRule.created_by)
                .where(ToolRule.id == rule_id))
        found = (await self.session.execute(stmt)).first()
        return (found[0], found[1], found[2]) if found else None

    async def same(self, project_id: str | None, tool: str, pattern: str) -> ToolRule | None:
        scope = ToolRule.project_id.is_(None) if project_id is None else ToolRule.project_id == project_id
        return await self.one(scope, ToolRule.tool == tool, ToolRule.pattern == pattern)
