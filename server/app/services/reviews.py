"""Reviews on demand: any diff read the way the runtime reads a run's.

A person names what to read — a branch against its base (one someone pushed, fetched first when they
ask), the working tree as it stands, or a range of commits — and it is read exactly as a run's review
step reads a run: the same reviewer prompt, held to the project's instructions and the taste rules a
person adopted, with the repository's REVIEW.md (or .neurocode/REVIEW.md) as the reviewer's brief, on a
lane other than the one that wrote the code whenever that is known. With no lane, rules read the diff
and the review says so, as a run's does.

The review is kept (`code_reviews`) with the fingerprint of the exact patch that was read, its size,
and what was handed to the reviewer — so "Send to a session" and "Make a plan from these findings" work
from what was really found, and a later change to the branch cannot be mistaken for what was reviewed.

The reading happens in the background (`perform`), like a run's step: the request only checks what it
can check at once, writes the row as running and hands the rest off. Every change is announced on the
stream as a `review` event and written to the activity feed.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from .. import agent
from ..agent import review as reading
from ..ai.gateway import REVIEW, Gateway, NoModel, extract_json
from ..data import roster
from ..data.base import utcnow
from ..data.changes import announce
from ..data.engine import Database
from ..models import Chat, CodeReview, Plan, Project, Task
from ..repositories import ActivityRepository, NotFound, ProjectRepository
from ..repositories.custom_agents import CustomAgentRepository
from ..repositories.reviews import CodeReviewRepository
from ..schemas.work import when
from .code import Source, roots
from .errors import Refused
from .identity import Person
from .runs import (MAX_DIFF, REVIEW_SYSTEM, ReviewOut, _briefed, _instructed, _rule_review, _taste, _told,
                   clean_findings)

log = logging.getLogger(__name__)

TARGETS = ("branch", "working-tree", "commit-range")
#: What a question or a requirement made from a review may carry, in characters.
MAX_FOLLOW_UP = 4_000
#: Follow-ups kept on a review: the sessions and plans made from it.
MAX_SENT = 20


def describe_parts(target: str, base: str, head: str, label: str) -> str:
    """What was read, in the words the screen and the prompt use."""
    where = f" in {label}" if label else ""
    if target == "working-tree":
        return f"the working tree{where} (changes not committed yet)"
    if target == "branch":
        return f"{head} against {base}{where}"
    return f"commits {base}..{head}{where}"


def describe(review: CodeReview) -> str:
    return describe_parts(review.target, review.base, review.head, review.source)


def review_json(review: CodeReview, *, project_name: str = "", by: str | None = None) -> dict[str, Any]:
    stats = review.stats or {}
    return {
        "id": review.id, "ref": review.ref, "projectId": review.project_id, "projectName": project_name,
        "source": review.source, "target": review.target, "base": review.base, "head": review.head,
        "title": describe(review), "status": review.status, "verdict": review.verdict,
        "findings": list(review.findings or []),
        "fingerprint": f"sha256:{review.fingerprint}" if review.fingerprint else None,
        "stats": {k: stats.get(k) for k in ("files", "insertions", "deletions", "commits", "untracked", "bytes",
                                            "truncated", "baseSha", "headSha", "fetched") if k in stats},
        "paths": list(stats.get("paths") or []),
        "brief": stats.get("brief"), "instructions": list(stats.get("instructions") or []),
        "taste": list(stats.get("taste") or []), "writer": stats.get("writer"),
        "sent": list(stats.get("sent") or []),
        "lane": review.lane, "model": review.model, "offline": review.model == "offline rules",
        "requestedBy": by, "createdAt": when(review.created_at), "finishedAt": when(review.finished_at),
    }


def _source(sources: list[Source], label: str) -> Source:
    """The checkout a review reads: the named source, or the project's first. A reference source is read
    like any other — reviewing someone else's code is exactly what a reference is for."""
    if not sources:
        raise Refused("This project's code is not on this machine, so there is no diff to read. Onboard its "
                      "repository in Projects first.")
    if not label:
        return sources[0]
    found = next((x for x in sources if x.label == label), None)
    if found is None:
        raise Refused(f"This project has no source called {label}. Its sources are: "
                      f"{', '.join(x.label for x in sources)}.", status=422)
    if not found.ready or not found.root.is_dir():
        raise Refused(f"The {label} source is not on this machine yet, so there is no diff to read.")
    return found


def _digest(patch: str) -> str:
    """The patch's sha-256 as the column keeps it — the hex alone; `review_json` gives it back as the runtime's
    receipts write it, `sha256:<hex>`."""
    return agent.fingerprint(patch).removeprefix("sha256:")


def _label(source: Source) -> str:
    """How the patch names this source's paths: plain for the first source, under its label otherwise."""
    return "" if source.primary else source.label


class ReviewService:
    """Asking for a review, reading them back, and turning one into a session or a plan."""

    def __init__(self, session: AsyncSession, gateway: Gateway) -> None:
        self.session = session
        self.gateway = gateway
        self.reviews = CodeReviewRepository(session)
        self.projects = ProjectRepository(session)
        self.activity = ActivityRepository(session)

    async def _project(self, project_id: str) -> Project:
        project = await self.projects.get(project_id)
        if project is None:
            raise NotFound(f"project {project_id}")
        return project

    async def _json(self, review: CodeReview) -> dict[str, Any]:
        project = await self.projects.get(review.project_id)
        names = await CustomAgentRepository(self.session).authors([review.requested_by or ""])
        return review_json(review, project_name=project.name if project else review.project_id,
                           by=names.get(review.requested_by or ""))

    # ── what can be reviewed ─────────────────────────────────────
    async def targets(self, project_id: str, source: str = "") -> dict[str, Any]:
        """What the "New review" form offers for one source: its branches, the branch its checkout is on,
        how many files differ in its working tree, its remotes, and the brief a reviewer would be handed."""
        project = await self._project(project_id)
        sources = await roots(self.session, project)
        chosen = _source(sources, source)

        def read() -> dict[str, Any]:
            found = agent.repo_of(chosen.root)
            if found is None:
                return {"git": False}
            repo, _ = found
            told = reading.brief(chosen.root)
            return {"git": True, "branches": reading.branches(repo), "current": reading.current_branch(repo),
                    "dirty": reading.dirty_files(chosen.root), "remotes": reading.remote_names(repo),
                    "brief": {"path": told[1], "bytes": len(told[0].encode())} if told else None}

        out = await asyncio.to_thread(read)
        return {"projectId": project.id, "source": "" if chosen.primary else chosen.label,
                "sources": [{"label": "" if x.primary else x.label, "name": x.label, "primary": x.primary,
                             "ready": x.ready} for x in sources], **out}

    # ── asking ───────────────────────────────────────────────────
    async def request(self, project_id: str, *, target: str, base: str = "", head: str = "", source: str = "",
                      fetch: bool = False, who: Person) -> tuple[CodeReview, bool]:
        """Write the review as running, after checking everything that can be checked now: the target, the
        source, and — unless a pushed branch is to be fetched first — that git knows both ends. The reading
        itself is `perform`, in the background. Returns the review and whether it is new: the same diff
        asked for again while it is being read is the review already under way."""
        who.must("runs:run", "ask for a review")
        if target not in TARGETS:
            raise Refused(f"A review reads a branch, the working tree or a range of commits ({', '.join(TARGETS)}).",
                          status=422)
        project = await self._project(project_id)
        chosen = _source(await roots(self.session, project), source)
        label = "" if chosen.primary else chosen.label
        base, head = base.strip(), head.strip()

        def check() -> tuple[str, str]:
            found = agent.repo_of(chosen.root)
            if found is None:
                raise Refused("This checkout is not a git repository, so there is no diff to read.")
            repo, _ = found
            if target == "working-tree":
                if reading.dirty_files(chosen.root) == 0:
                    raise Refused("Nothing in the working tree differs from its last commit, so there is nothing "
                                  "to review.")
                return "HEAD", ""
            start = base or (reading.current_branch(repo) if target == "branch" else "") or ""
            if not head:
                raise Refused("Name the branch or commit to review.", status=422)
            if not start:
                raise Refused("Name the base to compare with: the checkout is not on a branch.", status=422)
            try:
                if not (fetch and target == "branch"):
                    if reading.checked_ref(repo, head, "head") == reading.checked_ref(repo, start, "base"):
                        raise Refused(f"{head} and {start} are the same commit, so there is nothing to review.")
                elif not reading.REF.match(head) or ".." in head:
                    raise reading.Unreviewable(f"{head} is not a branch NeuroCode will fetch.")
            except reading.Unreviewable as e:
                raise Refused(str(e), status=422) from e
            return start, head

        base, head = await asyncio.to_thread(check)
        same = await self.reviews.same_running(project.id, label, target, base, head)
        if same is not None:
            return same, False
        ref = await self.reviews.next_ref()
        review = await self.reviews.add(CodeReview(
            id=f"rv{ref.split('-')[-1]}-{int(time.time())}", ref=ref, project_id=project.id, source=label,
            target=target, base=base, head=head, status="running", requested_by=who.id,
            stats={"fetch": bool(fetch and target == "branch")}))
        await self.activity.record(actor=who.name, actor_kind="human", action="Review requested",
                                   detail=f"{review.ref} · {describe(review)}", project_id=project.id)
        announce(self.session, "review", await self._json(review))
        return review, True

    # ── reading back ─────────────────────────────────────────────
    async def listed(self, project_id: str, *, limit: int | None, offset: int) -> dict[str, Any]:
        project = await self._project(project_id)
        page = await self.reviews.of(project.id, limit=limit, offset=offset)
        names = await CustomAgentRepository(self.session).authors([r.requested_by or "" for r in page.items])
        return {"reviews": [review_json(r, project_name=project.name, by=names.get(r.requested_by or ""))
                            for r in page.items],
                "total": page.total, "more": page.more, "nextOffset": page.next_offset}

    async def one(self, ref: str) -> dict[str, Any]:
        return await self._json(await self._found(ref))

    async def _found(self, ref: str) -> CodeReview:
        review = await self.reviews.by_ref(ref)
        if review is None:
            raise NotFound(f"review {ref}")
        return review

    async def _done(self, ref: str) -> CodeReview:
        review = await self._found(ref)
        if review.status != "done":
            raise Refused(f"{ref} is {'still being read' if review.status == 'running' else 'a review that failed'}"
                          ", so there are no findings to work from yet.")
        return review

    # ── what a person does with one ──────────────────────────────
    def _findings_text(self, review: CodeReview) -> str:
        lines = [f"- [{f.get('severity', 'LOW')}] "
                 + (f"{f['file']}{':' + str(f['line']) if f.get('line') else ''} — " if f.get("file") else "")
                 + str(f.get("note", "")) for f in review.findings or []]
        return "\n".join(lines)

    async def _sent(self, review: CodeReview, kind: str, ref: str, who: Person) -> None:
        stats = dict(review.stats or {})
        stats["sent"] = [*list(stats.get("sent") or []), {"kind": kind, "ref": ref, "by": who.name,
                                                          "at": utcnow().isoformat(timespec="seconds")}][-MAX_SENT:]
        review.stats = stats
        await self.session.flush()
        announce(self.session, "review", await self._json(review))

    async def to_session(self, ref: str, who: Person) -> Chat:
        """A new session on the review's project, asked to go through the findings against the code. The
        question is written now; the route hands the answering to the background, like any question."""
        who.must("sessions:chat", "send a review to a session")
        review = await self._done(ref)
        if not review.findings:
            raise Refused(f"{ref} found nothing, so there is nothing to send to a session.")
        from .chat import ChatService
        chats = ChatService(self.session, self.gateway)
        chat = await chats.start(review.project_id, who.name, f"Findings of {review.ref}")
        head = (f"Review {review.ref} read {describe(review)}"
                + (f" (patch {review.fingerprint[:12]})" if review.fingerprint else "")
                + f" and found this. Verdict: {review.verdict or 'none given'}\n\n")
        ask = ("\n\nGo through them one by one: read the code each points at, say whether it really holds, and how "
               "to fix the ones that do.")
        body = self._findings_text(review)[:MAX_FOLLOW_UP - len(head) - len(ask)]
        await chats.ask(chat.ref, head + body + ask, who.name)
        await self._sent(review, "session", chat.ref, who)
        return chat

    async def to_plan(self, ref: str, who: Person) -> tuple[Plan, Task]:
        """A plan to fix what the review found, compiled like any requirement — so it asks its questions
        and waits for a person before anything runs. Needs a model, as compiling does."""
        who.must("plans:compile", "make a plan from a review")
        review = await self._done(ref)
        if not review.findings:
            raise Refused(f"{ref} found nothing, so there is nothing to plan.")
        head = f"Fix what review {review.ref} found in {describe(review)}:\n"
        text = (head + self._findings_text(review))[:MAX_FOLLOW_UP]
        from .plans import PlanService
        plan, task = await PlanService(self.session, self.gateway).compile(review.project_id, text, by=who.name,
                                                                           by_id=who.id)
        await self._sent(review, "plan", plan.ref, who)
        return plan, task


# ── the reading, in the background ───────────────────────────────
async def _finish(db: Database, ref: str, **fields: Any) -> None:
    async with db.session() as s:
        review = await CodeReviewRepository(s).by_ref(ref)
        if review is None:
            return
        for key, value in fields.items():
            setattr(review, key, value)
        review.finished_at = utcnow()
        project = await ProjectRepository(s).get(review.project_id)
        high = sum(1 for f in review.findings or [] if f.get("severity") == "HIGH")
        said = (f"{review.ref} · {len(review.findings or [])} findings ({high} high) · {review.model or 'no model'}"
                if review.status == "done" else f"{review.ref} · {review.verdict[:200]}")
        await ActivityRepository(s).record(actor=roster.REVIEWER, actor_kind="agent",
                                           action="Review done" if review.status == "done" else "Review failed",
                                           detail=said, level="warn" if high or review.status == "failed" else "ok",
                                           project_id=review.project_id)
        names = await CustomAgentRepository(s).authors([review.requested_by or ""])
        announce(s, "review", review_json(review, project_name=project.name if project else review.project_id,
                                          by=names.get(review.requested_by or "")))


async def perform(db: Database, gateway: Gateway, ref: str) -> None:
    """Read one review's diff and write what was found. A failure is written on the review in words, never
    left as a review that says it is running forever."""
    try:
        await _perform(db, gateway, ref)
    except Exception as failed:                      # noqa: BLE001 — the review must end, whatever broke
        log.exception("review %s failed", ref)
        await _finish(db, ref, status="failed", verdict=f"The review could not be read: {type(failed).__name__}.")


async def _perform(db: Database, gateway: Gateway, ref: str) -> None:
    async with db.read() as s:
        review = await CodeReviewRepository(s).by_ref(ref)
        if review is None or review.status != "running":
            return
        project = await ProjectRepository(s).get(review.project_id)
        if project is None:
            return
        try:
            chosen = _source(await roots(s, project), review.source)
        except Refused as refused:
            chosen, missing = None, str(refused)
        target, base, head, pid = review.target, review.base, review.head, review.project_id
        wants_fetch = bool((review.stats or {}).get("fetch"))
        requested_by = review.requested_by
    if chosen is None:
        await _finish(db, ref, status="failed", verdict=missing)
        return

    def read() -> tuple[reading.Diff, tuple[str, str] | None, str | None, list[str]]:
        fetched = reading.fetch(chosen.root, head) if wants_fetch else None
        diff = reading.build(chosen.root, target, base, head, _label(chosen))
        found = agent.repo_of(chosen.root)
        runs = reading.run_refs(found[0], diff.base, diff.head) if found and target != "working-tree" else []
        return diff, reading.brief(chosen.root), fetched, runs

    try:
        diff, brief, fetched, run_refs = await asyncio.to_thread(read)
    except reading.Unreviewable as refused:
        await _finish(db, ref, status="failed", verdict=str(refused))
        return

    whole = diff.patch
    shown = whole[:MAX_DIFF]
    stats: dict[str, Any] = {**diff.stats, "bytes": len(whole.encode()), "truncated": len(whole) > MAX_DIFF,
                             "baseSha": diff.base, "headSha": diff.head or None, "fetched": fetched,
                             "paths": diff.files[:200]}
    if not shown.strip():
        await _finish(db, ref, status="done", findings=[], fingerprint=_digest(whole), stats=stats,
                      verdict="Nothing differs, so there is nothing to review.", model=None, lane=None)
        return

    # The lane that wrote this code, when a run of ours did: the reviewer is asked to be another one.
    async with db.read() as s:
        repo = CodeReviewRepository(s)
        writer = None
        if target == "branch":
            local = head.split("/", 1)[1] if fetched else head
            writer = await repo.run_on_branch(pid, local)
        for run_ref in ([] if writer else run_refs):
            writer = await repo.run_by_ref(pid, run_ref)
            if writer is not None:
                break
        writer_lane = writer.lane if writer is not None else None
        stats["writer"] = {"run": writer.ref, "lane": writer.lane} if writer is not None else None
        touched = [f["path"] for f in diff.files]
        told = await _told(s, pid, touched)
        taste = await _taste(s, pid)
    given = [{"path": f["path"], "bytes": f["bytes"]} for f in told.files if f["applied"]]
    stats["instructions"], stats["taste"] = given, list(taste.refs)
    stats["brief"] = {"path": brief[1], "bytes": len(brief[0].encode())} if brief else None

    system = _instructed(REVIEW_SYSTEM, told, taste) + (_briefed(brief) if brief else "")
    what = f"Under review: {describe_parts(target, base, head, _label(chosen))}"
    prompt = [{"role": "system", "content": system},
              {"role": "user", "content": f"{what}\n\nDiff:\n{shown}"
                                          + ("\n\n(The diff was cut to fit; review what is shown.)"
                                             if stats["truncated"] else "")}]
    try:
        result = await asyncio.to_thread(
            gateway.ask, prompt, lambda raw: ReviewOut.model_validate(extract_json(raw, trim=False)),
            feature="review", actor=requested_by, project=pid, role=REVIEW, avoid=writer_lane,
            agent=roster.REVIEWER)
        findings = clean_findings(result.data.findings)
        verdict, model, lane = result.data.verdict[:300], result.provider.model, result.provider.id
    except NoModel:
        findings, verdict, model = _rule_review(shown)
        lane = None
    except Exception as e:                                   # a bad answer must not lose the review
        findings, verdict, model = _rule_review(shown)
        verdict, lane = f"{verdict} The model failed: {type(e).__name__}.", None
    await _finish(db, ref, status="done", findings=findings, verdict=verdict, model=model, lane=lane,
                  fingerprint=_digest(whole), stats=stats)

