"""Accounts and sessions.

Passwords are hashed with scrypt. A session is an opaque random token carried in an HttpOnly cookie,
or in an Authorization: Bearer header for scripts; the database keeps only its SHA-256. Five wrong
passwords for one address lock it for 30 seconds.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets as pysecrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException, Request

from .db import Store, now_iso
from .rbac import Rbac

COOKIE = "nc_session"
SESSION_TTL = timedelta(days=14)
MIN_PASSWORD = 10
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
LOCK_AFTER, LOCK_SECONDS, WINDOW = 5, 30, 300
_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    return "$".join(["scrypt", str(_SCRYPT["n"]), str(_SCRYPT["r"]), str(_SCRYPT["p"]),
                     base64.b64encode(salt).decode(), base64.b64encode(digest).decode()])


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class User:
    id: str
    email: str
    name: str
    status: str
    roles: tuple[str, ...]
    permissions: frozenset[str]

    def can(self, *perms: str) -> bool:
        return all(p in self.permissions for p in perms)

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "email": self.email, "name": self.name, "status": self.status,
                "roles": list(self.roles), "permissions": sorted(self.permissions)}


class Accounts:
    def __init__(self, store: Store, rbac: Rbac) -> None:
        self.store, self.rbac = store, rbac
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        store.execute("DELETE FROM sessions WHERE expires_at < ?", (now_iso(),))  # an expired session is only clutter

    # ── reading ──────────────────────────────────────────────────
    def count(self) -> int:
        return self.store.row("SELECT COUNT(*) FROM users")[0]

    def get(self, user_id: str) -> User | None:
        r = self.store.row("SELECT id, email, name, status FROM users WHERE id = ?", (user_id,))
        if r is None:
            return None
        return User(r["id"], r["email"], r["name"], r["status"], tuple(self.rbac.roles_of(r["id"])), self.rbac.permissions_of(r["id"]))

    def need(self, user_id: str) -> User:
        user = self.get(user_id)
        if user is None:
            raise HTTPException(404, "person not found")
        return user

    def list(self) -> list[dict[str, Any]]:
        teams: dict[str, list[str]] = {}
        for r in self.store.rows("SELECT user_id, team_id FROM team_members"):
            teams.setdefault(r["user_id"], []).append(r["team_id"])
        roles: dict[str, list[str]] = {}
        for r in self.store.rows("SELECT user_id, role_id FROM user_roles ORDER BY role_id"):
            roles.setdefault(r["user_id"], []).append(r["role_id"])
        return [{"id": r["id"], "email": r["email"], "name": r["name"], "status": r["status"],
                 "roles": roles.get(r["id"], []), "teams": teams.get(r["id"], []),
                 "lastLoginAt": r["last_login_at"], "createdAt": r["created_at"]}
                for r in self.store.rows("SELECT * FROM users ORDER BY created_at, rowid")]

    # ── changing people ──────────────────────────────────────────
    @staticmethod
    def check(email: str | None = None, name: str | None = None, password: str | None = None) -> None:
        if email is not None and not EMAIL.match(email.strip()):
            raise HTTPException(422, "That is not an email address")
        if name is not None and not name.strip():
            raise HTTPException(422, "A name is needed")
        if password is not None and len(password) < MIN_PASSWORD:
            raise HTTPException(422, f"Use a password of at least {MIN_PASSWORD} characters")

    def create(self, email: str, name: str, password: str, roles: list[str]) -> User:
        self.check(email, name, password)
        for r in roles:
            if not self.rbac.exists(r):
                raise HTTPException(422, f"Unknown role: {r}")
        email = email.strip()
        if self.store.row("SELECT 1 FROM users WHERE email = ?", (email,)):
            raise HTTPException(409, "Someone already uses that email")
        uid = "u_" + pysecrets.token_hex(6)
        with self.store.tx() as c:
            c.execute("INSERT INTO users(id, email, name, password_hash, status, created_at) VALUES (?, ?, ?, ?, 'active', ?)",
                      (uid, email, name.strip(), hash_password(password), now_iso()))
            c.executemany("INSERT INTO user_roles VALUES (?, ?)", [(uid, r) for r in dict.fromkeys(roles)])
        return self.need(uid)

    def _other_active_owners(self, user_id: str) -> int:
        return self.store.row("SELECT COUNT(*) FROM users u JOIN user_roles ur ON ur.user_id = u.id "
                              "WHERE ur.role_id = 'owner' AND u.status = 'active' AND u.id != ?", (user_id,))[0]

    def update(self, user_id: str, *, actor: User, name: str | None = None, status: str | None = None,
               roles: list[str] | None = None) -> User:
        user = self.need(user_id)
        self.check(name=name)
        if status == "disabled" and user_id == actor.id:
            raise HTTPException(409, "You cannot disable your own account")
        if roles is not None:
            for r in roles:
                if not self.rbac.exists(r):
                    raise HTTPException(422, f"Unknown role: {r}")
            if ("owner" in roles) != ("owner" in user.roles) and "owner" not in actor.roles:
                raise HTTPException(403, "Only an Owner can grant or remove the Owner role")
        losing_owner = "owner" in user.roles and ((roles is not None and "owner" not in roles) or status == "disabled")
        if losing_owner and self._other_active_owners(user_id) == 0:
            raise HTTPException(409, "This is the last active Owner. Make someone else an Owner first.")
        with self.store.tx() as c:
            if name is not None:
                c.execute("UPDATE users SET name = ? WHERE id = ?", (name.strip(), user_id))
            if status is not None:
                c.execute("UPDATE users SET status = ? WHERE id = ?", (status, user_id))
                if status == "disabled":
                    c.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            if roles is not None:
                c.execute("DELETE FROM user_roles WHERE user_id = ?", (user_id,))
                c.executemany("INSERT INTO user_roles VALUES (?, ?)", [(user_id, r) for r in dict.fromkeys(roles)])
        return self.need(user_id)

    def set_password(self, user_id: str, password: str, *, keep: str | None = None) -> None:
        """New password; every other session of that person ends. `keep` is the session to spare."""
        self.check(password=password)
        with self.store.tx() as c:
            c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user_id))
            c.execute("DELETE FROM sessions WHERE user_id = ? AND token_hash != ?", (user_id, token_hash(keep) if keep else ""))

    def verify(self, user_id: str, password: str) -> bool:
        r = self.store.row("SELECT password_hash FROM users WHERE id = ?", (user_id,))
        return bool(r) and verify_password(password, r[0])

    # ── sessions ─────────────────────────────────────────────────
    def start_session(self, user_id: str, user_agent: str = "") -> str:
        token = pysecrets.token_urlsafe(32)
        expires = (datetime.now() + SESSION_TTL).isoformat(timespec="seconds")
        with self.store.tx() as c:
            c.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?)", (token_hash(token), user_id, now_iso(), expires, user_agent[:200]))
            c.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (now_iso(), user_id))
        return token

    def login(self, email: str, password: str, user_agent: str = "") -> tuple[User, str]:
        key = email.strip().lower()
        with self._lock:
            now = time.monotonic()
            fails = [t for t in self._fails.get(key, []) if now - t < WINDOW]
            if len(fails) >= LOCK_AFTER and now - fails[-1] < LOCK_SECONDS:
                raise HTTPException(429, "Too many attempts. Wait 30 seconds and try again.")
        r = self.store.row("SELECT id, password_hash, status FROM users WHERE email = ?", (email.strip(),))
        if r is None or not verify_password(password, r["password_hash"]):
            with self._lock:
                self._fails[key] = [*fails, time.monotonic()]
            raise HTTPException(401, "Wrong email or password")
        if r["status"] != "active":
            raise HTTPException(403, "This account is disabled. Ask an admin to turn it back on.")
        with self._lock:
            self._fails.pop(key, None)
        return self.need(r["id"]), self.start_session(r["id"], user_agent)

    def session(self, token: str) -> User | None:
        r = self.store.row("SELECT s.user_id, s.expires_at, u.status FROM sessions s JOIN users u ON u.id = s.user_id "
                           "WHERE s.token_hash = ?", (token_hash(token),))
        if r is None or r["status"] != "active" or r["expires_at"] < now_iso():
            return None
        return self.get(r["user_id"])

    def logout(self, token: str) -> None:
        self.store.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))


# ── FastAPI dependencies ─────────────────────────────────────────
def token_from(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(COOKIE)


def current_user(request: Request) -> User:
    token = token_from(request)
    user = request.app.state.ctx.accounts.session(token) if token else None
    if user is None:
        raise HTTPException(401, "Sign in to continue")
    return user


def require(*perms: str):
    """A dependency that lets the request through only when the user holds every permission."""
    def dependency(user: User = Depends(current_user)) -> User:
        missing = [p for p in perms if p not in user.permissions]
        if missing:
            raise HTTPException(403, f"Your role cannot do this. It needs: {', '.join(missing)}")
        return user
    return dependency


def require_any(*perms: str):
    """Lets the request through when the user holds at least one of the permissions."""
    def dependency(user: User = Depends(current_user)) -> User:
        if not any(p in user.permissions for p in perms):
            raise HTTPException(403, f"Your role cannot see this. It needs one of: {', '.join(perms)}")
        return user
    return dependency
