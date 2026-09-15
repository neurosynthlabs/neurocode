"""The rules behind the access screens: the permission catalogue, custom roles, and teams.

Accounts are not here. The last active Owner, who may grant the Owner role, and what a new password
does to open sessions are all `IdentityService`'s, and the admin routes call it rather than saying
any of that twice. What is left over is what that service does not own — and it is mostly one idea:
a workspace ships with a floor of built-in roles, and an admin with a mouse must not be able to take
it away.

Nothing in this file knows what HTTP is. A rule says no in words, and carries the status that refusal
deserves.
"""
from __future__ import annotations

import asyncio
import re
import secrets as pysecrets
from collections.abc import Iterable
from functools import lru_cache
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..data.loader import load_seed_file
from ..models.identity import Role, Team
from ..repositories.base import NotFound
from ..repositories.identity import RoleRepository, TeamRepository, UserRepository
from .errors import Refused

#: The longest a generated role id may be before its uniqueness suffix.
ID_LENGTH = 40


@lru_cache(maxsize=1)
def _catalogue() -> tuple[tuple[dict[str, Any], ...], dict[str, int]]:
    """The permission catalogue, and where each id sits in it.

    Read from the seed, which is where the frontend's own copy is exported from, so the two cannot
    drift. Read once: the file is the same file for the life of the process.
    """
    entries = tuple(load_seed_file().get("rbac", {}).get("permissions", []))
    return entries, {entry["id"]: i for i, entry in enumerate(entries)}


async def catalogue() -> list[dict[str, Any]]:
    """Every permission this workspace knows about, each carrying the group a screen files it under.
    Reading a file blocks, so the first call does it on a worker thread and the rest is cached."""
    entries, _ = await asyncio.to_thread(_catalogue)
    return [dict(entry) for entry in entries]


async def sorted_permissions(chosen: Iterable[str]) -> list[str]:
    """Catalogue order, not alphabetical — it is the order the access screen draws its groups in."""
    _, order = await asyncio.to_thread(_catalogue)
    return sorted(set(chosen), key=lambda p: order.get(p, len(order)))


async def known_permissions(chosen: Iterable[str]) -> list[str]:
    """The same, refusing anything the catalogue has never heard of. A misspelt permission grants
    nothing, and would sit in the role looking exactly as though it did."""
    _, order = await asyncio.to_thread(_catalogue)
    unknown = sorted(set(chosen) - set(order))
    if unknown:
        raise Refused(f"Unknown permissions: {', '.join(unknown)}", status=422)
    return await sorted_permissions(chosen)


class RoleService:
    """Custom roles, beside the built-in ones rather than over them.

    A built-in role is re-written from the catalogue by `sync_roles` on every start, so editing one
    here would be quietly undone at the next restart; deleting one would take its holders' access
    with it. Both are refused outright — a rule that half-applies is worse than one that says no.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.roles = RoleRepository(session)

    async def need(self, role_id: str) -> Role:
        found = await self.roles.get(role_id)
        if found is None:
            raise NotFound(f"role {role_id}")
        return found

    async def create(self, name: str, description: str, permissions: Iterable[str]) -> Role:
        chosen = await known_permissions(permissions)
        role = await self.roles.add(Role(id=await self._free_id(name), name=name.strip(),
                                         description=description.strip(), builtin=False,
                                         permissions=[]))
        await self.roles.replace_permissions(role, chosen)
        return role

    async def update(self, role_id: str, *, name: str | None = None, description: str | None = None,
                     permissions: Iterable[str] | None = None) -> Role:
        role = await self._changeable(role_id, "changed. Create a custom role instead")
        chosen = await known_permissions(permissions) if permissions is not None else None
        if name is not None:
            role.name = name.strip()
        if description is not None:
            role.description = description.strip()
        await self.session.flush()
        if chosen is not None:
            await self.roles.replace_permissions(role, chosen)
        return role

    async def remove(self, role: Role) -> None:
        await self._changeable(role.id, "deleted")
        worn = await self.roles.worn_by(role.id)
        if worn:
            raise Refused(f"{worn} people still have this role. Move them to another role first.")
        await self.roles.remove(role)

    async def _changeable(self, role_id: str, what: str) -> Role:
        role = await self.need(role_id)
        if role.builtin:
            raise Refused(f"Built-in roles cannot be {what}.")
        return role

    async def _free_id(self, name: str) -> str:
        """A readable id from the name — "QA Lead" becomes `qa-lead` — numbered if it is taken."""
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:ID_LENGTH] or "role"
        candidate, n = base, 1
        while await self.roles.get(candidate) is not None:
            n += 1
            candidate = f"{base}-{n}"
        return candidate


class TeamService:
    """Teams and who is in them. A team is a grouping, not a grant: it carries no permissions."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.teams = TeamRepository(session)
        self.users = UserRepository(session)

    async def need(self, team_id: str) -> Team:
        found = await self.teams.get(team_id)
        if found is None:
            raise NotFound(f"team {team_id}")
        return found

    async def create(self, name: str, description: str, members: Iterable[str]) -> Team:
        await self._name_is_free(name)
        wanted = await self._known_people(members)
        # The team is flushed before its members: the rows point at it, and a child cannot be written
        # before the parent it names.
        team = await self.teams.add(Team(id="t_" + pysecrets.token_hex(5), name=name.strip(),
                                         description=description.strip(), members=[]))
        await self.teams.set_members(team, wanted)
        return team

    async def update(self, team_id: str, *, name: str | None = None, description: str | None = None,
                     members: Iterable[str] | None = None) -> Team:
        team = await self.need(team_id)
        if name is not None and name.strip().casefold() != team.name.casefold():
            await self._name_is_free(name)
        wanted = await self._known_people(members) if members is not None else None
        if name is not None:
            team.name = name.strip()
        if description is not None:
            team.description = description.strip()
        await self.session.flush()
        if wanted is not None:
            await self.teams.set_members(team, wanted)
        return team

    async def remove(self, team: Team) -> None:
        """Membership goes with it. Nobody's account or roles are touched — they were never the
        team's to hold."""
        await self.teams.remove(team)

    async def _name_is_free(self, name: str) -> None:
        if await self.teams.by_name(name) is not None:
            raise Refused("A team with that name already exists.")

    async def _known_people(self, members: Iterable[str]) -> list[str]:
        """Checked before the write, so a typo is a sentence on screen rather than a foreign key."""
        wanted = list(dict.fromkeys(members))
        here = await self.users.existing(wanted)
        if unknown := [m for m in wanted if m not in here]:
            raise Refused(f"Unknown people: {', '.join(unknown)}", status=422)
        return wanted
