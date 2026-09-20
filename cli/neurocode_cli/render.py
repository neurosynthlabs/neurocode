"""How things read in a terminal: times, statuses, tables, a plan, a run's log line, a patch.

Only presentation — every figure shown comes from the API's answer, and nothing is shown that it did not
say. Colours follow one rule: green is done or allowed, yellow is waiting on a person, red failed or
refused, dim is detail.

Two rules about width, because a terminal is not always a 120-column window on a laptop. The console is
as wide as the window, or as wide as `$COLUMNS` says, or — when the answer is going into a pipe or a
file — as wide as it likes, never Rich's 80-column guess for a pipe. And a list gives way as the window
narrows: the detail columns go first, and under 60 columns a table is the wrong shape altogether.
"""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import Any

from rich.console import Console, ConsoleOptions, RenderResult
from rich.markup import escape
from rich.table import Table
from rich.text import Text

out = Console(highlight=False, soft_wrap=False)
err = Console(stderr=True, highlight=False)

#: How wide a console is when nothing is watching it — a pipe, a file, `tee`. Rich would say 80 and cut
#: a run's requirement in half on its way into `grep`; there is no window there to be cut to.
PIPED = 10_000
#: Under this, the detail columns of a list go. Under NARROW, the list stops being a table at all.
ROOMY, NARROW = 100, 60

STATUS_STYLE = {
    "done": "green", "approved": "green", "active": "green", "idle": "green", "allowed": "green",
    "running": "cyan", "thinking": "cyan", "queued": "cyan", "onboarding": "cyan", "dispatched": "cyan",
    "waiting": "yellow", "pending": "yellow", "draft": "yellow", "paused": "yellow",
    "failed": "red", "denied": "red", "cancelled": "red", "refused": "red", "error": "red",
}
LEVEL_STYLE = {"ok": "green", "warn": "yellow", "err": "red", "tool": "cyan", "info": ""}
#: Runs in these states will not change again on their own.
FINISHED = ("done", "failed", "cancelled")


def fit() -> None:
    """Work out how wide the two consoles may be, before a command prints anything.

    Rich reads `$COLUMNS` itself, so the only thing that has to be said here is what a pipe means: not
    80 columns, but no limit. It is settled every time rather than once at import, because a process
    that runs more than one command — the tests, a shell that sources `nc` twice — must not carry the
    first command's window, or the first command's idea of a terminal, into the second. Rich caches
    both and offers no public way to forget them, which is what the two private fields here are."""
    for console, stream in ((out, sys.stdout), (err, sys.stderr)):
        console._force_terminal = None       # noqa: SLF001 — read from the environment again
        named = os.environ.get("COLUMNS", "")
        if named.isdigit() and int(named) > 0:
            console.width = int(named)
        elif stream is not None and not stream.isatty():
            console.width = PIPED
        else:
            console._width = None            # noqa: SLF001 — measure the window again


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


def columns(*names: str) -> Table:
    """A bare table whose cells are cut, not folded: half a word on three lines reads worse than a
    sentence that ends in an ellipsis, and at 60 columns folding turns a list into rubble."""
    grid = Table(box=None, show_edge=False, pad_edge=False, header_style="dim", expand=False)
    for name in names:
        grid.add_column(name, overflow="ellipsis", no_wrap=True)
    return grid


def _cut(markup: str) -> Text:
    """One line that is cut at the window's edge rather than wrapped onto the next."""
    line = Text.from_markup(markup, overflow="ellipsis")
    line.no_wrap = True
    return line


class Grid:
    """A list, laid out for the window it is going into.

    With room (100 columns or more) every column is shown. Narrower, the columns named `detail` go —
    a missing column is easier to read than a folded one, and the ref and the status are what a person
    came for. Under 60 columns — an SSH window on a phone, a narrow tmux pane — a table is the wrong
    shape: each record becomes two lines, what it is and how it stands, then its one sentence.
    """

    def __init__(self, *names: str, detail: Sequence[str] = (), sentence: str = "") -> None:
        self.names = list(names)
        self.detail = set(detail)
        self.sentence = sentence
        self.rows: list[list[str]] = []

    def add_row(self, *cells: str) -> None:
        self.rows.append([str(c) for c in cells])

    def _at(self, row: Sequence[str], name: str) -> str:
        return row[self.names.index(name)] if name in self.names else ""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        width = options.max_width
        if width < NARROW:
            for row in self.rows:
                head = [c for name, c in zip(self.names, row, strict=False)
                        if name not in self.detail and name != self.sentence and c]
                yield _cut(" · ".join(head))
                line = self._at(row, self.sentence)
                if line:
                    yield _cut(f"[dim]  {line}[/]")
            return
        keep = [i for i, name in enumerate(self.names) if width >= ROOMY or name not in self.detail]
        grid = columns(*[self.names[i] for i in keep])
        for row in self.rows:
            grid.add_row(*[row[i] for i in keep])
        yield grid


def table(*names: str, detail: Sequence[str] = (), sentence: str = "") -> Grid:
    """The list every command prints. `detail` names the columns a narrow window may do without, and
    `sentence` the one column that becomes the second line when there is no room for a table."""
    return Grid(*names, detail=detail, sentence=sentence)


def log_line(line: dict[str, Any]) -> str:
    level = str(line.get("level") or "info")
    style = LEVEL_STYLE.get(level, "")
    step = line.get("step")
    where = f"[dim]{step:>2}[/] " if isinstance(step, int) else ""
    text = escape(str(line.get("line") or ""))
    return f"{where}[{style}]{text}[/]" if style else f"{where}{text}"


# ── a patch, read in a terminal ──────────────────────────────────
def patch_path(header: str) -> str:
    """The file a `diff --git` line is about: the b side, or the a side when the file was removed."""
    left, _, right = header[len("diff --git "):].partition(" b/")
    before = left[2:] if left.startswith("a/") else left
    after = right or before
    return (after if after.strip('"') != "dev/null" else before).strip('"')


def patch_sections(patch: str) -> list[tuple[str, str]]:
    """The patch cut into (path, that file's own `diff --git` section), in the order git wrote them.
    Anything before the first header — a labelled patch's preamble — is kept under an empty path, so
    nothing is dropped on the way to `git apply`."""
    found: list[tuple[str, str]] = []
    path: str | None = None
    held: list[str] = []
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if held:
                found.append((path if path is not None else "", "".join(held)))
            path, held = patch_path(line.rstrip("\n")), [line]
        else:
            held.append(line)
    if held:
        found.append((path if path is not None else "", "".join(held)))
    return found


def patch_stat(section: str) -> tuple[int, int]:
    """How many lines the section adds and removes, counted from the patch itself — the server sends
    only the totals for the whole diff, so a per-file figure can come from nowhere else."""
    added = removed = 0
    for line in section.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


#: Header lines the styled view drops: they are machinery for `git apply`, not for a person. `--raw`
#: (and anything piped) keeps them, because that is the patch itself.
_MACHINERY = ("index ", "--- ", "+++ ", "old mode", "new mode", "similarity index", "rename from",
              "rename to", "new file mode", "deleted file mode", "GIT binary patch")


def patch_line(line: str) -> Text | None:
    """One line of a patch, in its colour — or None when the styled view leaves it out.

    Deliberately not `rich.syntax.Syntax(patch, "diff")`: pygments over a 200 KB patch costs hundreds
    of milliseconds, and it word-wraps, which pulls every column out of line at 80 columns. A patch is
    six shapes of line; this is those six."""
    if line.startswith("diff --git "):
        return Text(patch_path(line), style="bold")
    if line.startswith(_MACHINERY):
        return None
    if line.startswith("@@"):
        return Text(line, style="dim cyan")
    if line.startswith("+"):
        return Text(line, style="green")
    if line.startswith("-"):
        return Text(line, style="red")
    if line.startswith("\\"):
        return Text(line, style="dim")
    return Text(line)


def print_patch(patch: str, console: Console | None = None) -> None:
    """The patch, coloured, one line per line: cut at the window's edge, never wrapped, so the columns
    stay where the diff put them."""
    screen = console or out
    first = True
    for line in patch.splitlines():
        if line.startswith("diff --git ") and not first:
            screen.print()
        shown = patch_line(line)
        if shown is None:
            continue
        first = False
        screen.print(shown, no_wrap=True, crop=True, overflow="ellipsis")


def print_patch_files(sections: Iterable[tuple[str, str]], console: Console | None = None) -> None:
    """Which files the patch touches and by how much — counted here, from the patch."""
    screen = console or out
    grid = columns("File", "+", "−")
    for path, body in sections:
        added, removed = patch_stat(body)
        grid.add_row(escape(path or "(no file named)"), f"[green]+{added}[/]", f"[red]−{removed}[/]")
    screen.print(grid)
    screen.print("[dim]counted from the patch[/]")


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
        # Numbered, because the number is how a person answers one: `nc plan-answer PLAN-3 2 "…"`.
        out.print(f"[yellow]Open questions ({len(questions)})[/] — answer one with "
                  f"`nc plan-answer {escape(str(plan.get('ref', '')))} <n> \"…\"`")
        for n, q in enumerate(questions, start=1):
            out.print(f"  {n}. {escape(str(q))}")
