"""The rules around the work itself: moving a task, ticking its checklist, settling a plan.

These were spread through route handlers, which is why the same rule was written twice in two places
and drifted. Here each one is a method with a name, and the reason it refuses is a sentence.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import ChecklistItem, Plan, PlanQuestion, Task
from ..repositories import ActivityRepository, NotFound, PlanRepository, ProjectRepository, TaskRepository
from .errors import Refused

#: What a task may become, from where. A board that allows anything is a board nobody trusts.
MOVES: dict[str, set[str]] = {
    "backlog": {"planning", "in_progress", "blocked"},
    "planning": {"backlog", "in_progress", "blocked"},
    "in_progress": {"review", "blocked", "planning"},
    "review": {"done", "in_progress", "blocked"},
    "blocked": {"backlog", "planning", "in_progress"},
    "done": {"review"},                       # reopening is allowed; skipping review is not
}


@dataclass(slots=True)
class Actor:
    """Who is asking. Services check permissions themselves, so no route can forget to."""

    name: str
    permissions: frozenset[str] = frozenset()

    def may(self, permission: str) -> bool:
        return permission in self.permissions


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tasks = TaskRepository(session)
        self.projects = ProjectRepository(session)
        self.activity = ActivityRepository(session)

    async def move(self, ref: str, to: str, actor: Actor) -> Task:
        """Move a task on the board, or say why it cannot go there."""
        task = await self.tasks.by_ref(ref)
        if task is None:
            raise NotFound(f"task {ref}")
        if to == task.status:
            return task
        allowed = MOVES.get(task.status, set())
        if to not in allowed:
            raise Refused(f"A task in {task.status.replace('_', ' ')} can go to "
                          f"{', '.join(sorted(s.replace('_', ' ') for s in allowed))} — not to {to}.")
        if to == "done" and any(not item.done for item in task.checklist):
            left = sum(1 for item in task.checklist if not item.done)
            raise Refused(f"{left} of {len(task.checklist)} checklist items are still open.")
        was, task.status = task.status, to
        await self.session.flush()
        await self.activity.record(actor=actor.name, actor_kind="human", action="Task moved",
                                   detail=f"{task.ref} · {was} → {to}", project_id=task.project_id,
                                   task_ref=task.ref)
        return task

    async def tick(self, ref: str, item_id: str, done: bool, actor: Actor) -> Task:
        """Tick or untick one checklist item, and keep the task's progress honest."""
        task = await self.tasks.by_ref(ref)
        if task is None:
            raise NotFound(f"task {ref}")
        item: ChecklistItem | None = next((i for i in task.checklist if i.id == item_id), None)
        if item is None:
            raise NotFound(f"checklist item {item_id}")
        item.done = done
        ticked = sum(1 for i in task.checklist if i.done)
        task.progress = round(100 * ticked / len(task.checklist)) if task.checklist else task.progress
        await self.session.flush()
        # Its sibling above records a move, and the old stack recorded this too. Ticking an item is a
        # person saying a piece of work is finished, which is exactly the kind of thing the feed is for.
        await self.activity.record(
            actor=actor.name, actor_kind="human", action="Checklist updated",
            detail=f"{task.ref} · {'✓' if done else '○'} {item.label} · {ticked}/{len(task.checklist)}",
            level="ok" if done else "info", project_id=task.project_id, task_ref=task.ref)
        return task


class PlanQuestions:
    """Settling what a plan could not decide. Compiling and dispatching live in `services/plans.py`;
    keeping the two apart matters, because only one of them is allowed to guess — and it is neither."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.plans = PlanRepository(session)
        self.activity = ActivityRepository(session)

    async def answer(self, ref: str, n: int, answer: str = "", *, defer: bool = False) -> Plan:
        """Settle one open question. Deferring is an answer too — it is recorded, not lost."""
        plan = await self.plans.by_ref(ref)
        if plan is None:
            raise NotFound(f"plan {ref}")
        question: PlanQuestion | None = next((q for q in plan.questions if q.n == n), None)
        if question is None:
            raise NotFound(f"question {n} of {ref}")
        if not defer and not answer.strip():
            raise Refused("An answer cannot be empty. Defer it instead if you do not know yet.", status=400)
        question.answer, question.deferred = answer.strip(), defer
        await self.session.flush()
        return plan

