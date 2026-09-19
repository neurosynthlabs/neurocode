"""A session as a terminal follows it: the turns that have arrived, the answer being written, and whether
the answer is over.

Shared by `nc ask` and `nc chat`, so both read the stream the same way the web app does: turns arrive as
`chat` events and are kept by id (a permission card that was answered arrives again, changed); the words
of an answer being written arrive as `stream` pieces appended by position, and a missed piece stops the
rest rather than showing words out of order — the finished turn replaces them all. Whatever the stream
missed, `catch_up` reads from the session itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .client import Client, Event

#: What each tool turn did, in words — the same labels the web app's Sessions screen uses.
TOOL_LABEL = {
    "search_code": "searched the code", "read_file": "read a file", "list_files": "listed files",
    "find": "searched the index", "impact": "traced the blast radius", "search_memory": "searched memory",
    "project_summary": "looked at the project", "web_fetch": "read a web page", "web_search": "searched the web",
    "mcp": "called an MCP tool", "context": "you attached", "command": "ran a command",
    "grounding": "grounded the question", "load_skill": "loaded a skill", "permission": "asked your permission",
}


def ends(message: dict[str, Any]) -> bool:
    """A turn that ends the wait for an answer: the answer, a note saying why there is none, or a card
    waiting on the person."""
    role = message.get("role")
    return (role == "assistant" or (role == "note" and not message.get("detail"))
            or (message.get("permission") or {}).get("state") == "pending")


def waiting_card(message: dict[str, Any]) -> bool:
    return message.get("tool") == "permission" and (message.get("permission") or {}).get("state") == "pending"


@dataclass
class Pending:
    """What has arrived of the answer being written, for one step."""

    step: int = -1
    answer: str = ""
    reasoning: str = ""
    ms: int = 0
    broken: bool = False

    def add(self, piece: dict[str, Any]) -> None:
        if piece.get("restart") or piece.get("step") != self.step:
            self.step, self.answer, self.reasoning, self.broken = int(piece.get("step") or 0), "", "", False
            if piece.get("restart"):
                return
        if self.broken:
            return
        if "answer" in piece:
            if piece.get("answerAt") == len(self.answer):
                self.answer += str(piece["answer"])
            else:
                self.broken = True
        if "reasoning" in piece:
            if piece.get("reasoningAt") == len(self.reasoning):
                self.reasoning += str(piece["reasoning"])
            else:
                self.broken = True
        self.ms = int(piece.get("ms") or self.ms)


@dataclass
class Update:
    """What changed: a turn arrived or changed (`message`), or the answer being written grew (`stream`)."""

    kind: str
    message: dict[str, Any] | None = None
    pending: Pending | None = None


@dataclass
class Conversation:
    client: Client
    ref: str
    #: Every turn held, by id — updated in place when one changes (an answered permission card).
    turns: dict[int, dict[str, Any]] = field(default_factory=dict)
    #: The question this answer is for: turns at or before it do not end the wait.
    since: int = 0
    pending: Pending = field(default_factory=Pending)
    #: The session's own word on it — idle | thinking — as last read.
    status: str | None = None

    @property
    def last_id(self) -> int:
        return max(self.turns, default=0)

    def load(self) -> dict[str, Any]:
        """The session and every turn it holds, read afresh."""
        found = self.client.session(self.ref)
        for message in found.get("messages", []):
            self.turns[int(message["id"])] = message
        return found

    def _take(self, message: dict[str, Any]) -> Update | None:
        key = int(message["id"])
        if self.turns.get(key) == message:
            return None
        self.turns[key] = message
        if message.get("role") in ("assistant", "note"):
            self.pending = Pending()
        return Update("message", message=message)

    def feed(self, event: Event) -> Update | None:
        """One event from the stream, if it is about this session."""
        if event.kind != "chat" or not isinstance(event.data, dict) or event.data.get("sessionRef") != self.ref:
            return None
        if "stream" in event.data:
            self.pending.add(event.data["stream"])
            return Update("stream", pending=self.pending)
        message = {k: v for k, v in event.data.items() if k != "sessionRef"}
        return self._take(message) if "id" in message else None

    def catch_up(self) -> list[Update]:
        """Whatever the stream missed — and the session's status, read from the session itself."""
        found = self.client.session(self.ref, after=max(self.since - 1, 0))
        updates = [u for m in found.get("messages", []) if (u := self._take(m)) is not None]
        self.status = found.get("status")
        return updates

    def answered(self) -> bool:
        """Over: a turn after the question ends the wait."""
        return any(ends(m) for key, m in self.turns.items() if key > self.since)

    def waiting(self) -> dict[str, Any] | None:
        """The permission card the answer waits on, if it waits on one."""
        cards = [m for key, m in sorted(self.turns.items()) if key > self.since and waiting_card(m)]
        return cards[-1] if cards else None

    def after(self) -> list[dict[str, Any]]:
        """The turns after the question, in order."""
        return [m for key, m in sorted(self.turns.items()) if key > self.since]
