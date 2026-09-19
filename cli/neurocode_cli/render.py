"""How things read in a terminal: times, statuses, tables, a plan, a run's log line.

Only presentation — every figure shown comes from the API's answer, and nothing is shown that it did not
say. Colours follow one rule: green is done or allowed, yellow is waiting on a person, red failed or
refused, dim is detail.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from rich.console import Console
from rich.markup import escape
from rich.table import Table

out = Console(highlight=False, soft_wrap=False)
err = Console(stderr=True, highlight=False)

STATUS_STYLE = {
    "done": "green", "approved": "green", "active": "green", "idle": "green", "allowed": "green",
    "running": "cyan", "thinking": "cyan", "queued": "cyan", "onboarding": "cyan", "dispatched": "cyan",
    "waiting": "yellow", "pending": "yellow", "draft": "yellow", "paused": "yellow",
    "failed": "red", "denied": "red", "cancelled": "red", "refused": "red", "error": "red",
}
LEVEL_STYLE = {"ok": "green", "warn": "yellow", "err": "red", "tool": "cyan", "info": ""}
#: Runs in these states will not change again on their own.
FINISHED = ("done", "failed", "cancelled")


def dump(data: Any) -> None:
    """What `--json` prints: the API's answer, exactly, as one JSON document on stdout."""
    print(json.dumps(data, indent=2, ensure_ascii=False))


def status(value: str | None) -> str:
    text = value or "—"
    style = STATUS_STYLE.get(text, "")
    return f"[{style}]{escape(text)}[/]" if style else escape(text)


def when(iso: str | None, now: datetime | None = None) -> str:
    """"3 min ago", "2 h ago", "4 days ago" — or the date, for anything older than a month."""
    if not iso:
        return "—"
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    seconds = ((now or datetime.now(UTC)) - moment).total_seconds()
    if seconds < 0:
        future = -seconds
        if future < 3600:
            return f"in {max(1, int(future // 60))} min"
        if future < 86400 * 2:
            return f"in {int(future // 3600)} h"
        return f"in {int(future // 86400)} days"
    if seconds < 45:
        return "just now"
    if seconds < 3600:
        return f"{max(1, int(seconds // 60))} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} h ago"
    if seconds < 86400 * 30:
        days = int(seconds // 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"
    return moment.date().isoformat()


def table(*columns: str) -> Table:
    grid = Table(box=None, show_edge=False, pad_edge=False, header_style="dim", expand=False)
    for name in columns:
        grid.add_column(name, overflow="fold")
    return grid


def log_line(line: dict[str, Any]) -> str:
    level = str(line.get("level") or "info")
    style = LEVEL_STYLE.get(level, "")
    step = line.get("step")
    where = f"[dim]{step:>2}[/] " if isinstance(step, int) else ""
    text = escape(str(line.get("line") or ""))
    return f"{where}[{style}]{text}[/]" if style else f"{where}{text}"


def print_plan(plan: dict[str, Any]) -> None:
    out.print(f"[bold]{escape(plan.get('ref', ''))}[/]  {status(plan.get('status'))}  "
              f"risk {escape(str(plan.get('risk') or '—'))} · confidence {escape(str(plan.get('confidence') or '—'))}"
              + (f" · task {escape(plan['taskRef'])}" if plan.get("taskRef") else ""))
    if plan.get("businessRequirement"):
        out.print(escape(plan["businessRequirement"]))
    steps = plan.get("steps") or []
    if steps:
        out.print()
        out.print("[dim]Steps[/]")
        for s in steps:
            out.print(f"  {s.get('n')}. {escape(str(s.get('label')))}  [dim]{escape(str(s.get('agent') or ''))}[/]")
    for title, key in (("Affected files", "affectedFiles"), ("Acceptance criteria", "acceptanceCriteria")):
        items = plan.get(key) or []
        if items:
            out.print()
            out.print(f"[dim]{title}[/]")
            for item in items:
                out.print(f"  · {escape(str(item))}")
    questions = plan.get("openQuestions") or []
    if questions:
        out.print()
        out.print(f"[yellow]Open questions ({len(questions)})[/] — answer them on the Plans screen before dispatching")
        for q in questions:
            out.print(f"  ? {escape(str(q))}")
