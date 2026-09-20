"""Single sign-on, end to end, against a provider made in this repository.

Nothing here reaches the internet: `tests/fixtures/idp.py` is an OpenID Connect provider with a real
RSA key, and it is handed to the service through the one port that fetches anything. What is checked
is the part of SSO that is easy to get wrong and expensive to get wrong — that a token is only
believed when the issuer, the audience, the nonce, the expiry *and* the signature all hold; that the
state that left this browser is the state that came back; that the userinfo endpoint is never read;
that a claim can never make an Owner; and that requiring SSO leaves exactly one door open, the
Owner's, so a workspace can never lock itself out of itself.
"""
from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.app import create_api
from app.data.loader import sync_roles
from app.models import AuditEntry, User
from app.secrets import Secrets
from app.services import identity as identity_module
from app.services.identity import SSO_COOKIE

from tests.fixtures import idp as fake

OWNER = {"workspace": "Acme", "name": "Rajat", "email": "owner@example.com", "password": "correct horse battery"}
HEADERS = {"X-NC-Client": "test"}
REDIRECT = "https://work.example.com/api/auth/sso/callback"
SETTINGS = {"enabled": True, "issuer": fake.ISSUER, "clientId": fake.CLIENT_ID,
            "clientSecret": "sh-secret", "label": "Okta", "redirectUri": REDIRECT,
            "roleClaim": "groups", "roleMap": {"eng": "engineer"}, "defaultRoles": ["viewer"]}


class Holder:
    """Whatever the gateway dependency is asked for here: the secrets file, and nothing else."""

    def __init__(self, secrets: Secrets) -> None:
        self.secrets = secrets


@pytest_asyncio.fixture
async def client(session: AsyncSession, tmp_path: Path) -> AsyncIterator[AsyncClient]:
    await sync_roles(session)
    await session.flush()
    api = create_api(db=None)
    secrets = Secrets(tmp_path / "secrets.json")

    async def use_the_test_session() -> AsyncIterator[AsyncSession]:
        yield session

    api.dependency_overrides[deps.session] = use_the_test_session
    api.dependency_overrides[deps.gateway] = lambda: Holder(secrets)
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://api", headers=HEADERS) as c:
        yield c


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> fake.FakeIdp:
    """The provider the service will reach, in this module's place."""
    made = fake.FakeIdp()
    monkeypatch.setattr(identity_module, "http_json", made.fetch)
    return made


async def owner(client: AsyncClient) -> None:
    assert (await client.post("/auth/setup", json=OWNER)).status_code == 201


async def configure(client: AsyncClient, **over: Any) -> dict[str, Any]:
    answer = await client.put("/admin/sso", json={**SETTINGS, **over})
    assert answer.status_code == 200, answer.text
    return answer.json()


async def begin(client: AsyncClient) -> tuple[str, str]:
    """Start a sign-in and read back the state the server minted and the cookie it set."""
    started = await client.post("/auth/sso/start")
    assert started.status_code == 200, started.text
    query = parse_qs(urlparse(started.json()["url"]).query)
    return query["state"][0], query["nonce"][0]


async def audited(session: AsyncSession, action: str) -> list[AuditEntry]:
    return list((await session.execute(
        select(AuditEntry).where(AuditEntry.action == action).order_by(AuditEntry.seq))).scalars())


# ── before anything is configured ────────────────────────────────
async def test_a_workspace_with_no_provider_has_no_button_and_cannot_start_a_sign_in(client: AsyncClient):
    status = (await client.get("/auth/status")).json()
    assert status["sso"] == {"enabled": False, "label": "single sign-on", "passwordsOff": False}
    refused = await client.post("/auth/sso/start")
    assert refused.status_code == 409 and "not set up" in refused.json()["detail"]


async def test_the_sign_in_screen_learns_there_is_a_button_and_nothing_else_about_the_provider(
        client: AsyncClient, provider: fake.FakeIdp):
    await owner(client)
    await configure(client)
    await client.post("/auth/logout")
    status = (await client.get("/auth/status")).json()
    assert status["sso"] == {"enabled": True, "label": "Okta", "passwordsOff": False}
    # The issuer and the client id are an admin's business, not a stranger's.
    assert "issuer" not in status["sso"] and "clientId" not in status["sso"]


async def test_the_client_secret_is_kept_like_a_model_key_and_never_read_back(client: AsyncClient,
                                                                              tmp_path: Path):
    await owner(client)
    doc = await configure(client)
    assert doc["hasSecret"] is True and doc["secretMask"] == "••••cret"
    assert "clientSecret" not in doc and "sh-secret" not in str(doc)


# ── configuring it ───────────────────────────────────────────────
async def test_a_claim_can_never_carry_somebody_into_the_owner_role(client: AsyncClient):
    await owner(client)
    refused = await client.put("/admin/sso", json={**SETTINGS, "roleMap": {"admins": "owner"}})
    assert refused.status_code == 422
    assert "cannot carry someone into the Owner role" in refused.json()["detail"]


async def test_sso_cannot_be_required_before_it_works_or_turned_on_half_configured(client: AsyncClient):
    await owner(client)
    half = await client.put("/admin/sso", json={"enabled": True, "issuer": fake.ISSUER})
    assert half.status_code == 422 and "client id" in half.json()["detail"]

    early = await client.put("/admin/sso", json={"requireSso": True})
    assert early.status_code == 422 and "nobody" in early.json()["detail"]


async def test_only_an_admin_may_read_or_change_it(client: AsyncClient):
    await owner(client)
    await configure(client)
    await client.post("/auth/logout")
    assert (await client.get("/admin/sso")).status_code == 401


# ── a sign-in that works ─────────────────────────────────────────
async def test_a_person_the_provider_vouches_for_gets_an_account_a_session_and_an_audit_trail(
        client: AsyncClient, provider: fake.FakeIdp, session: AsyncSession):
    await owner(client)
    await configure(client)
    await client.post("/auth/logout")

    state, nonce = await begin(client)
    provider.token = fake.sign(fake.claims(nonce=nonce, groups=["eng"]))
    back = await client.get("/auth/sso/callback", params={"code": "the-code", "state": state})

    assert back.status_code == 303 and back.headers["location"] == "https://work.example.com/"
    assert back.cookies.get("nc_session")
    me = (await client.get("/auth/me")).json()
    assert me["user"]["email"] == "dev@example.com" and me["user"]["roles"] == ["engineer"]

    # The code really was exchanged, with the client secret, at the token endpoint.
    assert provider.posted[0]["code"] == "the-code" and provider.posted[0]["client_secret"] == "sh-secret"
    assert provider.posted[0]["redirect_uri"] == REDIRECT
    # And nothing but the id token decided who this is: userinfo was never asked for.
    assert not any("userinfo" in url for url in provider.asked)

    assert [e.target for e in await audited(session, "sso.create")] == ["dev@example.com"]
    assert [e.target for e in await audited(session, "sso.login")] == ["dev@example.com"]


async def test_the_one_time_cookie_is_spent_and_cleared(client: AsyncClient, provider: fake.FakeIdp):
    await owner(client)
    await configure(client)
    state, nonce = await begin(client)
    assert client.cookies.get(SSO_COOKIE)
    provider.token = fake.sign(fake.claims(nonce=nonce))
    await client.get("/auth/sso/callback", params={"code": "c", "state": state})
    assert not client.cookies.get(SSO_COOKIE)


# ── the checks a forged or stray sign-in has to fail ─────────────
async def failed(client: AsyncClient, provider: fake.FakeIdp, *, token: str, state: str | None = None,
                 fresh: bool = True) -> str:
    """Run a callback that should not sign anybody in, and hand back the reason it gave the person."""
    if fresh:
        state = (await begin(client))[0] if state is None else state
    provider.token = token
    back = await client.get("/auth/sso/callback", params={"code": "c", "state": state or ""})
    assert back.status_code == 303
    where = urlparse(back.headers["location"])
    assert where.path == "/" and "ssoError" in where.query, back.headers["location"]
    assert not back.cookies.get("nc_session")
    return unquote(parse_qs(where.query)["ssoError"][0])


@pytest.mark.parametrize("spoil, expected", [
    ({"iss": "https://elsewhere.test"}, "issued by somebody else"),
    ({"aud": "another-app"}, "meant for another application"),
    ({"azp": "another-app"}, "issued to another application"),
    ({"exp": int(time.time()) - 600}, "has expired"),
    ({"iat": int(time.time()) + 3600}, "dated in the future"),
    ({"sub": ""}, "names no subject"),
    ({"email": ""}, "carries no email address"),
])
async def test_a_token_that_fails_any_one_check_signs_nobody_in(client: AsyncClient,
                                                                provider: fake.FakeIdp,
                                                                spoil: dict[str, Any], expected: str):
    await owner(client)
    await configure(client)
    await client.post("/auth/logout")
    state, nonce = await begin(client)
    reason = await failed(client, provider, token=fake.sign(fake.claims(nonce=nonce, **spoil)),
                          state=state, fresh=False)
    assert expected in reason
    assert (await client.get("/auth/me")).status_code == 401


async def test_the_nonce_and_the_state_must_both_be_the_ones_this_browser_started_with(
        client: AsyncClient, provider: fake.FakeIdp):
    await owner(client)
    await configure(client)
    await client.post("/auth/logout")

    state, nonce = await begin(client)
    # Right state, a nonce from somewhere else: a token replayed from another sign-in.
    assert "nonce is not the one" in await failed(
        client, provider, token=fake.sign(fake.claims(nonce="somebody-elses")), state=state, fresh=False)

    state, nonce = await begin(client)
    assert "state did not come back" in await failed(
        client, provider, token=fake.sign(fake.claims(nonce=nonce)), state="not-the-state", fresh=False)

    # No cookie at all: the sign-in did not start here.
    state, nonce = await begin(client)
    client.cookies.delete(SSO_COOKIE)
    assert "did not start here" in await failed(
        client, provider, token=fake.sign(fake.claims(nonce=nonce)), state=state, fresh=False)


async def test_a_signature_that_is_not_the_provider_s_is_not_believed(client: AsyncClient,
                                                                      provider: fake.FakeIdp):
    await owner(client)
    await configure(client)
    await client.post("/auth/logout")
    state, nonce = await begin(client)
    good = fake.sign(fake.claims(nonce=nonce))
    head, body, signature = good.split(".")
    flipped = signature[:-4] + ("aaaa" if not signature.endswith("aaaa") else "bbbb")
    assert "signature is not this identity provider's" in await failed(
        client, provider, token=f"{head}.{body}.{flipped}", state=state, fresh=False)


async def test_an_unsigned_token_is_refused_by_name(client: AsyncClient, provider: fake.FakeIdp):
    await owner(client)
    await configure(client)
    await client.post("/auth/logout")
    state, nonce = await begin(client)
    assert "NeuroCode verifies" in await failed(
        client, provider, token=fake.sign(fake.claims(nonce=nonce), alg="none"), state=state, fresh=False)


async def test_a_provider_that_publishes_no_key_signs_nobody_in(client: AsyncClient, monkeypatch):
    made = fake.FakeIdp(keys=[])
    monkeypatch.setattr(identity_module, "http_json", made.fetch)
    await owner(client)
    await configure(client)
    await client.post("/auth/logout")
    state, nonce = await begin(client)
    assert "published no RSA key" in await failed(
        client, made, token=fake.sign(fake.claims(nonce=nonce)), state=state, fresh=False)


async def test_a_provider_that_returns_no_id_token_signs_nobody_in(client: AsyncClient, monkeypatch):
    made = fake.FakeIdp(token_answer={"access_token": "at"})
    monkeypatch.setattr(identity_module, "http_json", made.fetch)
    await owner(client)
    await configure(client)
    await client.post("/auth/logout")
    state, _ = await begin(client)
    back = await client.get("/auth/sso/callback", params={"code": "c", "state": state})
    assert "no id token" in unquote(back.headers["location"])


async def test_an_identity_provider_is_only_ever_reached_over_https():
    from app.services.errors import Refused

    with pytest.raises(Refused) as refused:
        identity_module.http_json("http://idp.test/.well-known/openid-configuration")
    assert "must be reached over https" in str(refused.value)


# ── accounts: linking, roles, and who may not come in ────────────
async def test_an_account_that_already_exists_is_linked_by_its_email_once_and_by_subject_after(
        client: AsyncClient, provider: fake.FakeIdp, session: AsyncSession):
    await owner(client)
    await configure(client, createUsers=False)
    made = await client.post("/admin/users", json={"email": "dev@example.com", "name": "Dev",
                                                   "password": "another good password", "roles": ["viewer"]})
    assert made.status_code == 201
    await client.post("/auth/logout")

    state, nonce = await begin(client)
    provider.token = fake.sign(fake.claims(nonce=nonce))
    assert (await client.get("/auth/sso/callback", params={"code": "c", "state": state})).status_code == 303
    row = (await session.execute(select(User).where(User.email == "dev@example.com"))).scalar_one()
    assert (row.idp, row.idp_subject) == (fake.ISSUER, "idp-subject-1")
    assert [e.target for e in await audited(session, "sso.link")] == ["dev@example.com"]

    # Their address changes at the provider; the subject is the identity, so it follows.
    await client.post("/auth/logout")
    state, nonce = await begin(client)
    provider.token = fake.sign(fake.claims(nonce=nonce, email="dev.person@example.com"))
    assert (await client.get("/auth/sso/callback", params={"code": "c", "state": state})).status_code == 303
    await session.refresh(row)
    assert row.email == "dev.person@example.com"
    assert len(await audited(session, "sso.link")) == 1      # linked once, not again


async def test_a_workspace_that_does_not_make_accounts_says_so_instead_of_making_one(
        client: AsyncClient, provider: fake.FakeIdp):
    await owner(client)
    await configure(client, createUsers=False)
    await client.post("/auth/logout")
    state, nonce = await begin(client)
    reason = await failed(client, provider, token=fake.sign(fake.claims(nonce=nonce)), state=state,
                          fresh=False)
    assert "has no account here" in reason and "Ask an admin" in reason


async def test_a_claim_moves_somebody_between_roles_on_every_sign_in_and_it_is_written_down(
        client: AsyncClient, provider: fake.FakeIdp, session: AsyncSession):
    await owner(client)
    await configure(client, roleMap={"eng": "engineer", "pm": "approver"})
    await client.post("/auth/logout")

    state, nonce = await begin(client)
    provider.token = fake.sign(fake.claims(nonce=nonce, groups=["eng"]))
    await client.get("/auth/sso/callback", params={"code": "c", "state": state})
    assert (await client.get("/auth/me")).json()["user"]["roles"] == ["engineer"]

    # Moved to another group at the provider: the role here follows on the next sign-in.
    await client.post("/auth/logout")
    state, nonce = await begin(client)
    provider.token = fake.sign(fake.claims(nonce=nonce, groups=["pm"]))
    await client.get("/auth/sso/callback", params={"code": "c", "state": state})
    assert (await client.get("/auth/me")).json()["user"]["roles"] == ["approver"]

    [line] = await audited(session, "sso.roles")
    assert line.detail == {"from": ["engineer"], "to": ["approver"], "claim": "groups"}

    # Taken out of every mapped group: back to what the workspace gives a newcomer, never nothing.
    await client.post("/auth/logout")
    state, nonce = await begin(client)
    provider.token = fake.sign(fake.claims(nonce=nonce, groups=["finance"]))
    await client.get("/auth/sso/callback", params={"code": "c", "state": state})
    assert (await client.get("/auth/me")).json()["user"]["roles"] == ["viewer"]


async def test_an_owner_keeps_their_roles_whatever_a_claim_says(client: AsyncClient,
                                                                provider: fake.FakeIdp):
    await owner(client)
    await configure(client, roleMap={"eng": "engineer"})
    state, nonce = await begin(client)
    # The Owner signs in through the provider: linked by email, and left an Owner.
    provider.token = fake.sign(fake.claims(nonce=nonce, email=OWNER["email"], groups=["eng"]))
    assert (await client.get("/auth/sso/callback", params={"code": "c", "state": state})).status_code == 303
    assert (await client.get("/auth/me")).json()["user"]["roles"] == ["owner"]


async def test_a_disabled_account_cannot_be_let_in_by_a_perfect_token(client: AsyncClient,
                                                                      provider: fake.FakeIdp,
                                                                      session: AsyncSession):
    await owner(client)
    await configure(client)
    await client.post("/auth/logout")
    state, nonce = await begin(client)
    provider.token = fake.sign(fake.claims(nonce=nonce))
    await client.get("/auth/sso/callback", params={"code": "c", "state": state})
    person = (await client.get("/auth/me")).json()["user"]
    await client.post("/auth/logout")

    await client.post("/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
    assert (await client.patch(f"/admin/users/{person['id']}", json={"status": "disabled"})).status_code == 200
    await client.post("/auth/logout")

    state, nonce = await begin(client)
    assert "account is disabled" in await failed(
        client, provider, token=fake.sign(fake.claims(nonce=nonce)), state=state, fresh=False)


async def test_an_account_the_provider_made_has_no_password_that_will_ever_work(client: AsyncClient,
                                                                                provider: fake.FakeIdp):
    """What "a person whose IdP account is gone cannot sign in" comes to in practice: the only door
    into an account the provider made is the provider, and once it stops vouching there is no other."""
    await owner(client)
    await configure(client)
    await client.post("/auth/logout")
    state, nonce = await begin(client)
    provider.token = fake.sign(fake.claims(nonce=nonce))
    await client.get("/auth/sso/callback", params={"code": "c", "state": state})
    await client.post("/auth/logout")

    for guess in ("", "dev@example.com", "password12", "correct horse battery"):
        refused = await client.post("/auth/login", json={"email": "dev@example.com", "password": guess})
        assert refused.status_code in (401, 429)


# ── requiring it ─────────────────────────────────────────────────
async def test_requiring_sso_turns_passwords_off_for_everyone_but_an_owner(client: AsyncClient,
                                                                           provider: fake.FakeIdp,
                                                                           session: AsyncSession):
    await owner(client)
    await configure(client)
    made = await client.post("/admin/users", json={"email": "eng@example.com", "name": "Eng",
                                                   "password": "a long enough password", "roles": ["engineer"]})
    assert made.status_code == 201
    await configure(client, requireSso=True)
    await client.post("/auth/logout")

    assert (await client.get("/auth/status")).json()["sso"]["passwordsOff"] is True

    shut = await client.post("/auth/login", json={"email": "eng@example.com",
                                                  "password": "a long enough password"})
    assert shut.status_code == 403 and "signs in through Okta" in shut.json()["detail"]

    # The Owner is the one door left, on purpose: a provider that breaks must not lock the workspace.
    still = await client.post("/auth/login", json={"email": OWNER["email"], "password": OWNER["password"]})
    assert still.status_code == 200

    # And the refusal is in the log, like every other refused sign-in.
    assert any("signs in through Okta" in (e.detail or {}).get("reason", "")
               for e in await audited(session, "auth.login_failed"))


async def test_requiring_sso_does_not_stop_the_provider_itself(client: AsyncClient,
                                                               provider: fake.FakeIdp):
    await owner(client)
    await configure(client, requireSso=True)
    await client.post("/auth/logout")
    state, nonce = await begin(client)
    provider.token = fake.sign(fake.claims(nonce=nonce, groups=["eng"]))
    assert (await client.get("/auth/sso/callback", params={"code": "c", "state": state})).status_code == 303
    assert (await client.get("/auth/me")).json()["user"]["email"] == "dev@example.com"


# ── the wizard keeps the first Owner ─────────────────────────────
async def test_the_first_owner_is_the_wizard_s_and_a_provider_cannot_make_one(client: AsyncClient,
                                                                              provider: fake.FakeIdp):
    # With nobody here at all, there is no workspace to configure a provider for, and no button.
    assert (await client.get("/auth/status")).json()["needsSetup"] is True
    assert (await client.put("/admin/sso", json=SETTINGS)).status_code == 401

    await owner(client)
    await configure(client)
    await client.post("/auth/logout")
    state, nonce = await begin(client)
    provider.token = fake.sign(fake.claims(nonce=nonce, groups=["eng"]))
    await client.get("/auth/sso/callback", params={"code": "c", "state": state})
    # The person the provider sent gets what the workspace hands a newcomer, and never the workspace.
    assert (await client.get("/auth/me")).json()["user"]["roles"] == ["engineer"]
