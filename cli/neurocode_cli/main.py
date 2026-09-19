"""`nc` — every command, each one a thin reading of the API.

Every command takes `--json` and then prints the API's own answer and nothing else, so scripts can pipe
it into `jq`. Without it, the answer is laid out for a person. A refusal is the API's own words on stderr
and exit code 1; exit code 3 means an answer is waiting on a person (a permission card, a gate).
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
from .render import FINISHED, dump, err, log_line, out, print_plan, status, table, when

app = typer.Typer(name="nc", no_args_is_help=True, add_completion=False, rich_markup_mode="rich",
                  help="NeuroCode in the terminal: sessions, plans, approvals, runs and memory.",
                  context_settings={"help_option_names": ["-h", "--help"]})
memory_app = typer.Typer(no_args_is_help=True, help="Search what the workspace remembers, or add to it.")
app.add_typer(memory_app, name="memory")

#: An answer is waiting on a person: a permission card in a session, or a run parked at a gate.
WAITING = 3

Json = Annotated[bool, typer.Option("--json", help="Print the API's answer as JSON, and nothing else.")]
ProjectOpt = Annotated[str | None, typer.Option("--project", "-p", help="Project id. Defaults to `nc use`'s.")]


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
    grid = table("", "Project", "Name", "Status", "Stack", "Tasks", "Active")
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


@app.command()
def ask(
    question: Annotated[str, typer.Argument(help="What to ask. The project's code, memory and tools answer it.")],
    project: ProjectOpt = None,
    session: Annotated[str | None, typer.Option("--session", "-s", help="Ask in this session (SES-…) instead of a new one.")] = None,
    reasoning: Annotated[bool, typer.Option(help="Show the model's reasoning, when its lane returns it.")] = False,
    json_out: Json = False,
) -> None:
    """Ask a question and watch the answer being written. Permission cards are asked here."""
    client = connect()
    if session:
        started = attempt(client.session, session)
    else:
        started = attempt(client.start_session, project_of(client, project), question[:80])
    ref = started["ref"]
    listener = Listener(client).start()
    try:
        asked = attempt(client.ask, ref, question)
        talk = Conversation(client, ref, since=int(asked["message"]["id"]))
        if not json_out:
            out.print(f"[dim]{escape(ref)} · {escape(started.get('projectName') or started.get('projectId') or '')}[/]")
        while True:
            follow(client, listener, talk, reasoning=reasoning, quiet=json_out)
            card = talk.waiting()
            if card is None:
                break
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
             json_out: Json = False) -> None:
    """Recent sessions, newest first."""
    client = connect()
    found = attempt(client.sessions, project or config.load().project, limit=limit)
    if json_out:
        dump(found)
        return
    if not found:
        out.print("No sessions yet. Start one with `nc chat` or `nc ask \"…\"`.")
        return
    grid = table("Session", "Project", "Title", "Turns", "Status", "Last")
    for s in found:
        state = "waiting on you" if s.get("waitingOn") else s.get("status")
        grid.add_row(escape(s["ref"]), escape(s.get("projectName") or s["projectId"]), escape((s.get("title") or "")[:48]),
                     str(s.get("turns", 0)), status(state) if state != "waiting on you" else "[yellow]waiting on you[/]",
                     when(s.get("lastAt")))
    out.print(grid)


@app.command()
def chat(project: ProjectOpt = None,
         session: Annotated[str | None, typer.Option("--session", "-s", help="Open this session (SES-…).")] = None,
         json_out: Json = False) -> None:
    """A full-screen session: streaming answers, reasoning folded, tool calls, permission prompts, @ and /."""
    client = connect()
    if json_out:
        # A full-screen app has nothing to print; --json says which session it would open, for scripts.
        ref = session or attempt(client.start_session, project_of(client, project), "")["ref"]
        dump(attempt(client.session, ref))
        return
    if not interactive():
        fail("`nc chat` needs a terminal. Use `nc ask` in scripts.")
    from .tui import ChatApp

    pid = None if session else project_of(client, project)
    ChatApp(client, project=pid, session=session).run()


# ── plans ────────────────────────────────────────────────────────
@app.command()
def plan(requirement: Annotated[str, typer.Argument(help="What you want built, in your words.")],
         project: ProjectOpt = None, json_out: Json = False) -> None:
    """Compile a requirement into a plan (steps, risk, open questions). It is not dispatched."""
    client = connect()
    pid = project_of(client, project)
    if not json_out:
        err.print(f"[dim]Compiling for {escape(pid)}…[/]")
    made = attempt(client.compile, pid, requirement)
    if json_out:
        dump(made)
        return
    print_plan(made)
    out.print()
    out.print(f"[dim]Dispatch it with `nc dispatch {escape(made['ref'])}` once the questions are settled.[/]")


@app.command()
def plans(project: ProjectOpt = None, limit: Annotated[int, typer.Option(min=1, max=100)] = 20,
          json_out: Json = False) -> None:
    """Recent plans, newest first. One in full: `nc show-plan <ref>`."""
    client = connect()
    found = attempt(client.plans, project or config.load().project, limit=limit)
    if json_out:
        dump(found)
        return
    if not found:
        out.print("No plans yet. Make one with `nc plan \"<requirement>\"`.")
        return
    grid = table("Plan", "Project", "Status", "Risk", "Steps", "Open ?", "Requirement")
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
              json_out: Json = False) -> None:
    """What is waiting on a person: tests to run, a signature, a command, a question."""
    client = connect()
    found = attempt(client.approvals, pending=not all_)
    if json_out:
        dump(found)
        return
    if not found:
        out.print("Nothing is waiting on a person." if not all_ else "No gates yet.")
        return
    grid = table("Gate", "Kind", "Risk", "Status", "Title", "Run", "Asked")
    for a in found:
        grid.add_row(escape(a["ref"]), escape(a.get("kind") or ""), escape(str(a.get("risk") or "")), status(a.get("status")),
                     escape((a.get("title") or "")[:60]), escape(a.get("runRef") or ""), when(a.get("requestedAt")))
    out.print(grid)
    out.print("[dim]Answer with `nc approve <gate>` or `nc deny <gate>`.[/]")


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
        out.print(f"  [yellow]waiting on {escape(str(run['waitingOn']))}[/] — `nc approvals`")
    if run.get("note"):
        out.print(f"  [dim]{escape(run['note'])}[/]")


@app.command()
def runs(ref: Annotated[str | None, typer.Argument(help="A run (RUN-…) for its detail and log.")] = None,
         project: ProjectOpt = None,
         watch: Annotated[bool, typer.Option("--watch", "-w", help="Follow the log live until the run stops.")] = False,
         limit: Annotated[int, typer.Option(min=1, max=100)] = 20,
         json_out: Json = False) -> None:
    """Agent runs. With a run: its steps and log; with --watch, the log as it is written."""
    client = connect()
    if ref is None:
        if watch:
            watch_all(client, project, json_out)
            return
        found = attempt(client.runs, project or config.load().project, limit=limit)
        if json_out:
            dump(found)
            return
        if not found:
            out.print("No runs yet. Dispatch a plan with `nc dispatch <plan>`.")
            return
        grid = table("Run", "Project", "Status", "Agent", "Branch", "Started", "Requirement")
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
        watch_one(client, listener, found, json_out)
    finally:
        if listener is not None:
            listener.stop()


def watch_one(client: Client, listener: Listener | None, found: dict[str, Any], json_out: bool) -> None:
    """The run's log from the start, then live until the run stops or waits on a person. With --json,
    one JSON object per line (`{"kind": "log"|"run", …}`), which is what a script can follow."""
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
    quiet_since = time.monotonic()
    while state not in FINISHED and state != "waiting":
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
    """Push an accepted run's branch with your git credentials (never forced). Prints the compare link."""
    client = connect()
    done = attempt(client.push, ref, remote)
    if json_out:
        dump(done)
        return
    pushed = done.get("pushed") or {}
    out.print(f"[green]✓[/] Pushed {escape(str(done.get('branch')))} to {escape(str(pushed.get('remote')))}.")
    if pushed.get("compareUrl"):
        out.print(f"  Open the pull request: {escape(pushed['compareUrl'])}")


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
               body: Annotated[str, typer.Argument(help="The fact itself.")],
               project: Annotated[str | None, typer.Option("--project", "-p", help="Project id; `global` for the whole workspace. Default: `nc use`'s.")] = None,
               category: Annotated[str, typer.Option(help="project | business_rules | architecture | … (as Memory lists them).")] = "project",
               confidence: Annotated[str, typer.Option(help="HIGH | MEDIUM | LOW")] = "MEDIUM",
               reason: Annotated[str, typer.Option(help="Why it is true, or where it came from.")] = "",
               json_out: Json = False) -> None:
    """Add a fact to memory. Facts are never deleted, only archived."""
    client = connect()
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
