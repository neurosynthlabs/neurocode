"""The product's catalogue: the permissions, the built-in roles and the agent roster.

These are the only rows a brand-new workspace holds before anyone has done anything, and they belong
to the application rather than to the workspace — a permission added in a release has to reach an
installation that has been running for months. So they live in one file, `catalogue.json`, beside
this module, and `loader.sync_roles` and `loader.sync_agents` write them into the database on every
start. Everything else a workspace holds, somebody made.

The file is read once, when this module is first imported: it is part of the program, and it cannot
change while the process runs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

PATH = Path(__file__).resolve().parent / "catalogue.json"


#: Where rights live, in the sidebar's own words: every module, and the sub-modules under it.
#: This is one half of a pair — the other is `NavSection` and the `sub` values in src/lib/nav.ts.
#: A right filed under a module a person cannot find on the sidebar is a right nobody will grant.
MODULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Home", ()),
    ("Build", ("Planning", "Execution", "Quality", "Delivery")),
    ("Knowledge", ("Thinking",)),
    ("Platform", ("Extensions", "Connections")),
    ("Governance", ()),
    ("Admin", ()),
)

#: What a right lets someone do, in four words. The columns of the Roles matrix.
VERBS: tuple[str, ...] = ("use", "write", "decide", "admin")


@dataclass(frozen=True, slots=True)
class Permission:
    """One right, filed where the product itself would file it.

    `group` is the module's own name again. It is kept for one release so a browser still holding the
    old bundle — which reads `group` and nothing else — files the right somewhere sensible instead of
    dropping it off the access screen.
    """

    id: str
    module: str
    sub: str | None
    verb: str
    label: str
    description: str

    @property
    def group(self) -> str:
        return self.module


@dataclass(frozen=True, slots=True)
class BuiltinRole:
    id: str
    name: str
    description: str
    permissions: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RosterAgent:
    """An agent as the product declares it: who it is, and what it says of itself.

    Nothing here is a measurement. Its status, its record and its spend are derived from the runs and
    the ledger, and the model it works through is whichever lane the router gives it.
    """

    id: str
    name: str
    role: str
    icon: str
    autonomy: str
    tools: tuple[str, ...]
    skills: tuple[str, ...]
    guardrails: tuple[str, ...]
    system_prompt: str


def _read() -> tuple[tuple[Permission, ...], tuple[BuiltinRole, ...], tuple[RosterAgent, ...]]:
    raw = json.loads(PATH.read_text())
    permissions = tuple(Permission(p["id"], p["module"], p.get("sub"), p["verb"], p["label"], p["description"])
                        for p in raw["permissions"])
    roles = tuple(BuiltinRole(r["id"], r["name"], r["description"], tuple(r["permissions"]))
                  for r in raw["roles"])
    agents = tuple(RosterAgent(a["id"], a["name"], a["role"], a["icon"], a["autonomy"], tuple(a["tools"]),
                               tuple(a["skills"]), tuple(a["guardrails"]), a["systemPrompt"])
                   for a in raw["agents"])
    subs = dict(MODULES)
    for p in permissions:
        # A misspelt module puts a right in a row of the matrix nobody can find — the same class of
        # failure as a misspelt permission below, and it deserves the same refusal to start.
        if p.module not in subs:
            raise ValueError(f"catalogue.json: {p.id} names unknown module {p.module!r}")
        if p.sub is not None and p.sub not in subs[p.module]:
            raise ValueError(f"catalogue.json: {p.id} names unknown sub-module {p.sub!r} of {p.module}")
        if p.verb not in VERBS:
            raise ValueError(f"catalogue.json: {p.id} names unknown verb {p.verb!r}")

    known = {p.id for p in permissions}
    for role in roles:
        # A misspelt permission in a built-in role would grant nothing and look as though it did —
        # on every installation at once. Refusing to start is the kinder failure.
        if unknown := sorted(set(role.permissions) - known):
            raise ValueError(f"catalogue.json: role {role.id} names unknown permissions {unknown}")
    return permissions, roles, agents


PERMISSIONS, ROLES, AGENTS = _read()
#: Where each permission sits in the catalogue: the order the access screen draws its groups in.
PERMISSION_ORDER = {p.id: n for n, p in enumerate(PERMISSIONS)}
ROLE_BY_ID = {r.id: r for r in ROLES}
