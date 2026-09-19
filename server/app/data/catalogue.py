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


@dataclass(frozen=True, slots=True)
class Permission:
    id: str
    group: str
    label: str
    description: str


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
    permissions = tuple(Permission(p["id"], p["group"], p["label"], p["description"])
                        for p in raw["permissions"])
    roles = tuple(BuiltinRole(r["id"], r["name"], r["description"], tuple(r["permissions"]))
                  for r in raw["roles"])
    agents = tuple(RosterAgent(a["id"], a["name"], a["role"], a["icon"], a["autonomy"], tuple(a["tools"]),
                               tuple(a["skills"]), tuple(a["guardrails"]), a["systemPrompt"])
                   for a in raw["agents"])
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
