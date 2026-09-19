"""`nc chat` — a session, full screen.

The same session the web app's Sessions screen shows, live in both at once: the answer streams in as it is
written, its reasoning folded under it, every tool call on a line of its own, and a permission card asked
right here — allow once, for this session, or refuse. `@` finds files, symbols, facts and plans to attach
(the web composer's own lookup); `/` offers the terminal's commands and the project's own slash commands,
which the server expands exactly as it does for the web.

Every call to the API runs on a worker thread, so the screen never waits on the network; the live stream is
read by `Listener` on a thread of its own and handed to the screen with `call_from_thread`.
"""
from __future__ import annotations

import queue
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from rich.markup import escape
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalGroup, VerticalScroll
from textual.widgets import Button, Collapsible, Input, Markdown, OptionList, Static
from textual.widgets.option_list import Option

from .client import ApiError, Client, Event, Listener
from .conversation import TOOL_LABEL, Conversation, Update, waiting_card
from .render import when

#: The terminal's own commands. Anything else after `/` is the project's command, or a question.
LOCAL = {
    "help": "What the keys and commands do",
    "new": "Start a new session in this project",
    "sessions": "Switch to another session",
    "stop": "Stop the answer being written",
    "compact": "Fold older turns into a summary a model writes",
    "plan": "Make the last question a plan",
    "export": "Write this session to a Markdown file here",
    "quit": "Leave",
}
MENTION = re.compile(r"(?:^|\s)@([^\s@]*)$")
SLASH = re.compile(r"^/([\w:.-]*)$")

HELP = """**Keys** — Enter sends · Esc stops the answer · Tab or ↑↓ choose a suggestion · Ctrl+N new session · Ctrl+Q quit

**@** attaches a file, symbol, fact or plan (typed `@` then a few letters). **/** runs a command:
""" + "\n".join(f"- `/{k}` — {v}" for k, v in LOCAL.items()) + """
- the project's own slash commands (from `.claude/commands` and `.neurocode/commands`) are listed too.
"""


def tool_line(message: dict[str, Any]) -> str:
    tool = str(message.get("tool") or "")
    label = TOOL_LABEL.get(tool, tool.replace("_", " "))
    detail = str(message.get("detail") or message.get("why") or "")[:140]
    mark = "[red]✗[/]" if message.get("ok") is False else "·"
    return f"[dim]{mark} {escape(label)}{(' — ' + escape(detail)) if detail else ''}[/]"


class Prompt(Input):
    """The composer. Up, down and Tab work the suggestion list while it is open."""

    BINDINGS: ClassVar = [
        Binding("down", "app.suggest(1)", show=False),
        Binding("up", "app.suggest(-1)", show=False),
        Binding("tab", "app.accept", show=False),
    ]


class ChatApp(App[None]):
    TITLE = "NeuroCode"
    CSS = """
    Screen { background: $surface; }
    #top { height: 1; padding: 0 2; color: $text-muted; background: $panel; }
    #log { padding: 1 2; scrollbar-size-vertical: 1; }
    .you { color: $text; text-style: bold; margin: 1 0 0 0; }
    .tool { color: $text-muted; }
    .note { color: $warning; }
    .card { color: $warning; margin: 1 0 0 0; }
    .meta { color: $text-muted; margin: 0 0 1 0; }
    .thinking { color: $text-muted; text-style: italic; }
    Markdown { margin: 0; padding: 0; background: $surface; }
    Collapsible { border: none; padding: 0; margin: 0; background: $surface; }
    Collapsible Static { color: $text-muted; text-style: italic; }
    #suggest { height: auto; max-height: 10; margin: 0 2; border: round $primary 40%; display: none; }
    #suggest.open { display: block; }
    #card { height: auto; padding: 0 2; display: none; }
    #card.open { display: block; }
    #card Button { margin: 0 1 0 0; min-width: 12; }
    #prompt { margin: 0 2; border: round $primary 50%; }
    #hint { height: 1; padding: 0 3; color: $text-muted; }
    """
    BINDINGS: ClassVar = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("escape", "stop", "Stop / close", show=False),
        Binding("ctrl+n", "new", "New session"),
    ]

    def __init__(self, client: Client, *, project: str | None, session: str | None) -> None:
        super().__init__()
        self.client = client
        self.project = project
        self.ref = session
        self.talk: Conversation | None = None
        self.listener: Listener | None = None
        self.widgets: dict[int, Any] = {}
        self.attached: list[dict[str, str]] = []
        self.commands: list[dict[str, Any]] = []
        self.busy = False
        self.card: dict[str, Any] | None = None
        self.mode = ""                        # what the suggestion list holds: mention | slash | session
        self.suggestions: list[dict[str, Any]] = []
        self.live_answer: Markdown | None = None
        self.live_thinking: Static | None = None
        self.last_draw = 0.0
        self.quiet_since = time.monotonic()
        self.closing = False
        self.stream_up = False

    # ── layout ───────────────────────────────────────────────────
    def compose(self) -> ComposeResult:
        yield Static("Opening…", id="top")
        yield VerticalScroll(id="log")
        yield OptionList(id="suggest")
        with Horizontal(id="card"):
            yield Button("Allow once", id="once", variant="primary")
            yield Button("Allow for this session", id="session")
            yield Button("Refuse", id="refuse", variant="error")
        yield Prompt(placeholder="Ask about this project — @ attaches, / runs a command", id="prompt")
        yield Static("", id="hint")

    def on_mount(self) -> None:
        self.query_one("#prompt", Input).focus()
        self.run_worker(self._pump, thread=True, name="stream", exit_on_error=False)
        self.set_interval(1, self._catch_up_if_quiet)
        self.background(self._open, self.ref, self.project, then=self._opened)

    def on_unmount(self) -> None:
        self.closing = True
        if self.listener is not None:
            self.listener.stop()

    # ── talking to the API off the screen's thread ───────────────
    def background(self, call: Callable[..., Any], *args: Any, then: Callable[[Any], None] | None = None) -> None:
        def job() -> None:
            try:
                result = call(*args)
            except ApiError as e:
                self.call_from_thread(self.say_error, e.detail)
                return
            if then is not None:
                self.call_from_thread(then, result)
        self.run_worker(job, thread=True, exit_on_error=False)

    def say_error(self, detail: str) -> None:
        self.busy = False
        self.notify(detail, severity="error", timeout=8)
        self._hint()

    def _pump(self) -> None:
        """The live stream, handed to the screen one event at a time — and opened again when it drops, with
        the session read afresh meanwhile, so a sleeping laptop or a restarted API loses nothing."""
        while not self.closing:
            listener = self.listener = Listener(self.client)
            listener.start()
            self.call_from_thread(self.stream_state, True)
            while not self.closing:
                try:
                    event = listener.events.get(timeout=1.0)
                except queue.Empty:
                    continue
                if event.kind == "error":
                    listener.stop()
                    self.call_from_thread(self.stream_state, False)
                    break
                self.call_from_thread(self.take_event, event)
            time.sleep(2.0)

    def stream_state(self, up: bool) -> None:
        self.stream_up = up
        self._hint()

    def _open(self, ref: str | None, project: str | None) -> dict[str, Any]:
        if ref is None:
            assert project is not None
            ref = self.client.start_session(project, "")["ref"]
        found = self.client.session(ref)
        commands = []
        try:
            commands = self.client.commands(found["projectId"])
        except ApiError:
            pass                                  # no project commands readable: the terminal's own still work
        return {"session": found, "commands": commands}

    def _opened(self, result: dict[str, Any]) -> None:
        found = result["session"]
        self.commands = result["commands"]
        self.ref, self.project = found["ref"], found["projectId"]
        self.talk = Conversation(self.client, self.ref)
        log = self.query_one("#log", VerticalScroll)
        log.remove_children()
        self.widgets.clear()
        self.live_answer = self.live_thinking = None
        for message in found.get("messages", []):
            self.talk.turns[int(message["id"])] = message
            self.draw(message)
        self.talk.since = self.talk.last_id
        if not found.get("messages"):
            log.mount(Static(f"[dim]A new session on {escape(found.get('projectName') or found['projectId'])}. "
                             "Ask about the code, its history or its rules. Type /help for keys and commands.[/]"))
        pending = next((m for m in reversed(found.get("messages", [])) if waiting_card(m)), None)
        self.busy = found.get("status") == "thinking" and pending is None
        self.show_card(pending)
        self.top(found)
        self._hint()
        log.scroll_end(animate=False)

    def top(self, found: dict[str, Any]) -> None:
        bits = [found.get("projectName") or found.get("projectId") or "", found.get("ref", "")]
        if found.get("title"):
            bits.append(found["title"][:40])
        if found.get("lane"):
            bits.append(f"{found['lane']}/{found.get('model') or ''}")
        used, window = found.get("contextTokens"), found.get("contextWindow")
        if used and window:
            bits.append(f"{round(used / 1000)}k / {round(window / 1000)}k tokens")
        elif used:
            bits.append(f"{round(used / 1000)}k tokens")
        self.query_one("#top", Static).update(escape("  ·  ".join(b for b in bits if b)))

    def _hint(self) -> None:
        parts = []
        if self.attached:
            parts.append("attached: " + " ".join("@" + a["name"] for a in self.attached))
        if not self.stream_up:
            parts.append("Live stream down — reconnecting; answers still arrive")
        if self.card is not None:
            parts.append("Waiting on you — choose below")
        elif self.busy:
            parts.append("Writing… Esc stops it")
        else:
            parts.append("Enter sends · @ attaches · / commands · Ctrl+Q quits")
        self.query_one("#hint", Static).update(escape("   ".join(parts)))

    # ── drawing turns ────────────────────────────────────────────
    def draw(self, message: dict[str, Any]) -> None:
        """A turn, drawn once — or redrawn in place when it changed (an answered permission card)."""
        key = int(message["id"])
        log = self.query_one("#log", VerticalScroll)
        old = self.widgets.get(key)
        made = self.render_turn(message)
        if made is None:
            return
        if old is not None:
            log.mount(made, after=old)
            old.remove()
        else:
            log.mount(made)
        self.widgets[key] = made

    def render_turn(self, message: dict[str, Any]) -> Any:
        role = message.get("role")
        if message.get("supersededBy"):
            return None                               # the line the model is sent is the current one
        if role == "you":
            text = str(message.get("text") or "")
            attached = [a for a in message.get("attachments") or [] if f"@{a.get('name')}" not in text]
            extra = ("  [dim]" + escape(" ".join("@" + str(a.get("name") or a.get("ref")) for a in attached)) + "[/]"
                     if attached else "")
            return Static(f"› {escape(str(message.get('text') or ''))}{extra}", classes="you")
        if role == "tool":
            if message.get("tool") == "permission":
                card = message.get("permission") or {}
                state = card.get("state")
                if state == "pending":
                    return Static(f"? The answer wants to use [b]{escape(str(card.get('tool')))}[/b] on "
                                  f"{escape(str(card.get('subject') or ''))}"
                                  + (f" — {escape(str(card.get('why')))}" if card.get("why") else ""), classes="card")
                who = card.get("decidedBy") or "the rules"
                scope = f" ({card.get('scope')})" if card.get("scope") else ""
                return Static(f"[dim]· {escape(str(card.get('tool')))}: {escape(str(state))}{escape(scope)} by "
                              f"{escape(str(who))}[/]", classes="tool")
            return Static(tool_line(message), classes="tool")
        if role == "note":
            return Static(f"! {escape(str(message.get('text') or ''))}", classes="note")
        if role == "summary":
            folded = message.get("folded") or {}
            body = Static(escape(str(message.get("text") or "")))
            return Collapsible(body, title=f"Summary of {folded.get('turns', 0)} earlier turns", collapsed=True)
        if role == "assistant":
            parts: list[Any] = []
            if message.get("reasoning"):
                thought = message.get("thought") or {}
                seconds = f" · {thought['ms'] / 1000:.1f} s" if thought.get("ms") else ""
                parts.append(Collapsible(Static(escape(str(message["reasoning"]))), title=f"Reasoning{seconds}",
                                         collapsed=True))
            parts.append(Markdown(str(message.get("text") or "")))
            meta = [str(message[k]) for k in ("lane", "model") if message.get(k)]
            if message.get("ms"):
                meta.append(f"{message['ms'] / 1000:.1f} s")
            if message.get("at"):
                meta.append(when(message["at"]))
            parts.append(Static(escape(" · ".join(meta)), classes="meta"))
            return VerticalGroup(*parts)
        return None

    def clear_live(self) -> None:
        for widget in (self.live_answer, self.live_thinking):
            if widget is not None:
                widget.remove()
        self.live_answer = self.live_thinking = None

    def draw_stream(self, update: Update) -> None:
        pending = update.pending
        if pending is None or pending.broken:
            return
        now = time.monotonic()
        if now - self.last_draw < 0.08:
            return                                    # a redraw per piece is more than any eye can read
        self.last_draw = now
        log = self.query_one("#log", VerticalScroll)
        if pending.answer:
            if self.live_thinking is not None:
                self.live_thinking.remove()
                self.live_thinking = None
            if self.live_answer is None:
                self.live_answer = Markdown(pending.answer)
                log.mount(self.live_answer)
            else:
                self.live_answer.update(pending.answer)
        else:
            text = f"thinking… {pending.ms / 1000:.0f} s" + (" · reasoning" if pending.reasoning else "")
            if self.live_thinking is None:
                self.live_thinking = Static(text, classes="thinking")
                log.mount(self.live_thinking)
            else:
                self.live_thinking.update(text)
        log.scroll_end(animate=False)

    # ── the live stream ──────────────────────────────────────────
    def take_event(self, event: Event) -> None:
        if self.talk is None:
            return
        update = self.talk.feed(event)
        if update is None:
            return
        self.quiet_since = time.monotonic()
        self.apply(update)

    def apply(self, update: Update) -> None:
        if update.kind == "stream":
            self.busy = True
            self.draw_stream(update)
            self._hint()
            return
        message = update.message
        if message is None or message.get("role") == "you" and int(message["id"]) in self.widgets:
            return
        if message.get("role") in ("assistant", "note") or waiting_card(message):
            self.clear_live()
        self.draw(message)
        if waiting_card(message):
            self.busy = False
            self.show_card(message)
        elif message.get("tool") == "permission" and self.card is not None and int(self.card["id"]) == int(message["id"]):
            self.show_card(None)
        if self.talk is not None and self.talk.answered() and self.talk.waiting() is None:
            self.busy = False
            self.background(self.client.session, self.ref, then=self.top)
        self._hint()
        self.query_one("#log", VerticalScroll).scroll_end(animate=False)

    def _catch_up_if_quiet(self) -> None:
        """While an answer is being written, read the session itself when the stream has been quiet for a
        while — at once every couple of seconds when the stream is down."""
        quiet = time.monotonic() - self.quiet_since
        if self.talk is None or not self.busy or quiet < (5 if self.stream_up else 1.5):
            return
        self.quiet_since = time.monotonic()

        def done(updates: list[Update]) -> None:
            for update in updates:
                self.apply(update)
            if self.talk is not None and self.talk.status == "idle" and self.card is None:
                self.busy = False
                self.clear_live()
                self._hint()

        self.background(self.talk.catch_up, then=done)

    # ── permission cards ─────────────────────────────────────────
    def show_card(self, card: dict[str, Any] | None) -> None:
        self.card = card
        bar = self.query_one("#card", Horizontal)
        bar.set_class(card is not None, "open")
        if card is not None:
            self.query_one("#once", Button).focus()
        else:
            self.query_one("#prompt", Input).focus()
        self._hint()

    @on(Button.Pressed)
    def decide(self, pressed: Button.Pressed) -> None:
        if self.card is None or self.ref is None or pressed.button.id not in ("once", "session", "refuse"):
            return
        decision = {"once": "once", "session": "session", "refuse": "refuse"}[pressed.button.id or "refuse"]
        card = self.card
        self.show_card(None)
        self.busy = True
        if self.talk is not None:
            self.talk.since = int(card["id"])
        self.background(self.client.permit, self.ref, int(card["id"]), decision, then=lambda _: self._hint())

    # ── the composer: @ and / ────────────────────────────────────
    @on(Input.Changed, "#prompt")
    def typed(self, changed: Input.Changed) -> None:
        text = changed.value
        slash = SLASH.match(text)
        mention = MENTION.search(text)
        if slash is not None:
            self.offer_slash(slash.group(1))
        elif mention is not None and self.project:
            q = mention.group(1)
            self.background(self.client.mentions, self.project, q, then=lambda found, q=q: self.offer_mentions(found, q))
        elif self.mode in ("mention", "slash"):
            self.close_suggestions()

    def offer_slash(self, typed: str) -> None:
        found = [{"name": k, "detail": v, "local": True} for k, v in LOCAL.items() if k.startswith(typed)]
        found += [{"name": c["name"], "detail": c.get("description") or c.get("scope") or "", "local": False}
                  for c in self.commands if str(c.get("name", "")).startswith(typed) and c["name"] not in LOCAL]
        self.open_suggestions("slash", found, lambda s: f"/{escape(s['name'])}  [dim]{escape(str(s['detail'])[:70])}[/]")

    def offer_mentions(self, found: list[dict[str, Any]], typed: str) -> None:
        current = MENTION.search(self.query_one("#prompt", Input).value)
        if current is None or current.group(1) != typed:
            return                                    # the person typed on; a later answer will come
        self.open_suggestions("mention", found,
                              lambda s: f"[b]{escape(s['kind'])}[/b] {escape(s['name'])}  [dim]{escape(str(s.get('detail') or ''))[:60]}[/]")

    def open_suggestions(self, mode: str, found: list[dict[str, Any]], label: Callable[[dict[str, Any]], str]) -> None:
        box = self.query_one("#suggest", OptionList)
        self.mode, self.suggestions = mode, found
        box.clear_options()
        if not found:
            box.set_class(False, "open")
            return
        box.add_options([Option(label(s)) for s in found[:30]])
        box.highlighted = 0
        box.set_class(True, "open")

    def close_suggestions(self) -> None:
        self.mode, self.suggestions = "", []
        self.query_one("#suggest", OptionList).set_class(False, "open")

    def action_suggest(self, step: int) -> None:
        box = self.query_one("#suggest", OptionList)
        if not self.suggestions:
            return
        at = (box.highlighted or 0) + step
        box.highlighted = max(0, min(at, len(self.suggestions) - 1))

    def action_accept(self) -> None:
        box = self.query_one("#suggest", OptionList)
        if self.suggestions and box.highlighted is not None:
            self.take_suggestion(box.highlighted)

    @on(OptionList.OptionSelected, "#suggest")
    def picked(self, chosen: OptionList.OptionSelected) -> None:
        self.take_suggestion(chosen.option_index)

    def take_suggestion(self, index: int) -> None:
        if not 0 <= index < len(self.suggestions):
            return
        chosen, mode = self.suggestions[index], self.mode
        prompt = self.query_one("#prompt", Input)
        self.close_suggestions()
        if mode == "slash":
            prompt.value = f"/{chosen['name']} "
        elif mode == "mention":
            prompt.value = MENTION.sub(lambda m: m.group(0)[: m.group(0).index("@")] + f"@{chosen['name']} ",
                                       prompt.value)
            if not any(a["ref"] == chosen["ref"] and a["kind"] == chosen["kind"] for a in self.attached):
                self.attached.append({"kind": chosen["kind"], "ref": chosen["ref"], "name": chosen["name"]})
        elif mode == "session":
            self.clear_live()
            self.show_card(None)
            self.background(self._open, chosen["ref"], None, then=self._opened)
        prompt.cursor_position = len(prompt.value)
        prompt.focus()
        self._hint()

    # ── sending ──────────────────────────────────────────────────
    @on(Input.Submitted, "#prompt")
    def submitted(self, sent: Input.Submitted) -> None:
        if self.suggestions and self.mode in ("mention", "slash", "session"):
            self.action_accept()
            return
        text = sent.value.strip()
        if not text or self.ref is None:
            return
        command = re.match(r"^/(\w+)\s*$", text)
        if command and command.group(1) in LOCAL:
            sent.input.value = ""
            self.local(command.group(1))
            return
        if self.busy:
            self.notify("An answer is still being written. Esc stops it.", severity="warning")
            return
        if self.card is not None:
            self.notify("The answer waits on your permission first.", severity="warning")
            return
        attached = list(self.attached)
        sent.input.value = ""
        self.attached = []
        self.busy = True
        self._hint()

        def asked(result: dict[str, Any]) -> None:
            if self.talk is not None:
                self.talk.since = int(result["message"]["id"])
                self.apply(Update("message", message=result["message"]))
                self.talk.turns[int(result["message"]["id"])] = result["message"]

        self.background(self.client.ask, self.ref, text, attached, then=asked)

    def local(self, name: str) -> None:
        log = self.query_one("#log", VerticalScroll)
        ref = self.ref
        if name == "help":
            log.mount(Markdown(HELP))
            log.scroll_end(animate=False)
        elif name == "quit":
            self.exit()
        elif name == "new":
            self.action_new()
        elif name == "stop":
            self.action_stop()
        elif name == "sessions":
            def listed(found: list[dict[str, Any]]) -> None:
                self.open_suggestions("session", found, lambda s: (
                    f"{escape(s['ref'])}  {escape((s.get('title') or 'untitled')[:50])}  "
                    f"[dim]{s.get('turns', 0)} turns · {when(s.get('lastAt'))}"
                    + (" · waiting on you" if s.get("waitingOn") else "") + "[/]"))
                self.query_one("#suggest", OptionList).focus()
            self.background(self.client.sessions, self.project, then=listed)
        elif name == "compact" and ref:
            self.notify("Folding older turns…")
            self.background(self.client.compact, ref,
                            then=lambda result: (self.apply(Update("message", message=result["summary"])),
                                                 self.top(result["session"])))
        elif name == "plan" and ref:
            self.notify("Compiling a plan from the last question…")

            def planned(plan: dict[str, Any]) -> None:
                questions = plan.get("openQuestions") or []
                log.mount(Static(f"[green]✓[/] {escape(plan['ref'])} written — {len(plan.get('steps') or [])} steps, "
                                 f"risk {escape(str(plan.get('risk')))}"
                                 + (f", {len(questions)} open questions" if questions else "")
                                 + f". Dispatch it with `nc dispatch {escape(plan['ref'])}`.", classes="tool"))
                log.scroll_end(animate=False)
            self.background(self.client.to_plan, ref, then=planned)
        elif name == "export" and ref:
            def exported(doc: dict[str, Any]) -> None:
                target = Path.cwd() / Path(str(doc.get("filename") or f"{ref}.md")).name
                target.write_text(str(doc.get("text") or ""), encoding="utf-8")
                self.notify(f"Written to {target}")
            self.background(self.client.export, ref, "md", then=exported)

    def action_stop(self) -> None:
        if self.mode:
            self.close_suggestions()
            self.query_one("#prompt", Input).focus()
            return
        if self.busy and self.ref:
            self.background(self.client.stop, self.ref, then=lambda _: self.notify("Stopping the answer…"))

    def action_new(self) -> None:
        if not self.project:
            return
        self.clear_live()
        self.show_card(None)
        self.background(self._open, None, self.project, then=self._opened)
