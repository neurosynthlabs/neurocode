"""`nc` — every command, each one a thin reading of the API.

Every command takes `--json` and then prints the API's own answer and nothing else, so scripts can pipe
it into `jq`. Without it, the answer is laid out for a person. A refusal is the API's own words on stderr
and exit code 1; exit code 2 is a usage mistake; exit code 3 means an answer is waiting on a person (a
permission card, a gate); exit code 4 means `--watch --timeout` gave up while the run was still going.

There is no `--yes`. Every other tool in this shape has one, and here it would mean a signature nobody
read and a merge nobody watched — the single thing the gates and the review receipt exist to prevent.
The terminal's honest equivalents are `nc diff` (read it), `nc accept` (sign it) and `nc send-back`.
"""
from __future__ import annotations

import json
import os
import platform
import queue
import socket
import subprocess
import sys
import time
import webbrowser
from fnmatch import fnmatch
from pathlib import Path
from typing import Annotated, Any, NoReturn
from urllib.parse import quote

import typer
from rich.live import Live
from rich.markdown import Markdown
from rich.markup import escape
from rich.text import Text

from . import __version__, config
from .client import ApiError, Client, Listener
from .conversation import TOOL_LABEL, Conversation, Update, waiting_card
from .render import (FINISHED, dump, err, fit, log_line, out, patch_sections, print_patch,
                     print_patch_files, print_plan, status, table, when)

app = typer.Typer(name="nc", no_args_is_help=True, add_completion=True, rich_markup_mode="rich",
                  help="NeuroCode in the terminal: sessions, plans, approvals, runs and memory.\n\n"
                       "There is no --yes: a run is read with `nc diff` and signed with `nc accept`, "
                       "by a person, every time.",
                  context_settings={"help_option_names": ["-h", "--help"]})
memory_app = typer.Typer(no_args_is_help=True, help="Search what the workspace remembers, or add to it.")
app.add_typer(memory_app, name="memory")

#: An answer is waiting on a person: a permission card in a session, or a run parked at a gate.
WAITING = 3
#: `--watch --timeout` gave up. The run is untouched and still going; only the watching stopped.
TIMED_OUT = 4
#: The most that may be piped into a question. Refused above it, never cut: a lane is charged for every
#: byte of a prompt, and silently sending a tenth of a log file is worse than saying no.
MAX_PIPED = 100_000

Json = Annotated[bool, typer.Option("--json", help="Print the API's answer as JSON, and nothing else.")]
ProjectOpt = Annotated[str | None, typer.Option("--project", "-p", help="Project id. Defaults to `nc use`'s.")]


@app.callback()
def before_any_command() -> None:
    """Settled before a command prints a word: how wide the window it is printing into is."""
    fit()


# ── the plumbing every command shares ────────────────────────────
def fail(message: str, code: int = 1) -> NoReturn:
    err.print(f"[red]✗[/] {escape(message)}")
    raise typer.Exit(code)


def connect(*, need_token: bool = True) -> Client:
    profile = config.load()
    if not profile.server:
        fail("Not signed in. Run `nc login <url>` — the address of the NeuroCode web app or its API.")
    if need_token and not profile.token:
        fail(f"No token for {profile.server}. Run `nc login {profile.server}`.")
    return Client(profile.server, profile.token)


def attempt(call: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return call(*args, **kwargs)
    except ApiError as e:
        if e.status == 401:
            fail(f"{e.detail} Run `nc login` again.")
        fail(e.detail)


def project_of(client: Client, named: str | None) -> str:
    """--project, else the saved default, else the only project there is."""
    chosen = named or config.load().project
    if chosen:
        return chosen
    found = attempt(client.projects)
    if len(found) == 1:
        return str(found[0]["id"])
    if not found:
        fail("There are no projects yet. Add one in the web app (Projects → New project).")
    fail("Name a project with --project, or choose one with `nc use <id>`: "
         + ", ".join(str(p["id"]) for p in found))


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def kb(size: int) -> str:
    return f"{size / 1000:.0f} KB" if size >= 1000 else f"{size} bytes"


def piped() -> str | None:
    """What was piped in, or None when stdin is a terminal or nothing came down it.

    Over the cap it refuses and says both sizes, because the alternative — cutting it here — sends a
    model a fragment of a file and charges a lane for it without anyone knowing which fragment."""
    if sys.stdin.isatty():
        return None
    try:
        text = sys.stdin.read()
    except (OSError, ValueError):                    # a closed or unreadable stdin is simply no input
        return None
    if not text.strip():
        return None
    size = len(text.encode("utf-8", "replace"))
    if size > MAX_PIPED:
        fail(f"stdin is {kb(size)}; the cap is {kb(MAX_PIPED)}. Cut it yourself, or attach the file in "
             f"the web app.")
    return text


def from_file(where: str) -> str:
    """`--from <file>`, or `-` for stdin. The same cap either way."""
    if where == "-":
        text = piped()
        if text is None:
            fail("Nothing came in on stdin.", 2)
        return text
    try:
        text = Path(where).expanduser().read_text(encoding="utf-8")
    except OSError as e:
        fail(f"Could not read {where}: {e.strerror or e}.")
    if len(text.encode("utf-8", "replace")) > MAX_PIPED:
        fail(f"{where} is {kb(len(text.encode('utf-8', 'replace')))}; the cap is {kb(MAX_PIPED)}. "
             f"Cut it yourself, or attach the file in the web app.")
    return text


def whole_page(found: list[Any], limit: int) -> None:
    """Say so when a list came back exactly full: the routes do not send their page's total yet, so a
    list of 100 may be the first 100 of 412 and nothing in the answer would say which."""
    if len(found) == limit:
        err.print(f"[dim]showing {limit} — the server's page; --limit raises it, --offset reads the next.[/]")


# ── signing in ───────────────────────────────────────────────────
def locate(url: str) -> tuple[str, str | None]:
    """Where the API is, and where the web app is, from one address a person knows.

    The web app serves its API under /api (the dev server proxies it; a deployment serves it beside the
    app), so the web app's own address answers `/api/health`. An API reached directly answers `/health`
    and says nothing about where a web app is."""
    base = url.rstrip("/")
    if "://" not in base:
        base = "http://" + base
    for api, web in ((base + "/api", base), (base, None)):
        with Client(api, timeout=8) as probe:
            try:
                body = probe.health()
            except ApiError:
                continue
        if isinstance(body, dict) and "ok" in body and "counts" in body:
            return api, web
    fail(f"No NeuroCode API answers at {base} (tried {base}/api/health and {base}/health).")


@app.command()
def login(
    url: Annotated[str | None, typer.Argument(help="The web app's address (e.g. http://localhost:5180) or the API's.")] = None,
    token: Annotated[str | None, typer.Option(help="Use a token made in Settings → Access tokens. `-` reads it from stdin.")] = None,
    email: Annotated[str | None, typer.Option(help="Sign in with your email and password to make a token for this machine.")] = None,
    password_stdin: Annotated[bool, typer.Option("--password-stdin", help="Read the password from stdin.")] = False,
    name: Annotated[str | None, typer.Option(help="What to call the token. Defaults to “nc on <this machine>”.")] = None,
    scope: Annotated[list[str] | None, typer.Option("--scope", help="A permission the token may use; repeat. None: all you hold except machine:access.")] = None,
    days: Annotated[int | None, typer.Option(min=1, max=366, help="Days until the token stops working. Default: until revoked.")] = None,
    web: Annotated[str | None, typer.Option(help="The web app's address, when URL is the API's own.")] = None,
    json_out: Json = False,
) -> None:
    """Sign in to a NeuroCode server. The token is kept in the OS keychain (or a 0600 file where there is none)."""
    saved = config.load()
    target = url or saved.server
    if not target:
        if not interactive():
            fail("Name the server: `nc login <url>`.")
        target = typer.prompt("NeuroCode address", default="http://localhost:5180")
    api, found_web = locate(target)
    web_url = (web or found_web or (saved.web if saved.server == api else None))

    if token == "-":
        token = sys.stdin.readline().strip()
    if token:
        with Client(api, token) as client:
            me = attempt(client.me)
        made = None
    else:
        with Client(api) as client:
            if attempt(client.status).get("needsSetup"):
                fail(f"This workspace has no Owner yet. Open {web_url or api} in a browser to set it up first.")
            who = email or (typer.prompt("Email", default=saved_email()) if interactive() else None)
            if not who:
                fail("Give --email (and --password-stdin), or --token.")
            if password_stdin:
                secret = sys.stdin.readline().rstrip("\n")
            elif interactive():
                secret = typer.prompt("Password", hide_input=True)
            else:
                fail("Give the password with --password-stdin, or use --token.")
            attempt(client.sign_in, who, secret)
            try:
                made = attempt(client.make_token, name or f"nc on {socket.gethostname()}", scope or [], days)
            finally:
                # The session was only a way to make the token; it ends here.
                try:
                    client.sign_out()
                except ApiError:
                    pass
        token = made["token"]
        with Client(api, token) as client:
            me = attempt(client.me)

    where = config.store_token(api, token)
    config.save(server=api, web=web_url, email=me["user"]["email"])
    if json_out:
        dump({"server": api, "web": web_url, "user": me["user"], "workspace": me.get("workspace"),
              "tokenStore": where, **({"token": {k: v for k, v in made.items() if k != "token"}} if made else {})})
        return
    store = ("the system keychain" if where == "keyring"
             else f"{config.home() / 'credentials.json'} (readable by you only)")
    out.print(f"[green]✓[/] Signed in to [bold]{escape(api)}[/] as {escape(me['user']['name'])} "
              f"({escape(me['user']['email'])}).")
    if made:
        scopes = ", ".join(made["scopes"]) if made["scopes"] else "everything you hold except machine:access"
        out.print(f"  Token [bold]{escape(made['name'])}[/] ({escape(made['prefix'])}…) · {escape(scopes)}"
                  + (f" · expires {when(made['expiresAt'])}" if made.get("expiresAt") else " · until revoked"))
    out.print(f"  Kept in {escape(store)}. Revoke it any time in Settings → Access tokens.")
    if not web_url:
        out.print("  [dim]`nc open` needs the web app's address: run `nc login <api> --web <url>`.[/]")


def saved_email() -> str | None:
    try:
        return json.loads((config.home() / "config.json").read_text()).get("email")
    except (OSError, ValueError):
        return None


@app.command()
def logout(json_out: Json = False) -> None:
    """Forget this machine's token for the server. It keeps working until it is revoked in Settings."""
    profile = config.load()
    if not profile.server:
        fail("Not signed in.")
    config.forget_token(profile.server)
    if json_out:
        dump({"server": profile.server, "forgotten": True})
        return
    out.print(f"[green]✓[/] Forgot the token for {escape(profile.server)}. "
              "Revoke it in Settings → Access tokens if it may have been copied anywhere.")


@app.command()
def use(project: Annotated[str, typer.Argument(help="The project the other commands default to.")],
        json_out: Json = False) -> None:
    """Choose the project `ask`, `chat`, `plan` and `open` use when --project is not given."""
    client = connect()
    found = attempt(client.project, project)
    config.save(project=found["id"])
    if json_out:
        dump(found)
        return
    out.print(f"[green]✓[/] Using [bold]{escape(found['name'])}[/] ({escape(found['id'])}).")


# ── where things stand ───────────────────────────────────────────
@app.command("status")
def status_cmd(json_out: Json = False) -> None:
    """The server, who you are here, and what is waiting on you."""
    profile = config.load()
    client = connect()
    health = attempt(client.health)
    me = attempt(client.me)
    waiting = attempt(client.approvals, pending=True, limit=100)
    runs = attempt(client.runs, limit=50)
    live = [r for r in runs if r.get("status") in ("queued", "running", "waiting")]
    if json_out:
        dump({"server": profile.server, "web": profile.web, "project": profile.project,
              "tokenStore": profile.token_store, "health": health, "user": me["user"],
              "workspace": me.get("workspace"), "approvalsPending": len(waiting),
              "runs": [{"ref": r["ref"], "status": r["status"], "projectId": r["projectId"]} for r in live]})
        return
    user = me["user"]
    workspace = (me.get("workspace") or {}).get("name")
    out.print(f"[bold]{escape(workspace or 'NeuroCode')}[/] at {escape(str(profile.server))}  "
              + ("[green]up[/]" if health.get("ok") else "[red]database unreachable[/]")
              + f" [dim]· {escape(str(health.get('db') or ''))}[/]")
    compiler = health.get("compiler") or {}
    if compiler:
        lanes = compiler.get("lanes")
        out.print(f"  Models: {escape(str(compiler.get('note') or compiler.get('model') or compiler.get('provider')))}"
                  + (f" [dim]· {lanes} lane{'s' if lanes != 1 else ''} open[/]" if isinstance(lanes, int) else ""))
    out.print(f"  You: {escape(user['name'])} ({escape(user['email'])}) · {escape(', '.join(user['roles']) or 'no role')}"
              f" · {len(user['permissions'])} permissions through this token"
              f" [dim]({'env' if profile.token_store == 'env' else profile.token_store})[/]")
    if profile.project:
        out.print(f"  Project: {escape(profile.project)}")
    out.print(f"  Waiting on a person: [yellow]{len(waiting)}[/] gate{'s' if len(waiting) != 1 else ''}"
              if waiting else "  Nothing is waiting on a person.")
    for r in live[:10]:
        out.print(f"  {escape(r['ref'])}  {status(r['status'])}  [dim]{escape(r.get('projectName') or r['projectId'])} · "
                  f"{escape((r.get('requirement') or '')[:70])}[/]")


@app.command()
def projects(json_out: Json = False) -> None:
    """Every project in the workspace."""
    client = connect()
    found = attempt(client.projects)
    if json_out:
        dump(found)
        return
    if not found:
        out.print("No projects yet. Add one in the web app: Projects → New project.")
        return
    default = config.load().project
    grid = table("", "Project", "Name", "Status", "Stack", "Tasks", "Active",
                 detail=("Stack", "Tasks", "Active"), sentence="Name")
    for p in found:
        work = p.get("work") or {}
        grid.add_row("●" if p["id"] == default else "", escape(p["id"]), escape(p["name"]), status(p.get("status")),
                     escape(", ".join(p.get("stack") or [])[:40]), str(work.get("tasks", 0)), when(p.get("lastActive")))
    out.print(grid)


@app.command("open")
def open_cmd(
    project: Annotated[str | None, typer.Argument(help="Project id. Defaults to `nc use`'s.")] = None,
    desktop: Annotated[bool | None, typer.Option("--desktop/--browser", help="The desktop app, or the browser. Default: the desktop app when it is installed.")] = None,
    json_out: Json = False,
) -> None:
    """Open the project's Workbench in the desktop app or the browser."""
    profile = config.load()
    client = connect()
    pid = project_of(client, project)
    attempt(client.project, pid)
    route = f"/workbench?project={quote(pid, safe='')}"
    app_path = desktop_app()
    use_desktop = desktop if desktop is not None else app_path is not None
    if use_desktop:
        if app_path is None:
            fail("The NeuroCode desktop app is not installed here (looked in /Applications and ~/Applications).")
        # The app's own link, not `open -a`: macOS hands a neurocode:// link to the app — starting it, or to the
        # window already open — which goes to that route, where `open -a` would only bring it to the front.
        link = "neurocode://" + route
        subprocess.Popen(["open", link], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        where = link
    else:
        if not profile.web:
            fail("The web app's address is not known. Run `nc login <web-app-url>` (or add --web <url>).")
        where = profile.web + route
        webbrowser.open(where)
    if json_out:
        dump({"project": pid, "route": route, "opened": "desktop" if use_desktop else "browser", "at": where})
        return
    out.print(f"[green]✓[/] Opened the Workbench for {escape(pid)} in "
              + ("the desktop app." if use_desktop else f"the browser: {escape(where)}"))


def desktop_app() -> Path | None:
    if platform.system() != "Darwin":
        return None
    for folder in (Path("/Applications"), Path.home() / "Applications"):
        found = folder / "NeuroCode.app"
        if (found / "Contents" / "MacOS" / "NeuroCode").exists():
            return found
    return None


# ── sessions ─────────────────────────────────────────────────────
def say_tool(message: dict[str, Any]) -> str:
    tool = str(message.get("tool") or "")
    label = TOOL_LABEL.get(tool, tool.replace("_", " "))
    ok = message.get("ok")
    mark = "[red]✗[/]" if ok is False else "[dim]·[/]"
    detail = message.get("detail") or message.get("why") or ""
    return f"{mark} [dim]{escape(label)}{(' — ' + escape(str(detail)[:120])) if detail else ''}[/]"


def show_turn(message: dict[str, Any], *, reasoning: bool) -> None:
    role = message.get("role")
    if role == "tool":
        if waiting_card(message):
            card = message.get("permission") or {}
            out.print(f"[yellow]?[/] The answer wants to use [bold]{escape(str(card.get('tool')))}[/] on "
                      f"{escape(str(card.get('subject') or ''))}" + (f" — {escape(str(card.get('why')))}" if card.get("why") else ""))
        elif message.get("tool") == "permission":
            card = message.get("permission") or {}
            out.print(f"[dim]· {escape(str(card.get('state')))} by {escape(str(card.get('decidedBy') or 'the rules'))}[/]")
        else:
            out.print(say_tool(message))
    elif role == "note":
        out.print(f"[yellow]![/] {escape(str(message.get('text') or ''))}")
    elif role == "summary":
        folded = message.get("folded") or {}
        out.print(f"[dim]· older turns folded into a summary ({folded.get('turns', 0)} turns)[/]")
    elif role == "assistant":
        if reasoning and message.get("reasoning"):
            out.print(Text(str(message["reasoning"]), style="dim italic"))
        out.print(Markdown(str(message.get("text") or "")))
        bits = [str(message[k]) for k in ("lane", "model") if message.get(k)]
        if message.get("ms"):
            bits.append(f"{message['ms'] / 1000:.1f} s")
        if bits:
            out.print(f"[dim]{escape(' · '.join(bits))}[/]")


def follow(client: Client, listener: Listener, talk: Conversation, *, reasoning: bool, quiet: bool) -> None:
    """Show the answer as it is written, until it is over. `quiet` shows nothing (for --json)."""
    shown: set[tuple[int, str]] = set()
    live: Live | None = None

    def close_live() -> None:
        nonlocal live
        if live is not None:
            live.stop()
            live = None

    def take(update: Update | None) -> None:
        nonlocal live
        if update is None or quiet:
            return
        if update.kind == "stream" and update.pending is not None:
            p = update.pending
            if not out.is_terminal:
                return
            view = Markdown(p.answer) if p.answer else Text(
                f"thinking… {p.ms / 1000:.0f} s" + (f"\n{p.reasoning[-400:]}" if reasoning and p.reasoning else ""),
                style="dim")
            if live is None:
                live = Live(view, console=out, refresh_per_second=12, transient=True)
                live.start()
            else:
                live.update(view)
            return
        if update.message is not None:
            m = update.message
            key = (int(m["id"]), str((m.get("permission") or {}).get("state") or ""))
            if key in shown or m.get("role") == "you":
                return
            shown.add(key)
            close_live()
            show_turn(m, reasoning=reasoning)

    quiet_since = began = time.monotonic()
    try:
        while not talk.answered():
            try:
                event = listener.events.get(timeout=1.0)
            except queue.Empty:
                event = None
            if event is not None and event.kind == "error":
                # The stream went away; the session itself is still there to read, and is read each second.
                for update in attempt(talk.catch_up):
                    take(update)
                if not talk.answered():
                    time.sleep(1.0)
                    listener.events.put(event)
                continue
            if event is not None:
                update = talk.feed(event)
                take(update)
                if update is not None:
                    quiet_since = time.monotonic()
                continue
            if time.monotonic() - quiet_since > 4:
                # Quiet for a while: read what the stream may have missed, and whether the answer is over.
                for update in attempt(talk.catch_up):
                    take(update)
                quiet_since = time.monotonic()
                # Idle with no answer: stopped, or the answer ended without a word. Nothing more is coming.
                if talk.status == "idle" and not talk.answered() and (talk.after() or time.monotonic() - began > 15):
                    break
    finally:
        close_live()


def permit_prompt(client: Client, talk: Conversation, card: dict[str, Any]) -> bool:
    """Ask the person at the terminal. True when they answered and the answer goes on."""
    choice = typer.prompt("Allow it? [o]nce, for this [s]ession, or [r]efuse", default="r").strip().lower()[:1]
    decision = {"o": "once", "s": "session", "r": "refuse"}.get(choice)
    if decision is None:
        return False
    attempt(client.permit, talk.ref, int(card["id"]), decision)
    return True


def continue_session(client: Client, project: str | None) -> dict[str, Any]:
    """The session `--continue` continues: the one this machine was last in for this project, else the
    newest the server holds.

    Which one it chose, and when it was last spoken to, is always said. Resuming silently is how a
    question lands in a week-old conversation and is answered from the wrong context."""
    pid = project_of(client, project)
    remembered = config.last_session(config.load().server, pid)
    found: dict[str, Any] | None = None
    if remembered:
        try:
            found = client.session(remembered)
        except ApiError:
            found = None                             # gone, or not this person's: fall back to the newest
    where = "the one you were last in"
    if found is None:
        newest = attempt(client.sessions, pid, limit=1)
        if not newest:
            fail(f"There is no session to continue in {pid}. Start one: `nc ask \"…\"`.", 2)
        found = attempt(client.session, newest[0]["ref"])
        where = "the newest one here"
    err.print(f"[dim]Continuing {escape(found['ref'])} — {where}, last spoken to "
              f"{escape(when(found.get('lastAt')))}.[/]")
    return found


def question_text(question: str | None, *, missing: str = "Ask something: `nc ask \"…\"`, or pipe it "
                                                          "in (`nc ask < notes.md`).") -> str:
    """The question: the argument, what was piped in, or both — the argument, a blank line, then the
    piped text under a marker, so the model is never shown a wall of log with no idea why."""
    extra = piped()
    if extra is None:
        if not (question or "").strip():
            fail(missing, 2)
        return str(question)
    if not (question or "").strip():
        return extra
    err.print(f"[dim]Sent {kb(len(extra.encode('utf-8', 'replace')))} from stdin with the question.[/]")
    return f"{question}\n\n--- piped input ---\n{extra}"


@app.command()
def ask(
    question: Annotated[str | None, typer.Argument(help="What to ask. Left out, what is piped in is the question.")] = None,
    project: ProjectOpt = None,
    session: Annotated[str | None, typer.Option("--session", "-s", help="Ask in this session (SES-…) instead of a new one.")] = None,
    keep_going: Annotated[bool, typer.Option("--continue", "-c", help="Carry on the session you were last in.")] = False,
    reasoning: Annotated[bool, typer.Option(help="Show the model's reasoning, when its lane returns it.")] = False,
    on_permission: Annotated[str, typer.Option("--on-permission", help="In a script, what a permission card does: wait (exit 3, the card left for a person) or refuse.")] = "wait",
    json_out: Json = False,
) -> None:
    """Ask a question and watch the answer being written. Permission cards are asked here.

    The question can come in on stdin: `nc ask < bug-report.md`, or `cat trace.log | nc ask "why?"`."""
    if on_permission not in ("wait", "refuse"):
        fail("--on-permission is wait or refuse. There is no flag that allows a tool for you.", 2)
    client = connect()
    text = question_text(question)
    if session:
        started = attempt(client.session, session)
    elif keep_going:
        started = continue_session(client, project)
    else:
        started = attempt(client.start_session, project_of(client, project), text[:80])
    ref = started["ref"]
    config.remember_session(config.load().server, started.get("projectId"), ref)
    listener = Listener(client).start()
    try:
        asked = attempt(client.ask, ref, text)
        talk = Conversation(client, ref, since=int(asked["message"]["id"]))
        if not json_out:
            out.print(f"[dim]{escape(ref)} · {escape(started.get('projectName') or started.get('projectId') or '')}[/]")
        while True:
            follow(client, listener, talk, reasoning=reasoning, quiet=json_out)
            card = talk.waiting()
            if card is None:
                break
            if on_permission == "refuse":
                # Asked for by name, in a script: the tool is refused and the answer finishes without
                # it. Deterministic, and the session records plainly that it was refused.
                attempt(client.permit, ref, int(card["id"]), "refuse")
                if not json_out:
                    out.print("[dim]· refused (--on-permission refuse) — the answer goes on without it[/]")
                talk.since = talk.last_id
                continue
            if json_out or not interactive() or not permit_prompt(client, talk, card):
                if json_out:
                    dump({"session": attempt(client.session, ref, after=0) | {"messages": talk.after()},
                          "waitingOn": card})
                else:
                    err.print(f"[yellow]Waiting on you.[/] Answer it with `nc chat --session {ref}`, "
                              f"or in the web app's Sessions screen.")
                raise typer.Exit(WAITING)
            talk.since = talk.last_id
    finally:
        listener.stop()
    if json_out:
        found = attempt(client.session, ref, after=int(asked["message"]["id"]) - 1)
        dump({"session": {k: v for k, v in found.items() if k != "messages"}, "question": asked["message"],
              "messages": [m for m in found.get("messages", []) if m["id"] > asked["message"]["id"]]})


@app.command()
def sessions(project: ProjectOpt = None, limit: Annotated[int, typer.Option(min=1, max=100)] = 20,
             offset: Annotated[int, typer.Option(min=0, help="Skip this many; with --limit, the next page.")] = 0,
             json_out: Json = False) -> None:
    """Recent sessions, newest first."""
    client = connect()
    found = attempt(client.sessions, project or config.load().project, limit=limit, offset=offset)
    whole_page(found, limit)
    if json_out:
        dump(found)
        return
    if not found:
        out.print("No sessions yet. Start one with `nc chat` or `nc ask \"…\"`.")
        return
    grid = table("Session", "Project", "Title", "Turns", "Status", "Last",
                 detail=("Project", "Turns"), sentence="Title")
    for s in found:
        state = "waiting on you" if s.get("waitingOn") else s.get("status")
        grid.add_row(escape(s["ref"]), escape(s.get("projectName") or s["projectId"]), escape((s.get("title") or "")[:48]),
                     str(s.get("turns", 0)), status(state) if state != "waiting on you" else "[yellow]waiting on you[/]",
                     when(s.get("lastAt")))
    out.print(grid)


@app.command()
def chat(project: ProjectOpt = None,
         session: Annotated[str | None, typer.Option("--session", "-s", help="Open this session (SES-…).")] = None,
         keep_going: Annotated[bool, typer.Option("--continue", "-c", help="Open the session you were last in.")] = False,
         json_out: Json = False) -> None:
    """A full-screen session: streaming answers, reasoning folded, tool calls, permission prompts, @ and /."""
    client = connect()
    if keep_going and not session:
        session = continue_session(client, project)["ref"]
    if json_out:
        # A full-screen app has nothing to print; --json says which session it would open, for scripts.
        ref = session or attempt(client.start_session, project_of(client, project), "")["ref"]
        found = attempt(client.session, ref)
        config.remember_session(config.load().server, found.get("projectId"), ref)
        dump(found)
        return
    if not interactive():
        fail("`nc chat` needs a terminal. Use `nc ask` in scripts.")
    from .tui import ChatApp

    pid = None if session else project_of(client, project)
    if session:
        # Read once before the screen opens, so `--continue` next time knows which project it was in.
        config.remember_session(config.load().server, attempt(client.session, session).get("projectId"), session)
    ChatApp(client, project=pid, session=session).run()


# ── plans ────────────────────────────────────────────────────────
@app.command()
def plan(requirement: Annotated[str | None, typer.Argument(help="What you want built, in your words.")] = None,
         from_: Annotated[str | None, typer.Option("--from", help="Read the requirement from a file, or `-` for stdin.")] = None,
         project: ProjectOpt = None, json_out: Json = False) -> None:
    """Compile a requirement into a plan (steps, risk, open questions). It is not dispatched.

    The requirement can come from a file: `nc plan --from requirement.md`, or `--from -` for stdin."""
    client = connect()
    written = from_file(from_) if from_ else question_text(
        requirement, missing="Say what you want built: `nc plan \"…\"`, `--from requirement.md`, or pipe it in.")
    pid = project_of(client, project)
    if not json_out:
        err.print(f"[dim]Compiling for {escape(pid)}…[/]")
    made = attempt(client.compile, pid, written)
    if json_out:
        dump(made)
        return
    print_plan(made)
    out.print()
    out.print(f"[dim]Dispatch it with `nc dispatch {escape(made['ref'])}` once the questions are settled.[/]")


@app.command()
def plans(project: ProjectOpt = None, limit: Annotated[int, typer.Option(min=1, max=100)] = 20,
          offset: Annotated[int, typer.Option(min=0, help="Skip this many; with --limit, the next page.")] = 0,
          json_out: Json = False) -> None:
    """Recent plans, newest first. One in full: `nc show-plan <ref>`."""
    client = connect()
    found = attempt(client.plans, project or config.load().project, limit=limit, offset=offset)
    whole_page(found, limit)
    if json_out:
        dump(found)
        return
    if not found:
        out.print("No plans yet. Make one with `nc plan \"<requirement>\"`.")
        return
    grid = table("Plan", "Project", "Status", "Risk", "Steps", "Open ?", "Requirement",
                 detail=("Project", "Risk", "Steps"), sentence="Requirement")
    for p in found:
        grid.add_row(escape(p["ref"]), escape(p["projectId"]), status(p.get("status")), escape(str(p.get("risk") or "—")),
                     str(len(p.get("steps") or [])), str(len(p.get("openQuestions") or [])),
                     escape((p.get("rawRequirement") or "")[:60]))
    out.print(grid)


@app.command("show-plan")
def show_plan(ref: Annotated[str, typer.Argument(help="PLAN-…")], json_out: Json = False) -> None:
    """One plan, in full."""
    client = connect()
    found = attempt(client.plan, ref)
    if json_out:
        dump(found)
        return
    print_plan(found)


@app.command("plan-answer")
def plan_answer(ref: Annotated[str, typer.Argument(help="PLAN-…")],
                n: Annotated[int, typer.Argument(min=1, help="Which open question, as `nc show-plan` numbers them.")],
                answer: Annotated[str | None, typer.Argument(help="Your answer. `-` reads it from stdin.")] = None,
                json_out: Json = False) -> None:
    """Answer one of a plan's open questions, here, rather than in the browser.

    The answer is kept on the plan and becomes a business rule the workspace remembers, so the next
    plan on this project is compiled knowing it — the same thing the Plans screen does."""
    client = connect()
    if answer == "-":
        text = from_file("-")
    elif (answer or "").strip():
        text = str(answer)
    else:
        fail("Give the answer: `nc plan-answer PLAN-3 1 \"…\"`, or `-` to read it from stdin.", 2)
    before = attempt(client.plan, ref)
    asked = (before.get("openQuestions") or [])
    # The index the route takes counts the open questions from zero; a person counts from one. An n
    # with no question behind it is the API's own refusal, not a guess made here.
    settled = attempt(client.answer_question, ref, n - 1, text.strip())
    if json_out:
        dump(settled)
        return
    if 0 <= n - 1 < len(asked):
        out.print(f"[green]✓[/] [strike dim]{escape(str(asked[n - 1]))}[/]")
        out.print(f"  {escape(text.strip())}")
    left = len(settled.get("openQuestions") or [])
    out.print(f"[dim]{left} open question{'s' if left != 1 else ''} left.[/]")
    out.print()
    print_plan(settled)


@app.command()
def dispatch(ref: Annotated[str, typer.Argument(help="PLAN-…")],
             goal: Annotated[int | None, typer.Option(min=1, max=5, help="Run until done: attempts in all.")] = None,
             step_gate: Annotated[bool, typer.Option("--step-gate", help="Pause for your approval before each step.")] = False,
             json_out: Json = False) -> None:
    """Hand a plan to the agents. Follow it with `nc runs <ref> --watch`."""
    client = connect()
    made = attempt(client.dispatch, ref, goal_budget=goal, step_gate=step_gate)
    if json_out:
        dump(made)
        return
    run_ref = made.get("runRef")
    out.print(f"[green]✓[/] {escape(ref)} dispatched" + (f" as {escape(run_ref)}. Watch it: `nc runs {escape(run_ref)} --watch`"
                                                        if run_ref else "."))


# ── gates ────────────────────────────────────────────────────────
@app.command()
def approvals(all_: Annotated[bool, typer.Option("--all", help="Decided ones too, newest first.")] = False,
              limit: Annotated[int, typer.Option(min=1, max=500)] = 50,
              offset: Annotated[int, typer.Option(min=0, help="Skip this many; with --limit, the next page.")] = 0,
              json_out: Json = False) -> None:
    """What is waiting on a person: tests to run, a signature, a command, a question."""
    client = connect()
    found = attempt(client.approvals, pending=not all_, limit=limit, offset=offset)
    whole_page(found, limit)
    if json_out:
        dump(found)
        return
    if not found:
        out.print("Nothing is waiting on a person." if not all_ else "No gates yet.")
        return
    grid = table("Gate", "Kind", "Risk", "Status", "Title", "Run", "Asked",
                 detail=("Kind", "Risk", "Asked"), sentence="Title")
    for a in found:
        grid.add_row(escape(a["ref"]), escape(a.get("kind") or ""), escape(str(a.get("risk") or "")), status(a.get("status")),
                     escape((a.get("title") or "")[:60]), escape(a.get("runRef") or ""), when(a.get("requestedAt")))
    out.print(grid)
    out.print("[dim]Answer with `nc approve <gate>` or `nc deny <gate>`. A run's signature reads better "
              "as `nc review <run>`.[/]")


def _decide(ref: str, decision: str, scope: str | None, answer: str | None, json_out: bool) -> None:
    client = connect()
    done = attempt(client.decide, ref, decision, scope=scope, answer=answer)
    if json_out:
        dump(done)
        return
    word = "Approved" if decision == "approve" else "Denied"
    out.print(f"[green]✓[/] {word} {escape(ref)}" + (f" ({escape(scope)})" if scope else "")
              + (f" — {escape(done['runRef'])} carries on." if done.get("runRef") and decision == "approve" else "."))


@app.command()
def approve(ref: Annotated[str, typer.Argument(help="The gate (APR-…).")],
            scope: Annotated[str | None, typer.Option(help="For a command or edit: once | run | project.")] = None,
            answer: Annotated[str | None, typer.Option(help="For an agent's question: your answer.")] = None,
            json_out: Json = False) -> None:
    """Approve a gate. A decision is final."""
    if scope is not None and scope not in ("once", "run", "project"):
        fail("--scope is once, run or project.", 2)
    _decide(ref, "approve", scope, answer, json_out)


@app.command()
def deny(ref: Annotated[str, typer.Argument(help="The gate (APR-…).")], json_out: Json = False) -> None:
    """Deny a gate. A decision is final."""
    _decide(ref, "deny", None, None, json_out)


# ── runs ─────────────────────────────────────────────────────────
def show_run(run: dict[str, Any]) -> None:
    tests = run.get("tests") or {}
    diff = run.get("diff") or {}
    out.print(f"[bold]{escape(run['ref'])}[/]  {status(run.get('status'))}  [dim]{escape(run.get('projectName') or run['projectId'])}"
              f" · {escape(run.get('branch') or '')}[/]")
    if run.get("requirement"):
        out.print(escape(run["requirement"]))
    for s in run.get("steps") or []:
        out.print(f"  {status(s.get('status'))} {escape(str(s.get('label') or s.get('kind') or ''))}")
    if diff.get("files") is not None:
        out.print(f"  diff: {diff.get('files')} files, +{diff.get('insertions')} −{diff.get('deletions')}")
    if tests.get("status"):
        out.print(f"  tests: {status(tests['status'])} {escape(str(tests.get('summary') or ''))}")
    if run.get("waitingOn"):
        out.print(f"  [yellow]waiting on {escape(str(run['waitingOn']))}[/] — "
                  f"`nc review {escape(run['ref'])}`")
    if run.get("note"):
        out.print(f"  [dim]{escape(run['note'])}[/]")


# ── reading the change, and answering it ─────────────────────────
def parked_run(client: Client, project: str | None) -> str:
    """The run a bare `nc diff` means: the one in this project stopped waiting for a person.

    More than one, and naming it is the person's job. Guessing is how a diff gets read for one run and
    a signature given to another."""
    pid = project or config.load().project
    waiting = [r for r in attempt(client.runs, pid, limit=100) if r.get("status") == "waiting"]
    if not waiting:
        fail("Name a run: `nc diff RUN-7`. Nothing is waiting for you"
             + (f" in {pid}" if pid else "") + ".", 2)
    if len(waiting) > 1:
        fail("More than one run is waiting for you — name one: "
             + ", ".join(str(r["ref"]) for r in waiting[:10]), 2)
    return str(waiting[0]["ref"])


@app.command("diff")
def diff_cmd(
    ref: Annotated[str | None, typer.Argument(help="RUN-…. Left out: the one run here that waits on you.")] = None,
    project: ProjectOpt = None,
    file: Annotated[list[str] | None, typer.Option("--file", help="Only the files matching this glob; repeat it.")] = None,
    stat: Annotated[bool, typer.Option("--stat", help="The files and their counts only, counted from the patch.")] = False,
    raw: Annotated[bool, typer.Option("--raw", help="The patch exactly as it is: `nc diff RUN-7 --raw | git apply --3way`.")] = False,
    json_out: Json = False,
) -> None:
    """A run's change, read here — the same patch the reviewer was fingerprinted against.

    Piped anywhere, it is the patch itself and nothing else, so `git apply` takes it unchanged."""
    client = connect()
    ref = ref or parked_run(client, project)
    answer = attempt(client.diff, ref)
    if json_out:
        dump(answer)
        return
    run = attempt(client.run, ref)
    if answer.get("gone"):
        fail(f"The worktree was removed, so there is no diff to show for {ref}.")
    patch = str(answer.get("patch") or "")
    sections = patch_sections(patch)
    shown = sections
    if file:
        shown = [(path, body) for path, body in sections if any(fnmatch(path, glob) for glob in file)]
        if not shown:
            fail(f"No file in {ref}'s diff matches " + ", ".join(file)
                 + f" ({len(sections)} file{'s' if len(sections) != 1 else ''} changed).")
        patch = "".join(body for _, body in shown)

    def cut() -> None:
        if answer.get("truncated"):
            err.print(f"[yellow]Cut at {kb(len(str(answer.get('patch') or '')))}.[/] The rest is in the "
                      f"worktree: {escape(str(run.get('worktree') or ''))}")

    # Not a terminal — a pipe, a file, `git apply` — or asked for raw: the bytes, and nothing of ours.
    # --stat is asked for by name, so it is answered even into a pipe; --raw always wins over it.
    if raw or (not out.is_terminal and not stat):
        sys.stdout.write(patch)
        cut()
        return
    count = answer.get("stat") or {}
    commits = count.get("commits")
    out.print(f"[bold]{escape(ref)}[/] · {escape(str(run.get('branch') or ''))} · "
              f"{count.get('files')} files [green]+{count.get('insertions')}[/] [red]−{count.get('deletions')}[/]"
              + (f" · {commits} commit{'s' if commits != 1 else ''}" if commits is not None else ""))
    if file:
        out.print(f"[dim]{len(shown)} of {len(sections)} files shown[/]")
    if stat or out.width < 60:
        # Under 60 columns a patch line is unreadable anyway: the files, and how to open one.
        print_patch_files(shown)
        if not stat:
            out.print(f"[dim]Too narrow for the patch — `nc diff {escape(ref)} --file <path>`[/]")
        cut()
        return
    height = out.size.height or 24
    if sys.stdout.isatty() and patch.count("\n") > height:
        os.environ.setdefault("PAGER", "less -R")    # the patch keeps its colours in the pager
        with out.pager(styles=True):
            print_patch(patch)
    else:
        print_patch(patch)
    cut()


def gate_of(client: Client, run: dict[str, Any], *, pending: bool) -> dict[str, Any] | None:
    """The run's signature: the gate raised at its handoff step — the same one the Review screen looks
    for, so the terminal and the browser can never disagree about which gate a signature is."""
    handoff = next((s for s in run.get("steps") or [] if s.get("kind") == "handoff"), None)
    if handoff is None:
        return None
    found = attempt(client.approvals, pending=pending, limit=200)
    return next((a for a in found if a.get("runRef") == run.get("ref") and a.get("step") == handoff.get("n")
                 and (not pending or a.get("status") == "pending")), None)


#: How loud a finding is. The severities the reviewer may write are fixed (`clean_findings` on the
#: server keeps only these three), so an unknown one is printed plainly rather than guessed at.
SEVERITY = {"HIGH": "red", "MEDIUM": "yellow", "LOW": "dim"}


def show_review(run: dict[str, Any], gate: dict[str, Any] | None) -> None:
    """What the reviewer found, in front of the person who is about to sign it."""
    review = run.get("review") or {}
    diff = run.get("diff") or {}
    out.print(f"[bold]{escape(run['ref'])}[/]  {status(run.get('status'))}  "
              f"[dim]{escape(run.get('projectName') or run['projectId'])} · {escape(run.get('branch') or '')}[/]")
    if run.get("requirement"):
        out.print(escape(str(run["requirement"])))
    out.print()
    # The reviewer's own words, softened for nobody: "offline rules" means no lane could answer, and
    # saying "reviewed" without saying by what is how a rules-read diff gets mistaken for a read one.
    if review.get("by") or review.get("verdict"):
        out.print(f"[dim]read by[/] {escape(str(review.get('by') or 'nobody yet'))}")
        if review.get("verdict"):
            out.print(f"  {escape(str(review['verdict']))}")
    findings = review.get("findings") or []
    if findings:
        out.print()
        grid = table("Severity", "Where", "What", sentence="What")
        for f in findings:
            where = str(f.get("file") or "")
            if f.get("line"):
                where += f":{f['line']}"
            severity = str(f.get("severity") or "")
            style = SEVERITY.get(severity.upper(), "")
            grid.add_row(f"[{style}]{escape(severity)}[/]" if style else escape(severity),
                         escape(where), escape(str(f.get("note") or "")))
        out.print(grid)
    else:
        out.print("[dim]No findings.[/]")
    checks = run.get("checks") or []
    if checks:
        out.print()
        for c in checks:
            out.print(f"  {status(str(c.get('status') or ''))} {escape(str(c.get('name') or ''))}"
                      f" [dim]{escape(str(c.get('summary') or ''))}[/]")
    goal = run.get("goal") or {}
    if goal.get("verdict"):
        out.print(f"  goal: {status(str(goal['verdict']))} [dim]{escape(str(goal.get('why') or ''))}[/]")
    receipt = (review.get("receipt") or {}).get("sha256") or ""
    if receipt:
        out.print(f"[dim]receipt {escape(receipt.removeprefix('sha256:')[:12])} · "
                  f"{diff.get('files')} files +{diff.get('insertions')} −{diff.get('deletions')}[/]")
    elif diff.get("files") is not None:
        out.print(f"[dim]{diff.get('files')} files +{diff.get('insertions')} −{diff.get('deletions')}[/]")
    out.print()
    if gate is not None and gate.get("status") == "pending":
        out.print(f"[yellow]Waiting for your signature.[/] Read it first: `nc diff {escape(run['ref'])}`")
        out.print(f"  `nc accept {escape(run['ref'])}`, or "
                  f"`nc send-back {escape(run['ref'])} --notes \"…\"`")
    elif gate is not None:
        out.print(f"[dim]{escape(str(gate.get('status')))} by {escape(str(gate.get('decidedBy') or 'a person'))} "
                  f"· {escape(when(gate.get('decidedAt')))}[/]")


@app.command()
def review(ref: Annotated[str, typer.Argument(help="RUN-…")],
           again: Annotated[bool, typer.Option("--again", help="Read the diff again, as the branch is now.")] = False,
           json_out: Json = False) -> None:
    """What the review found, and the two answers that end a run. Exit 3 while it waits on you."""
    client = connect()
    if again:
        # Said before it is asked for, not after: a re-read is a model call, on a lane other than the
        # one that wrote the code, and it renews the receipt a merge is checked against.
        err.print("[dim]Reading it again costs one model call, on a lane other than the writer's.[/]")
        run = attempt(client.review_again, ref)
    else:
        run = attempt(client.run, ref)
    gate = gate_of(client, run, pending=False)
    if json_out:
        dump({"run": {k: v for k, v in run.items() if k != "logs"}, "gate": gate})
    else:
        show_review(run, gate)
    if gate is not None and gate.get("status") == "pending":
        raise typer.Exit(WAITING)


@app.command()
def accept(ref: Annotated[str, typer.Argument(help="RUN-…")], json_out: Json = False) -> None:
    """Sign a run's diff: approve the signature it is waiting at. Read it first with `nc diff`."""
    client = connect()
    run = attempt(client.run, ref)
    gate = gate_of(client, run, pending=True)
    if gate is None:
        # Never invent a gate: say where the run actually is, and let the person go there.
        where = f"{ref} is {run.get('status')}"
        if run.get("waitingOn"):
            where += f", waiting at {run['waitingOn']} — not at its signature (`nc approvals`)"
        fail(f"{where}. There is no signature of yours waiting on it.")
    done = attempt(client.decide, gate["ref"], "approve")
    if json_out:
        dump({"approval": done, "run": attempt(client.run, ref)})
        return
    out.print(f"[green]✓[/] Accepted {escape(ref)} — signed {escape(gate['ref'])}.")
    out.print(f"  [dim]Merge it: `nc merge {escape(ref)}` · push the branch: `nc push {escape(ref)}` · "
              f"open the request: `nc pr {escape(ref)}`[/]")


@app.command("send-back")
def send_back(ref: Annotated[str, typer.Argument(help="RUN-…")],
              notes: Annotated[str, typer.Option("--notes", help="What was wrong, in your words. `-` reads it from stdin.")],
              json_out: Json = False) -> None:
    """Send a run back: the same plan again, as a new run told your notes and the review's findings."""
    client = connect()
    text = from_file("-") if notes == "-" else notes
    if not text.strip():
        fail("Say what was wrong: --notes \"…\".", 2)
    if not json_out:
        # Before it is dispatched, not after: the new run costs what the plan costs.
        err.print(f"[dim]Sending {escape(ref)} back starts a new run, which costs what the plan costs.[/]")
    made = attempt(client.rework, ref, text.strip())
    if json_out:
        dump(made)
        return
    out.print(f"[green]✓[/] {escape(ref)} sent back as {escape(str(made.get('ref')))}"
              + (" — the signature it waited for was refused." if made.get("ref") else "."))
    out.print(f"  [dim]Watch it: `nc runs {escape(str(made.get('ref')))} --watch`[/]")


@app.command()
def stop(ref: Annotated[str, typer.Argument(help="RUN-…")], json_out: Json = False) -> None:
    """Stop a run. Its worktree stays where it is, for you to look at."""
    client = connect()
    stopped = attempt(client.cancel_run, ref)
    if json_out:
        dump(stopped)
        return
    out.print(f"[green]✓[/] {escape(ref)} is {status(stopped.get('status'))}. "
              f"[dim]Its worktree is still at {escape(str(stopped.get('worktree') or ''))}.[/]")


@app.command()
def discard(ref: Annotated[str, typer.Argument(help="RUN-…")], json_out: Json = False) -> None:
    """Remove a stopped run's worktree and branch. The run and its log stay."""
    client = connect()
    removed = attempt(client.discard, ref)
    if json_out:
        dump(removed)
        return
    out.print(f"[green]✓[/] {escape(ref)}'s worktree and branch are gone. The run and its log stay.")


@app.command()
def revert(ref: Annotated[str, typer.Argument(help="RUN-…")],
           n: Annotated[int, typer.Argument(min=0, help="Go back to how the branch stood after this step.")],
           redo: Annotated[bool, typer.Option("--redo", help="Run the later steps again from step n + 1.")] = False,
           json_out: Json = False) -> None:
    """Take a run's own branch back to a step of its own — never your checkout."""
    client = connect()
    back = attempt(client.revert, ref, n, redo=redo)
    if json_out:
        dump(back)
        return
    out.print(f"[green]✓[/] {escape(ref)} is back to how it stood after step {n}"
              + (" — the later steps run again." if redo else ", and the later steps are taken back."))


@app.command()
def runs(ref: Annotated[str | None, typer.Argument(help="A run (RUN-…) for its detail and log.")] = None,
         project: ProjectOpt = None,
         watch: Annotated[bool, typer.Option("--watch", "-w", help="Follow the log live until the run stops.")] = False,
         timeout: Annotated[int, typer.Option(min=0, help="With --watch: give up after this many seconds (exit 4). 0 waits for ever.")] = 0,
         limit: Annotated[int, typer.Option(min=1, max=100)] = 20,
         offset: Annotated[int, typer.Option(min=0, help="Skip this many; with --limit, the next page.")] = 0,
         json_out: Json = False) -> None:
    """Agent runs. With a run: its steps and log; with --watch, the log as it is written."""
    client = connect()
    if ref is None:
        if watch:
            watch_all(client, project, json_out)
            return
        found = attempt(client.runs, project or config.load().project, limit=limit, offset=offset)
        whole_page(found, limit)
        if json_out:
            dump(found)
            return
        if not found:
            out.print("No runs yet. Dispatch a plan with `nc dispatch <plan>`.")
            return
        grid = table("Run", "Project", "Status", "Agent", "Branch", "Started", "Requirement",
                     detail=("Project", "Agent", "Branch", "Started"), sentence="Requirement")
        for r in found:
            grid.add_row(escape(r["ref"]), escape(r["projectId"]), status(r.get("status")), escape(r.get("agent") or r.get("role") or ""),
                         escape(r.get("branch") or ""), when(r.get("startedAt")), escape((r.get("requirement") or "")[:50]))
        out.print(grid)
        return

    listener = Listener(client).start() if watch else None
    try:
        found = attempt(client.run, ref)
        if not watch:
            if json_out:
                dump(found)
                return
            show_run(found)
            for line in found.get("logs") or []:
                out.print(log_line(line))
            return
        watch_one(client, listener, found, json_out, timeout=timeout)
    finally:
        if listener is not None:
            listener.stop()


def watch_one(client: Client, listener: Listener | None, found: dict[str, Any], json_out: bool,
              *, timeout: int = 0) -> None:
    """The run's log from the start, then live until the run stops or waits on a person. With --json,
    one JSON object per line (`{"kind": "log"|"run", …}`), which is what a script can follow.

    `timeout` is what a CI job needs and a person does not: without a ceiling a wedged run holds the
    runner open until something else kills it. Giving up is not stopping the run — it says so, and the
    run carries on where it is."""
    assert listener is not None
    ref = found["ref"]
    last = 0
    state = found.get("status")

    def emit_line(line: dict[str, Any]) -> None:
        nonlocal last
        if int(line["id"]) <= last:
            return
        last = int(line["id"])
        if json_out:
            dump_line({"kind": "log", "runRef": ref, **line})
        else:
            out.print(log_line(line))

    if not json_out:
        out.print(f"[bold]{escape(ref)}[/] {status(state)} [dim]{escape(found.get('requirement') or '')[:80]}[/]")
    for line in found.get("logs") or []:
        emit_line(line)
    quiet_since = began = time.monotonic()
    while state not in FINISHED and state != "waiting":
        if timeout and time.monotonic() - began > timeout:
            fresh = attempt(client.run, ref, after=last)
            for line in fresh.get("logs") or []:
                emit_line(line)
            err.print(f"[yellow]Timed out after {timeout} s[/] — {escape(ref)} is still "
                      f"{escape(str(fresh.get('status')))}. Nothing was stopped.")
            raise typer.Exit(TIMED_OUT)
        try:
            event = listener.events.get(timeout=1.0)
        except queue.Empty:
            event = None
        if event is not None and event.kind == "run" and event.data.get("runRef") == ref:
            emit_line(event.data)
            quiet_since = time.monotonic()
        elif event is not None and event.kind == "change" and event.data.get("collection") == "runs" \
                and (event.data.get("doc") or {}).get("ref") == ref:
            state = event.data["doc"].get("status")
        elif time.monotonic() - quiet_since > 5 or (event is not None and event.kind == "error"):
            fresh = attempt(client.run, ref, after=last)
            for line in fresh.get("logs") or []:
                emit_line(line)
            state = fresh.get("status")
            quiet_since = time.monotonic()
    final = attempt(client.run, ref, after=last)
    for line in final.get("logs") or []:
        emit_line(line)
    if json_out:
        dump_line({"kind": "run", **{k: v for k, v in final.items() if k != "logs"}})
    else:
        out.print()
        show_run(final)
    if final.get("status") == "waiting":
        raise typer.Exit(WAITING)
    if final.get("status") in ("failed", "cancelled"):
        raise typer.Exit(1)


def watch_all(client: Client, project: str | None, json_out: bool) -> None:
    """Every run's log lines as they are written, until Ctrl-C."""
    listener = Listener(client).start()
    wanted = project or config.load().project
    names: dict[str, str] = {}
    if not json_out:
        out.print("[dim]Following every run" + (f" in {escape(wanted)}" if wanted else "") + " — Ctrl-C to stop.[/]")
    try:
        while True:
            try:
                event = listener.events.get(timeout=1.0)
            except queue.Empty:
                continue
            if event.kind == "error":
                fail(str(event.data))
            if event.kind == "change" and event.data.get("collection") == "runs":
                doc = event.data.get("doc") or {}
                names[doc.get("ref", "")] = doc.get("projectId", "")
                if wanted and doc.get("projectId") != wanted:
                    continue
                if json_out:
                    dump_line({"kind": "run", "ref": doc.get("ref"), "status": doc.get("status")})
                else:
                    out.print(f"[bold]{escape(str(doc.get('ref')))}[/] {status(doc.get('status'))}")
            elif event.kind == "run":
                ref = event.data.get("runRef", "")
                if wanted and names.get(ref, wanted) != wanted:
                    continue
                if json_out:
                    dump_line({"kind": "log", **event.data})
                else:
                    out.print(f"[dim]{escape(ref)}[/] {log_line(event.data)}")
    except KeyboardInterrupt:
        pass
    finally:
        listener.stop()


def dump_line(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False), flush=True)


@app.command()
def merge(ref: Annotated[str, typer.Argument(help="An accepted run (RUN-…).")], json_out: Json = False) -> None:
    """Merge an accepted run into the branch the repository has checked out. Prints the undo."""
    client = connect()
    done = attempt(client.merge, ref)
    if json_out:
        dump(done)
        return
    if done.get("merged"):
        out.print(f"[green]✓[/] Merged {escape(ref)} into {escape(str(done.get('into')))} at {escape(str(done.get('commit'))[:10])}.")
        if done.get("undo"):
            out.print(f"  [dim]Undo: {escape(str(done['undo']))}[/]")
    else:
        collided = done.get("conflicts") or []
        fail(f"Nothing was merged: {len(collided)} file{'s' if len(collided) != 1 else ''} collide with "
             f"{done.get('into')}: " + ", ".join(str(c) for c in collided[:8]))


@app.command()
def push(ref: Annotated[str, typer.Argument(help="An accepted run (RUN-…).")],
         remote: Annotated[str | None, typer.Option(help="Which remote. Default: origin, or the only one.")] = None,
         json_out: Json = False) -> None:
    """Push an accepted run's branch with your git credentials (never forced). Then `nc pr` opens the request."""
    client = connect()
    done = attempt(client.push, ref, remote)
    if json_out:
        dump(done)
        return
    pushed = done.get("pushed") or {}
    out.print(f"[green]✓[/] Pushed {escape(str(done.get('branch')))} to {escape(str(pushed.get('remote')))}.")
    if pushed.get("compareUrl"):
        out.print(f"  [dim]Open the request from here: `nc pr {escape(ref)}` · or in a browser: "
                  f"{escape(pushed['compareUrl'])}[/]")


def _say_request(made: dict[str, Any], compare: str | None, why: str) -> None:
    """One request, in the forge's own words. Nothing is filled in when the forge said nothing: a run
    with no request says so and hands back the link, which is what there was before this existed."""
    if not made:
        out.print(why or "No request has been opened for this branch yet.")
        if compare:
            out.print(f"  [dim]In a browser: {escape(compare)}[/]")
        return
    tone = {"merged": "green", "closed": "red", "open": "cyan"}.get(str(made.get("state")), "white")
    draft = "draft · " if made.get("draft") else ""
    out.print(f"[bold]{escape(str(made.get('noun', 'pull request')).capitalize())} #{made.get('number')}[/] "
              f"on {escape(str(made.get('host')))} — [{tone}]{draft}{escape(str(made.get('state')))}[/]")
    out.print(f"  {escape(str(made.get('url')))}")
    if made.get("draftBecause"):
        out.print(f"  [dim]{escape(str(made['draftBecause']))}[/]")
    if made.get("checkFailed"):
        out.print(f"  [yellow]It could not be read again just now:[/] {escape(str(made['checkFailed']))}")


@app.command()
def pr(ref: Annotated[str, typer.Argument(help="An accepted run whose branch is pushed (RUN-…).")],
       check: Annotated[bool, typer.Option("--check", help="Only read the state back; open nothing.")] = False,
       json_out: Json = False) -> None:
    """Open the pull or merge request for a pushed branch with your own `gh` or `glab` — or, with
    --check, read back the state of the one it already has. A draft while its findings stand
    unanswered, ready once it is signed; it says which."""
    client = connect()
    where = f"/runs/{quote(ref, safe='')}/pr"
    if check:
        read = attempt(client.get, where)
        if json_out:
            dump(read)
            return
        _say_request(read.get("pullRequest") or {}, read.get("compareUrl"), read.get("why", ""))
        return
    done = attempt(client.post, where, timeout=180)
    if json_out:
        dump(done)
        return
    pushed = done.get("pushed") or {}
    out.print("[green]✓[/] Opened.")
    _say_request(pushed.get("pullRequest") or {}, pushed.get("compareUrl"), "")


@app.command("forges")
def forges(set_token: Annotated[str | None, typer.Option("--set-token", metavar="HOST",
                                help="Keep a token for this forge, read from stdin. Empty input clears it.")] = None,
           json_out: Json = False) -> None:
    """What this machine can open a request with: your own `gh` or `glab`, or a token kept here.

    A token is read from stdin and never from the command line, so it cannot end up in shell history:
    `printf %s $TOKEN | nc forges --set-token github.com`. It is kept beside the model keys, mode 0600,
    and nothing here or on the server ever prints more than its last four characters."""
    client = connect()
    if set_token:
        token = sys.stdin.read().strip()
        kept = attempt(client.call, "PUT", f"/runs/forges/{quote(set_token, safe='')}/token", json_body={"token": token})
        if json_out:
            dump(kept)
            return
        out.print(f"[green]✓[/] {escape(set_token)}: "
                  + (f"token kept, {escape(str(kept.get('tokenMask')))}." if token else "token cleared."))
        return
    found = attempt(client.get, "/runs/forges")
    if json_out:
        dump(found)
        return
    for each in found:
        how = each.get("reach")
        mark = "[green]✓[/]" if how else "[yellow]–[/]"
        said = {"gh": "your own gh", "glab": "your own glab", "token": "a token kept here"}.get(str(how), "nothing here")
        out.print(f"{mark} [bold]{escape(each['host'])}[/] — {said}"
                  + (f" [dim]{escape(each['tokenMask'])}[/]" if each.get("tokenMask") else ""))
        if each.get("why"):
            out.print(f"  [dim]{escape(each['why'])}[/]")


# ── memory ───────────────────────────────────────────────────────
@memory_app.command("search")
def memory_search(query: Annotated[str, typer.Argument(help="Words to look for.")], project: ProjectOpt = None,
                  category: Annotated[str | None, typer.Option(help="Only this category.")] = None,
                  json_out: Json = False) -> None:
    """Search the facts the workspace holds (full-text)."""
    client = connect()
    found = attempt(client.memory, query, project=project or config.load().project, category=category)
    if json_out:
        dump(found)
        return
    if not found:
        out.print(f"Nothing remembered about “{escape(query)}”.")
        return
    for f in found[:50]:
        pin = "[yellow]★[/] " if f.get("pinned") else ""
        out.print(f"{pin}[bold]{escape(f['ref'])}[/] {escape(f['title'])} [dim]· {escape(f.get('category') or '')} · "
                  f"{escape(f.get('projectId') or 'workspace')} · {escape(str(f.get('confidence') or ''))}[/]")
        out.print(f"  {escape((f.get('body') or '')[:240])}")


@memory_app.command("add")
def memory_add(title: Annotated[str, typer.Argument(help="A short title.")],
               body: Annotated[str, typer.Argument(help="The fact itself. `-` reads it from stdin.")],
               project: Annotated[str | None, typer.Option("--project", "-p", help="Project id; `global` for the whole workspace. Default: `nc use`'s.")] = None,
               category: Annotated[str, typer.Option(help="project | business_rules | architecture | … (as Memory lists them).")] = "project",
               confidence: Annotated[str, typer.Option(help="HIGH | MEDIUM | LOW")] = "MEDIUM",
               reason: Annotated[str, typer.Option(help="Why it is true, or where it came from.")] = "",
               json_out: Json = False) -> None:
    """Add a fact to memory. Facts are never deleted, only archived."""
    client = connect()
    if body == "-":
        body = from_file("-").strip()
    where = project or config.load().project
    added = attempt(client.remember, title, body, project=None if where in (None, "global") else where,
                    category=category, confidence=confidence.upper(), reason=reason)
    if json_out:
        dump(added)
        return
    for f in added:
        out.print(f"[green]✓[/] Remembered {escape(f['ref'])}: {escape(f['title'])}"
                  f" [dim]({escape(f.get('projectId') or 'workspace')})[/]")


@app.command()
def version(json_out: Json = False) -> None:
    """The version of nc, and of the server it is pointed at."""
    profile = config.load()
    if json_out:
        dump({"nc": __version__, "server": profile.server})
        return
    out.print(f"nc {__version__}" + (f" · {escape(profile.server)}" if profile.server else ""))


def run() -> None:
    """The console script. Ctrl-C ends quietly, as a terminal expects."""
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        app()
    except KeyboardInterrupt:
        err.print("[dim]Stopped.[/]")
        sys.exit(130)


if __name__ == "__main__":
    run()
