"""Signing in, and the guards that keep a workspace from locking itself out.

Every rule here used to live in a route handler. Testing them now means calling a method — no web
request, no client, no fixtures pretending to be a browser.
"""
from __future__ import annotations

import pytest

from app.repositories import SessionRepository, UserRepository
from app.services.errors import Refused
from app.services.identity import IdentityService

PASSWORD = "a long enough password"


async def person(service: IdentityService, email: str, name: str, roles: list[str]):
    return await service.create(email, name, PASSWORD, roles)


async def test_a_person_signs_in_and_carries_every_permission_they_wear(seeded):
    identity = IdentityService(seeded)
    made = await person(identity, "asha@example.com", "Asha", ["engineer", "approver"])
    assert made.can("sessions:chat") and made.can("runs:merge")      # from both roles at once

    signed_in, token = await identity.login("asha@example.com", PASSWORD, user_agent="pytest")
    assert signed_in.id == made.id
    assert (await identity.whoami(token)).email == "asha@example.com"
    assert (await UserRepository(seeded).get(made.id)).last_login_at is not None

    await identity.logout(token)
    assert await identity.whoami(token) is None


async def test_the_lock_out_lives_in_the_database_not_in_a_process(seeded):
    """The old lock-out was a dict in memory: it forgot everything on restart and did nothing at all
    with two workers. Here a second service — a stand-in for another worker — sees the same lock."""
    identity = IdentityService(seeded)
    await person(identity, "vik@example.com", "Vik", ["viewer"])

    for _ in range(5):
        with pytest.raises(Refused) as wrong:
            await identity.login("vik@example.com", "not the password")
        assert wrong.value.status == 401

    other_worker = IdentityService(seeded)
    with pytest.raises(Refused) as locked:
        await other_worker.login("vik@example.com", PASSWORD)       # the right password, still refused
    assert locked.value.status == 429 and "Wait" in str(locked.value)


async def test_the_last_owner_cannot_be_demoted_or_disabled(seeded):
    identity = IdentityService(seeded)
    owner = await person(identity, "owner@example.com", "Owner", ["owner"])

    with pytest.raises(Refused) as demoted:
        await identity.update(owner.id, actor=owner, roles=["engineer"])
    assert "last active Owner" in str(demoted.value)

    with pytest.raises(Refused) as disabled:
        await identity.update(owner.id, actor=owner, status="disabled")
    assert disabled.value.status == 409

    second = await person(identity, "second@example.com", "Second", ["owner"])
    after = await identity.update(owner.id, actor=second, roles=["engineer"])
    assert "owner" not in after.roles                                # allowed once someone else holds it


async def test_only_an_owner_may_grant_the_owner_role(seeded):
    identity = IdentityService(seeded)
    owner = await person(identity, "boss@example.com", "Boss", ["owner"])
    admin = await person(identity, "admin@example.com", "Admin", ["admin"])

    with pytest.raises(Refused) as refused:
        await identity.update(admin.id, actor=admin, roles=["owner"])
    assert refused.value.status == 403
    assert "owner" in (await identity.update(admin.id, actor=owner, roles=["owner"])).roles


async def test_a_new_password_ends_every_other_session(seeded):
    identity = IdentityService(seeded)
    made = await person(identity, "dev@example.com", "Dev", ["engineer"])
    _, phone = await identity.login("dev@example.com", PASSWORD)
    _, laptop = await identity.login("dev@example.com", PASSWORD)

    await identity.set_password(made.id, "an even longer password", keep=laptop)
    assert await identity.whoami(phone) is None
    assert (await identity.whoami(laptop)).id == made.id
    assert len(await SessionRepository(seeded).open_for(made.id)) == 1


async def test_a_disabled_account_is_signed_out_everywhere(seeded):
    identity = IdentityService(seeded)
    owner = await person(identity, "chief@example.com", "Chief", ["owner"])
    hired = await person(identity, "temp@example.com", "Temp", ["engineer"])
    _, token = await identity.login("temp@example.com", PASSWORD)

    await identity.update(hired.id, actor=owner, status="disabled")
    assert await identity.whoami(token) is None
    with pytest.raises(Refused) as refused:
        await identity.login("temp@example.com", PASSWORD)
    assert refused.value.status == 403


async def test_an_address_is_the_same_address_however_it_is_typed(seeded):
    identity = IdentityService(seeded)
    await person(identity, "Rajat@Example.com", "Rajat", ["owner"])

    with pytest.raises(Refused) as duplicate:
        await person(identity, "rajat@example.com", "Someone else", ["viewer"])
    assert "already uses that email" in str(duplicate.value)

    signed_in, _ = await identity.login("RAJAT@EXAMPLE.COM", PASSWORD)
    assert signed_in.name == "Rajat"


async def test_a_password_and_an_address_are_checked_before_anything_is_written(seeded):
    identity = IdentityService(seeded)
    for email, name, password, why in (
        ("not an address", "X", PASSWORD, "email"),
        ("ok@example.com", " ", PASSWORD, "name"),
        ("ok@example.com", "X", "short", "password"),
    ):
        with pytest.raises(Refused) as refused:
            await identity.create(email, name, password, ["viewer"])
        assert refused.value.status == 422, why
    assert await identity.count() == 0

    with pytest.raises(Refused) as unknown:
        await identity.create("ok@example.com", "X", PASSWORD, ["wizard"])
    assert "Unknown role" in str(unknown.value)
