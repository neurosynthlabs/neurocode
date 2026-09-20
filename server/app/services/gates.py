"""The gates: approvals, final decisions, and the small settings a person changes on a screen.

One rule runs through all of it — **a decision is final**. An approval that was already answered
cannot be answered again, and a decision that was recorded cannot be quietly re-recorded. Those were
`if` statements in two different route handlers before; here each is one method, said once — and the
approval's is the database's lock on the row rather than an `if` at all, because two Approve posts
arriving together both read `pending` and both got past an `if`.

A run's gates are not all yes-or-no. When a tool rule asks about a command or about files an agent
wants to write, the answer is "Allow once", "Allow for this run" (kept on the run as a grant), "Always
allow in this project" (written as a project tool rule, audited) or "Refuse". When an agent stops to ask
a question, the answer is words: kept on the step, and remembered as a decision in the project's memory
with the run as its evidence. What the run then does with the answer is `services.runs.resume`'s.
"""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..data.base import utcnow
from ..models import Approval, Decision, Pref
from ..repositories import ActivityRepository, NotFound, ProjectRepository, RunRepository
from ..repositories.platform import ToolRuleRepository
from ..repositories.runtime import ResultsRepository
from ..schemas.work import GATE_OPTIONS, gate_kind, test_rule_json, when
from .errors import Denied, Refused
from .identity import Person
from .knowledge import MemoryService, NewFact
from .testing import _commands
from .tool_rules import MANAGE, ToolRuleService

#: The longest answer to an agent's question: a decision, not a document.
MAX_ANSWER = 4_000
#: How a scope reads in the activity log.
SCOPE_WORDS = {"once": "allowed once", "run": "allowed for this run", "project": "always allowed in this project"}


def literal_pattern(subject: str) -> str:
    """A tool rule's glob that matches exactly this subject and nothing else — `app/[id]/page.tsx` would be
    a character class to fnmatch, so each wildcard character is put in brackets of its own."""
    return "".join(f"[{ch}]" if ch in "*?[" else ch for ch in subject)


async def _known_project(session: AsyncSession, project_id: str | None) -> None:
    """A decision or a setting is about the workspace unless it names a project, and a project it
    names has to exist: the log line it writes points at it, and a dangling id would be a 500."""
    if project_id and await ProjectRepository(session).get(project_id) is None:
        raise NotFound(f"project {project_id}")


class ApprovalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.activity = ActivityRepository(session)

    async def _claim(self, ref: str) -> Approval:
        """The gate, with its row held for the rest of this transaction.

        "A decision is final" was a read, a check and a write in three steps, which under READ
        COMMITTED is not a guard at all: two Approve posts — a double click, or a browser retrying on
        a flaky connection — both read `pending`, both passed the check, and both handed off a resume
        of the same run. The run then ran the gated step twice: the same test command twice, or the
        same edit committed into the worktree twice. It is timing-dependent, so it comes back as "the
        run did the step twice and I don't know why" and never reproduces.

        Locking the row makes the transition itself the guard. The second caller waits here until the
        first has committed and then reads what the first wrote — `approved`, not `pending` — so it
        refuses in the same words as any late decision. `populate_existing` because the row may
        already be in this session from an earlier read, and a stale copy of it would defeat the
        whole point.
        """
        approval = (await self.session.execute(
            select(Approval).where(Approval.ref == ref).with_for_update()
            .execution_options(populate_existing=True))).scalar_one_or_none()
        if approval is None:
            raise NotFound(f"approval {ref}")
        return approval

    async def decide(self, ref: str, decision: str, *, by_id: str, by_name: str, scope: str | None = None,
                     answer: str | None = None, who: Person | None = None, ip: str = "") -> Approval:
        """Approve or deny, once. Returns the approval; the caller resumes whatever was waiting on it.

        `scope` answers a tool rule's ask — once, for the run, or always in the project (which needs
        rules:manage); `answer` answers an agent's question. Each is refused on a gate that did not ask
        for it, and checked before anything is decided, so a refused answer leaves the gate pending."""
        if decision not in ("approve", "deny"):
            raise Refused("A gate is approved or denied.", status=422)
        approval = await self._claim(ref)
        if approval.status != "pending":
            raise Refused(f"{ref} was already {approval.status} — a decision is final.")
        kind = gate_kind(approval.tool)
        options = GATE_OPTIONS[kind]
        words = (answer or "").strip()
        if scope is not None and (decision != "approve" or scope not in options):
            raise Refused(f"{ref} is not answered with a scope." if scope not in options
                          else "A refusal has no scope: it refuses this once.", status=422)
        if answer is not None and "answer" not in options:
            raise Refused(f"{ref} is not a question; it is approved or denied.", status=422)
        if kind == "question" and decision == "approve" and not words:
            raise Refused("Write the answer the agent asked for, or decline to answer.", status=422)
        if len(words) > MAX_ANSWER:
            raise Refused(f"An answer is at most {MAX_ANSWER:,} characters.", status=422)
        if scope == "project" and (who is None or not who.can(MANAGE)):
            raise Denied(MANAGE, "always allow this in the project")

        run = await RunRepository(self.session).by_ref(approval.run_ref) if approval.run_ref else None
        approval.status = "approved" if decision == "approve" else "denied"
        approval.decided_at, approval.decided_by = utcnow(), by_id
        await self.session.flush()
        said = ""
        if run is not None and decision == "approve" and kind in ("command", "edit"):
            said = await self._grant(approval, run, scope or "once", by_name, who, ip)
        elif run is not None and decision == "approve" and kind == "question":
            said = await self._answer(approval, run, words, by_name)
        await self.activity.record(actor=by_name, actor_kind="human",
                                   action="Approved" if decision == "approve" else "Denied",
                                   detail=f"{ref} · {approval.title}{f' · {said}' if said else ''}"[:500],
                                   project_id=approval.project_id,
                                   level="ok" if decision == "approve" else "warn")
        return approval

    async def _grant(self, approval: Approval, run: Any, scope: str, by: str, who: Person | None,
                     ip: str) -> str:
        """What "Allow" means for a tool rule's ask: a grant on the run for exactly what was asked about —
        for this step only, or for the rest of the run — and, for "always", a project rule too."""
        asked = ((run.review or {}).get("asks") or {}).get(str(approval.step)) or {}
        tool, subjects = asked.get("tool", ""), [str(x) for x in asked.get("subjects") or []]
        if not tool or not subjects:
            return SCOPE_WORDS[scope]
        at = utcnow().isoformat()
        run.grants = [*(run.grants or []), *({"tool": tool, "subject": subject, "scope": "once" if scope == "once"
                                              else "run", "step": approval.step, "by": by, "at": at}
                                             for subject in subjects)]
        if scope == "project" and who is not None and run.project_id:
            rules, service = ToolRuleRepository(self.session), ToolRuleService(self.session)
            for subject in subjects[:20]:
                pattern = literal_pattern(subject)
                same = await rules.same(run.project_id, tool, pattern)
                note = f"Allowed from {approval.ref} on {run.ref}."
                if same is None:
                    await service.create(tool=tool, pattern=pattern, action="allow", note=note,
                                         project_id=run.project_id, who=who, ip=ip)
                elif same.action != "allow":
                    await service.update(same.id, pattern=None, action="allow", note=note, who=who, ip=ip)
        await self.session.flush()
        return f"{SCOPE_WORDS[scope]}: {', '.join(subjects[:3])}{' …' if len(subjects) > 3 else ''}"

    async def _answer(self, approval: Approval, run: Any, words: str, by: str) -> str:
        """An agent's question answered: on the step, and in the project's memory as a decision whose
        evidence is the run — so the next plan and the next agent start from it."""
        step = next((x for x in run.steps if x.n == approval.step), None)
        if step is None:
            return "answered"
        step.answer = words
        question = step.question or approval.payload or approval.title
        await MemoryService(self.session).add(
            [NewFact(title=question.strip()[:200], body=words, category="decisions", confidence="HIGH",
                     reason=f"{by} answered it when {approval.agent or 'an agent'} asked during {run.ref}.",
                     evidence=[f"{run.ref} · step {step.n} · {approval.ref}"])],
            project_id=run.project_id, by=by, source=f"{run.ref} · agent question")
        return "answered"


class RuleService:
    """A project's standing answer to its first test run — one of the rules the runtime really applies.

    It is kept as a setting the test step reads before it runs anything, so listing those settings is
    listing the rule itself, not a description of it that could drift. Tool rules (`services.tool_rules`)
    sit in front of it: a rule a person wrote about a command decides first, and this answer decides only
    when no rule covers the command.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.results = ResultsRepository(session)

    async def rules(self, *, limit: int | None = None, offset: int = 0,
                    hidden: frozenset[str] = frozenset()) -> list[dict[str, Any]]:
        answers = await self.results.standing_answers(limit=limit, offset=offset, hidden=hidden)
        projects = [project for project, _ in answers]
        # Only a project whose code is on this machine has a command to find; asking for one with no
        # source would look in whatever directory the server happens to run from.
        commands = await asyncio.to_thread(_commands, [p for p in projects if p.source_kind])
        deciders = await self.results.gate_deciders([p.id for p in projects])
        rows = []
        for project, setting in answers:
            answer = "allowed" if setting.value == "allowed" else "refused"
            decided = deciders.get((project.id, "approved" if answer == "allowed" else "denied"))
            row = test_rule_json(project, setting, answer=answer, command=commands.get(project.id),
                                 decided_by=decided[0] if decided else None)
            if decided:
                # Who and when from the same approval. The setting's time is only the fallback, for an
                # answer no person's decision wrote.
                row["decidedAt"] = when(decided[1])
            rows.append(row)
        return rows


class DecisionService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.activity = ActivityRepository(session)

    async def record(self, key: str, *, verdict: str, action: str, detail: str = "",
                     project_id: str | None = None, level: str = "ok", by_id: str | None = None,
                     by_name: str = "") -> Decision:
        if await self.session.get(Decision, key) is not None:
            raise Refused(f"{key} was already decided — a decision is final.")
        await _known_project(self.session, project_id)
        made = Decision(id=key, subject=action, verdict=verdict, note=detail, project_id=project_id,
                        by_user_id=by_id)
        self.session.add(made)
        await self.session.flush()
        await self.activity.record(actor=by_name or "System", actor_kind="human", action=action,
                                   detail=detail, project_id=project_id, level=level)
        return made


class PrefService:
    """A screen setting. Written every time; only a deliberate change is worth a line in the log."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.activity = ActivityRepository(session)

    async def set(self, key: str, value: Any, *, detail: str = "", project_id: str | None = None,
                  by: str = "") -> Pref:
        await _known_project(self.session, project_id)
        pref = await self.session.get(Pref, key)
        if pref is None:
            pref = Pref(id=key, value=value)
            self.session.add(pref)
        else:
            pref.value = value
        await self.session.flush()
        if detail:
            await self.activity.record(actor=by or "System", actor_kind="human",
                                       action="Setting changed", detail=detail, project_id=project_id)
        return pref
