"""Personal access tokens: how the `nc` terminal client, and any script, signs in as a person.

A token acts as the person who made it, and never as more. Its scopes are the permissions it may use,
checked again on every request against what the person holds *now* — a role taken away yesterday is
not kept alive by a token made the week before. An empty scope list means everything the person holds,
with one exception: `machine:access` (a shell on the API's machine) is carried only by a token that
names it. A token that leaks out of a dotfile or a CI log must not open a terminal.

Only the token's hash is stored, beside its first few characters so a list can tell two apart; the
token itself is shown once, when it is made. Expired and revoked tokens are refused with 401 — the
same answer as a token that was never made, so a caller cannot probe which of the three it holds.

Tokens are managed from a signed-in session, never with a token: otherwise a leaked token without
`machine:access` could mint itself a new one that has it.
"""
from __future__ import annotations

import secrets as pysecrets
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from ..data.base import utcnow
from ..data.catalogue import PERMISSION_ORDER, PERMISSIONS
from ..models import ApiToken
from ..repositories import AuditRepository, NotFound, Page, Repository, UserRepository
from .errors import Refused
from .identity import IdentityService, Person
from .security import token_hash

#: What every personal token starts with, so deps can tell one from a session token without a lookup,
#: and a secret scanner (or a person) can recognise one in a log.
PREFIX = "nc_pat_"
#: How many characters of the token a list shows: the prefix and four more.
SHOWN = len(PREFIX) + 4
#: The permission a token carries only when it names it.
MACHINE = "machine:access"
#: `last_used_at` is written at most this often per token: every request of a script polling a run
#: would otherwise be an UPDATE on the same row.
TOUCH_EVERY = timedelta(minutes=1)
#: The longest a token may be made to last. Left out, a token lasts until it is revoked.
MAX_DAYS = 366
#: The most tokens a person may hold at once, revoked and expired ones not counted.
MAX_LIVE = 50
MAX_NAME = 120
#: The words every refused token gets. The same for unknown, expired and revoked, on purpose.
REFUSED = "This access token is not valid. It may have expired or been revoked — make a new one in Settings."


@dataclass(frozen=True)
class TokenPerson(Person):
    """A person, as a token lets them act: their permissions cut down to the token's scopes."""

    token_id: str = ""
    token_name: str = ""


def effective(held: Iterable[str], scopes: Iterable[str]) -> frozenset[str]:
    """What a token may do: its scopes within what the person holds, or — with no scopes — all of it
    except `machine:access`, which only a token naming it carries."""
    have, named = frozenset(held), [s for s in scopes if isinstance(s, str)]
    if not named:
        return have - {MACHINE}
    return have & frozenset(named)


def state_of(row: ApiToken, now: datetime | None = None) -> str:
    """active | expired | revoked — decided here, once, for the list and for the door alike."""
    if row.revoked_at is not None:
        return "revoked"
    if row.expires_at is not None and row.expires_at <= (now or utcnow()):
        return "expired"
    return "active"


def token_json(row: ApiToken) -> dict[str, object]:
    """A token as a list shows it. The token itself is never in here — only its first characters."""
    scopes = [s for s in (row.scopes or []) if isinstance(s, str)]
    return {"id": row.id, "name": row.name, "prefix": row.prefix, "scopes": scopes,
            # What an empty list means, said by the server rather than guessed by each client.
            "allScopes": not scopes,
            "createdAt": row.created_at.isoformat() if row.created_at else None,
            "lastUsedAt": row.last_used_at.isoformat() if row.last_used_at else None,
            "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
            "revokedAt": row.revoked_at.isoformat() if row.revoked_at else None,
            "state": state_of(row)}


class TokenRepository(Repository[ApiToken]):
    model = ApiToken

    async def by_hash(self, digest: str) -> ApiToken | None:
        return await self.one(ApiToken.token_hash == digest)

    async def of(self, user_id: str, *, limit: int | None = None, offset: int = 0) -> Page[ApiToken]:
        """A person's tokens, newest first: the live ones and the ones that are not, so a revoked
        token's last use can still be read."""
        return await self.page(ApiToken.user_id == user_id, order_by=(ApiToken.created_at.desc(), ApiToken.id),
                               limit=limit, offset=offset)

    async def live_count(self, user_id: str) -> int:
        now = utcnow()
        return await self.count(ApiToken.user_id == user_id, ApiToken.revoked_at.is_(None),
                                (ApiToken.expires_at.is_(None)) | (ApiToken.expires_at > now))


class TokenService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tokens = TokenRepository(session)
        self.audit = AuditRepository(session)

    # ── the door ─────────────────────────────────────────────────
    async def person(self, secret: str) -> TokenPerson:
        """The person behind a token, cut to its scopes — or a 401. Touches `last_used_at` when the last
        touch is more than a minute old."""
        row = await self.tokens.by_hash(token_hash(secret)) if secret.startswith(PREFIX) else None
        if row is None or state_of(row) != "active":
            raise Refused(REFUSED, status=401)
        found = await IdentityService(self.session).person(row.user_id)
        if found is None or found.status != "active":
            raise Refused(REFUSED, status=401)
        now = utcnow()
        if row.last_used_at is None or now - row.last_used_at >= TOUCH_EVERY:
            row.last_used_at = now
            await self.session.flush()
        return TokenPerson(found.id, found.email, found.name, found.status, found.roles,
                           effective(found.permissions, row.scopes or []), token_id=row.id, token_name=row.name)

    # ── a person's own tokens ────────────────────────────────────
    async def mine(self, who: Person, *, limit: int | None = None, offset: int = 0) -> Page[ApiToken]:
        return await self.tokens.of(who.id, limit=limit, offset=offset)

    async def create(self, who: Person, *, name: str, scopes: list[str], expires_days: int | None,
                     ip: str = "") -> tuple[ApiToken, str]:
        """A new token for this person. Its scopes must be permissions they hold; it is returned with the
        one copy of its secret there will ever be."""
        name = name.strip()
        if not name:
            raise Refused("Give the token a name, so you can tell it apart later.", status=422)
        if len(name) > MAX_NAME:
            raise Refused(f"Keep the name to {MAX_NAME} characters.", status=422)
        known = {p.id for p in PERMISSIONS}
        wanted = sorted(set(scopes), key=lambda p: PERMISSION_ORDER.get(p, len(PERMISSION_ORDER)))
        if unknown := [s for s in wanted if s not in known]:
            raise Refused(f"Unknown permission: {', '.join(unknown)}.", status=422)
        if missing := [s for s in wanted if s not in who.permissions]:
            raise Refused(f"A token cannot carry what you do not hold: {', '.join(missing)}.", status=403)
        if expires_days is not None and not 1 <= expires_days <= MAX_DAYS:
            raise Refused(f"A token lasts between 1 and {MAX_DAYS} days, or until it is revoked.", status=422)
        if await self.tokens.live_count(who.id) >= MAX_LIVE:
            raise Refused(f"You already hold {MAX_LIVE} live tokens. Revoke one you no longer use first.")
        if await UserRepository(self.session).get(who.id) is None:
            raise Refused("That person is not here.", status=404)

        secret = PREFIX + pysecrets.token_urlsafe(32)
        row = ApiToken(id="tok_" + pysecrets.token_hex(8), user_id=who.id, name=name, prefix=secret[:SHOWN],
                       token_hash=token_hash(secret), scopes=wanted, created_at=utcnow(),
                       expires_at=utcnow() + timedelta(days=expires_days) if expires_days else None)
        await self.tokens.add(row)
        await self.audit.record(action="token.create", user_id=who.id, target=f"{name} ({row.prefix}…)",
                                detail={"token": row.id, "scopes": wanted or "all but machine:access",
                                        "expiresAt": row.expires_at.isoformat() if row.expires_at else None},
                                ip=ip)
        return row, secret

    async def revoke(self, who: Person, token_id: str, *, ip: str = "") -> ApiToken:
        """Revoked at once, for good. Someone else's token reads as not there — not as forbidden, which
        would say it exists. Revoking twice answers the token as it already stands."""
        row = await self.tokens.get(token_id)
        if row is None or row.user_id != who.id:
            raise NotFound(f"token {token_id}")
        if row.revoked_at is None:
            row.revoked_at = utcnow()
            await self.session.flush()
            await self.audit.record(action="token.revoke", user_id=who.id, target=f"{row.name} ({row.prefix}…)",
                                    detail={"token": row.id}, ip=ip)
        return row
