"""`nc chat` driven like a person would, against the in-memory API: the session drawn, `@` attaching a
file, `/` offering commands, a question sent with its attachment, and a permission card answered with a
button."""
from __future__ import annotations

import asyncio

from textual.widgets import Button, Input, Markdown, OptionList, Static

from neurocode_cli.client import Client
from neurocode_cli.tui import ChatApp

from .conftest import API, TOKEN, FakeApi


async def settle(pilot, until, tries: int = 60) -> None:
    """Workers run on threads; wait until what they bring back is on screen."""
    for _ in range(tries):
        await pilot.pause(0.05)
        if until():
            return
    raise AssertionError("the screen never got there")


def test_a_session_attaches_asks_and_answers_a_card(signed: FakeApi):
    async def scenario() -> None:
        app = ChatApp(Client(API, TOKEN), project="erp", session=None)
        async with app.run_test(size=(110, 40)) as pilot:
            await settle(pilot, lambda: app.ref is not None)
            assert app.ref == "SES-1"
            assert "ERP" in str(app.query_one("#top", Static).render())

            prompt = app.query_one("#prompt", Input)
            prompt.focus()
            await pilot.press("/", "c", "o")
            box = app.query_one("#suggest", OptionList)
            await settle(pilot, lambda: box.has_class("open"))
            assert [s["name"] for s in app.suggestions] == ["compact"]
            await pilot.press("escape")
            assert not box.has_class("open")

            prompt.value = ""
            await pilot.press(*"what does @inv")
            await settle(pilot, lambda: app.mode == "mention" and bool(app.suggestions))
            assert app.suggestions[0]["ref"] == "src/invoice.py"
            await pilot.press("tab")
            assert prompt.value == "what does @src/invoice.py "
            assert app.attached == [{"kind": "file", "ref": "src/invoice.py", "name": "src/invoice.py"}]

            signed.after_ask = "card"
            await pilot.press(*"do?", "enter")
            await settle(pilot, lambda: app.card is not None)
            sent = next(b for m, p, b in signed.seen if p == "/api/sessions/SES-1/messages")
            assert sent["text"] == "what does @src/invoice.py do?"
            assert sent["attachments"] == [{"kind": "file", "ref": "src/invoice.py", "name": "src/invoice.py"}]
            assert app.query_one("#card").has_class("open")

            await pilot.click("#once")
            await settle(pilot, lambda: any("/permissions/" in p for _, p, _ in signed.seen))
            assert ("POST", "/api/sessions/SES-1/permissions/2", {"decision": "once"}) in signed.seen
            await settle(pilot, lambda: bool(app.query(Markdown)) and not app.busy)
            assert not app.query_one("#card").has_class("open")

    asyncio.run(scenario())


def test_help_and_a_new_session_are_the_terminals_own(signed: FakeApi):
    async def scenario() -> None:
        app = ChatApp(Client(API, TOKEN), project="erp", session=None)
        async with app.run_test(size=(100, 36)) as pilot:
            await settle(pilot, lambda: app.ref == "SES-1")
            prompt = app.query_one("#prompt", Input)
            prompt.value = "/help"
            await pilot.press("space", "backspace")
            app.close_suggestions()
            await pilot.press("enter")
            await settle(pilot, lambda: bool(app.query(Markdown)))
            assert not any(p.endswith("/messages") for _, p, _ in signed.seen)   # /help is never sent
            await pilot.press("ctrl+n")
            await settle(pilot, lambda: app.ref == "SES-2")
            assert isinstance(app.query_one("#once"), Button)

    asyncio.run(scenario())
