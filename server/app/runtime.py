"""The agent runtime.

A run is real work on real code: its own git worktree and branch, a model writing whole files, the
project's own tests, a review of the actual diff, and your signature at the end. Three rules make it
safe to leave running on your machine:

  * nothing touches your working tree — every change happens in a worktree on a new branch;
  * nothing a model says is ever executed — it may only propose file contents, which are written
    inside the worktree and shown to you as a diff;
  * the only command that runs is the project's own test command, and the first time a project runs
    it, the run stops and waits for your approval.

With no model configured, the runtime says so and skips the writing steps rather than inventing code.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import BaseModel, Field

from . import codeindex, onboarding
from .ai.gateway import NoModel, extract_json
from .context import Ctx
from .db import now_iso

WORKTREES = Path(__file__).resolve().parent.parent / ".worktrees"
MAX_FILES, MAX_FILE_BYTES, MAX_CONTEXT, MAX_DIFF = 20, 256_000, 60_000, 200_000
TEST_TIMEOUT, TEST_LINES = 600, 400
GATE = re.compile(r"\b(approval|approve|sign[- ]?off|signature)\b", re.I)
SECRETS = re.compile(r"(sk-[A-Za-z0-9]{10,}|password\s*=\s*['\"][^'\"]{3,}|api[_-]?key\s*=\s*['\"][^'\"]{6,})", re.I)
LEFTOVERS = re.compile(r"\b(console\.log|debugger|print\()")
TODO = re.compile(r"\b(TODO|FIXME|XXX)\b")

# How a project runs its own tests. The first match whose tool is installed wins.
TEST_RECIPES: list[tuple[str, list[str], str | None]] = [
    ("Makefile", ["make", "test"], "test:"),
    ("uv.lock", ["uv", "run", "pytest", "-q"], None),
    ("pytest.ini", ["python", "-m", "pytest", "-q"], None),
    ("pyproject.toml", ["python", "-m", "pytest", "-q"], "pytest"),
    ("package.json", ["npm", "test", "--silent"], '"test"'),
    ("go.mod", ["go", "test", "./..."], None),
]


class Refused(RuntimeError):
    """Why a run cannot start: no code on this machine, or nothing to branch from."""


class FileOut(BaseModel):
    path: str
    content: str


class EditOut(BaseModel):
    summary: str = ""
    files: list[FileOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    severity: str = "LOW"
    file: str = ""
    note: str


class ReviewOut(BaseModel):
    findings: list[Finding] = Field(default_factory=list)
    verdict: str = ""


# ── git, always with fixed arguments and a timeout ───────────────
def git(args: list[str], cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-c", "core.fsmonitor=false", *args], cwd=cwd, capture_output=True, text=True,
                          timeout=timeout, env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})


def repo_of(root: Path) -> tuple[Path, str] | None:
    """The repository that holds a project's root, and where the root sits inside it."""
    try:
        out = git(["rev-parse", "--show-toplevel"], root)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    top, here = Path(os.path.realpath(out.stdout.strip())), Path(os.path.realpath(root))
    try:
        return top, ("" if here == top else str(here.relative_to(top)))
    except ValueError:
        return None


def detect_tests(root: Path) -> dict[str, Any] | None:
    for name, argv, needle in TEST_RECIPES:
        f = root / name
        if not f.is_file() or shutil.which(argv[0]) is None:
            continue
        if needle:
            try:
                if needle not in f.read_text(errors="replace"):
                    continue
            except OSError:
                continue
        return {"argv": argv, "command": " ".join(argv)}
    if (next(root.glob("*.sln"), None) or next(root.glob("*.csproj"), None)) and shutil.which("dotnet"):
        return {"argv": ["dotnet", "test"], "command": "dotnet test"}
    return None


# ── making a run ─────────────────────────────────────────────────
def _steps(plan: dict[str, Any], tests: dict[str, Any] | None) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for s in plan.get("steps", []):
        if GATE.search(s["label"]) or s["agent"] in ("AI Commander", "AI Project Manager"):
            continue  # your signature is the last step of every run anyway
        steps.append({"n": len(steps) + 1, "kind": "edit", "label": s["label"], "agent": s["agent"],
                      "status": "todo", "detail": s.get("detail", ""), "ms": 0, "startedAt": None, "finishedAt": None})
    if tests:
        steps.append({"n": len(steps) + 1, "kind": "test", "label": f"Run the project's tests · {tests['command']}",
                      "agent": "QA Engineer", "status": "todo", "detail": "", "ms": 0, "startedAt": None, "finishedAt": None})
    steps.append({"n": len(steps) + 1, "kind": "review", "label": "Review the diff", "agent": "Code Reviewer",
                  "status": "todo", "detail": "", "ms": 0, "startedAt": None, "finishedAt": None})
    steps.append({"n": len(steps) + 1, "kind": "handoff", "label": "Your approval", "agent": "You",
                  "status": "todo", "detail": "", "ms": 0, "startedAt": None, "finishedAt": None})
    return steps


def _free_branch(repo: Path, wanted: str) -> str:
    name, n = wanted, 1
    while git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], repo).returncode == 0:
        n += 1
        name = f"{wanted}-{n}"
    return name


def prepare(c: Ctx, plan: dict[str, Any], task: dict[str, Any] | None, project: dict[str, Any], by: str) -> dict[str, Any]:
    """Everything a run needs, decided before anything moves. Raises Refused with the reason."""
    root = onboarding.source_root(project)
    if root is None or not root.is_dir():
        raise Refused(f"{project['name']} has no code on this machine, so there is nothing to work on. "
                      "Onboard a repository in Projects first.")
    if shutil.which("git") is None:
        raise Refused("git is not installed, and every run works in a worktree of its own.")
    found = repo_of(root)
    if found is None:
        raise Refused(f"{project['name']} is not a git repository, so a run has nothing to branch from.")
    repo, prefix = found
    head = git(["rev-parse", "HEAD"], repo)
    if head.returncode != 0:
        raise Refused(f"{project['name']} has no commit yet. Make one, and a run can branch from it.")
    n = c.store.next_run_number()
    ref, base = f"RUN-{n}", head.stdout.strip()
    tests = detect_tests(root)
    doc = {
        "id": f"r{n}-{int(time.time())}", "ref": ref, "projectId": project["id"], "projectName": project["name"],
        "taskRef": (task or {}).get("ref"), "planRef": plan["ref"], "requirement": plan.get("rawRequirement", ""),
        "status": "queued", "branch": _free_branch(repo, f"neurocode/{(task or plan)['ref'].lower()}"),
        "worktree": str(WORKTREES / project["id"] / ref), "repo": str(repo), "prefix": prefix,
        "base": base, "shortBase": base[:7], "startedAt": now_iso(), "finishedAt": None, "requestedBy": by,
        "targets": list(plan.get("affectedFiles", []))[:12],
        "steps": _steps(plan, tests),
        "tests": {"command": tests["command"] if tests else None, "argv": tests["argv"] if tests else None,
                  "status": "not run", "summary": ""},
        "review": {"findings": [], "verdict": "", "by": ""},
        "diff": {"files": 0, "insertions": 0, "deletions": 0, "commits": 0},
        "model": None, "note": "", "removed": False,
    }
    return c.store.insert_run(doc)


# ── running it ───────────────────────────────────────────────────
def _work(doc: dict[str, Any]) -> Path:
    return Path(doc["worktree"]) / doc["prefix"] if doc["prefix"] else Path(doc["worktree"])


def _save(c: Ctx, doc: dict[str, Any]) -> None:
    c.put("runs", c.store.save_run(doc))


def _log(c: Ctx, doc: dict[str, Any], step: int | None, level: str, line: str) -> None:
    entry = c.store.add_run_log(doc["id"], step, level, line)
    c.bus.publish("run", {"runRef": doc["ref"], **entry})


def _cancel_flag(c: Ctx, ref: str) -> threading.Event:
    state = c.runtime.setdefault(ref, {})
    if "cancel" not in state:
        state["cancel"] = threading.Event()
    return state["cancel"]


def execute(c: Ctx, ref: str, resume_from: int | None = None) -> None:
    """Work the steps in order. Blocking: FastAPI runs it as a background task on a worker thread."""
    doc = c.store.one("runs", ref)
    if doc is None or doc["status"] in ("done", "failed", "cancelled"):
        return
    cancel = _cancel_flag(c, ref)
    if resume_from is None:
        cancel.clear()
    try:
        if resume_from is None:
            _open_worktree(c, doc)
            c.record("Run started", f"{ref} · {len(doc['steps'])} steps on {doc['branch']}", project=doc["projectId"],
                     level="info", actor="Orchestrator", kind="agent", task_ref=doc["taskRef"])
        doc["status"] = "running"
        _save(c, doc)
        for step in doc["steps"]:
            if (resume_from is not None and step["n"] < resume_from) or step["status"] in ("done", "skipped", "failed"):
                continue
            if cancel.is_set():
                _finish(c, doc, "cancelled", "Stopped by you.")
                return
            if _step(c, doc, step, cancel):
                return  # waiting for a person
        _finish(c, doc, _verdict(doc), doc.get("note", ""))
    except Exception as e:  # a run never pretends to be alive
        _log(c, doc, None, "err", f"{type(e).__name__}: {str(e)[:300]}")
        _finish(c, doc, "failed", str(e)[:200] or type(e).__name__)


def _open_worktree(c: Ctx, doc: dict[str, Any]) -> None:
    path = Path(doc["worktree"])
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)
        git(["worktree", "prune"], Path(doc["repo"]))
    out = git(["worktree", "add", "-b", doc["branch"], str(path), doc["base"]], Path(doc["repo"]), timeout=300)
    if out.returncode != 0:
        raise RuntimeError(f"git worktree: {out.stderr.strip()[:200]}")
    _log(c, doc, None, "ok", f"worktree ready · {doc['branch']} from {doc['shortBase']}")


def _step(c: Ctx, doc: dict[str, Any], step: dict[str, Any], cancel: threading.Event) -> bool:
    step["status"], step["startedAt"] = "running", now_iso()
    _save(c, doc)
    _log(c, doc, step["n"], "info", f"{step['agent']} · {step['label']}")
    t0 = time.monotonic()
    try:
        if step["kind"] == "edit":
            paused = _edit(c, doc, step)
        elif step["kind"] == "test":
            paused = _test(c, doc, step, cancel)
        elif step["kind"] == "review":
            paused = _review(c, doc, step)
        else:
            paused = _handoff(c, doc, step)
    except Exception as e:
        step["status"], step["detail"] = "failed", f"{type(e).__name__}: {str(e)[:200]}"
        _log(c, doc, step["n"], "err", step["detail"])
        paused = False
    if not paused and step["status"] == "running":
        step["status"] = "done"
    step["ms"], step["finishedAt"] = round((time.monotonic() - t0) * 1000), now_iso()
    _save(c, doc)
    return paused


# ── the steps ────────────────────────────────────────────────────
EDIT_SYSTEM = """You are a senior engineer working inside NeuroCode. You get a requirement, one step of an agreed
plan, and the current contents of the files you may change. Return the complete new content of every file you
change — never a patch, never a fragment, never "// unchanged". Change as little as the step needs, keep the
file's existing style and imports, and never invent an API you cannot see in the files you were given. You may
add a new file beside the ones you are shown. You do not run commands and you never touch anything else.
Reply with one JSON object: {"summary": "what you changed and why", "files": [{"path": "...", "content": "..."}],
"notes": ["anything the operator must know"]}"""

REVIEW_SYSTEM = """You are the reviewer inside NeuroCode. You are given a real diff. Report only what a careful
engineer would stop at: correctness, a missing test for the behaviour that changed, a security or data risk, a
convention the surrounding code follows and this diff breaks. No style nits, no praise.
Reply with one JSON object: {"findings": [{"severity": "HIGH|MEDIUM|LOW", "file": "...", "note": "..."}],
"verdict": "one sentence"}"""


def _context(c: Ctx, doc: dict[str, Any], step: dict[str, Any], work: Path) -> list[tuple[str, str]]:
    """The files this step may change: what the plan named, plus what the code index finds for its words."""
    wanted = list(doc["targets"])
    try:
        wanted += [hit["path"] for hit in codeindex.search(c.store, doc["projectId"], step["label"], limit=6)]
    except Exception:  # a project with no index still gets the plan's files
        pass
    files: list[tuple[str, str]] = []
    seen, total = set(), 0
    for rel in wanted:
        if rel in seen or len(files) >= 8:
            continue
        seen.add(rel)
        f = work / rel
        if not f.is_file():
            continue
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        if total + len(text) > MAX_CONTEXT:
            continue
        total += len(text)
        files.append((rel, text))
    return files


def _edit(c: Ctx, doc: dict[str, Any], step: dict[str, Any]) -> bool:
    work = _work(doc)
    files = _context(c, doc, step, work)
    shown = "\n\n".join(f"--- {rel}\n{text}" for rel, text in files) or "(no file matched; create what the step needs)"
    messages = [
        {"role": "system", "content": EDIT_SYSTEM},
        {"role": "user", "content": f"Project: {doc['projectName']}\nRequirement: {doc['requirement']}\n"
                                    f"Step {step['n']}: {step['label']}\n{step['detail']}\n\nFiles you may change:\n{shown}"},
    ]
    try:
        result = c.gateway.ask(messages, lambda raw: EditOut.model_validate(extract_json(raw, trim=False)),
                               feature="agent", project=doc["projectId"])
    except NoModel as e:
        step["status"] = "skipped"
        step["detail"] = "Needs a model. NeuroCode will not pretend to write code it cannot write."
        _log(c, doc, step["n"], "warn", str(e))
        return False
    doc["model"] = result.provider.model
    _log(c, doc, step["n"], "tool", f"{result.provider.model} read {len(files)} files and answered in {result.ms / 1000:.1f}s")
    written = _apply(work, result.data.files)
    for note in result.data.notes[:5]:
        _log(c, doc, step["n"], "info", str(note)[:300])
    if not written:
        step["detail"] = (result.data.summary or "The model proposed no change for this step.")[:300]
        _log(c, doc, step["n"], "warn", "no file changed")
        return False
    for path in written:
        _log(c, doc, step["n"], "tool", f"wrote {path}")
    _commit(c, doc, step, result.data.summary)
    step["detail"] = result.data.summary[:300]
    return False


def _safe(rel: str) -> Path:
    """A path the runtime is willing to write: inside the worktree, never .git, never upwards. A path
    that tries to leave is refused, never quietly rewritten."""
    p = PurePosixPath(rel.strip().replace("\\", "/"))
    parts = [part for part in p.parts if part != "."]
    if not parts or p.is_absolute() or any(part in ("..", ".git") for part in parts):
        raise ValueError(f"refused to write outside the worktree: {rel}")
    return Path(*parts)


def _apply(work: Path, files: list[FileOut]) -> list[str]:
    written: list[str] = []
    root = work.resolve()
    for f in files[:MAX_FILES]:
        rel = _safe(f.path)
        target = (work / rel).resolve()
        if not str(target).startswith(f"{root}{os.sep}") and target != root:
            raise ValueError(f"refused to write outside the worktree: {f.path}")
        if len(f.content.encode()) > MAX_FILE_BYTES:
            raise ValueError(f"refused to write {f.path}: it is larger than {MAX_FILE_BYTES // 1000} kB")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f.content)
        written.append(str(rel))
    return written


def _stats(doc: dict[str, Any]) -> None:
    tree = Path(doc["worktree"])
    files = insertions = deletions = 0
    for line in git(["diff", "--numstat", f"{doc['base']}..HEAD"], tree).stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        files += 1
        insertions += int(parts[0]) if parts[0].isdigit() else 0
        deletions += int(parts[1]) if parts[1].isdigit() else 0
    count = git(["rev-list", "--count", f"{doc['base']}..HEAD"], tree).stdout.strip()
    doc["diff"] = {"files": files, "insertions": insertions, "deletions": deletions, "commits": int(count or 0)}


def _commit(c: Ctx, doc: dict[str, Any], step: dict[str, Any], summary: str) -> None:
    work = _work(doc)
    git(["add", "-A"], work)
    if not git(["status", "--porcelain"], work).stdout.strip():
        return
    message = f"{step['label']}\n\n{summary.strip()[:500]}\n\nNeuroCode {doc['ref']} · plan {doc['planRef']}"
    out = git(["-c", "user.name=NeuroCode", "-c", "user.email=neurocode@localhost", "-c", "commit.gpgsign=false",
               "commit", "-m", message], work)
    if out.returncode != 0:
        raise RuntimeError(f"git commit: {out.stderr.strip()[:200]}")
    _stats(doc)
    d = doc["diff"]
    _log(c, doc, step["n"], "ok", f"committed · {d['files']} files +{d['insertions']} −{d['deletions']}")


def _test(c: Ctx, doc: dict[str, Any], step: dict[str, Any], cancel: threading.Event) -> bool:
    argv = doc["tests"]["argv"]
    if not argv:
        step["status"], step["detail"] = "skipped", "No test command was found in this project."
        return False
    allowed = c.store.setting(f"runtime.tests.{doc['projectId']}")
    if allowed is None:
        return _pause(c, doc, step, title=f"Run `{doc['tests']['command']}` in {doc['projectName']}",
                      tool=f"Bash({doc['tests']['command']})", risk="MEDIUM",
                      payload=f"cwd {_work(doc)}\ncommand {doc['tests']['command']}",
                      reason="The agent wants to run this project's own tests inside its worktree. Nothing else is run, "
                             "and your answer is remembered for this project.")
    if allowed != "allowed":
        step["status"], step["detail"] = "skipped", "You chose not to run tests in this project."
        return False

    work = _work(doc)
    _log(c, doc, step["n"], "tool", f"$ {doc['tests']['command']}")
    proc = subprocess.Popen(argv, cwd=work, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env={**os.environ, "CI": "1", "NO_COLOR": "1"})
    killer = threading.Timer(TEST_TIMEOUT, proc.kill)
    killer.start()
    tail: list[str] = []
    try:
        for i, line in enumerate(proc.stdout or []):
            if cancel.is_set():
                proc.kill()
                break
            text = line.rstrip()[:400]
            tail = [*tail[-4:], text]
            if i < TEST_LINES:
                _log(c, doc, step["n"], "tool", text)
            elif i == TEST_LINES:
                _log(c, doc, step["n"], "info", f"…output past {TEST_LINES} lines is not kept")
        code = proc.wait(timeout=60)
    finally:
        killer.cancel()
    passed = code == 0
    doc["tests"] = {**doc["tests"], "status": "passed" if passed else "failed",
                    "summary": " · ".join(t for t in tail if t.strip())[:300] or f"exit code {code}"}
    step["status"] = "done" if passed else "failed"
    step["detail"] = doc["tests"]["summary"]
    _log(c, doc, step["n"], "ok" if passed else "err", f"tests {'passed' if passed else f'failed (exit {code})'}")
    return False


def _rule_review(diff: str) -> tuple[list[dict[str, Any]], str, str]:
    added = [ln[1:] for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
    paths = re.findall(r"^\+\+\+ b/(.+)$", diff, re.M)
    findings: list[dict[str, Any]] = []
    for rx, severity, note in ((SECRETS, "HIGH", "A secret looks hard-coded:"), (LEFTOVERS, "MEDIUM", "Debugging output left in:"),
                               (TODO, "LOW", "A TODO was added:")):
        for line in [ln.strip()[:120] for ln in added if rx.search(ln)][:3]:
            findings.append({"severity": severity, "file": "", "note": f"{note} {line}"})
    if paths and not any(codeindex.TEST_FILE.search(p) for p in paths):
        findings.append({"severity": "MEDIUM", "file": "", "note": "No test file was touched, so nothing new guards this change."})
    return findings, "Checked by rules only — a model would read the diff properly.", "offline rules"


def _review(c: Ctx, doc: dict[str, Any], step: dict[str, Any]) -> bool:
    diff = git(["diff", f"{doc['base']}..HEAD"], Path(doc["worktree"]), timeout=60).stdout[:MAX_DIFF]
    if not diff.strip():
        step["status"], step["detail"] = "skipped", "Nothing changed, so there is nothing to review."
        return False
    messages = [{"role": "system", "content": REVIEW_SYSTEM},
                {"role": "user", "content": f"Requirement: {doc['requirement']}\n\nDiff:\n{diff}"}]
    try:
        result = c.gateway.ask(messages, lambda raw: ReviewOut.model_validate(extract_json(raw, trim=False)),
                               feature="review", project=doc["projectId"])
        findings = [f.model_dump() for f in result.data.findings][:20]
        verdict, by = result.data.verdict[:300], result.provider.model
    except NoModel:
        findings, verdict, by = _rule_review(diff)
    except Exception as e:  # a model that answers badly must not lose the review
        findings, verdict, by = _rule_review(diff)
        verdict = f"{verdict} The model failed: {type(e).__name__}."
    doc["review"] = {"findings": findings, "verdict": verdict, "by": by}
    step["detail"] = verdict or f"{len(findings)} findings"
    high = sum(1 for f in findings if f["severity"] == "HIGH")
    _log(c, doc, step["n"], "warn" if high else "ok", f"{len(findings)} findings ({high} high) · reviewed by {by}")
    return False


def _handoff(c: Ctx, doc: dict[str, Any], step: dict[str, Any]) -> bool:
    d = doc["diff"]
    if d["files"] == 0:
        step["status"], step["detail"] = "skipped", "Nothing to accept: no file changed."
        return False
    high = [f for f in doc["review"]["findings"] if f["severity"] == "HIGH"]
    failed = doc["tests"]["status"] == "failed"
    lines = [f"branch {doc['branch']} from {doc['shortBase']}",
             f"{d['files']} files · +{d['insertions']} −{d['deletions']} · {d['commits']} commits",
             f"tests {doc['tests']['status']}{f' · {doc['tests']['summary']}' if doc['tests']['summary'] else ''}",
             f"review by {doc['review']['by'] or 'nobody'}: {doc['review']['verdict'] or '—'}"]
    return _pause(c, doc, step, title=f"Accept {doc['ref']}: {d['files']} files on {doc['branch']}",
                  tool=f"Merge({doc['branch']})", risk="HIGH" if (high or failed) else "MEDIUM",
                  payload="\n".join(lines),
                  reason=f"{doc['taskRef'] or doc['planRef']} — approve and the branch is yours to merge; refuse and the "
                         f"branch and its worktree are removed. Nothing was merged and nothing left the worktree.")


def _pause(c: Ctx, doc: dict[str, Any], step: dict[str, Any], *, title: str, tool: str, risk: str, payload: str,
           reason: str) -> bool:
    n = c.store.next_approval_number()
    approval = {"id": f"ap-{doc['ref'].lower()}-{step['n']}", "ref": f"APPR-{n}", "title": title, "agent": step["agent"],
                "tool": tool, "risk": risk, "requestedAt": "just now", "projectId": doc["projectId"], "payload": payload,
                "reason": reason, "status": "pending", "runRef": doc["ref"], "step": step["n"], "taskRef": doc["taskRef"]}
    c.put("approvals", c.store.insert("approvals", approval, ref=approval["ref"], status="pending"))
    step["status"], step["detail"] = "waiting", f"Waiting for you · {approval['ref']}"
    doc["status"], doc["waitingOn"] = "waiting", approval["ref"]
    _log(c, doc, step["n"], "warn", f"waiting for your decision · {approval['ref']} · {title}")
    c.record("Approval needed", f"{approval['ref']} · {title}", project=doc["projectId"], level="warn",
             actor=step["agent"], kind="agent", task_ref=doc["taskRef"])
    _save(c, doc)
    return True


# ── decisions, stopping, cleaning up ─────────────────────────────
def resume(c: Ctx, ref: str, step_n: int, approved: bool) -> None:
    """Called when a person decides on an approval a run is waiting for."""
    doc = c.store.one("runs", ref)
    if doc is None or doc["status"] != "waiting":
        return
    step = next((s for s in doc["steps"] if s["n"] == step_n), None)
    if step is None:
        return
    doc.pop("waitingOn", None)
    if step["kind"] == "test":
        c.store.set_setting(f"runtime.tests.{doc['projectId']}", "allowed" if approved else "refused")
        if approved:
            step["status"] = "todo"
            _log(c, doc, step_n, "ok", "you allowed this project's tests to run")
            _save(c, doc)
            execute(c, ref, resume_from=step_n)
        else:
            step["status"], step["detail"] = "skipped", "You chose not to run tests in this project."
            _log(c, doc, step_n, "warn", "tests refused")
            _save(c, doc)
            execute(c, ref, resume_from=step_n + 1)
        return
    if approved:
        step["status"], step["detail"] = "done", "Accepted by you."
        _log(c, doc, step_n, "ok", f"accepted · merge it with: git merge {doc['branch']}")
        _task(c, doc, "review")
        _finish(c, doc, _verdict(doc), f"Accepted. Merge it with: git merge {doc['branch']}")
    else:
        step["status"], step["detail"] = "failed", "You refused the changes."
        _log(c, doc, step_n, "warn", "refused — removing the branch and its worktree")
        cleanup(c, doc)
        _finish(c, doc, "cancelled", "You refused the changes; the branch and its worktree were removed.")


def cancel(c: Ctx, doc: dict[str, Any]) -> dict[str, Any]:
    _cancel_flag(c, doc["ref"]).set()
    _log(c, doc, None, "warn", "stop requested")
    if doc["status"] in ("queued", "waiting"):  # nothing is working, so stop it here
        _finish(c, doc, "cancelled", "Stopped by you.")
    return c.store.one("runs", doc["ref"]) or doc


def cleanup(c: Ctx, doc: dict[str, Any]) -> None:
    """Remove the worktree and the branch. The commits stay in the repository until git prunes them."""
    repo, tree = Path(doc["repo"]), Path(doc["worktree"])
    if tree.exists():
        git(["worktree", "remove", "--force", str(tree)], repo, timeout=120)
    if tree.exists():
        shutil.rmtree(tree, ignore_errors=True)
    git(["worktree", "prune"], repo)
    git(["branch", "-D", doc["branch"]], repo)
    doc["removed"] = True
    _log(c, doc, None, "info", f"worktree and branch {doc['branch']} removed")


def diff_of(doc: dict[str, Any]) -> dict[str, Any]:
    tree = Path(doc["worktree"])
    if doc.get("removed") or not tree.exists():
        return {"patch": "", "truncated": False, "stat": doc["diff"], "gone": True}
    patch = git(["diff", f"{doc['base']}..HEAD"], tree, timeout=60).stdout
    return {"patch": patch[:MAX_DIFF], "truncated": len(patch) > MAX_DIFF, "stat": doc["diff"], "gone": False}


def _verdict(doc: dict[str, Any]) -> str:
    return "failed" if any(s["status"] == "failed" for s in doc["steps"]) else "done"


def _task(c: Ctx, doc: dict[str, Any], status: str) -> None:
    task = c.store.one("tasks", doc["taskRef"]) if doc.get("taskRef") else None
    if task and task["status"] != status:
        task.update(status=status, updatedAt="just now")
        c.put("tasks", c.store.save_task(task))


def _finish(c: Ctx, doc: dict[str, Any], status: str, note: str) -> None:
    doc["status"], doc["finishedAt"] = status, now_iso()
    if note:
        doc["note"] = note
    doc.pop("waitingOn", None)
    _save(c, doc)
    c.runtime.pop(doc["ref"], None)
    d = doc["diff"]
    level = "ok" if status == "done" else "warn" if status == "cancelled" else "err"
    _log(c, doc, None, level, f"run {status}")
    c.record(f"Run {status}", f"{doc['ref']} · {d['files']} files +{d['insertions']} −{d['deletions']} on {doc['branch']}"
                              + (f" · {note}" if note else ""),
             project=doc["projectId"], level=level, actor="Orchestrator", kind="agent", task_ref=doc["taskRef"])
