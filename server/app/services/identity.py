"""Accounts, sign-in and the guards around them.

The rules that used to be scattered through route handlers are here, each with a name: the last
active Owner cannot be removed, nobody disables their own account, only an Owner grants the Owner
role, a new password ends every other session. A route's job is now to say who is asking and turn a
refusal into a status code.

Nothing in this file knows what HTTP *serving* is. The queries it writes — the sign-in lock-out
window, a person's project grants, the workspace's single sign-on settings — are asked of the session
directly because each is part of deciding who is asking, which is this file's whole job. The requests
it makes *outwards*, to an identity provider, are here for the same reason and for no other: a
provider's discovery document, its keys and its token endpoint are how a sign-in through it is
decided. They go through one injected port (`http_json`), so a test never reaches a real provider.

**Single sign-on, in four rules that do not bend.** The first Owner is made by the setup wizard and
never by a provider — a workspace whose first account came from an IdP is a workspace that IdP owns.
Nothing but the id token says who someone is: the token endpoint's answer is verified against the
keys the issuer published, and the userinfo endpoint is never asked, because an access token that
reached a second endpoint would be a second thing to trust. State and nonce both come back or the
sign-in does not happen. And a claim can carry someone into any role except Owner, because an Owner
is what the workspace grants, not what a directory somewhere says.
"""
from __future__ import annotations

import asyncio

import json
import re
import secrets as pysecrets
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.base import utcnow
from ..models import (LoginAttempt, Project, ProjectRole, RolePermission,
                      Session as SessionRow, Setting, User as UserRow)
from ..repositories import AuditRepository, RoleRepository, SessionRepository, UserRepository
from ..secrets import Secrets
from ..settings import Settings, settings as get_settings
from .errors import Denied, Refused
from .security import (BadToken, hash_password, new_token, read_state, sign_state, token_hash,
                       verify_jwt, verify_password)

#: scrypt is meant to be expensive — about a fifth of a second of pure CPU at these parameters, which
#: is the whole point — so it runs on a worker thread. On the event loop it stopped every other
#: request in the process for that long, on every sign-in and every password an admin set.
async def _hash(password: str) -> str:
    return await asyncio.to_thread(hash_password, password)


async def _verify(password: str, stored: str) -> bool:
    return await asyncio.to_thread(verify_password, password, stored)

MIN_PASSWORD = 10
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
#: Wrong attempts older than this are forgotten, so a slow trickle never adds up to a lock-out.
WINDOW = timedelta(minutes=5)

#: Where the workspace's single sign-on settings live, beside its other settings.
SSO_KEY = "sso"
#: The client secret, in the secrets file with the model keys — never in the database, never in a
#: response, only ever reported as its last four characters.
SSO_SECRET = "sso_client_secret"
#: The key that signs the one-time cookie carrying a sign-in's state and nonce. Made once, here, so a
#: workspace that never uses SSO never has one.
SSO_STATE_SECRET = "sso_state_secret"
#: The cookie that holds one sign-in's state and nonce while the browser is at the provider.
SSO_COOKIE = "nc_sso"
#: How long that cookie is good for. A sign-in is a minute's work; ten is generous and still short.
SSO_MINUTES = 10
#: How far a provider's clock may be from this one. Two minutes is what every OIDC library allows.
SSO_SKEW = 120
#: What NeuroCode asks the provider for. `openid` names the id token; the other two name the claims
#: an account here is made from. Nothing else is asked, because nothing else is used.
SSO_SCOPE = "openid email profile"


#: The rights a project grant can never take away. An Owner who restricts a project must still be able
#: to unrestrict it, and the audit log must still cover every project — otherwise a restricted project
#: becomes a room in the workspace that the workspace cannot get back into.
NEVER_NARROWED = frozenset({"workspace:admin", "users:manage", "roles:manage", "teams:manage",
                            "audit:read"})


@dataclass(frozen=True)
class Person:
    """Who is asking, as the rest of the app sees them. The same shape the screens already read."""

    id: str
    email: str
    name: str
    status: str
    roles: tuple[str, ...]
    permissions: frozenset[str]
    #: The restricted projects this person holds a grant in, and what that grant carries. Empty in
    #: every workspace until somebody restricts a project, which is the point of the flag.
    #: `compare=False`: a mapping is not hashable, and a Person is compared by who they are.
    project_rights: Mapping[str, frozenset[str]] = field(default_factory=dict, compare=False)

    def can(self, *perms: str) -> bool:
        return all(p in self.permissions for p in perms)

    def must(self, permission: str, what: str = "") -> None:
        if permission not in self.permissions:
            raise Denied(permission, what)

    def in_project(self, project_id: str, restricted: bool) -> frozenset[str]:
        """What this person may do inside one project. A grant narrows; it can never widen.

        An open project is the workspace set, unchanged — which is every project on the day this
        ships. A restricted one is that set cut to what the grant carries, so nobody ever gains a
        right from a project they did not already hold across the workspace, and reviewing somebody's
        access stays one screen. What `NEVER_NARROWED` names survives either way.
        """
        if not restricted:
            return self.permissions
        kept = self.permissions & NEVER_NARROWED
        granted = self.project_rights.get(project_id)
        return kept if granted is None else frozenset((self.permissions & granted) | kept)

    def may_see(self, project_id: str, restricted: bool) -> bool:
        """Whether this project exists at all for this person.

        A restricted project somebody holds no grant in answers 404 rather than 403, because "there
        is a project here you may not open" is itself something they were not told.
        """
        return (not restricted or project_id in self.project_rights
                or bool(self.permissions & NEVER_NARROWED))

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "email": self.email, "name": self.name, "status": self.status,
                "roles": list(self.roles), "permissions": sorted(self.permissions),
                # Only the restricted projects; an open one is absent, meaning "the workspace set".
                "projectRights": {pid: sorted(held) for pid, held in sorted(self.project_rights.items())}}


class IdentityService:
    def __init__(self, session: AsyncSession, config: Settings | None = None) -> None:
        self.session = session
        self.config = config or get_settings()
        self.users = UserRepository(session)
        self.roles = RoleRepository(session)
        self.sessions = SessionRepository(session)
        self.audit = AuditRepository(session)

    # ── reading ──────────────────────────────────────────────────
    async def count(self) -> int:
        return await self.users.count()

    async def person(self, user_id: str) -> Person | None:
        row = await self.users.get(user_id)
        if row is None:
            return None
        return Person(row.id, row.email, row.name, row.status,
                      tuple(await self.users.role_ids(user_id)),
                      frozenset(await self.users.permissions(user_id)),
                      await self.project_rights(user_id))

    async def project_rights(self, user_id: str) -> dict[str, frozenset[str]]:
        """This person's grants in the restricted projects they are listed in.

        One indexed query, and in almost every workspace it returns nothing: a project is open to
        everyone until somebody restricts it, and only a restricted project narrows anything. The
        join is an outer one on purpose — a grant naming a role that carries no permissions is still
        a grant, and the person is still listed on that project.
        """
        stmt = (select(ProjectRole.project_id, RolePermission.permission)
                .join(Project, Project.id == ProjectRole.project_id)
                .outerjoin(RolePermission, RolePermission.role_id == ProjectRole.role_id)
                .where(ProjectRole.user_id == user_id, Project.restricted.is_(True)))
        found: dict[str, set[str]] = {}
        for project_id, permission in (await self.session.execute(stmt)).all():
            held = found.setdefault(project_id, set())
            if permission is not None:
                held.add(permission)
        return {pid: frozenset(held) for pid, held in found.items()}

    async def need(self, user_id: str) -> Person:
        found = await self.person(user_id)
        if found is None:
            raise Refused("That person is not here.", status=404)
        return found

    # ── making and changing people ───────────────────────────────
    @staticmethod
    def check(email: str | None = None, name: str | None = None, password: str | None = None) -> None:
        if email is not None and not EMAIL.match(email.strip()):
            raise Refused("That is not an email address.", status=422)
        if name is not None and not name.strip():
            raise Refused("A name is needed.", status=422)
        if password is not None and len(password) < MIN_PASSWORD:
            raise Refused(f"Use a password of at least {MIN_PASSWORD} characters.", status=422)

    async def _known_roles(self, roles: list[str]) -> None:
        for role_id in roles:
            if await self.roles.get(role_id) is None:
                raise Refused(f"Unknown role: {role_id}", status=422)

    async def create(self, email: str, name: str, password: str, roles: list[str]) -> Person:
        self.check(email, name, password)
        await self._known_roles(roles)
        if await self.users.by_email(email) is not None:
            raise Refused("Someone already uses that email.")
        user = await self.users.add(UserRow(id="u_" + pysecrets.token_hex(6), email=email.strip(),
                                            name=name.strip(), password_hash=await _hash(password)))
        await self.users.set_roles(user.id, roles)
        return await self.need(user.id)

    async def update(self, user_id: str, *, actor: Person, name: str | None = None,
                     status: str | None = None, roles: list[str] | None = None) -> Person:
        row = await self.users.require(user_id)
        person = await self.need(user_id)
        self.check(name=name)
        if status == "disabled" and user_id == actor.id:
            raise Refused("You cannot disable your own account.")
        if roles is not None:
            await self._known_roles(roles)
            if ("owner" in roles) != ("owner" in person.roles) and "owner" not in actor.roles:
                raise Refused("Only an Owner can grant or remove the Owner role.", status=403)
        losing_owner = "owner" in person.roles and (
            (roles is not None and "owner" not in roles) or status == "disabled")
        if losing_owner and await self._other_active_owners(user_id) == 0:
            raise Refused("This is the last active Owner. Make someone else an Owner first.")

        if name is not None:
            row.name = name.strip()
        if status is not None:
            row.status = status
            if status == "disabled":
                await self.sessions.end_all_for(user_id)   # a disabled account is signed out everywhere
        if roles is not None:
            await self.users.set_roles(user_id, roles)
        await self.session.flush()
        return await self.need(user_id)

    async def _other_active_owners(self, except_user: str) -> int:
        """The guard behind "this is the last Owner": one count, not a query per person."""
        return await self.users.count_active_with_role("owner", except_user=except_user)

    async def set_password(self, user_id: str, password: str, *, keep: str | None = None) -> None:
        """A new password ends every other session that person has open. `keep` spares this one."""
        self.check(password=password)
        row = await self.users.require(user_id)
        row.password_hash = await _hash(password)
        await self.session.flush()
        for open_session in await self.sessions.open_for(user_id):
            if not keep or open_session.token_hash != token_hash(keep):
                await self.sessions.end(open_session.token_hash)

    async def verify(self, user_id: str, password: str) -> bool:
        row = await self.users.get(user_id)
        return bool(row) and await _verify(password, row.password_hash)

    # ── signing in ───────────────────────────────────────────────
    async def _locked(self, email: str) -> bool:
        """Five wrong tries in five minutes locks that address until the last one is old enough."""
        since = utcnow() - WINDOW
        stmt = (select(func.count(), func.max(LoginAttempt.at))
                .where(LoginAttempt.email == email, LoginAttempt.ok.is_(False), LoginAttempt.at > since))
        tries, latest = (await self.session.execute(stmt)).one()
        if int(tries) < self.config.login_attempts or latest is None:
            return False
        return (utcnow() - latest).total_seconds() < self.config.lockout_seconds

    async def _attempt(self, email: str, ok: bool, ip: str = "") -> None:
        self.session.add(LoginAttempt(email=email.strip(), ok=ok, ip=ip))
        await self.session.flush()

    async def login(self, email: str, password: str, *, user_agent: str = "", ip: str = "") -> tuple[Person, str]:
        if await self._locked(email):
            raise Refused(f"Too many attempts. Wait {self.config.lockout_seconds} seconds and try again.",
                          status=429)
        row = await self.users.by_email(email)
        if row is None or not await _verify(password, row.password_hash):
            await self._attempt(email, ok=False, ip=ip)
            raise Refused("Wrong email or password.", status=401)
        if row.status != "active":
            await self._attempt(email, ok=False, ip=ip)
            raise Refused("This account is disabled. Ask an admin to turn it back on.", status=403)
        # Passwords off, if the workspace asked for that. The check is here and not on the route
        # because a password is a password whichever door it arrives at — and it is *after* the
        # password was verified on purpose, so this answer cannot be used to find out whose password
        # is right. An Owner is always spared: somebody must be able to get back in when the identity
        # provider is the thing that is broken.
        sso = await SsoService(self.session, config=self.config).settings()
        if sso.require_sso and "owner" not in await self.users.role_ids(row.id):
            await self._attempt(email, ok=False, ip=ip)
            raise Refused(f"This workspace signs in through {sso.label}. Use the {sso.label} button — "
                          "passwords are only left open for an Owner.", status=403)
        await self._attempt(email, ok=True, ip=ip)
        return await self.need(row.id), await self.start_session(row.id, user_agent)

    async def start_session(self, user_id: str, user_agent: str = "") -> str:
        """A new session. Only the token's hash is stored, so the database itself cannot sign in as
        anyone; the token exists in the browser alone."""
        token = new_token()
        row = await self.users.require(user_id)
        self.session.add(SessionRow(token_hash=token_hash(token), user_id=user_id,
                                    expires_at=utcnow() + timedelta(days=self.config.session_days),
                                    user_agent=user_agent[:200]))
        await self.users.touch_login(row)
        await self.session.flush()
        return token

    async def whoami(self, token: str) -> Person | None:
        """The person behind a token, or nobody — expired, signed out elsewhere, or disabled."""
        found = await self.sessions.by_token(token_hash(token))
        if found is None:
            return None
        person = await self.person(found.user_id)
        return person if person and person.status == "active" else None

    async def logout(self, token: str) -> None:
        await self.sessions.end(token_hash(token))


# ── single sign-on ───────────────────────────────────────────────
def http_json(url: str, *, data: bytes | None = None, headers: Mapping[str, str] | None = None,
              timeout: float = 10.0) -> dict[str, Any]:
    """One request to the identity provider, answering JSON. Blocking, so a caller runs it in a thread.

    HTTPS only, and no redirects followed: a discovery document that bounces somewhere else is a
    discovery document that has stopped describing the issuer it was asked about, and an id token is
    only worth what its issuer is worth. A provider that cannot be reached is a refusal with the
    provider's own words in it, never a silent fall back to some other way of deciding who is asking.
    """
    if not url.lower().startswith("https://"):
        raise Refused(f"An identity provider must be reached over https. {url[:120]} is not.", status=400)
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                     headers={"Accept": "application/json", **(headers or {})})
    opener = urllib.request.build_opener(_NoRedirects)
    try:
        with opener.open(request, timeout=timeout) as answer:        # noqa: S310 — https, checked above
            body = answer.read(1024 * 1024)
    except urllib.error.HTTPError as failed:
        detail = failed.read(2048).decode("utf-8", "replace").strip()
        raise Refused(f"The identity provider answered {failed.code} for {urllib.parse.urlparse(url).path}"
                      f"{f': {detail[:200]}' if detail else '.'}", status=502) from failed
    except OSError as failed:
        raise Refused(f"The identity provider could not be reached: {failed}", status=502) from failed
    try:
        found = json.loads(body)
    except ValueError as broken:
        raise Refused("The identity provider did not answer JSON.", status=502) from broken
    if not isinstance(found, dict):
        raise Refused("The identity provider did not answer an object.", status=502)
    return found


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201, D102
        raise Refused(f"The identity provider redirected to {newurl[:120]}; NeuroCode reads the "
                      "document the issuer itself serves and nothing it points at.", status=502)


@dataclass(frozen=True, slots=True)
class SsoConfig:
    """What an admin filled in on Admin → Workspace. The client secret is not here — it is in the
    secrets file, like a model key, and this object never carries it."""

    enabled: bool = False
    issuer: str = ""
    client_id: str = ""
    #: What the button on the sign-in screen says after "Continue with".
    label: str = "single sign-on"
    #: The claim the role map is read from. A group or role claim, whatever the provider calls it.
    role_claim: str = "groups"
    #: claim value → role id. Applied on every sign-in, so removing somebody from a group there
    #: removes the role here.
    role_map: Mapping[str, str] = field(default_factory=dict)
    #: What somebody gets when no claim of theirs is mapped.
    default_roles: tuple[str, ...] = ("viewer",)
    #: Whether a person the provider knows and this workspace does not is made an account here.
    create_users: bool = True
    #: Passwords off for everyone but an Owner.
    require_sso: bool = False
    #: Where the provider sends the browser back — the same address that is registered at the
    #: provider. It is stored and never taken from the request: a redirect URI a *caller* could
    #: choose is how an authorization code ends up somewhere else, and an admin has to type this
    #: address at the provider anyway.
    redirect_uri: str = ""

    @property
    def ready(self) -> bool:
        return bool(self.enabled and self.issuer and self.client_id and self.redirect_uri)

    @property
    def home(self) -> str:
        """Where the browser is sent once it is signed in: the app that owns the callback address."""
        parts = urllib.parse.urlparse(self.redirect_uri)
        return f"{parts.scheme}://{parts.netloc}/" if parts.scheme and parts.netloc else "/"

    @classmethod
    def read(cls, value: Any) -> SsoConfig:
        held = value if isinstance(value, dict) else {}
        roles = held.get("defaultRoles")
        mapped = held.get("roleMap")
        return cls(enabled=bool(held.get("enabled", False)), issuer=str(held.get("issuer", "")).strip(),
                   client_id=str(held.get("clientId", "")).strip(),
                   label=str(held.get("label") or "single sign-on").strip(),
                   role_claim=str(held.get("roleClaim") or "groups").strip(),
                   role_map={str(k): str(v) for k, v in mapped.items()} if isinstance(mapped, dict) else {},
                   default_roles=tuple(str(r) for r in roles) if isinstance(roles, list) else ("viewer",),
                   create_users=bool(held.get("createUsers", True)),
                   require_sso=bool(held.get("requireSso", False)),
                   redirect_uri=str(held.get("redirectUri", "")).strip())

    def stored(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "issuer": self.issuer, "clientId": self.client_id,
                "label": self.label, "roleClaim": self.role_claim, "roleMap": dict(self.role_map),
                "defaultRoles": list(self.default_roles), "createUsers": self.create_users,
                "requireSso": self.require_sso, "redirectUri": self.redirect_uri}

    def public(self) -> dict[str, Any]:
        """What the sign-in screen may know before anyone has signed in: that there is a button, and
        what it says. Never the issuer, the client id or anything a stranger could use."""
        return {"enabled": self.ready, "label": self.label, "passwordsOff": self.ready and self.require_sso}


class SsoService:
    """Signing in through an OpenID Connect provider, and the settings that describe one.

    Given a `Secrets` it can complete a sign-in; without one it can still read and judge the
    settings, which is what sign-in-by-password needs in order to know whether it is still allowed.
    """

    def __init__(self, session: AsyncSession, secrets: Secrets | None = None,
                 config: Settings | None = None,
                 fetch: Callable[..., dict[str, Any]] | None = None) -> None:
        self.session = session
        self.secrets = secrets
        self.config = config or get_settings()
        # Looked up rather than defaulted in the signature, so a test can put its own local provider
        # in this module's place and no route has to know that a test is running.
        self.fetch = fetch or http_json
        self.users = UserRepository(session)
        self.roles = RoleRepository(session)
        self.audit = AuditRepository(session)

    # ── the settings ─────────────────────────────────────────────
    async def settings(self) -> SsoConfig:
        row = await self.session.get(Setting, SSO_KEY)
        return SsoConfig.read(row.value if row else None)

    async def save(self, patch: Mapping[str, Any], *, actor: Person, ip: str = "") -> SsoConfig:
        """Store what an admin filled in, after checking it is a workspace that can still be entered.

        Two refusals are the whole of the policy here. A role map that names Owner is refused: an
        Owner is what this workspace grants, and a directory somewhere else must never be able to
        hand it out. And requiring SSO before SSO works is refused, because the next thing that would
        happen is a workspace nobody can sign in to.
        """
        current = await self.settings()
        wanted = SsoConfig.read({**current.stored(), **{k: v for k, v in patch.items() if v is not None}})
        for claim, role_id in wanted.role_map.items():
            if role_id == "owner":
                raise Refused("A claim cannot carry someone into the Owner role. An Owner is granted here, "
                              f"by an Owner — take `{claim}` off the map.", status=422)
            if await self.roles.get(role_id) is None:
                raise Refused(f"Unknown role: {role_id}", status=422)
        for role_id in wanted.default_roles:
            if await self.roles.get(role_id) is None:
                raise Refused(f"Unknown role: {role_id}", status=422)
        if wanted.enabled and not (wanted.issuer and wanted.client_id and wanted.redirect_uri):
            raise Refused("Single sign-on needs an issuer, a client id and the redirect address you "
                          "registered at the provider before it can be turned on.", status=422)
        if wanted.redirect_uri and not wanted.redirect_uri.lower().startswith(("https://", "http://localhost",
                                                                               "http://127.0.0.1")):
            raise Refused("A redirect address must be https, or http on this machine.", status=422)
        if wanted.require_sso and not wanted.ready:
            raise Refused("Turn single sign-on on and test it before you require it — otherwise nobody "
                          "but an Owner can sign in.", status=422)
        row = await self.session.get(Setting, SSO_KEY)
        if row is None:
            self.session.add(Setting(key=SSO_KEY, value=wanted.stored()))
        else:
            row.value = wanted.stored()
        await self.session.flush()
        await self.audit.record(action="sso.settings", user_id=actor.id, target=wanted.issuer,
                                detail={"enabled": wanted.enabled, "requireSso": wanted.require_sso,
                                        "roleMap": dict(wanted.role_map)}, ip=ip)
        return wanted

    async def set_secret(self, secret: str | None, *, actor: Person, ip: str = "") -> None:
        if self.secrets is None:
            raise Refused("This server holds no secrets file, so it cannot keep a client secret.", status=500)
        self.secrets.set(SSO_SECRET, (secret or "").strip() or None)
        await self.audit.record(action="sso.secret", user_id=actor.id,
                                target="set" if (secret or "").strip() else "cleared", ip=ip)

    # ── the sign-in itself ───────────────────────────────────────
    def _state_secret(self) -> str:
        """The key the one-time cookie is signed with, made the first time a sign-in needs one."""
        if self.secrets is None:
            raise Refused("This server holds no secrets file, so it cannot start a sign-in.", status=500)
        held = self.secrets.get(SSO_STATE_SECRET)
        if not held:
            held = pysecrets.token_urlsafe(32)
            self.secrets.set(SSO_STATE_SECRET, held)
        return held

    async def _discovery(self, issuer: str) -> dict[str, Any]:
        """The provider's own description of itself.

        Read on every sign-in rather than cached. A sign-in happens a handful of times a day in a
        workspace this size, and a cache is exactly what makes a provider that has rotated its keys
        look like a provider whose tokens are forged.
        """
        url = issuer.rstrip("/") + "/.well-known/openid-configuration"
        found = await asyncio.to_thread(self.fetch, url)
        if str(found.get("issuer", "")).rstrip("/") != issuer.rstrip("/"):
            raise Refused(f"That discovery document describes {found.get('issuer') or 'no issuer'}, "
                          f"not {issuer}.", status=502)
        for needed in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not found.get(needed):
                raise Refused(f"That identity provider's discovery document has no {needed}.", status=502)
        return found

    async def start(self) -> tuple[str, str]:
        """Where to send the browser, and the one-time cookie that must come back with it."""
        config = await self.settings()
        if not config.ready:
            raise Refused("Single sign-on is not set up for this workspace.", status=409)
        where = config.redirect_uri
        found = await self._discovery(config.issuer)
        state, nonce = pysecrets.token_urlsafe(24), pysecrets.token_urlsafe(24)
        query = urllib.parse.urlencode({
            "response_type": "code", "client_id": config.client_id, "redirect_uri": where,
            "scope": SSO_SCOPE, "state": state, "nonce": nonce})
        joiner = "&" if "?" in str(found["authorization_endpoint"]) else "?"
        cookie = sign_state(self._state_secret(), {
            "state": state, "nonce": nonce, "redirect": where,
            "until": (utcnow() + timedelta(minutes=SSO_MINUTES)).timestamp()})
        return f"{found['authorization_endpoint']}{joiner}{query}", cookie

    async def finish(self, *, code: str, state: str, cookie: str, user_agent: str = "",
                     ip: str = "") -> tuple[Person, str]:
        """The provider sent the browser back. Decide, from the id token and nothing else, who this is.

        Every refusal below is a sign-in that did not happen, and each says which rule stopped it —
        that is what makes a failed SSO sign-in something an admin can fix rather than guess at.
        """
        config = await self.settings()
        if not config.ready:
            raise Refused("Single sign-on is not set up for this workspace.", status=409)
        held = read_state(self._state_secret(), cookie) if cookie else None
        if held is None:
            raise Refused("That sign-in did not start here. Open the sign-in screen and try again.",
                          status=400)
        if float(held.get("until", 0)) < utcnow().timestamp():
            raise Refused("That sign-in took too long. Open the sign-in screen and try again.", status=400)
        if not state or not pysecrets.compare_digest(str(held.get("state", "")), state):
            raise Refused("That sign-in's state did not come back. It was not started by this browser.",
                          status=400)

        found = await self._discovery(config.issuer)
        secret = self.secrets.get(SSO_SECRET) if self.secrets else None
        form = {"grant_type": "authorization_code", "code": code,
                "redirect_uri": str(held.get("redirect", "")), "client_id": config.client_id}
        if secret:
            form["client_secret"] = secret
        answer = await asyncio.to_thread(
            self.fetch, str(found["token_endpoint"]),
            data=urllib.parse.urlencode(form).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        id_token = str(answer.get("id_token") or "")
        if not id_token:
            raise Refused("The identity provider returned no id token, so there is nothing that says "
                          "who this is.", status=502)
        keys = await asyncio.to_thread(self.fetch, str(found["jwks_uri"]))
        published = keys.get("keys")
        try:
            claims = verify_jwt(id_token, published if isinstance(published, list) else [])
        except BadToken as bad:
            raise Refused(str(bad), status=401) from bad
        self._judge(claims, config, nonce=str(held.get("nonce", "")))
        return await self._sign_in(claims, config, user_agent=user_agent, ip=ip)

    def _judge(self, claims: Mapping[str, Any], config: SsoConfig, *, nonce: str) -> None:
        """The claims that decide whether a correctly signed token is this workspace's token."""
        now = utcnow().timestamp()
        if str(claims.get("iss", "")).rstrip("/") != config.issuer.rstrip("/"):
            raise Refused("That token was issued by somebody else.", status=401)
        audience = claims.get("aud")
        holders = audience if isinstance(audience, list) else [audience]
        if config.client_id not in [str(a) for a in holders]:
            raise Refused("That token was meant for another application, not for this workspace.", status=401)
        if claims.get("azp") and str(claims["azp"]) != config.client_id:
            raise Refused("That token was issued to another application to use.", status=401)
        if float(claims.get("exp", 0) or 0) < now - SSO_SKEW:
            raise Refused("That sign-in has expired. Try again.", status=401)
        if float(claims.get("iat", 0) or 0) > now + SSO_SKEW:
            raise Refused("That token is dated in the future; check the clocks.", status=401)
        if not pysecrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise Refused("That token's nonce is not the one this sign-in asked for.", status=401)

    async def _sign_in(self, claims: Mapping[str, Any], config: SsoConfig, *, user_agent: str,
                       ip: str) -> tuple[Person, str]:
        """The account behind a verified token: found by subject, linked by email, or made."""
        subject = str(claims.get("sub") or "").strip()
        email = str(claims.get("email") or "").strip()
        name = str(claims.get("name") or "").strip() or email.split("@")[0]
        if not subject:
            raise Refused("That token names no subject, so there is nobody to be.", status=401)
        if not email or not EMAIL.match(email):
            raise Refused("That token carries no email address, and an account here is an email address. "
                          "Add the `email` scope to this application at the provider.", status=401)

        row = await self._by_subject(config.issuer, subject)
        linked = row is not None
        if row is None:
            row = await self.users.by_email(email)
            if row is not None:
                # An account that already existed, signing in through the provider for the first time.
                row.idp, row.idp_subject = config.issuer, subject
                await self.session.flush()
                await self.audit.record(action="sso.link", user_id=row.id, target=email,
                                        detail={"issuer": config.issuer}, ip=ip)
        if row is None:
            if not config.create_users:
                raise Refused(f"{email} signed in at {config.label}, but has no account here and this "
                              "workspace does not make them automatically. Ask an admin to add them.",
                              status=403)
            given = self._wanted_roles(claims, config)
            row = await self._make(email, name, subject, config, given)
            await self.audit.record(action="sso.create", user_id=row.id, target=email,
                                    detail={"issuer": config.issuer, "roles": given}, ip=ip)
        elif linked and row.email.lower() != email.lower():
            # The subject is the identity; the address is a label on it, and people change theirs.
            row.email = email
            await self.session.flush()

        if row.status != "active":
            await self._attempt_row(email, ip)
            raise Refused("This account is disabled. Ask an admin to turn it back on.", status=403)
        await self._apply_roles(row, claims, config, ip=ip)
        self.session.add(LoginAttempt(email=email, ok=True, ip=ip))
        await self.session.flush()
        identity = IdentityService(self.session, self.config)
        person = await identity.need(row.id)
        token = await identity.start_session(row.id, user_agent)
        await self.audit.record(action="sso.login", user_id=row.id, target=email,
                                detail={"issuer": config.issuer}, ip=ip)
        return person, token

    async def _attempt_row(self, email: str, ip: str) -> None:
        self.session.add(LoginAttempt(email=email, ok=False, ip=ip))
        await self.session.flush()

    async def _by_subject(self, issuer: str, subject: str) -> UserRow | None:
        """The account this provider's subject belongs to. Asked of the session here, beside the
        lock-out window, for the same reason: it is part of deciding who is asking."""
        stmt = select(UserRow).where(UserRow.idp == issuer, UserRow.idp_subject == subject).limit(1)
        return (await self.session.execute(stmt)).scalars().first()

    async def _make(self, email: str, name: str, subject: str, config: SsoConfig,
                    roles: list[str]) -> UserRow:
        """An account the provider vouched for. Its password hash is random and told to nobody, so
        this account can only ever be entered the way it was made — until an admin sets a password.

        It is given the roles its claims map to straight away, not the default ones a moment before
        the map is applied: two writes would put a role change nobody made into the audit log.
        """
        row = await self.users.add(UserRow(id="u_" + pysecrets.token_hex(6), email=email, name=name,
                                           password_hash=await _hash(pysecrets.token_urlsafe(32)),
                                           idp=config.issuer, idp_subject=subject))
        await self.users.set_roles(row.id, roles)
        await self.session.flush()
        return row

    def _wanted_roles(self, claims: Mapping[str, Any], config: SsoConfig) -> list[str]:
        """What the claim map makes of this person — or what a newcomer gets when it makes nothing."""
        if not config.role_map:
            return list(config.default_roles)
        raw = claims.get(config.role_claim)
        values = [str(v) for v in raw] if isinstance(raw, list) else ([str(raw)] if raw else [])
        mapped = sorted({config.role_map[v] for v in values if v in config.role_map})
        return mapped or sorted(config.default_roles)

    async def _apply_roles(self, row: UserRow, claims: Mapping[str, Any], config: SsoConfig,
                           *, ip: str) -> None:
        """What the claim map says this person is, every time they sign in.

        Applied on every sign-in and not only the first, because that is the point of a directory:
        taking somebody out of a group there must take the role away here. Three things are left
        alone — an empty map (the workspace is not using this), the Owner role (never granted or
        removed by a claim), and a person whose mapped roles come out the same as what they already
        have, which is nearly every sign-in and writes nothing.
        """
        if not config.role_map:
            return
        held = sorted(await self.users.role_ids(row.id))
        if "owner" in held:
            return                       # an Owner's roles are the workspace's business, not a claim's
        wanted = sorted(self._wanted_roles(claims, config))
        if wanted == held:
            return
        await self.users.set_roles(row.id, wanted)
        await self.session.flush()
        await self.audit.record(action="sso.roles", user_id=row.id, target=row.email,
                                detail={"from": held, "to": wanted, "claim": config.role_claim}, ip=ip)
