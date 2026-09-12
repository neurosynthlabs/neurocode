"""Who may do what.

Permissions are `resource:action` strings from one catalogue (src/mock/rbac.ts, exported into the seed).
Roles are named sets of them, and a user's permissions are the union of their roles. The built-in
roles are seeded once and cannot be edited or deleted; admins add custom roles beside them.
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import HTTPException

from .db import Store, load_seed


class Rbac:
    def __init__(self, store: Store) -> None:
        seed = load_seed()["rbac"]
        self.store = store
        self.catalogue: list[dict[str, Any]] = seed["permissions"]
        self.order = {p["id"]: i for i, p in enumerate(self.catalogue)}
        # Built-in roles follow the catalogue on every start, so a version that adds a permission grants
        # it at once. Custom roles are never touched.
        with store.tx() as c:
            for r in seed["roles"]:
                c.execute("INSERT INTO roles(id, name, description, builtin) VALUES (?, ?, ?, 1) "
                          "ON CONFLICT(id) DO UPDATE SET name = excluded.name, description = excluded.description "
                          "WHERE roles.builtin = 1", (r["id"], r["name"], r["description"]))
                if c.execute("SELECT builtin FROM roles WHERE id = ?", (r["id"],)).fetchone()[0]:
                    c.execute("DELETE FROM role_permissions WHERE role_id = ?", (r["id"],))
                    c.executemany("INSERT INTO role_permissions VALUES (?, ?)", [(r["id"], p) for p in r["permissions"]])

    # ── reading ──────────────────────────────────────────────────
    def permissions_of(self, user_id: str) -> frozenset[str]:
        rows = self.store.rows("SELECT DISTINCT rp.permission FROM user_roles ur "
                               "JOIN role_permissions rp ON rp.role_id = ur.role_id WHERE ur.user_id = ?", (user_id,))
        return frozenset(r[0] for r in rows)

    def roles_of(self, user_id: str) -> list[str]:
        return [r[0] for r in self.store.rows("SELECT role_id FROM user_roles WHERE user_id = ? ORDER BY role_id", (user_id,))]

    def exists(self, role_id: str) -> bool:
        return self.store.row("SELECT 1 FROM roles WHERE id = ?", (role_id,)) is not None

    def roles(self) -> list[dict[str, Any]]:
        perms: dict[str, list[str]] = {}
        for r in self.store.rows("SELECT role_id, permission FROM role_permissions"):
            perms.setdefault(r["role_id"], []).append(r["permission"])
        members = {r[0]: r[1] for r in self.store.rows("SELECT role_id, COUNT(*) FROM user_roles GROUP BY role_id")}
        return [
            {"id": r["id"], "name": r["name"], "description": r["description"], "builtin": bool(r["builtin"]),
             "permissions": self.sort(perms.get(r["id"], [])), "members": members.get(r["id"], 0)}
            for r in self.store.rows("SELECT id, name, description, builtin FROM roles ORDER BY builtin DESC, rowid")
        ]

    def role(self, role_id: str) -> dict[str, Any]:
        found = next((r for r in self.roles() if r["id"] == role_id), None)
        if found is None:
            raise HTTPException(404, f"role {role_id} not found")
        return found

    def sort(self, permissions: list[str]) -> list[str]:
        return sorted(set(permissions), key=lambda p: self.order.get(p, len(self.order)))

    def check(self, permissions: list[str]) -> list[str]:
        unknown = sorted(set(permissions) - set(self.order))
        if unknown:
            raise HTTPException(422, f"Unknown permissions: {', '.join(unknown)}")
        return self.sort(permissions)

    # ── custom roles ─────────────────────────────────────────────
    def create(self, name: str, description: str, permissions: list[str]) -> dict[str, Any]:
        perms = self.check(permissions)
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "role"
        rid, n = base, 1
        while self.exists(rid):
            n += 1
            rid = f"{base}-{n}"
        with self.store.tx() as c:
            c.execute("INSERT INTO roles(id, name, description, builtin) VALUES (?, ?, ?, 0)", (rid, name.strip(), description.strip()))
            c.executemany("INSERT INTO role_permissions VALUES (?, ?)", [(rid, p) for p in perms])
        return self.role(rid)

    def update(self, role_id: str, *, name: str | None, description: str | None, permissions: list[str] | None) -> dict[str, Any]:
        current = self.role(role_id)
        if current["builtin"]:
            raise HTTPException(409, "Built-in roles cannot be changed. Create a custom role instead.")
        perms = self.check(permissions) if permissions is not None else None
        with self.store.tx() as c:
            if name is not None or description is not None:
                c.execute("UPDATE roles SET name = ?, description = ? WHERE id = ?",
                          ((name or current["name"]).strip(), (description if description is not None else current["description"]).strip(), role_id))
            if perms is not None:
                c.execute("DELETE FROM role_permissions WHERE role_id = ?", (role_id,))
                c.executemany("INSERT INTO role_permissions VALUES (?, ?)", [(role_id, p) for p in perms])
        return self.role(role_id)

    def delete(self, role_id: str) -> dict[str, Any]:
        current = self.role(role_id)
        if current["builtin"]:
            raise HTTPException(409, "Built-in roles cannot be deleted.")
        if current["members"]:
            raise HTTPException(409, f"{current['members']} people still have this role. Move them to another role first.")
        self.store.execute("DELETE FROM roles WHERE id = ?", (role_id,))
        return current
