"""Tool rules: what the runtime may do without asking, what it must ask about, and what it never does.

A rule names a tool — `edit` (a path an agent writes), `command` (a command line run in a worktree),
`read` (a path a session reads), `web_fetch` (a URL), `web_search` (a query) or `mcp` (`server/tool`) —
a glob over what the tool acts on, and one of three answers: allow, ask or deny. A rule with no project
holds across the workspace; a project's own rule holds only there.

`decide` is the one place those answers are weighed, and the order is fixed so a person can predict it:

1. **The narrower scope wins.** A project's rule beats the workspace's, whatever either says — the
   person who wrote a rule for this project meant this project.
2. **Then the longer pattern.** Measured in the characters that are not wildcards, so `src/api/*` is
   more specific than `src/*`, and `**********` is no more specific than `*`.
3. **Then deny beats ask beats allow.** Two rules equally specific and in disagreement are a question
   nobody settled; the cautious answer is the one that cannot do harm.

No rule is an answer too: **ask**. The caller then does what it did before tool rules existed — pauses
for a person, or treats the person who pressed the button as the one who was asked. Nothing is ever
allowed that asked before unless a person wrote a rule saying so.

Globs are `fnmatch`'s: `*` matches anything, including `/`, `?` one character, `[abc]` a set. Matching is
case-sensitive, because paths and URLs are.
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ToolRule
from ..repositories import ActivityRepository, AuditRepository, NotFound, ProjectRepository
from ..repositories.platform import ToolRuleRepository
from ..schemas.platform import tool_rule_json
from .errors import Refused
from .identity import Person

TOOLS = ("edit", "command", "read", "web_fetch", "web_search", "mcp")
ACTIONS = ("allow", "ask", "deny")
#: What each tool acts on, in the words a screen and a refusal use.
SUBJECTS = {"edit": "path", "command": "command line", "read": "path", "web_fetch": "URL",
            "web_search": "query", "mcp": "server/tool"}
MAX_PATTERN = 300
MAX_NOTE = 300
MAX_SUBJECT = 2000
#: Writing a rule decides what runs unasked, so it has a permission of its own (Owner and Admin hold it).
MANAGE = "rules:manage"
WILDCARDS = frozenset("*?[]")
#: On a tie in scope and length, the cautious answer wins.
CAUTION = {"deny": 2, "ask": 1, "allow": 0}

Action = Literal["allow", "ask", "deny"]


@dataclass(frozen=True, slots=True)
class Decision:
    """What would happen, and why, in words fit for the screen and for a refusal."""

    action: Action
    rule_id: int | None
    why: str
    project_id: str | None = None

    def json(self) -> dict[str, Any]:
        return {"action": self.action, "ruleId": self.rule_id, "why": self.why}


def _literal(pattern: str) -> int:
    return sum(1 for ch in pattern if ch not in WILDCARDS)


def normalise(tool: str, subject: str) -> str:
    """The subject as rules see it. A path is matched as written in the checkout, never with `./`."""
    text = subject.strip()
    if tool in ("edit", "read"):
        while text.startswith("./"):
            text = text[2:]
    return text


def weigh(rules: list[ToolRule], tool: str, subject: str) -> Decision:
    """Pure: which of these rules decides, by scope, then length, then caution. Exposed for tests."""
    matching = [r for r in rules if r.tool == tool and fnmatch.fnmatchcase(subject, r.pattern)]
    if not matching:
        return Decision("ask", None, f"No tool rule covers this {SUBJECTS.get(tool, 'subject')}, so it asks.")
    best = max(matching, key=lambda r: (r.project_id is not None, _literal(r.pattern), len(r.pattern),
                                        CAUTION[r.action]))
    where = f"project {best.project_id}" if best.project_id else "the workspace"
    verb = {"allow": "allows", "ask": "asks about", "deny": "denies"}[best.action]
    return Decision(best.action, best.id,  # type: ignore[arg-type]  # the table's check keeps it one of three
                    f"Rule #{best.id} for {where} {verb} {tool} matching `{best.pattern}`."
                    + (f" ({best.note})" if best.note else ""), best.project_id)


async def decide(session: AsyncSession, tool: str, subject: str, project_id: str | None = None) -> Decision:
    """The answer the rules give for doing `tool` to `subject` here: allow, ask or deny, and which rule."""
    if tool not in TOOLS:
        raise Refused(f"There is no tool called {tool}. Tools are: {', '.join(TOOLS)}.", status=422)
    rules = await ToolRuleRepository(session).applicable(tool, project_id)
    return weigh(rules, tool, normalise(tool, subject))


class ToolRuleService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.rules = ToolRuleRepository(session)
        self.activity = ActivityRepository(session)
        self.audit = AuditRepository(session)

    # ── reading ──────────────────────────────────────────────────
    async def listed(self, *, project: str | None, tool: str | None, limit: int | None,
                     offset: int) -> list[dict[str, Any]]:
        if tool and tool not in TOOLS:
            raise Refused(f"There is no tool called {tool}.", status=422)
        rows = await self.rules.listed(project=project, tool=tool, limit=limit, offset=offset)
        return [tool_rule_json(rule, project_name=name, author=author) for rule, name, author in rows]

    async def one(self, rule_id: int) -> dict[str, Any]:
        found = await self.rules.named(rule_id)
        if found is None:
            raise NotFound(f"tool rule {rule_id}")
        rule, name, author = found
        return tool_rule_json(rule, project_name=name, author=author)

    async def test(self, tool: str, subject: str, project_id: str | None) -> dict[str, Any]:
        """What would happen, without doing anything: the "try it" box on the Permissions screen."""
        text = subject.strip()
        if not text or len(text) > MAX_SUBJECT:
            raise Refused(f"Give a {SUBJECTS.get(tool, 'subject')} of 1 to {MAX_SUBJECT} characters.", status=422)
        await self._known(project_id)
        decision = await decide(self.session, tool, text, project_id)
        rule = await self.one(decision.rule_id) if decision.rule_id is not None else None
        return {**decision.json(), "tool": tool, "subject": normalise(tool, text), "projectId": project_id,
                "rule": rule}

    # ── writing ──────────────────────────────────────────────────
    async def create(self, *, tool: str, pattern: str, action: str, note: str, project_id: str | None,
                     who: Person, ip: str = "") -> dict[str, Any]:
        who.must(MANAGE, "write a tool rule")
        tool, pattern, action, note = self._checked(tool, pattern, action, note)
        await self._known(project_id)
        if await self.rules.same(project_id, tool, pattern) is not None:
            raise Refused(f"There is already a {tool} rule for `{pattern}` "
                          f"{'in this project' if project_id else 'for the workspace'}. Edit that one instead.")
        rule = await self.rules.add(ToolRule(project_id=project_id, tool=tool, pattern=pattern, action=action,
                                             note=note, created_by=who.id))
        await self._record(who, "Tool rule added", "tool_rule.create", rule, ip,
                           {"tool": tool, "pattern": pattern, "action": action, "projectId": project_id})
        return await self.one(rule.id)

    async def update(self, rule_id: int, *, pattern: str | None, action: str | None, note: str | None,
                     who: Person, ip: str = "") -> dict[str, Any]:
        """Change what a rule matches, what it answers, or its note. Its tool and scope stay: a rule that
        moved to another tool or project is a different rule, and is deleted and written again."""
        who.must(MANAGE, "change a tool rule")
        rule = await self.rules.get(rule_id)
        if rule is None:
            raise NotFound(f"tool rule {rule_id}")
        _, new_pattern, new_action, new_note = self._checked(
            rule.tool, rule.pattern if pattern is None else pattern, rule.action if action is None else action,
            rule.note if note is None else note)
        if new_pattern != rule.pattern and await self.rules.same(rule.project_id, rule.tool, new_pattern):
            raise Refused(f"There is already a {rule.tool} rule for `{new_pattern}` in the same scope.")
        before = {"pattern": rule.pattern, "action": rule.action, "note": rule.note}
        rule.pattern, rule.action, rule.note = new_pattern, new_action, new_note
        after = {"pattern": rule.pattern, "action": rule.action, "note": rule.note}
        changed = {k: {"from": before[k], "to": after[k]} for k in before if before[k] != after[k]}
        if not changed:
            return await self.one(rule.id)
        await self.session.flush()
        await self._record(who, "Tool rule changed", "tool_rule.update", rule, ip, changed)
        return await self.one(rule.id)

    async def delete(self, rule_id: int, who: Person, ip: str = "") -> dict[str, Any]:
        who.must(MANAGE, "delete a tool rule")
        rule = await self.rules.get(rule_id)
        if rule is None:
            raise NotFound(f"tool rule {rule_id}")
        await self._record(who, "Tool rule removed", "tool_rule.delete", rule, ip,
                           {"tool": rule.tool, "pattern": rule.pattern, "action": rule.action,
                            "projectId": rule.project_id})
        await self.rules.remove(rule)
        return {"ok": True, "id": rule_id}

    # ── the parts every write shares ─────────────────────────────
    @staticmethod
    def _checked(tool: str, pattern: str, action: str, note: str) -> tuple[str, str, str, str]:
        if tool not in TOOLS:
            raise Refused(f"There is no tool called {tool}. Tools are: {', '.join(TOOLS)}.", status=422)
        if action not in ACTIONS:
            raise Refused("A rule allows, asks or denies.", status=422)
        text = pattern.strip()
        if not text or len(text) > MAX_PATTERN or "\n" in text:
            raise Refused(f"A pattern is one line of 1 to {MAX_PATTERN} characters.", status=422)
        if len(note) > MAX_NOTE:
            raise Refused(f"A note is at most {MAX_NOTE} characters.", status=422)
        return tool, normalise(tool, text), action, note.strip()

    async def _known(self, project_id: str | None) -> None:
        if project_id and await ProjectRepository(self.session).get(project_id) is None:
            raise NotFound(f"project {project_id}")

    async def _record(self, who: Person, what: str, audited: str, rule: ToolRule, ip: str,
                      detail: dict[str, Any]) -> None:
        """Both logs: the activity feed for the team, and the audit log — a rule decides what runs
        without a person, which is exactly the kind of change the audit log is for."""
        await self.activity.record(actor=who.name, actor_kind="human", action=what,
                                   detail=f"#{rule.id} · {rule.action} {rule.tool} `{rule.pattern}`"
                                          f"{f' · {rule.project_id}' if rule.project_id else ' · workspace'}",
                                   project_id=rule.project_id, level="warn" if rule.action == "allow" else "info")
        await self.audit.record(action=audited, user_id=who.id, target=f"tool rule #{rule.id}", detail=detail, ip=ip)
