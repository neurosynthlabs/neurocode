"""DevOps for what actually exists: this machine, its services, and the work the agents delivered.

NeuroCode deploys nothing anywhere, so this screen does not pretend to watch a staging slot or a
production farm. It reads what is here. The API is this process, measured by `ps`; Postgres is asked
about itself; the web dev server and Ollama are probed over HTTP with short deadlines; the checks are
run now, every time, and nothing about them is stored. A delivery is a run and what became of it — a
merge into your checkout, a discard, a failure. The CI workflow is a file, and whether it was committed
or pushed is git's answer, not a sentence someone wrote once. Logs are what the database already holds:
run output, the activity feed, and model calls that failed.

Everything that blocks — a subprocess, a socket, a stat, the gateway — runs in a worker thread with a
hard timeout, and they run at the same time; the database questions run one after another on the
request's own session, which cannot answer two at once.

The queries live in `OpsReads` below rather than under `repositories/`: that module was not this
change's to create. They follow the repository rules all the same — nothing unbounded, nothing commits.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from ..agent.git import git
from ..ai import lanes
from ..ai.gateway import Gateway
from ..models import (AiCall, Approval, AuditEntry, Plan, Project, Role, RolePermission, Run, RunLog, Task, User,
                      UserRole)
from ..repositories.base import NotFound, bounded
from ..schemas.ops import check, delivery_json, fact, stage_json
from ..schemas.work import when
from ..settings import SERVER_DIR, Settings
from . import maintenance
from .errors import Refused

log = logging.getLogger(__name__)

REPO_ROOT = SERVER_DIR.parent
WORKFLOW = ".github/workflows/verify.yml"
#: How long a probe may take. A status page that hangs on a dead socket reports nothing at all.
HTTP_TIMEOUT = 1.5
CLI_TIMEOUT = 3
DOCKER_TIMEOUT, DOCKER_STATS_TIMEOUT = 5, 8
#: Where dev.sh serves the web app: its own knob and its own default.
WEB_PORT_ENV, WEB_PORT_DEFAULT = "NC_PORT", "5180"
DELIVERY_ROLES = ("solo", "integration")
MAX_GATE = 50
MAX_LOGS = 500
MAX_CONTAINERS = 200
STALE_WORKTREE = timedelta(days=7)
SILENT_RUN = timedelta(minutes=30)
BACKUP_FRESH = timedelta(days=7)
LOW_DISK = 5 * 1024 ** 3
FAILURE_SHARE = 0.25
#: Docker's answer is kept this long, so a tab left open does not spawn `docker stats` every poll.
DOCKER_CACHE_S = 10.0


# ── blocking probes, each run in a worker thread ─────────────────
def _cli(argv: list[str], timeout: float = CLI_TIMEOUT) -> subprocess.CompletedProcess[str] | None:
    """A program's answer, or None when it is not installed, hangs or cannot be started."""
    if shutil.which(argv[0]) is None:
        return None
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        log.info("ops probe %s did not answer: %s", argv[0], type(e).__name__)
        return None


def _etime_seconds(etime: str) -> int | None:
    """`ps`'s elapsed time, [[dd-]hh:]mm:ss, in seconds."""
    found = re.fullmatch(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)", etime.strip())
    if not found:
        return None
    days, hours, minutes, seconds = (int(x) if x else 0 for x in found.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def process_facts(pid: int) -> dict[str, Any]:
    """CPU, resident memory and age of one process, as `ps` measures them. Null where it cannot."""
    done = _cli(["ps", "-o", "%cpu=,rss=,etime=", "-p", str(pid)])
    parts = done.stdout.split() if done and done.returncode == 0 else []
    if len(parts) < 3:
        return {"cpuPct": None, "rssBytes": None, "uptimeS": None}
    try:
        return {"cpuPct": float(parts[0]), "rssBytes": int(parts[1]) * 1024, "uptimeS": _etime_seconds(parts[2])}
    except ValueError:
        return {"cpuPct": None, "rssBytes": None, "uptimeS": None}


def checkout_version(root: Path) -> str:
    """The short commit this checkout is on, and whether tracked files have changed since."""
    try:
        head = git(["rev-parse", "--short", "HEAD"], root, timeout=CLI_TIMEOUT)
        if head.returncode != 0:
            return ""
        dirty = git(["status", "--porcelain", "--untracked-files=no"], root, timeout=CLI_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return ""
    return head.stdout.strip() + ("+dirty" if dirty.returncode == 0 and dirty.stdout.strip() else "")


def probe_http(url: str) -> tuple[bool, int, str]:
    """Is something answering at this URL: yes or no, how long it took, and what it said."""
    started = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT) as r:
            return 200 <= r.status < 300, round((time.monotonic() - started) * 1000), f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, round((time.monotonic() - started) * 1000), f"HTTP {e.code}"
    except (OSError, ValueError) as e:
        return False, round((time.monotonic() - started) * 1000), type(getattr(e, "reason", e)).__name__


def package_version(root: Path) -> str:
    try:
        return str(json.loads((root / "package.json").read_text()).get("version", ""))
    except (OSError, ValueError):
        return ""


def tool_version(argv: list[str]) -> str:
    done = _cli(argv)
    if done is None:
        return "not installed"
    said = (done.stdout or done.stderr).strip().splitlines()
    return said[0][:120] if done.returncode == 0 and said else "installed, did not answer"


def memory_bytes() -> int | None:
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        done = _cli(["sysctl", "-n", "hw.memsize"])
        return int(done.stdout.strip()) if done and done.returncode == 0 and done.stdout.strip().isdigit() else None


def workflow_state(root: Path) -> dict[str, Any]:
    """The CI workflow as git sees it: there or not, committed or not, and pushed or not.

    Worked out every time, because each answer changes the moment someone commits or pushes, and a
    sentence written down once ("not pushed yet") would stay long after it stopped being true.
    """
    path = root / WORKFLOW
    out: dict[str, Any] = {"path": WORKFLOW, "present": path.is_file(), "steps": [], "tracked": False,
                           "upstream": None, "pushed": None}
    if not out["present"]:
        out["note"] = f"There is no {WORKFLOW} in this checkout."
        return out
    try:
        body = path.read_text(errors="replace")
    except OSError as e:
        out["note"] = f"{WORKFLOW} is there but could not be read ({type(e).__name__})."
        return out
    out["steps"] = [m.group(1).strip().strip("'\"") for m in re.finditer(r"^\s*-\s*name:\s*(.+)$", body, re.M)]
    try:
        out["tracked"] = git(["ls-files", "--error-unmatch", WORKFLOW], root, timeout=CLI_TIMEOUT).returncode == 0
        upstream = git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"], root,
                       timeout=CLI_TIMEOUT)
        if upstream.returncode == 0 and upstream.stdout.strip():
            out["upstream"] = upstream.stdout.strip()
            out["pushed"] = git(["cat-file", "-e", f"@{{upstream}}:{WORKFLOW}"], root,
                                timeout=CLI_TIMEOUT).returncode == 0
    except (OSError, subprocess.SubprocessError) as e:
        log.info("workflow state: git did not answer (%s)", type(e).__name__)
    here = "Nothing runs it on this machine."
    if not out["tracked"]:
        out["note"] = f"Defined for GitHub Actions but not committed, so it has never run anywhere. {here}"
    elif out["upstream"] is None:
        out["note"] = f"Committed on this branch. {here}"
    elif out["pushed"]:
        out["note"] = f"Committed and pushed to {out['upstream']}, where GitHub Actions runs it. {here}"
    else:
        out["note"] = f"Committed, but {out['upstream']} does not have it yet. {here}"
    return out


def secrets_file(path: Path) -> tuple[str, str]:
    """Is the keys file readable only by this account? A missing file holds no keys to leak."""
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return "ok", "No keys file yet: no key has been set from this app."
    except OSError as e:
        return "warn", f"Could not be read ({type(e).__name__})."
    if mode & 0o077:
        return "warn", f"Mode {oct(mode & 0o777)}: other accounts on this machine can read it."
    return "ok", f"Mode {oct(mode & 0o777)}: readable by this account only."


def disk_free(directory: Path) -> int | None:
    target = directory if directory.is_dir() else directory.parent
    try:
        return shutil.disk_usage(target).free
    except OSError:
        return None


def existing(paths: list[str]) -> int:
    return sum(1 for p in paths if Path(p).exists())


def subdirectories(directory: Path) -> int | None:
    try:
        return sum(1 for p in directory.iterdir() if p.is_dir()) if directory.is_dir() else 0
    except OSError:
        return None


_docker_seen: tuple[float, dict[str, Any]] | None = None


def docker_state() -> dict[str, Any]:
    """What Docker reports on this machine — every container, whoever started it — or why it cannot."""
    global _docker_seen
    if _docker_seen and time.monotonic() - _docker_seen[0] < DOCKER_CACHE_S:
        return _docker_seen[1]
    if shutil.which("docker") is None:
        answer = {"available": False, "reason": "Docker is not installed. NeuroCode itself runs no containers.",
                  "containers": []}
    else:
        answer = _docker_containers()
    _docker_seen = (time.monotonic(), answer)
    return answer


def _json_lines(out: str) -> list[dict[str, Any]]:
    rows = []
    for line in out.splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def _docker_containers() -> dict[str, Any]:
    listed = _cli(["docker", "ps", "-a", "--format", "{{json .}}"], DOCKER_TIMEOUT)
    if listed is None or listed.returncode != 0:
        said = (listed.stderr.strip().splitlines() or ["Docker did not answer."])[0] if listed else \
            "Docker did not answer in time."
        return {"available": False, "reason": said[:200], "containers": []}
    rows = _json_lines(listed.stdout)[:MAX_CONTAINERS]
    stats: dict[str, dict[str, Any]] = {}
    if any(r.get("State") == "running" for r in rows):
        measured = _cli(["docker", "stats", "--no-stream", "--format", "{{json .}}"], DOCKER_STATS_TIMEOUT)
        if measured is not None and measured.returncode == 0:
            stats = {s.get("ID", ""): s for s in _json_lines(measured.stdout)}
    containers = []
    for row in rows:
        state = row.get("State", "")
        measured_row = next((s for sid, s in stats.items() if sid and str(row.get("ID", "")).startswith(sid)), {})
        try:
            cpu = float(str(measured_row.get("CPUPerc", "0")).rstrip("%") or 0)
        except ValueError:
            cpu = 0.0
        containers.append({
            "id": row.get("ID", ""), "name": row.get("Names", ""), "image": row.get("Image", ""),
            "status": "up" if state == "running" else "restarting" if state == "restarting" else "down",
            "cpu": cpu if state == "running" else 0, "mem": measured_row.get("MemUsage") or "—",
            "ports": row.get("Ports") or "—"})
    return {"available": True, "reason": "", "containers": containers}


# ── the database's answers ───────────────────────────────────────
#: How each source's lines are keyed in the one timeline — their id, as selected and as compared.
RUN_KEY, ACT_KEY, AI_KEY = "'run:' || l.id", "'act:' || a.seq", "'ai:' || c.id"


@dataclass(slots=True)
class OpsReads:
    session: AsyncSession

    async def postgres(self) -> dict[str, Any]:
        started = time.monotonic()
        await self.session.execute(text("SELECT 1"))
        ms = round((time.monotonic() - started) * 1000)
        row = (await self.session.execute(text(
            "SELECT current_setting('server_version') AS version, "
            "       current_setting('server_version_num')::int / 10000 AS major, "
            "       pg_postmaster_start_time() AS started, pg_database_size(current_database()) AS bytes, "
            "       (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()) AS connections, "
            "       d.blks_hit AS hit, d.blks_read AS read, d.xact_commit AS commits, d.xact_rollback AS rollbacks "
            "FROM pg_stat_database d WHERE d.datname = current_database()"))).mappings().one()
        return {**row, "ms": ms}

    async def gate(self) -> tuple[list[tuple[Approval, Run, str]], list[tuple[Run, str]]]:
        """Runs stopped at your signature, and accepted runs that have not been merged."""
        waiting = (await self.session.execute(
            select(Approval, Run, Project.name).join(Run, Run.ref == Approval.run_ref)
            .join(Project, Project.id == Run.project_id)
            .where(Approval.status == "pending", Approval.tool.like("Merge(%"))
            .order_by(Approval.created_at.desc()).limit(MAX_GATE))).all()
        ready = (await self.session.execute(
            select(Run, Project.name).join(Project, Project.id == Run.project_id)
            .where(Run.status == "done", Run.merged.is_(None), Run.removed.is_(False),
                   Run.role.in_(DELIVERY_ROLES))
            .order_by(Run.finished_at.desc()).limit(MAX_GATE))).all()
        return [(a, r, n) for a, r, n in waiting], [(r, n) for r, n in ready]

    async def stale_worktrees(self) -> list[str]:
        cutoff = datetime.now(UTC) - STALE_WORKTREE
        return list((await self.session.execute(
            select(Run.worktree).where(Run.removed.is_(False), Run.status.in_(("failed", "cancelled")),
                                       Run.finished_at < cutoff).limit(MAX_GATE * 4))).scalars())

    async def silent_runs(self) -> list[str]:
        cutoff = datetime.now(UTC) - SILENT_RUN
        last = (select(func.max(RunLog.at)).where(RunLog.run_id == Run.id).scalar_subquery())
        return list((await self.session.execute(
            select(Run.ref).where(Run.status == "running",
                                  func.coalesce(last, Run.created_at) < cutoff).limit(MAX_GATE))).scalars())

    async def model_calls(self) -> tuple[int, int, int]:
        """Calls in the last hour, how many of them failed, and every call today."""
        hour = datetime.now(UTC) - timedelta(hours=1)
        row = (await self.session.execute(select(
            func.count().filter(AiCall.at > hour),
            func.count().filter(AiCall.at > hour, AiCall.ok.is_(False)),
            func.count().filter(AiCall.at >= func.date_trunc("day", func.now())),
        ).where(AiCall.at >= func.least(hour, func.date_trunc("day", func.now()))))).one()
        return int(row[0]), int(row[1]), int(row[2])

    async def deliveries(self, limit: int, offset: int) -> list[tuple[Run, str]]:
        rows = (await self.session.execute(
            select(Run, Project.name).join(Project, Project.id == Run.project_id)
            .where(Run.role.in_(DELIVERY_ROLES)).order_by(Run.created_at.desc())
            .limit(bounded(limit)).offset(max(0, offset)))).all()
        return [(r, n) for r, n in rows]

    async def delivery_stats(self) -> dict[str, int]:
        row = (await self.session.execute(text(
            "SELECT count(*) FILTER (WHERE merged IS NOT NULL "
            "                          AND (merged->>'at')::timestamptz >= date_trunc('day', now())) AS today, "
            "       count(*) FILTER (WHERE merged IS NOT NULL) AS merged, "
            "       count(*) FILTER (WHERE status = 'failed') AS failed, "
            "       count(*) FILTER (WHERE removed AND merged IS NULL) AS discarded "
            "FROM runs WHERE role IN ('solo', 'integration')"))).mappings().one()
        blocked = await self.session.execute(
            select(func.count()).select_from(Approval)
            .where(Approval.status == "pending", Approval.run_ref.is_not(None)))
        return {"mergedToday": int(row["today"]), "merged": int(row["merged"]), "failed": int(row["failed"]),
                "discarded": int(row["discarded"]), "blockedOnYou": int(blocked.scalar_one())}

    async def run_for_pipeline(self, ref: str | None) -> Run | None:
        stmt = select(Run)
        stmt = stmt.where(Run.ref == ref) if ref else \
            stmt.where(Run.role.in_(DELIVERY_ROLES)).order_by(Run.created_at.desc())
        return (await self.session.execute(stmt.limit(1))).scalar_one_or_none()

    async def refs_of(self, run: Run) -> tuple[str | None, str | None]:
        task = (await self.session.execute(select(Task.ref).where(Task.id == run.task_id))).scalar_one_or_none() \
            if run.task_id else None
        plan = (await self.session.execute(select(Plan.ref).where(Plan.id == run.plan_id))).scalar_one_or_none() \
            if run.plan_id else None
        return task, plan

    async def logs(self, level: str | None, before: tuple[datetime, str] | None,
                   limit: int) -> list[dict[str, Any]]:
        """Run output, the activity feed and failed model calls, newest first, as one timeline.

        The level is translated before it reaches SQL: `debug` is what a run logs as `tool`, which
        neither the activity feed nor the ledger ever writes, and asking an enum column for a value
        it does not have is an error rather than an empty answer.
        """
        params: dict[str, Any] = {"limit": min(max(1, limit), MAX_LOGS)}
        if before is not None:
            params["before_t"], params["before_id"] = before

        # A keyset on (time, id), at full precision. The cursor used to be the last line's time cut to the
        # second and compared with `<`, so every line written in that second that did not fit on the page
        # was on no page at all — and a run writes many lines a second.
        def older(at: str, key: str) -> str:
            # `key` is the same expression the branch selects as its id, so the comparison and the
            # ORDER BY agree on what "before" means.
            return f" AND ({at}, {key}) < (:before_t, :before_id)" if before is not None else ""

        # Run output answers every level; `debug` is its `tool` lines.
        run_level = "tool" if level == "debug" else level
        if run_level:
            params["run_level"] = run_level
        branches = [
            f"SELECT {RUN_KEY} AS id, l.at AS t, "
            "       CASE l.level::text WHEN 'tool' THEN 'debug' ELSE l.level::text END AS level, "
            "       r.ref AS source, l.line AS text "
            "FROM run_logs l JOIN runs r ON r.id = l.run_id WHERE true"
            + (" AND l.level::text = :run_level" if run_level else "") + older("l.at", RUN_KEY)]
        if level != "debug":
            act_filter = " AND a.level::text = :level" if level else ""
            if level:
                params["level"] = level
            branches.append(
                f"SELECT {ACT_KEY}, a.at, a.level::text, a.actor, "
                "       a.action || CASE WHEN a.detail = '' THEN '' ELSE ' · ' || a.detail END "
                f"FROM activity a WHERE true{act_filter}{older('a.at', ACT_KEY)}")
        if level in (None, "err"):
            branches.append(
                f"SELECT {AI_KEY}, c.at, 'err', coalesce(nullif(c.lane, ''), 'gateway'), "
                "       c.feature || ' · ' || c.model || CASE WHEN c.error = '' THEN '' ELSE ' · ' || c.error END "
                f"FROM ai_calls c WHERE NOT c.ok{older('c.at', AI_KEY)}")
        rows = (await self.session.execute(text(
            f"SELECT id, t, level, source, text FROM ({' UNION ALL '.join(branches)}) timeline "
            "ORDER BY t DESC, id DESC LIMIT :limit"), params)).mappings().all()
        return [dict(r) for r in rows]

    async def last_set(self, keys: list[str]) -> datetime | None:
        """When one of these keys was last written from the AI providers screen, if it ever was."""
        return (await self.session.execute(
            select(func.max(AuditEntry.at)).where(
                AuditEntry.action == "ai.update",
                or_(*(AuditEntry.detail.has_key(k) for k in keys))))).scalar_one()

    async def admins(self) -> list[str]:
        return list((await self.session.execute(
            select(User.name).distinct().join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .join(RolePermission, RolePermission.role_id == UserRole.role_id)
            .where(RolePermission.permission == "workspace:admin", User.status == "active")
            .order_by(User.name).limit(MAX_GATE))).scalars())


def _cursor(value: str | None) -> tuple[datetime, str] | None:
    """`<ISO time>|<line id>`, as `logs` hands it out. Anything else is refused in words, not a 500."""
    if not value:
        return None
    at, sep, key = value.partition("|")
    try:
        moment = datetime.fromisoformat(at)
    except ValueError:
        moment = None
    if not sep or not key or moment is None:
        raise Refused("That is not a cursor this API handed out. Load the logs again from the newest.", status=422)
    return (moment if moment.tzinfo else moment.replace(tzinfo=UTC), key)


# ── what the screen asks for ─────────────────────────────────────
def _pct(part: int, whole: int) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "—"


def _dangers(run: Run) -> list[str]:
    """Why accepting this run could hurt, from what the run itself measured."""
    out = [f"HIGH review finding: {f.get('title') or f.get('detail') or f.get('message') or 'unnamed'}"
           for f in (run.review or {}).get("findings", []) if f.get("severity") == "HIGH"]
    if run.tests_status == "failed":
        out.append(f"Tests failed{': ' + run.tests_summary if run.tests_summary else ''}")
    for conflict in run.conflicts:
        out.append(f"Collided with {conflict.agent or conflict.branch} in {', '.join((conflict.files or [])[:3])}")
    return out


def _gate_item(kind: str, run: Run, project: str, approval: Approval | None) -> dict[str, Any]:
    dangers = _dangers(run)
    if approval is not None:
        ships, risk = [line for line in approval.payload.splitlines() if line.strip()], approval.risk
        undo = "Refuse removes the branch and its worktree; nothing has touched your checkout."
    else:
        ships = [f"branch {run.branch} from {run.base[:7]}",
                 f"{run.diff_files} files · +{run.diff_insertions} −{run.diff_deletions} · {run.diff_commits} commits"]
        # HIGH only when the run measured a reason; otherwise no risk is claimed at all. A MEDIUM here
        # used to be a baseline nobody measured, shown in the same pill as an assessment.
        risk = "HIGH" if dangers else None
        undo = "Merging hands back the git reset --hard command that undoes it."
    return {"kind": kind, "runRef": run.ref, "approvalRef": approval.ref if approval else None,
            "project": project, "projectId": run.project_id, "branch": run.branch,
            "diff": {"files": run.diff_files, "insertions": run.diff_insertions, "deletions": run.diff_deletions,
                     "commits": run.diff_commits},
            "tests": run.tests_status + (f" · {run.tests_summary}" if run.tests_summary else ""),
            "risk": risk, "ships": ships, "dangers": dangers, "undo": undo}


class OpsService:
    def __init__(self, session: AsyncSession, gateway: Gateway, config: Settings,
                 repo_root: Path = REPO_ROOT) -> None:
        self.reads = OpsReads(session)
        self.session = session
        self.gateway = gateway
        self.config = config
        self.root = repo_root

    # ── the overview ─────────────────────────────────────────────
    async def overview(self, app_version: str) -> dict[str, Any]:
        cfg, gw, now = self.config, self.gateway, datetime.now(UTC)
        web_url = f"http://127.0.0.1:{os.environ.get(WEB_PORT_ENV, WEB_PORT_DEFAULT)}"
        ollama = await asyncio.to_thread(gw.ollama)

        pg = await self.reads.postgres()
        blocking = asyncio.gather(
            asyncio.to_thread(process_facts, os.getpid()),
            asyncio.to_thread(checkout_version, self.root),
            asyncio.to_thread(probe_http, web_url),
            asyncio.to_thread(package_version, self.root),
            asyncio.to_thread(gw.ollama_ready),
            asyncio.to_thread(gw.report),
            asyncio.to_thread(gw.embed_lane),
            asyncio.to_thread(gw.preference),
            asyncio.to_thread(maintenance.find_pg_dump, int(pg["major"]), cfg.pg_bin_dir),
            asyncio.to_thread(maintenance._revisions),
            asyncio.to_thread(maintenance.MaintenanceService(self.session, cfg)._backups),
            asyncio.to_thread(disk_free, cfg.backups_dir),
            asyncio.to_thread(secrets_file, cfg.secrets_path),
            asyncio.to_thread(self._runtime_blocking),
        )
        revision = await maintenance.MaintenanceService(self.session, cfg).revision()
        waiting, ready = await self.reads.gate()
        stale = await self.reads.stale_worktrees()
        silent = await self.reads.silent_runs()
        hour_calls, hour_failed, today_calls = await self.reads.model_calls()
        (proc, commit, (web_ok, web_ms, web_said), web_version, ollama_ok, report, embed_lane, preference,
         pg_dump, revisions, backups, free, (secrets_status, secrets_note), machine) = await blocking
        stale_there = await asyncio.to_thread(existing, stale)

        pg_where = cfg.database_url.rsplit("@", 1)[-1].replace("+asyncpg", "")
        services = [
            {"id": "api", "name": "NeuroCode API", "status": "ok", "url": "this process",
             "version": f"{app_version}" + (f" · {commit}" if commit else ""),
             "startedAt": when(now - timedelta(seconds=proc["uptimeS"])) if proc["uptimeS"] is not None else None,
             **proc, "facts": [fact("PID", os.getpid()), fact("Python", platform.python_version())]},
            {"id": "postgres", "name": "PostgreSQL", "status": "ok", "url": pg_where, "version": pg["version"],
             "startedAt": when(pg["started"]), "cpuPct": None, "rssBytes": None,
             "uptimeS": round((now - pg["started"]).total_seconds()) if pg["started"] else None,
             "facts": [fact("Size", f"{pg['bytes'] / 1024 ** 2:.1f} MB"), fact("Connections", pg["connections"]),
                       fact("Cache hit", _pct(pg["hit"], pg["hit"] + pg["read"])),
                       fact("Rolled back", _pct(pg["rollbacks"], pg["commits"] + pg["rollbacks"]))]},
            {"id": "web", "name": "Web dev server", "status": "ok" if web_ok else "down", "url": web_url,
             "version": web_version, "startedAt": None, "uptimeS": None, "cpuPct": None, "rssBytes": None,
             "facts": [fact("Answered", f"{web_said} in {web_ms} ms")]},
            {"id": "ollama", "name": "Ollama", "status": "ok" if ollama_ok else "down", "url": ollama["url"],
             "version": ollama["model"], "startedAt": None, "uptimeS": None, "cpuPct": None, "rssBytes": None,
             "facts": [fact("Model", f"{ollama['model']} {'pulled' if ollama_ok else 'not available'}")]},
        ]

        needs_key = [x for x in report if x.get("needsKey")]
        ready_lanes = [x for x in report if x.get("ready")]
        rejected = [x["label"] for x in report if x.get("rejected")]
        head = revisions[-1][0] if revisions else ""
        latest = backups[0] if backups else None
        latest_at = datetime.fromisoformat(latest["at"]) if latest else None
        checks = [
            check("db", "Database answers", pg_where, "ok", f"SELECT 1 in {pg['ms']} ms", ms=pg["ms"], at=now),
            check("schema", "Schema at the latest migration", "alembic", "ok" if revision == head else "warn",
                  f"at {revision}" if revision == head
                  else f"at {revision or 'nothing'}, the latest is {head}: run alembic upgrade head", at=now),
            check("pg_dump", "Backup tool", "pg_dump", "ok" if pg_dump else "warn",
                  pg_dump or f"No pg_dump for PostgreSQL {pg['major']} or newer: backups cannot be taken.", at=now),
            check("backup", "Latest backup", str(cfg.backups_dir),
                  "ok" if latest_at and now - latest_at < BACKUP_FRESH else "warn",
                  f"{latest['name']} · {when(latest_at)}" if latest else "No backup has been taken.", at=now),
            check("disk", "Disk free for backups", str(cfg.backups_dir),
                  "warn" if free is None or free < LOW_DISK else "ok",
                  f"{free / 1024 ** 3:.1f} GiB free" if free is not None else "Could not be measured.", at=now),
            check("secrets", "Keys file is private", str(cfg.secrets_path), secrets_status, secrets_note, at=now),
            check("lanes", "Model lanes", "gateway", "ok" if ready_lanes else "warn",
                  f"{len(ready_lanes)} of {len(report)} can answer now" if ready_lanes
                  else "No lane can answer: the offline rules write every answer.", at=now),
            check("keys", "Keys accepted", "gateway", "warn" if rejected else "ok",
                  f"Refused by the provider: {', '.join(rejected)}" if rejected
                  else f"No provider refused a key ({sum(1 for x in needs_key if x.get('hasKey'))} set).", at=now),
            check("embeddings", "Embeddings", "retrieval", "ok" if embed_lane else "warn",
                  f"{embed_lane.label} makes them" if embed_lane else "No lane makes embeddings: retrieval is by words only.",
                  at=now),
            check("web", "Web dev server", web_url, "ok" if web_ok else "down", web_said, ms=web_ms, at=now),
            check("ollama", "Local model", ollama["url"], "ok" if ollama_ok else "warn",
                  f"{ollama['model']} is pulled and answering" if ollama_ok
                  else f"Not running, or {ollama['model']} is not pulled.", at=now),
            check("worktrees", "Stale worktrees", str(cfg.worktrees_dir), "warn" if stale_there else "ok",
                  f"{stale_there} from runs that stopped over a week ago are still on disk" if stale_there
                  else "None left behind by stopped runs.", at=now),
            check("silent", "Runs still talking", "runs", "warn" if silent else "ok",
                  f"No output for 30 minutes: {', '.join(silent[:6])}" if silent else "No running run has gone quiet.",
                  at=now),
            check("failures", "Model calls, last hour", "ai_calls",
                  "warn" if hour_calls and hour_failed / hour_calls > FAILURE_SHARE else "ok",
                  f"{hour_failed} of {hour_calls} failed" if hour_calls else "No model calls in the last hour.", at=now),
        ]

        runtime = [*machine, fact("PostgreSQL", pg["version"]), fact("pg_dump", pg_dump or "missing"),
                   fact("Backups", str(cfg.backups_dir)), fact("Repositories", str(cfg.repos_dir)),
                   fact("Routing", preference), fact("AI calls today", today_calls)]
        gate = [_gate_item("handoff", run, name, approval) for approval, run, name in waiting] + \
               [_gate_item("ready", run, name, None) for run, name in ready]
        return {"services": services, "gate": gate, "checks": checks, "runtime": runtime, "at": when(now)}

    def _runtime_blocking(self) -> list[dict[str, str]]:
        """The facts of this machine that only a program or the OS can answer. Blocking."""
        load = os.getloadavg() if hasattr(os, "getloadavg") else None
        total = memory_bytes()
        count = subdirectories(self.config.worktrees_dir)
        return [fact("Python", platform.python_version()), fact("Platform", platform.platform()),
                fact("CPUs", os.cpu_count() or "unknown"),
                fact("Load", " · ".join(f"{x:.2f}" for x in load) if load else "unknown"),
                fact("Memory", f"{total / 1024 ** 3:.1f} GiB" if total else "unknown"),
                fact("git", tool_version(["git", "--version"])), fact("uv", tool_version(["uv", "--version"])),
                fact("node", tool_version(["node", "--version"])),
                fact("docker", tool_version(["docker", "--version"])),
                fact("Worktrees", f"{self.config.worktrees_dir} · "
                                  + ("could not be read" if count is None else f"{count} folders"))]

    # ── deliveries ───────────────────────────────────────────────
    async def deliveries(self, limit: int, offset: int) -> dict[str, Any]:
        rows = await self.reads.deliveries(limit, offset)
        return {"items": [delivery_json(run, name) for run, name in rows],
                "stats": await self.reads.delivery_stats()}

    # ── the pipeline ─────────────────────────────────────────────
    async def pipeline(self, ref: str | None) -> dict[str, Any]:
        run = await self.reads.run_for_pipeline(ref)
        if ref and run is None:
            raise NotFound(f"run {ref}")
        workflow = await asyncio.to_thread(workflow_state, self.root)
        if run is None:
            return {"run": None, "stages": [], "workflow": workflow}
        task, plan = await self.reads.refs_of(run)
        end = run.finished_at or datetime.now(UTC)
        return {"run": {"ref": run.ref, "status": run.status,
                        "trigger": " · ".join(x for x in (task, plan) if x) or "started by hand",
                        "by": run.requested_by, "runner": f"local worktree · {run.lane or 'rules'}",
                        "branch": run.branch, "startedAt": when(run.created_at),
                        "elapsedS": round((end - run.created_at).total_seconds())},
                "stages": [stage_json(step) for step in run.steps], "workflow": workflow}

    # ── logs ─────────────────────────────────────────────────────
    async def logs(self, level: str | None, before: str | None, limit: int) -> dict[str, Any]:
        rows = await self.reads.logs(level, _cursor(before), limit)
        lines = [{"id": r["id"], "t": when(r["t"]), "level": r["level"], "source": r["source"], "text": r["text"]}
                 for r in rows]
        full = len(rows) >= min(max(1, limit), MAX_LOGS)
        # Opaque to the screen: the last line's exact time and its id, which is what the next page needs.
        return {"lines": lines,
                "next": f"{rows[-1]['t'].isoformat()}|{rows[-1]['id']}" if full and rows else None}

    # ── containers ───────────────────────────────────────────────
    async def containers(self) -> dict[str, Any]:
        return await asyncio.to_thread(docker_state)

    # ── secrets, by name only ────────────────────────────────────
    async def secrets(self) -> list[dict[str, Any]]:
        """Every key this app holds, by name: where it is kept, whether it is set, whether the provider
        refused it and when it was last set here. Never the value, and never a mask of it."""
        report = await asyncio.to_thread(self.gateway.report)
        admins = await self.reads.admins()
        out: list[dict[str, Any]] = []
        for lane in report:
            if not lane.get("needsKey"):
                continue
            spec = lanes.BY_ID.get(lane["id"])
            source = lane.get("keySource")
            store = ("server/secrets.json" if source == "workspace"
                     else f"environment {spec.env}" if source == "environment" and spec else "not set")
            keys = [f"{lane['id']}.key", *(["deepseekKey"] if lane["id"] == "deepseek" else [])]
            out.append({"id": lane["id"], "name": (spec.secret if spec else "") or (spec.env if spec else lane["id"]),
                        "env": spec.env if spec else "", "store": store, "set": bool(lane.get("hasKey")),
                        "rejected": bool(lane.get("rejected")), "lastSetAt": when(await self.reads.last_set(keys)),
                        "replaceableBy": admins, "usedBy": f"{lane['label']} · {lane['model']}", "note": ""})
        out.append(self._database_secret(admins))
        return out

    def _database_secret(self, admins: list[str]) -> dict[str, Any]:
        url = make_url(self.config.database_url)
        if "NEUROCODE_DATABASE_URL" in os.environ:
            store = "environment NEUROCODE_DATABASE_URL"
        elif "database_url" in self.config.model_fields_set:
            store = "server/.env"
        else:
            store = "built-in default"
        default = url.username == "neurocode" and url.password == "neurocode"
        return {"id": "database", "name": "NEUROCODE_DATABASE_URL (password)", "env": "NEUROCODE_DATABASE_URL",
                "store": store, "set": bool(url.password), "rejected": False, "lastSetAt": None,
                "replaceableBy": admins, "usedBy": f"the API and the gateway's ledger · {url.database}",
                "note": "The built-in development credentials: change them before this database holds anything "
                        "you would mind losing." if default else ""}
