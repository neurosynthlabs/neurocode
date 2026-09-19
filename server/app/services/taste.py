"""Taste: how a person or a team likes the work done, learnt from what they did rather than what they said.

Four kinds of moment say it. A signature accepted or refused, read beside what the review found. A run
sent back with notes. A plan's steps edited, or revised from comments, before it was dispatched. And the
person's own commits on a run's branch after the agent's last one — the change they made to what an agent
wrote before it was merged. Each is a **signal**: captured where it happens, with no model, and kept.

"Learn from my decisions" hands the signals nobody has read yet to a model through the gateway, and it
proposes **rules** — one sentence each — citing the signals behind them. A model's words are not a
figure: a rule's support and its contradictions are the signals it cites that exist and were handed to
it, counted here, and a rule that cites none is dropped. Nothing a model proposes is used until a person
adopts it. Adopted rules are handed to the compiler, the agents and the reviewer as a block after the
project's own instruction files (`applied`), and the plan or run records which ones it was handed.

The capture functions (`on_rework`, `on_verdict`, `on_merged`) are for the runtime to call at the moment
each happens. `harvest` reads the same moments back out of what is already recorded — decided
signatures, runs sent back, merged branches — keyed exactly as the capture functions key them, so a
signal is never counted twice whichever of the two saw it first, and nothing is lost if a call site is
missing.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..ai.gateway import Gateway, extract_json
from ..data.base import utcnow
from ..models import Approval, Run, RunLog, TasteRule, TasteSignal, User
from ..repositories import ActivityRepository, NotFound
from ..repositories.base import bounded
from .errors import Refused, needs_a_model

log = logging.getLogger(__name__)

KINDS = ("accept", "refuse", "rework_note", "edit_delta", "plan_edit")
STATUSES = ("proposed", "active", "retired")
#: Signals one distillation reads, newest first. The rest wait for the next one.
READ_AT_ONCE = 60
#: What one signal may put in the prompt.
SIGNAL_CHARS = 700
#: Rules one distillation may propose, and the words one rule may have.
MAX_PROPOSED = 8
MAX_RULE = 300
MIN_RULE = 8
#: Rules handed to a model with the project's instructions; more than this is a style guide, not taste.
MAX_APPLIED = 30
#: Existing rules shown to a distillation so it can support or contradict them rather than repeat them.
MAX_EXISTING = 40
#: Lists here stop at this many, whatever is asked.
CEILING = 200
#: Decided signatures, sent-back runs and merged runs a harvest looks back over.
HARVEST_WINDOW = 200
#: A person's commit on a run's branch, as much of its patch as a signal keeps.
PATCH_CHARS = 4_000
#: The identity agents commit as (`agent/git.py` AUTHOR): every other author on a run's branch is a person.
AGENT_EMAIL = "neurocode@localhost"
#: A run sent back by the completion check is the runtime trying again, not a person's taste.
MACHINE_REWORKERS = ("Completion check",)

REWORK_LINE = re.compile(r"^rework of (?P<ref>\S+), sent back by (?P<by>.+?): (?P<notes>.*)$", re.S)


def rule_ref(rule_id: int) -> str:
    return f"TASTE-{rule_id}"


def _clip(text: Any, limit: int) -> str:
    said = " ".join(str(text or "").split())
    return said if len(said) <= limit else said[: limit - 1].rstrip() + "…"


def _norm(text: str) -> str:
    """Two rules that differ only in case, spacing or a full stop are the same rule."""
    return " ".join(text.casefold().split()).rstrip(".")


def summary(signal: TasteSignal) -> str:
    """One line a person — and a model — can read a signal by. Built from the payload, so it says only what
    was recorded."""
    p = signal.payload or {}
    if signal.kind in ("accept", "refuse"):
        verb = "Accepted" if signal.kind == "accept" else "Refused"
        found = "; ".join(f"{f.get('severity', 'LOW')} {f.get('note', '')}".strip() for f in p.get("findings", [])[:4])
        return _clip(f"{verb} {p.get('run', 'a run')}: {p.get('requirement', '')}"
                     + (f" · the review found: {found}" if found else " · the review found nothing")
                     + (f" · tests {p['tests']}" if p.get("tests") else ""), SIGNAL_CHARS)
    if signal.kind == "rework_note":
        return _clip(f"Sent {p.get('run', 'a run')} back ({p.get('requirement', '')}) asking: {p.get('notes', '')}",
                     SIGNAL_CHARS)
    if signal.kind == "edit_delta":
        files = ", ".join(p.get("files", [])[:6])
        return _clip(f"Changed what an agent wrote on {p.get('run', 'a run')} before merging: "
                     f"\"{p.get('subject', '')}\" in {files or 'no file'}", SIGNAL_CHARS)
    op = p.get("op", "edit")
    if op == "revise":
        said = "; ".join(f"[{c.get('kind')}] {c.get('body', '')}" for c in p.get("comments", [])[:5])
        return _clip(f"Commented on {p.get('plan', 'a plan')} before dispatch: {said}", SIGNAL_CHARS)
    before, after = p.get("before") or {}, p.get("after") or {}
    what = {"edit": "Edited", "add": "Added", "remove": "Removed", "move": "Reordered"}.get(op, "Changed")
    if op == "edit":
        changed = [f"{k} \"{_clip(before.get(k), 90)}\" → \"{_clip(after.get(k), 90)}\""
                   for k in ("label", "agent", "detail") if before.get(k) != after.get(k)]
        return _clip(f"{what} step {p.get('step')} of {p.get('plan')}: " + "; ".join(changed), SIGNAL_CHARS)
    if op == "move":
        return _clip(f"{what} the steps of {p.get('plan')}: " + " → ".join(p.get("order", [])[:10]), SIGNAL_CHARS)
    step = after or before
    return _clip(f"{what} a step of {p.get('plan')}: \"{step.get('label', '')}\" ({step.get('agent', '')})"
                 + (f" — {step.get('detail')}" if step.get("detail") else ""), SIGNAL_CHARS)


@dataclass(frozen=True, slots=True)
class Applied:
    """The adopted rules a model is handed for a project: the block of text and the refs it carries."""

    text: str = ""
    refs: list[str] = field(default_factory=list)

    def grounding(self) -> list[dict[str, str]]:
        """As a plan records what it was handed: `{kind: 'taste', ref, path}` with no path."""
        return [{"kind": "taste", "ref": ref, "path": ""} for ref in self.refs]


# ── what a distillation asks ─────────────────────────────────────
class Proposed(BaseModel):
    text: str
    supports: list[int] = Field(default_factory=list)
    contradicts: list[int] = Field(default_factory=list)


class Weighed(BaseModel):
    ref: str
    supports: list[int] = Field(default_factory=list)
    contradicts: list[int] = Field(default_factory=list)


class Distilled(BaseModel):
    rules: list[Proposed] = Field(default_factory=list)
    existing: list[Weighed] = Field(default_factory=list)


SYSTEM = """You read what a software team did — signatures accepted or refused beside what a review found,
runs sent back with notes, plans edited before they were dispatched, and their own commits on top of an
agent's work — and say how this team likes the work done.

Rules:
- Propose at most {most} rules. Each is one sentence an engineer or an AI agent can follow while planning,
  writing or reviewing a change ("Money is Decimal, never float", "Every new endpoint gets a test through
  the HTTP route"). Never a summary of a run, never praise, never a rule about NeuroCode itself.
- A rule must rest on the signals. Cite the ids of the signals that support it in "supports", and of any
  that go against it in "contradicts". Propose nothing that no signal supports.
- A rule already listed is not proposed again: when a signal bears on one, put it under "existing" with
  its ref and the signal ids for and against.
- Write in plain English whatever language the signals are in.

Reply with one JSON object and nothing else:
{{"rules": [{{"text": "one sentence", "supports": [12, 15], "contradicts": []}}],
 "existing": [{{"ref": "TASTE-3", "supports": [18], "contradicts": [21]}}]}}"""


def _messages(signals: Sequence[TasteSignal], existing: Sequence[TasteRule]) -> list[dict[str, str]]:
    lines = ["Signals (id · kind · what happened):"]
    lines += [f"[{s.id}] {s.kind} · {summary(s)}" for s in signals]
    if existing:
        lines.append("\nRules already listed (ref · status · text):")
        lines += [f"[{rule_ref(r.id)}] {r.status} · {r.text}" for r in existing]
    return [{"role": "system", "content": SYSTEM.format(most=MAX_PROPOSED)},
            {"role": "user", "content": "\n".join(lines)}]


def _parse(handed: set[int], refs: set[str]):
    """The model's answer, kept to what it was handed: an id it was not given is not evidence, a ref it was
    not shown is not a rule, and a proposed rule left with no support is not a rule at all."""
    def parse(raw: str) -> Distilled:
        out = Distilled.model_validate(extract_json(raw))
        kept: list[Proposed] = []
        for rule in out.rules:
            text = _clip(rule.text, MAX_RULE)
            supports = sorted({i for i in rule.supports if i in handed})
            if len(text) < MIN_RULE or not supports:
                continue
            kept.append(Proposed(text=text, supports=supports,
                                 contradicts=sorted({i for i in rule.contradicts if i in handed} - set(supports))))
        weighed = [Weighed(ref=w.ref, supports=sorted({i for i in w.supports if i in handed}),
                           contradicts=sorted({i for i in w.contradicts if i in handed}))
                   for w in out.existing if w.ref in refs]
        return Distilled(rules=kept[:MAX_PROPOSED], existing=weighed)
    return parse


class TasteService:
    def __init__(self, session: AsyncSession, gateway: Gateway | None = None) -> None:
        self.session = session
        self.gateway = gateway
        self.activity = ActivityRepository(session)

    # ── recording a signal ───────────────────────────────────────
    async def _known(self, keys: Sequence[str]) -> set[str]:
        if not keys:
            return set()
        stmt = select(TasteSignal.payload["key"].astext).where(TasteSignal.payload["key"].astext.in_(list(keys)))
        return set((await self.session.execute(stmt)).scalars())

    async def record(self, kind: str, payload: dict[str, Any], *, project_id: str | None, run_id: str | None = None,
                     by_user_id: str | None = None, key: str | None = None) -> TasteSignal | None:
        """Keep one signal. With a `key`, a moment already recorded under it is not recorded again — the
        runtime's call and a later harvest describe the same moment. Returns None when it was known."""
        if kind not in KINDS:
            raise ValueError(f"not a taste signal: {kind}")
        if key is not None and await self._known([key]):
            return None
        signal = TasteSignal(project_id=project_id, run_id=run_id, kind=kind, by_user_id=by_user_id,
                             payload={**payload, **({"key": key} if key else {})})
        self.session.add(signal)
        await self.session.flush()
        return signal

    # ── the moments, one at a time (the runtime's call sites) ────
    async def on_plan_edit(self, plan_ref: str, project_id: str, op: str, *, by_user_id: str | None,
                           **what: Any) -> TasteSignal | None:
        """A plan's steps changed by a person before dispatch: `op` is edit | add | remove | move | revise."""
        return await self.record("plan_edit", {"plan": plan_ref, "op": op, **what}, project_id=project_id,
                                 by_user_id=by_user_id)

    async def on_rework(self, run: Run, notes: str, *, by: str, by_user_id: str | None,
                        again: str | None = None) -> TasteSignal | None:
        """A person sent a run back. Call it from `RunService.rework` for a person's rework only — the
        completion check sending a run back is the runtime, not taste."""
        if by in MACHINE_REWORKERS or not notes.strip():
            return None
        return await self.record("rework_note", {
            "run": run.ref, "again": again, "notes": _clip(notes, 1_500), "by": by,
            "requirement": _clip(run.requirement.split("\n\n", 1)[0], 300), "findings": _findings(run)},
            project_id=run.project_id, run_id=run.id, by_user_id=by_user_id, key=f"rework:{run.ref}")

    async def on_verdict(self, run: Run, approval: Approval, *, approved: bool,
                         by_user_id: str | None) -> TasteSignal | None:
        """A signature decided: accepted, or refused outright. Call it where the runtime settles a handoff
        approval. A refusal that sent the run back for changes is recorded as the rework instead."""
        if not approved and (run.review or {}).get("reworkedAs"):
            return None
        review = run.review or {}
        return await self.record("accept" if approved else "refuse", {
            "run": run.ref, "approval": approval.ref, "risk": approval.risk,
            "requirement": _clip(run.requirement.split("\n\n", 1)[0], 300), "findings": _findings(run),
            "verdict": _clip(review.get("verdict"), 300), "tests": run.tests_status,
            "checks": [f"{c.get('name')} {c.get('status', 'not run')}" for c in review.get("checks") or []][:6],
            "files": run.diff_files}, project_id=run.project_id, run_id=run.id, by_user_id=by_user_id,
            key=f"verdict:{approval.ref}")

    async def on_merged(self, run: Run, *, by_user_id: str | None = None) -> list[TasteSignal]:
        """A run merged: the person's own commits on its branch after the agent's last one, one signal
        each. Reads git in a worker thread; a branch that is gone has nothing left to read."""
        from .runs import _parts            # runs imports plans, and plans imports this module
        deltas: list[dict[str, Any]] = []
        for part in _parts(run):
            deltas += await asyncio.to_thread(_person_commits, part.repo, part.base, part.branch, part.prefix,
                                              part.lead)
        made: list[TasteSignal] = []
        for delta in deltas:
            signal = await self.record("edit_delta", {"run": run.ref, **delta}, project_id=run.project_id,
                                       run_id=run.id, by_user_id=by_user_id, key=f"edit:{run.ref}:{delta['commit']}")
            if signal is not None:
                made.append(signal)
        return made

    # ── the same moments, read back out of what is recorded ──────
    async def harvest(self, project_id: str | None = None) -> int:
        """Signals for the moments already recorded elsewhere that no signal holds yet. Returns how many
        were added. Every key is the one the capture functions use, so nothing is counted twice."""
        added = 0
        added += await self._harvest_verdicts(project_id)
        added += await self._harvest_reworks(project_id)
        added += await self._harvest_merges(project_id)
        return added

    async def _harvest_verdicts(self, project_id: str | None) -> int:
        stmt = (select(Approval).where(Approval.run_ref.is_not(None), Approval.tool.like("Merge(%"),
                                       Approval.status.in_(("approved", "denied")))
                .order_by(Approval.decided_at.desc().nulls_last(), Approval.id.desc()).limit(HARVEST_WINDOW))
        if project_id is not None:
            stmt = stmt.where(Approval.project_id == project_id)
        gates = list((await self.session.execute(stmt)).scalars())
        known = await self._known([f"verdict:{g.ref}" for g in gates])
        todo = [g for g in gates if f"verdict:{g.ref}" not in known]
        runs = await self._runs([g.run_ref for g in todo if g.run_ref])
        added = 0
        for gate in todo:
            run = runs.get(gate.run_ref or "")
            if run is not None and await self.on_verdict(run, gate, approved=gate.status == "approved",
                                                         by_user_id=gate.decided_by) is not None:
                added += 1
        return added

    async def _harvest_reworks(self, project_id: str | None) -> int:
        stmt = (select(Run).where(Run.review["reworkedAs"].astext.is_not(None))
                .order_by(Run.created_at.desc()).limit(HARVEST_WINDOW))
        if project_id is not None:
            stmt = stmt.where(Run.project_id == project_id)
        sent = list((await self.session.execute(stmt)).scalars())
        known = await self._known([f"rework:{r.ref}" for r in sent])
        todo = [r for r in sent if f"rework:{r.ref}" not in known]
        again = await self._runs([r.review["reworkedAs"] for r in todo])
        added = 0
        for run in todo:
            new = again.get(run.review["reworkedAs"])
            if new is None:
                continue
            # The runtime writes the reason as the first line of the run that does it again.
            first = (await self.session.execute(
                select(RunLog.line).where(RunLog.run_id == new.id, RunLog.line.like(f"rework of {run.ref},%"))
                .order_by(RunLog.id).limit(1))).scalar_one_or_none()
            said = REWORK_LINE.match(first or "")
            if said is None:
                continue
            if await self.on_rework(run, said["notes"], by=said["by"], by_user_id=await self._user_id(said["by"]),
                                    again=new.ref) is not None:
                added += 1
        return added

    async def _harvest_merges(self, project_id: str | None) -> int:
        stmt = (select(Run).where(Run.merged.is_not(None), Run.removed.is_(False))
                .order_by(Run.updated_at.desc()).limit(HARVEST_WINDOW))
        if project_id is not None:
            stmt = stmt.where(Run.project_id == project_id)
        added = 0
        for run in (await self.session.execute(stmt)).scalars():
            added += len(await self.on_merged(run, by_user_id=await self._user_id((run.merged or {}).get("by"))))
        return added

    async def _runs(self, refs: Sequence[str]) -> dict[str, Run]:
        if not refs:
            return {}
        found = await self.session.execute(select(Run).where(Run.ref.in_(list(set(refs)))))
        return {r.ref: r for r in found.scalars()}

    async def _user_id(self, name: str | None) -> str | None:
        """The account a name recorded on a run belongs to, when exactly one account has it."""
        if not name:
            return None
        ids = list((await self.session.execute(select(User.id).where(User.name == name).limit(2))).scalars())
        return ids[0] if len(ids) == 1 else None

    # ── learning ─────────────────────────────────────────────────
    async def learn(self, project_id: str | None, *, by: str, by_id: str | None) -> dict[str, Any]:
        """Read the signals nobody has read, and propose rules from them. Refused, with nothing marked
        read, when there is nothing new or no model can answer."""
        if self.gateway is None:
            raise RuntimeError("learning needs the gateway")
        harvested = await self.harvest(project_id)
        stmt = (select(TasteSignal).where(TasteSignal.distilled.is_(False))
                .order_by(TasteSignal.at.desc(), TasteSignal.id.desc()).limit(READ_AT_ONCE))
        if project_id is not None:
            stmt = stmt.where(TasteSignal.project_id == project_id)
        signals = list((await self.session.execute(stmt)).scalars())
        if not signals:
            raise Refused("Nothing new to learn from yet. Signals come from accepting or refusing a run, sending "
                          "one back with notes, editing a plan's steps, and your own commits on a run's branch.")
        existing = list((await self.session.execute(
            self._scoped(select(TasteRule), project_id).where(TasteRule.status.in_(("proposed", "active")))
            .order_by(TasteRule.status, TasteRule.support.desc(), TasteRule.id).limit(MAX_EXISTING))).scalars())
        handed = {s.id for s in signals}
        by_ref = {rule_ref(r.id): r for r in existing}
        messages = _messages(signals, existing)
        gw = self.gateway
        result = await asyncio.to_thread(needs_a_model, lambda: gw.ask(
            messages, _parse(handed, set(by_ref)), feature="taste", actor=by_id, project=project_id))
        out: Distilled = result.data
        lane = f"{result.provider.id} · {result.provider.model}"

        proposed: list[TasteRule] = []
        weighed: list[TasteRule] = []
        same = {_norm(r.text): r for r in existing}
        for rule in out.rules:
            twin = same.get(_norm(rule.text))
            if twin is not None:                    # said again in other words: it is more evidence, not a rule
                _weigh(twin, rule.supports, rule.contradicts)
                weighed.append(twin)
                continue
            made = TasteRule(project_id=project_id, text=rule.text, status="proposed", support=len(rule.supports),
                             contradict=len(rule.contradicts), proposed_by=lane,
                             evidence=[*({"id": i, "stance": "for"} for i in rule.supports),
                                       *({"id": i, "stance": "against"} for i in rule.contradicts)])
            self.session.add(made)
            same[_norm(rule.text)] = made
            proposed.append(made)
        for w in out.existing:
            rule = by_ref[w.ref]
            if w.supports or w.contradicts:
                _weigh(rule, w.supports, w.contradicts)
                weighed.append(rule)
        await self.session.execute(update(TasteSignal).where(TasteSignal.id.in_(list(handed))).values(distilled=True))
        await self.session.flush()
        await self.activity.record(
            actor=by, actor_kind="human", action="Taste learnt",
            detail=f"{len(signals)} signal{'s' if len(signals) != 1 else ''} read · {len(proposed)} rule"
                   f"{'s' if len(proposed) != 1 else ''} proposed · {len(set(map(id, weighed)))} weighed again · {lane}",
            level="ok", project_id=project_id)
        return {"read": len(signals), "harvested": harvested, "proposed": proposed,
                "weighed": list({id(r): r for r in weighed}.values()), "model": lane}

    # ── reading ──────────────────────────────────────────────────
    @staticmethod
    def _scoped(stmt: Any, project_id: str | None, *, model: Any = TasteRule, only: bool = False) -> Any:
        """A project's view is its own rows and the workspace's; the workspace's view is its own alone —
        unless `only`, which is the project's own rows and nothing else."""
        if project_id is None:
            return stmt.where(model.project_id.is_(None))
        if only:
            return stmt.where(model.project_id == project_id)
        return stmt.where((model.project_id == project_id) | model.project_id.is_(None))

    async def rules(self, project_id: str | None, *, status: str | None = None, limit: int | None = None,
                    offset: int = 0) -> tuple[list[TasteRule], dict[str, int]]:
        stmt = self._scoped(select(TasteRule), project_id)
        if status is not None:
            stmt = stmt.where(TasteRule.status == status)
        stmt = (stmt.order_by(TasteRule.status, TasteRule.support.desc(), TasteRule.id.desc())
                .limit(min(bounded(limit), CEILING)).offset(max(0, offset)))
        counts = dict.fromkeys(STATUSES, 0)
        for status_, n in (await self.session.execute(self._scoped(
                select(TasteRule.status, func.count()), project_id).group_by(TasteRule.status))).all():
            counts[status_] = n
        return list((await self.session.execute(stmt)).scalars()), counts

    async def signal_counts(self, project_id: str | None) -> dict[str, Any]:
        """How many signals there are by kind, and how many no distillation has read. A project's are its
        own; the workspace counts every one."""
        stmt = select(TasteSignal.kind, TasteSignal.distilled, func.count()).group_by(TasteSignal.kind,
                                                                                     TasteSignal.distilled)
        if project_id is not None:
            stmt = stmt.where(TasteSignal.project_id == project_id)
        by_kind = dict.fromkeys(KINDS, 0)
        unread = total = 0
        for kind, distilled, n in (await self.session.execute(stmt)).all():
            by_kind[kind] = by_kind.get(kind, 0) + n
            total += n
            unread += 0 if distilled else n
        return {"total": total, "unread": unread, "byKind": by_kind}

    async def signals(self, project_id: str | None, *, kind: str | None = None, unread: bool | None = None,
                      limit: int | None = None, offset: int = 0) -> list[TasteSignal]:
        stmt = select(TasteSignal)
        if project_id is not None:
            stmt = stmt.where(TasteSignal.project_id == project_id)
        if kind is not None:
            stmt = stmt.where(TasteSignal.kind == kind)
        if unread is not None:
            stmt = stmt.where(TasteSignal.distilled.is_(not unread))
        stmt = (stmt.order_by(TasteSignal.at.desc(), TasteSignal.id.desc())
                .limit(min(bounded(limit), CEILING)).offset(max(0, offset)))
        return list((await self.session.execute(stmt)).scalars())

    async def rule(self, rule_id: int) -> TasteRule:
        found = await self.session.get(TasteRule, rule_id)
        if found is None:
            raise NotFound(f"taste rule {rule_id}")
        return found

    async def evidence(self, rule_id: int) -> tuple[TasteRule, list[tuple[TasteSignal, str]]]:
        """The signals a rule cites, each with whether it was cited for or against it. A signal whose
        project was removed is gone from the list, not invented back."""
        rule = await self.rule(rule_id)
        stance = {int(e["id"]): e.get("stance", "for") for e in rule.evidence or [] if isinstance(e, dict)}
        found = (await self.session.execute(select(TasteSignal).where(TasteSignal.id.in_(list(stance)))
                                            .order_by(TasteSignal.at.desc()).limit(CEILING))).scalars()
        return rule, [(s, stance[s.id]) for s in found]

    async def names(self, ids: Sequence[str | None]) -> dict[str, str]:
        wanted = [i for i in set(ids) if i]
        if not wanted:
            return {}
        return dict((await self.session.execute(select(User.id, User.name).where(User.id.in_(wanted)))).all())

    # ── a person's word on a rule ────────────────────────────────
    async def change(self, rule_id: int, *, text: str | None, status: str | None, by: str,
                     by_id: str | None) -> TasteRule:
        """Adopt, reject, switch on or off, or reword a rule. Adopting is the only way a rule reaches a
        model; rejecting keeps it, retired, so the same proposal is recognised when it comes back."""
        rule = await self.rule(rule_id)
        said: list[str] = []
        if text is not None:
            clean = _clip(text, MAX_RULE)
            if len(clean) < MIN_RULE:
                raise Refused("Write the rule as a sentence someone could follow.", status=422)
            if rule.status == "retired":
                raise Refused(f"{rule_ref(rule.id)} is retired. Switch it on before rewording it.")
            if clean != rule.text:
                said.append(f"reworded \"{_clip(rule.text, 80)}\" → \"{_clip(clean, 80)}\"")
                rule.text = clean
        if status is not None and status != rule.status:
            if status not in STATUSES or status == "proposed":
                raise Refused("A rule is adopted (active) or retired; nothing goes back to proposed.", status=422)
            word = ("adopted" if rule.status == "proposed" else "switched on") if status == "active" else \
                ("rejected" if rule.status == "proposed" else "switched off")
            said.append(word)
            if status == "active":
                rule.adopted_by, rule.adopted_at = by_id, utcnow()
            rule.status = status
        if not said:
            return rule
        await self.session.flush()
        await self.activity.record(actor=by, actor_kind="human", action="Taste rule changed",
                                   detail=f"{rule_ref(rule.id)} {' · '.join(said)}", level="ok",
                                   project_id=rule.project_id)
        return rule

    # ── what a model is handed ───────────────────────────────────
    async def applied(self, project_id: str | None) -> Applied:
        """The adopted rules for a project — its own first, then the workspace's — as the block handed to
        the compiler, the agents and the reviewer after the project's instruction files."""
        stmt = (self._scoped(select(TasteRule), project_id).where(TasteRule.status == "active")
                .order_by(TasteRule.project_id.is_(None), TasteRule.support.desc(), TasteRule.id).limit(MAX_APPLIED))
        rules = list((await self.session.execute(stmt)).scalars())
        if not rules:
            return Applied()
        text = "\n".join(f"- [{rule_ref(r.id)}] {r.text}" for r in rules)
        return Applied(text=text, refs=[rule_ref(r.id) for r in rules])


async def applied_for(session: AsyncSession, project_id: str | None) -> Applied:
    """For the runtime: the taste block an agent step and the reviewer are handed for this project, and the
    refs to record where the step's instructions are recorded ("Taste applied")."""
    return await TasteService(session).applied(project_id)


def _weigh(rule: TasteRule, supports: Sequence[int], contradicts: Sequence[int]) -> None:
    """More evidence for or against a rule, each signal counted once whatever it is cited as."""
    seen = {int(e["id"]) for e in rule.evidence or [] if isinstance(e, dict)}
    fresh_for = [i for i in supports if i not in seen]
    fresh_against = [i for i in contradicts if i not in seen and i not in fresh_for]
    rule.evidence = [*(rule.evidence or []), *({"id": i, "stance": "for"} for i in fresh_for),
                     *({"id": i, "stance": "against"} for i in fresh_against)]
    rule.support += len(fresh_for)
    rule.contradict += len(fresh_against)


def _findings(run: Run) -> list[dict[str, Any]]:
    return [{"severity": f.get("severity", "LOW"), "file": f.get("file", ""), "note": _clip(f.get("note"), 200)}
            for f in (run.review or {}).get("findings", [])[:6]]


# ── git: a person's commits after the agent's ────────────────────
def _git(args: list[str], cwd: Path) -> str | None:
    from ..agent.git import git
    try:
        done = git(args, cwd, timeout=30)
    except Exception:                                  # a repository that moved or broke says nothing
        return None
    return done.stdout if done.returncode == 0 else None


def _person_commits(repo: Path, base: str, branch: str, prefix: str, lead: str) -> list[dict[str, Any]]:
    """Blocking. The commits on `branch` after the agent's last one whose author is not the agent, with the
    files they touched as the project names them and as much of their patch as a signal keeps."""
    if not base or not branch or not repo.is_dir():
        return []
    listed = _git(["log", "--reverse", "--format=%H%x1f%ae%x1f%s", f"{base}..{branch}"], repo)
    if not listed:
        return []
    commits = [line.split("\x1f", 2) for line in listed.splitlines() if line.count("\x1f") == 2]
    last_agent = max((i for i, (_, email, _) in enumerate(commits) if email == AGENT_EMAIL), default=-1)
    out: list[dict[str, Any]] = []
    for sha, email, subject in commits[last_agent + 1:]:
        if email == AGENT_EMAIL:
            continue
        names = (_git(["show", "--format=", "--name-only", sha], repo) or "").split()
        inside = [n for n in names if not prefix or n.startswith(prefix.rstrip("/") + "/")]
        files = [lead + (n[len(prefix.rstrip("/")) + 1:] if prefix else n) for n in inside]
        patch = _git(["show", "--format=", "--patch", "--no-color", sha], repo) or ""
        out.append({"commit": sha, "subject": _clip(subject, 200), "files": files[:40],
                    "patch": patch[:PATCH_CHARS], "cut": len(patch) > PATCH_CHARS})
    return out


# ── the documents the Memory screen's Taste tab reads ────────────
def _when(value: Any) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def rule_json(rule: TasteRule, names: dict[str, str] | None = None) -> dict[str, Any]:
    """A rule as the Taste tab shows it. `support` and `contradict` are counted signals, never a model's
    number; `evidence` is how many signals it cites; `adoptedBy` is a name, null until adopted."""
    return {"id": rule.id, "ref": rule_ref(rule.id), "projectId": rule.project_id, "text": rule.text,
            "status": rule.status, "support": rule.support, "contradict": rule.contradict,
            "evidence": len(rule.evidence or []), "proposedBy": rule.proposed_by or None,
            "adoptedBy": (names or {}).get(rule.adopted_by or "") if rule.adopted_by else None,
            "adoptedAt": _when(rule.adopted_at), "createdAt": _when(rule.created_at),
            "updatedAt": _when(rule.updated_at)}


def signal_json(signal: TasteSignal, names: dict[str, str] | None = None, *,
                stance: str | None = None) -> dict[str, Any]:
    """A signal: what happened in one line (`summary`), and the payload it was built from — a commit's
    patch among it — so the evidence can be read, not taken on trust."""
    payload = {k: v for k, v in (signal.payload or {}).items() if k != "key"}
    return {"id": signal.id, "kind": signal.kind, "projectId": signal.project_id, "summary": summary(signal),
            "payload": payload, "distilled": signal.distilled, "at": _when(signal.at),
            "by": (names or {}).get(signal.by_user_id or "") if signal.by_user_id else None,
            **({"stance": stance} if stance else {})}
