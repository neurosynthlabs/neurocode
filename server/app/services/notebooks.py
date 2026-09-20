"""Jupyter notebooks in the Workbench: reading and saving `.ipynb` files, and running their cells.

A notebook is a file on the machine like any other the Workbench opens, so it goes through the one door
every path goes through (`machine.inside`), is saved only over the version the page opened (the same
SHA-1 check as the editor's save), and every save is in the audit log. It is read and written as nbformat
4 JSON: the page gets each cell's source and each output's text as one string, and a save splits them
back into lines and writes the notebook exactly as Jupyter does (one-space indent, keys sorted), so a
notebook saved here and one saved by Jupyter differ only where their contents do.

Cells run on a real Jupyter kernel started with jupyter_client — never an imitation of one. Which kernel
is decided from the machine, in this order: the project's own interpreter (a `.venv`, `venv` or `env`
beside the notebook or in a folder above it) when it has ipykernel; the kernelspec the notebook's
metadata names, when it is installed; any installed kernelspec for the notebook's language; the
machine's python3 when it has ipykernel. Never the API's own environment: the notebook's packages are
not installed there, and the API's secrets are. When nothing fits, the refusal says what to install.
Opening a notebook only looks at the machine; an interpreter is run — which is how it is asked its
version and whether it has ipykernel — when the person starts a kernel, and that start is audited.

A kernel lives in this process and belongs to the person who started it, one per open notebook. It is
shut down when the notebook is closed, when nobody has had it open for a while (settings), and when the
API stops; the server keeps a ceiling on how many run at once. What a kernel prints is fanned out to the
sockets watching it and kept per execution, so a page that reconnects — or a script without a socket —
can read what a cell printed. Nothing here is driven by a model.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..repositories import AuditRepository, NotFound, ProjectRepository
from ..schemas.machine import Saved, stamp
from ..settings import Settings
from . import code, machine
from .debug import Watcher
from .errors import Refused
from .identity import Person
from .terminal import MACHINE, base_env

log = logging.getLogger(__name__)

#: A notebook the Workbench opens. Plots and tables are stored inside the file, so this is well above the
#: editor's 2 MB; beyond it the notebook is a dataset in disguise and a browser tab chokes on it.
MAX_BYTES = 40 * 1024 * 1024
MAX_CELLS = 5000
#: One cell's code as sent to run.
MAX_CODE = 1024 * 1024
#: What one execution keeps of its output, text and data together. Past it the cell says it was cut,
#: rather than a loop printing forever filling the server's memory and the page.
OUTPUT_BUDGET = 8 * 1024 * 1024
#: Executions a kernel remembers for pages and scripts that ask what a cell printed.
KEPT_RUNS = 200
#: Stream text is gathered this long before it is sent, so a loop printing ten thousand lines sends
#: dozens of messages rather than ten thousand.
FLUSH_SECONDS = 0.05
REAP_SECONDS = 10.0
#: How long an interpreter is given to say whether it has ipykernel.
PROBE_SECONDS = 20
SHUTDOWN_SECONDS = 10.0

CELL_TYPES = ("code", "markdown", "raw")
CELL_ID = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
#: Folders that hold a project's own interpreter, as `terminal.python_for` looks for them.
VENVS = (".venv", "venv", "env")
#: What to install for a language with no kernel on this machine.
KERNELS_FOR = {
    "r": "IRkernel (in R: install.packages('IRkernel'); IRkernel::installspec())",
    "julia": "IJulia (in Julia: using Pkg; Pkg.add(\"IJulia\"))",
    "javascript": "a JavaScript kernel, such as Deno's (deno jupyter --install)",
    "typescript": "a TypeScript kernel, such as Deno's (deno jupyter --install)",
    "scala": "Almond (https://almond.sh)",
    "rust": "evcxr_jupyter (cargo install evcxr_jupyter && evcxr_jupyter --install)",
    "go": "gonb (go install github.com/janpfeifer/gonb@latest && gonb --install)",
    "c++": "xeus-cling (conda install -c conda-forge xeus-cling)",
    "sql": "xeus-sql (conda install -c conda-forge xeus-sql)",
    "bash": "bash_kernel (pip install bash_kernel && python -m bash_kernel.install)",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(at: datetime | None) -> str | None:
    return at.isoformat(timespec="seconds") if at else None


# ── where a notebook is ───────────────────────────────────────────
async def resolve(session: AsyncSession, path: str, project_id: str | None) -> Path:
    """The real path of a notebook a request names: absolute, or a project path (label-prefixed for a
    further source) when a project is given — and in either case inside the machine's roots."""
    text = (path or "").strip()
    if project_id and text and not text.startswith(("/", "~")):
        project = await ProjectRepository(session).get(project_id)
        if project is None:
            raise NotFound(f"project {project_id}")
        located = await code.locate(session, project, text)
        if located is None:
            raise Refused(f"{text} is in a part of {project.name} that is not on this machine.", status=409)
        text = str(located)
    real = await asyncio.to_thread(machine.inside, text)
    if real.suffix.lower() != ".ipynb":
        raise Refused(f"{real.name} is not a notebook (.ipynb).", status=422)
    return real


def _regular(file: Path) -> os.stat_result:
    try:
        st = os.stat(file)
    except FileNotFoundError as gone:
        raise Refused(f"{file} does not exist.", status=404) from gone
    except PermissionError as denied:
        raise machine.unreadable(file.parent, denied) from denied
    if not stat.S_ISREG(st.st_mode):
        raise Refused(f"{file} is not a file.", status=409)
    return st


def _size(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB" if n >= 1024 * 1024 else f"{n / 1024:.0f} KB"


# ── the nbformat document ─────────────────────────────────────────
def _joined(value: Any) -> str:
    """nbformat stores multi-line text as a list of lines or as one string; the page gets one string."""
    if isinstance(value, list):
        return "".join(str(v) for v in value)
    return value if isinstance(value, str) else ""


def _json_mime(mime: str) -> bool:
    return mime == "application/json" or (mime.startswith("application/") and mime.endswith("+json"))


def _output_in(raw: Any) -> dict[str, Any] | None:
    """One stored output, as the page draws it. Anything that is not an nbformat 4 output is dropped."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("output_type")
    if kind == "stream":
        return {"output_type": "stream", "name": "stderr" if raw.get("name") == "stderr" else "stdout",
                "text": _joined(raw.get("text"))}
    if kind in ("display_data", "execute_result"):
        data: dict[str, Any] = {}
        for mime, value in (raw.get("data") or {}).items():
            if _json_mime(str(mime)):
                data[str(mime)] = value
            elif str(mime).startswith("image/") and not str(mime).startswith("image/svg"):
                data[str(mime)] = _joined(value).replace("\n", "")
            else:
                data[str(mime)] = _joined(value)
        out: dict[str, Any] = {"output_type": kind, "data": data,
                               "metadata": raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}}
        if kind == "execute_result":
            count = raw.get("execution_count")
            out["execution_count"] = count if isinstance(count, int) else None
        return out
    if kind == "error":
        trace = raw.get("traceback")
        return {"output_type": "error", "ename": str(raw.get("ename", "")), "evalue": str(raw.get("evalue", "")),
                "traceback": [str(t) for t in trace] if isinstance(trace, list) else []}
    return None


def _cell_in(raw: Any, seen: set[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise Refused("A cell of this notebook is not an object; the file is not a valid notebook.", status=422)
    kind = raw.get("cell_type")
    if kind not in CELL_TYPES:
        kind = "raw"
    cell_id = raw.get("id")
    # Every cell gets an id the page can key on: nbformat 4.5 requires one, and an older notebook is
    # upgraded to 4.5 the first time it is saved here, as Jupyter does.
    if not isinstance(cell_id, str) or not CELL_ID.match(cell_id) or cell_id in seen:
        cell_id = uuid.uuid4().hex[:8]
    seen.add(cell_id)
    cell: dict[str, Any] = {"id": cell_id, "cell_type": kind, "source": _joined(raw.get("source")),
                            "metadata": raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}}
    if kind == "code":
        count = raw.get("execution_count")
        cell["execution_count"] = count if isinstance(count, int) else None
        cell["outputs"] = [o for o in (_output_in(x) for x in raw.get("outputs") or []) if o is not None]
    elif isinstance(raw.get("attachments"), dict):
        cell["attachments"] = raw["attachments"]
    return cell


def blank(language: str = "python") -> dict[str, Any]:
    """What an empty `.ipynb` file opens as: a notebook with one empty code cell. Nothing is written
    until the person saves."""
    metadata: dict[str, Any] = {"language_info": {"name": language}}
    return {"nbformat": 4, "nbformat_minor": 5, "metadata": metadata,
            "cells": [{"id": uuid.uuid4().hex[:8], "cell_type": "code", "source": "", "metadata": {},
                       "execution_count": None, "outputs": []}]}


def parse(data: bytes, name: str) -> dict[str, Any]:
    """An `.ipynb` file's bytes as the page reads them, or a refusal that says what is wrong with it."""
    if not data.strip():
        return blank()
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as broken:
        raise Refused(f"{name} is not valid notebook JSON: {broken}.", status=422) from broken
    if not isinstance(raw, dict) or not isinstance(raw.get("cells"), list):
        raise Refused(f"{name} is not a Jupyter notebook: it has no list of cells.", status=422)
    major = raw.get("nbformat")
    if major != 4:
        raise Refused(f"{name} is a version {major} notebook. This Workbench reads version 4 — open it once in "
                      "Jupyter, which converts it when it saves.", status=422)
    if len(raw["cells"]) > MAX_CELLS:
        raise Refused(f"{name} has {len(raw['cells'])} cells; the Workbench opens up to {MAX_CELLS}.", status=413)
    seen: set[str] = set()
    minor = raw.get("nbformat_minor")
    return {"nbformat": 4, "nbformat_minor": minor if isinstance(minor, int) else 0,
            "metadata": raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
            "cells": [_cell_in(c, seen) for c in raw["cells"]]}


def _lines(text: str) -> list[str]:
    return text.splitlines(keepends=True)


def _output_out(raw: Any) -> dict[str, Any]:
    out = _output_in(raw)
    if out is None:
        raise Refused("An output of this notebook is not an nbformat 4 output.", status=422)
    if out["output_type"] == "stream":
        out["text"] = _lines(out["text"])
    elif out["output_type"] in ("display_data", "execute_result"):
        out["data"] = {mime: value if _json_mime(mime) else _lines(value) for mime, value in out["data"].items()}
    return out


def serialise(document: dict[str, Any]) -> bytes:
    """The page's notebook as an nbformat 4.5 file, written the way Jupyter writes one: sources and texts
    split into lines, one-space indent, keys sorted, a newline at the end. Anything a cell or output
    carries beyond nbformat (a page's own bookkeeping) is left out."""
    cells = document.get("cells")
    if not isinstance(cells, list):
        raise Refused("A notebook has a list of cells.", status=422)
    if len(cells) > MAX_CELLS:
        raise Refused(f"A notebook saved here has at most {MAX_CELLS} cells.", status=413)
    seen: set[str] = set()
    written: list[dict[str, Any]] = []
    for raw in cells:
        cell = _cell_in(raw, seen)
        if raw.get("cell_type") not in CELL_TYPES:
            raise Refused(f"A cell's type is one of: {', '.join(CELL_TYPES)}.", status=422)
        out: dict[str, Any] = {"id": cell["id"], "cell_type": cell["cell_type"], "metadata": cell["metadata"],
                               "source": _lines(cell["source"])}
        if cell["cell_type"] == "code":
            out["execution_count"] = cell["execution_count"]
            out["outputs"] = [_output_out(o) for o in raw.get("outputs") or []]
        elif "attachments" in cell:
            out["attachments"] = cell["attachments"]
        written.append(out)
    metadata = document.get("metadata")
    minor = document.get("nbformat_minor")
    notebook = {"cells": written, "metadata": metadata if isinstance(metadata, dict) else {},
                "nbformat": 4, "nbformat_minor": max(minor if isinstance(minor, int) else 0, 5)}
    return (json.dumps(notebook, indent=1, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def language_of(document: dict[str, Any]) -> str:
    """The notebook's language, as its metadata says: the kernelspec's, else language_info's, else Python."""
    metadata = document.get("metadata") or {}
    spec = metadata.get("kernelspec") if isinstance(metadata.get("kernelspec"), dict) else {}
    info = metadata.get("language_info") if isinstance(metadata.get("language_info"), dict) else {}
    return str(spec.get("language") or info.get("name") or "python").strip().lower() or "python"


def read(file: Path) -> dict[str, Any]:
    """A notebook for the page: the document, and the SHA-1 of the file's bytes a save sends back."""
    st = _regular(file)
    if st.st_size > MAX_BYTES:
        raise Refused(f"{file.name} is {_size(st.st_size)}; the Workbench opens notebooks up to "
                      f"{_size(MAX_BYTES)}. Clear its outputs in Jupyter to make it smaller.", status=413)
    try:
        data = file.read_bytes()
    except PermissionError as denied:
        raise machine.unreadable(file.parent, denied) from denied
    document = parse(data, file.name)
    spec = document["metadata"].get("kernelspec") if isinstance(document["metadata"].get("kernelspec"), dict) else {}
    return {"path": str(file), "name": file.name, "size": len(data), "modified": stamp(st.st_mtime),
            "sha1": hashlib.sha1(data).hexdigest(), "language": language_of(document),
            "kernelName": spec.get("name") if isinstance(spec.get("name"), str) else None,
            "new": not data.strip(), "notebook": document}


def write(file: Path, document: dict[str, Any], expect_sha1: str) -> tuple[Saved, str]:
    """Save the page's notebook over the file — only if the file is still the one the page opened.

    Written in place, as the editor's save is (see `machine.write` for why not a rename)."""
    st = _regular(file)
    # The whole file is read below, to see whether it is still the one the page opened. A file too big to
    # open here is refused before that read rather than pulled into memory: nothing this page opened, and
    # so nothing it is saving over, can be that large.
    if st.st_size > MAX_BYTES:
        raise Refused(f"{file.name} is {_size(st.st_size)}; the Workbench opens notebooks up to "
                      f"{_size(MAX_BYTES)}, so this is not a notebook it can save over.", status=413)
    data = serialise(document)
    if len(data) > MAX_BYTES:
        raise Refused(f"The notebook is {_size(len(data))}; notebooks are saved up to {_size(MAX_BYTES)}. Clear "
                      "some outputs first.", status=413)
    try:
        before = hashlib.sha1(file.read_bytes()).hexdigest()
    except PermissionError as denied:
        raise machine.unreadable(file.parent, denied) from denied
    if before != expect_sha1.strip().lower():
        raise Refused(f"{file.name} changed on disk since you opened it.", status=409)
    try:
        with open(file, "r+b") as out:
            out.write(data)
            out.truncate()
    except PermissionError as denied:
        raise Refused(f"This server's account may not write {file}.", status=403) from denied
    st = os.stat(file)
    return Saved(path=str(file), size=st.st_size, modified=st.st_mtime, sha1=hashlib.sha1(data).hexdigest()), before


# ── which kernel runs a notebook ──────────────────────────────────
@dataclass(frozen=True, slots=True)
class Choice:
    """A kernel that can run a notebook. `source`: project (its own interpreter), kernelspec (installed
    on the machine) or machine (the machine's python3)."""

    name: str
    display: str
    language: str
    source: str
    argv: tuple[str, ...]
    interpreter: str | None
    #: The folder of the project's environment, put first on PATH so `!pip` in a cell uses it.
    venv: str | None = None

    def json(self) -> dict[str, Any]:
        return {"name": self.name, "displayName": self.display, "language": self.language, "source": self.source,
                "interpreter": self.interpreter}


_probed: dict[tuple[str, float], str | None] = {}


def python_version(python: str) -> str | None:
    """The interpreter's version when it can import ipykernel, else None. Asked of the interpreter
    itself — the only honest answer — and remembered until the interpreter file changes."""
    try:
        key = (python, os.stat(python).st_mtime)
    except OSError:
        return None
    if key in _probed:
        return _probed[key]
    try:
        done = subprocess.run([python, "-c", "import sys, ipykernel; print('%d.%d.%d' % sys.version_info[:3])"],
                              capture_output=True, text=True, timeout=PROBE_SECONDS, env=base_env(), check=False)
        found = done.stdout.strip() if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        found = None
    _probed[key] = found
    return found


def project_python(folder: Path) -> Path | None:
    """The nearest project environment's python: beside the notebook, or in a folder above it — never
    above the machine's roots."""
    bounds = machine._real_roots()
    here = folder
    while True:
        for venv in VENVS:
            candidate = here / venv / "bin" / "python"
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return candidate
        if here in bounds or here.parent == here or not any(here.is_relative_to(b) for b in bounds):
            return None
        here = here.parent


def installed_specs() -> dict[str, Any]:
    """The kernelspecs installed on this machine, by name — less the one in the API's own environment,
    which would run a person's notebook beside the API's secrets and without the notebook's packages."""
    from jupyter_client.kernelspec import KernelSpecManager

    manager = KernelSpecManager(ensure_native_kernel=False)
    own = Path(os.path.realpath(sys.prefix))
    found: dict[str, Any] = {}
    for name, resource in sorted(manager.find_kernel_specs().items()):
        if Path(os.path.realpath(resource)).is_relative_to(own):
            continue
        try:
            found[name] = manager.get_kernel_spec(name)
        except Exception as broken:  # a broken kernel.json is the machine's, and is left out, said in the log
            log.warning("kernelspec %s could not be read: %s", name, broken)
    return found


def _spec_argv(argv: list[str]) -> tuple[str, ...]:
    """A kernelspec's command, with a bare `python` found on the machine's PATH. jupyter_client would put
    the API's own interpreter there instead."""
    words = [str(a) for a in argv]
    if words and re.fullmatch(r"python[\d.]*", words[0]):
        words[0] = shutil.which(words[0], path=base_env().get("PATH")) or words[0]
    return tuple(words)


def _from_spec(name: str, spec: Any) -> Choice:
    argv = _spec_argv(list(spec.argv))
    return Choice(name=name, display=str(spec.display_name or name), language=str(spec.language or "").lower(),
                  source="kernelspec", argv=argv, interpreter=argv[0] if argv else None)


def _python_argv(python: str) -> tuple[str, ...]:
    return (python, "-m", "ipykernel_launcher", "-f", "{connection_file}")


def missing(language: str, folder: Path) -> str:
    """What to install when no kernel can run a notebook in this language, in words that can be followed."""
    if language == "python":
        own = project_python(folder)
        if own is not None:
            return (f"No Jupyter kernel for Python here. The project's environment ({own}) does not have ipykernel: "
                    f"run {own} -m pip install ipykernel, then start the kernel again.")
        return ("No Jupyter kernel for Python here. Make an environment beside the notebook "
                "(python3 -m venv .venv) and install ipykernel in it (.venv/bin/python -m pip install ipykernel), "
                "or install ipykernel for the machine's python3.")
    what = KERNELS_FOR.get(language, f"a Jupyter kernel for {language}")
    return f"No Jupyter kernel for {language} on this machine. Install {what}, then start the kernel again."


def options(file: Path, language: str, named: str | None, *, probe: bool = True) -> dict[str, Any]:
    """What could run this notebook, and what would be chosen. Blocking: with `probe`, it asks interpreters.

    Asking an interpreter its version runs it, and a `.venv/bin/python` beside a notebook is a file the
    repository controls — anything that can write one file in a project folder would be writing a command
    this server runs. So it is asked only where a person asked for it to run: starting a kernel, which is
    audited. Merely opening a notebook passes `probe=False` and gets each interpreter as it was found on
    disk, with no version, and no claim yet that it has ipykernel; that is settled when the kernel starts,
    and `missing` then says what to install.
    """
    folder = file.parent
    offered: list[Choice] = []
    own = project_python(folder)
    if language == "python" and own is not None:
        version = python_version(str(own)) if probe else None
        if version is not None or not probe:
            venv = own.parent.parent
            display = f"Python {version} ({venv.name})" if version else f"Python ({venv.name})"
            offered.append(Choice(name="project", display=display, language="python",
                                  source="project", argv=_python_argv(str(own)), interpreter=str(own),
                                  venv=str(venv)))
    specs = installed_specs()
    for name, spec in specs.items():
        offered.append(_from_spec(name, spec))
    if language == "python":
        system = shutil.which("python3", path=base_env().get("PATH"))
        if system and (own is None or os.path.realpath(system) != os.path.realpath(own)):
            version = python_version(system) if probe else None
            if version is not None or not probe:
                display = f"Python {version} (this machine)" if version else "Python (this machine)"
                offered.append(Choice(name="machine", display=display, language="python",
                                      source="machine", argv=_python_argv(system), interpreter=system))
    chosen = next((c for c in offered if c.source == "project"), None)
    chosen = chosen or next((c for c in offered if named and c.source == "kernelspec" and c.name == named), None)
    chosen = chosen or next((c for c in offered if c.language == language), None)
    return {"language": language, "choice": chosen, "available": offered,
            "missing": None if chosen else missing(language, folder)}


def options_json(found: dict[str, Any]) -> dict[str, Any]:
    return {"language": found["language"], "choice": found["choice"].json() if found["choice"] else None,
            "available": [c.json() for c in found["available"]], "missing": found["missing"]}


# ── a running kernel ──────────────────────────────────────────────
class Run:
    """One execution of one cell: what it printed, as nbformat outputs, and how it ended."""

    def __init__(self, request_id: str, cell_id: str) -> None:
        self.id = request_id
        self.cell_id = cell_id
        self.status = "queued"           # queued | running | ok | error | aborted
        self.execution_count: int | None = None
        self.outputs: list[dict[str, Any]] = []
        #: The display id each output was shown under, when it had one — `update_display_data` names it.
        self.display_ids: list[str | None] = []
        self.clear_on_next = False
        self.used = 0
        self.truncated = False
        #: The kernel's reply (ok | error | aborted) and its idle status: a run is over once both arrived.
        self.replied = False
        self.reply = "ok"
        self.idle = False
        self.started_at: datetime | None = None
        self.ended_at: datetime | None = None

    @property
    def finished(self) -> bool:
        return self.status in ("ok", "error", "aborted")

    def json(self) -> dict[str, Any]:
        return {"requestId": self.id, "cellId": self.cell_id, "status": self.status,
                "executionCount": self.execution_count, "outputs": self.outputs, "truncated": self.truncated,
                "startedAt": _stamp(self.started_at), "endedAt": _stamp(self.ended_at)}


def _weight(output: dict[str, Any]) -> int:
    if output["output_type"] == "stream":
        return len(output["text"])
    if output["output_type"] == "error":
        return sum(len(t) for t in output["traceback"]) + len(output["evalue"])
    return sum(len(v) if isinstance(v, str) else len(json.dumps(v)) for v in output["data"].values())


class Kernel:
    """A Jupyter kernel process and the client speaking to it, for one person's one notebook."""

    def __init__(self, *, owner: str, path: Path, choice: Choice, start_seconds: float) -> None:
        self.id = uuid.uuid4().hex[:12]
        self.owner = owner
        self.path = path
        self.choice = choice
        self.start_seconds = start_seconds
        self.status = "starting"         # starting | idle | busy | restarting | dead | closed
        self.note = ""
        self.execution_count = 0
        self.started_at = _now()
        self.last_activity = self.started_at
        self.watchers: set[Watcher] = set()
        self.runs: OrderedDict[str, Run] = OrderedDict()
        self.restarts = 0
        self._manager: Any = None
        self._client: Any = None
        self._folder = tempfile.mkdtemp(prefix="nc-kernel-")
        self._log = os.path.join(self._folder, "kernel.log")
        self._readers: list[asyncio.Task[None]] = []
        self._pending: OrderedDict[tuple[str, str], list[str]] = OrderedDict()
        self._flush: asyncio.TimerHandle | None = None
        self._log_file: Any = None

    # ── life ──────────────────────────────────────────────────────
    def _env(self) -> dict[str, str]:
        extra: dict[str, str] = {"PYDEVD_DISABLE_FILE_VALIDATION": "1"}
        env = base_env(extra)
        if self.choice.venv:
            env["VIRTUAL_ENV"] = self.choice.venv
            env["PATH"] = os.pathsep.join([os.path.join(self.choice.venv, "bin"), env.get("PATH", "")])
        return env

    async def start(self) -> None:
        from jupyter_client.kernelspec import KernelSpec, KernelSpecManager
        from jupyter_client.manager import AsyncKernelManager

        choice = self.choice

        class _This(KernelSpecManager):
            """The one kernelspec this kernel runs, whatever its name: jupyter_client asks a manager."""

            def get_kernel_spec(self, kernel_name: str) -> KernelSpec:  # noqa: ARG002 — one spec only
                return KernelSpec(argv=list(choice.argv), display_name=choice.display, language=choice.language)

        # A Python kernel talks over Unix sockets in a folder only this account can open; other kernels do
        # not all speak IPC, and get loopback TCP (every message is still signed with the session key).
        ipc = choice.language == "python"
        options: dict[str, Any] = {"kernel_name": "neurocode", "kernel_spec_manager": _This(),
                                   "connection_file": os.path.join(self._folder, "connection.json")}
        if ipc:
            options.update(transport="ipc", ip=os.path.join(self._folder, "k"))
        self._manager = AsyncKernelManager(**options)
        # The kernel's own output goes to a file of its folder — kept open for the kernel's life, because a
        # restart launches the process again with the same arguments — and its tail explains a failure.
        self._log_file = open(self._log, "ab")  # noqa: SIM115 — closed in `close`
        try:
            await self._manager.start_kernel(cwd=str(self.path.parent), env=self._env(),
                                             stdout=self._log_file, stderr=self._log_file)
        except (OSError, RuntimeError, ValueError) as failed:
            self._end("dead", f"The kernel did not start: {failed}")
            raise Refused(f"The kernel did not start: {failed}", status=502) from failed
        self._client = self._manager.client()
        self._client.start_channels()
        await self._ready()

    async def _ready(self) -> None:
        try:
            await self._client.wait_for_ready(timeout=self.start_seconds)
        except (RuntimeError, TimeoutError) as failed:
            said = self._said()
            await self._stop_process()
            words = "The kernel did not start" + (f": {said}" if said else f" ({failed}).")
            self._end("dead", words)
            raise Refused(words, status=502) from failed
        self._readers = [asyncio.create_task(self._read_iopub()), asyncio.create_task(self._read_shell())]
        self._set_status("idle")

    def _said(self) -> str:
        """The last lines the kernel wrote before it failed: an ImportError says more than a timeout."""
        try:
            with open(self._log, "rb") as log_file:
                tail = log_file.read()[-2000:].decode("utf-8", "replace")
        except OSError:
            return ""
        lines = [line for line in tail.splitlines() if line.strip()]
        return " ".join(lines[-3:])[:600]

    async def restart(self) -> None:
        if self.status in ("dead", "closed") and self._manager is None:
            raise Refused("This kernel is closed. Start a new one.", status=409)
        self._set_status("restarting")
        await self._stop_readers()
        self._abort_all("The kernel restarted before this cell finished.")
        try:
            await self._manager.restart_kernel(now=True)
        except (RuntimeError, OSError) as failed:
            self._end("dead", f"The kernel did not restart: {failed}")
            raise Refused(f"The kernel did not restart: {failed}", status=502) from failed
        self.execution_count = 0
        self.restarts += 1
        self.note = ""
        await self._ready()

    async def interrupt(self) -> None:
        self._alive()
        await self._manager.interrupt_kernel()
        self.touch()

    async def close(self) -> None:
        if self.status == "closed":
            return
        await self._stop_readers()
        self._abort_all("The kernel was shut down before this cell finished.")
        await self._stop_process()
        self._end("closed", self.note)
        self._broadcast({"type": "closed"})
        if self._log_file is not None:
            self._log_file.close()
        shutil.rmtree(self._folder, ignore_errors=True)

    async def _stop_readers(self) -> None:
        for task in self._readers:
            task.cancel()
        for task in self._readers:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._readers = []

    async def _stop_process(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.stop_channels()
        if self._manager is not None:
            try:
                await asyncio.wait_for(self._manager.shutdown_kernel(now=True), timeout=SHUTDOWN_SECONDS)
            except Exception as failed:  # the process is going regardless; say so in the log, not to the page
                log.warning("kernel %s did not shut down cleanly: %s", self.id, failed)

    def alive(self) -> bool:
        return self._manager is not None and self.status not in ("dead", "closed", "starting")

    async def check(self) -> None:
        """A kernel that died on its own (killed, out of memory) says so, and its cells stop waiting."""
        if self._manager is None or self.status in ("dead", "closed", "starting", "restarting"):
            return
        if not await self._manager.is_alive():
            await self._stop_readers()
            said = self._said()
            self._abort_all("The kernel died before this cell finished.", status="error")
            self._end("dead", "The kernel stopped" + (f": {said}" if said else ". Restart it to go on."))

    def _alive(self) -> None:
        if self.status in ("dead", "closed"):
            raise Refused(self.note or "The kernel stopped. Restart it to go on.", status=409)
        if self.status in ("starting", "restarting"):
            raise Refused("The kernel is still starting.", status=409)

    # ── running cells ─────────────────────────────────────────────
    def execute(self, cell_id: str, code_text: str) -> Run:
        self._alive()
        if len(code_text) > MAX_CODE:
            raise Refused("A cell sent to run is at most 1 MB of code.", status=413)
        # `stop_on_error`: a cell that fails aborts the cells queued behind it, as "Run all" in Jupyter does.
        request_id = self._client.execute(code_text, store_history=True, allow_stdin=False, stop_on_error=True)
        # Registered before anything awaits, so the kernel's first message for it cannot arrive unclaimed.
        run = Run(request_id, cell_id)
        self.runs[request_id] = run
        # The oldest go first, but only those that are over: a run the kernel is still working on is how
        # its messages are found again, so forgetting it would throw its output away and leave its cell
        # waiting forever. "Run all" queues every cell at once, so this may hold more than KEPT_RUNS
        # until they finish.
        while len(self.runs) > KEPT_RUNS:
            oldest = next((r for r in self.runs.values() if r.finished), None)
            if oldest is None:
                break
            del self.runs[oldest.id]
        self.touch()
        self._broadcast({"type": "queued", "requestId": request_id, "cellId": cell_id})
        return run

    async def _read_iopub(self) -> None:
        while True:
            message = await self._client.get_iopub_msg()
            try:
                self._on_iopub(message)
            except Exception:  # one odd message must not stop every later one being read
                log.exception("kernel %s: an iopub message was not understood", self.id)

    async def _read_shell(self) -> None:
        while True:
            message = await self._client.get_shell_msg()
            if message.get("msg_type") != "execute_reply":
                continue
            run = self.runs.get(message.get("parent_header", {}).get("msg_id", ""))
            if run is None or run.finished:
                continue
            content = message.get("content") or {}
            run.replied = True
            if isinstance(content.get("execution_count"), int):
                run.execution_count = content["execution_count"]
                self.execution_count = max(self.execution_count, run.execution_count)
            run.reply = str(content.get("status", "ok"))
            # An aborted request gets its reply and may get no busy/idle of its own.
            if run.idle or run.reply == "aborted":
                self._finish(run, run.reply)

    def _on_iopub(self, message: dict[str, Any]) -> None:
        kind = message.get("msg_type") or message.get("header", {}).get("msg_type")
        content = message.get("content") or {}
        run = self.runs.get(message.get("parent_header", {}).get("msg_id", ""))
        self.touch()
        if kind == "status":
            state = content.get("execution_state")
            if run is not None and state == "busy" and run.status == "queued":
                run.status, run.started_at = "running", _now()
            if run is not None and state == "idle":
                run.idle = True
                if run.replied and not run.finished:
                    self._finish(run, run.reply)
            busy = any(r.status == "running" for r in self.runs.values())
            if self.status in ("idle", "busy"):
                self._set_status("busy" if busy or (state == "busy" and run is not None) else "idle")
            return
        if run is None:
            return
        if kind == "execute_input":
            if isinstance(content.get("execution_count"), int):
                run.execution_count = content["execution_count"]
            if run.status == "queued":
                run.status, run.started_at = "running", _now()
            self._event(run, {"type": "started", "executionCount": run.execution_count})
        elif kind == "stream":
            self._stream(run, "stderr" if content.get("name") == "stderr" else "stdout", str(content.get("text", "")))
        elif kind in ("display_data", "execute_result", "error"):
            raw = {"output_type": kind, **content}
            output = _output_in(raw)
            if output is not None:
                transient = content.get("transient") or {}
                self._output(run, output, transient.get("display_id") if isinstance(transient, dict) else None)
        elif kind == "clear_output":
            if content.get("wait"):
                run.clear_on_next = True
            else:
                self._clear(run)
        elif kind == "update_display_data":
            transient = content.get("transient") or {}
            display_id = transient.get("display_id") if isinstance(transient, dict) else None
            output = _output_in({"output_type": "display_data", **content})
            if display_id and output is not None:
                self._update_display(str(display_id), output)

    def _clear(self, run: Run) -> None:
        self._flush_now()
        run.outputs, run.display_ids, run.used, run.clear_on_next = [], [], 0, False
        self._event(run, {"type": "clear"})

    def _stream(self, run: Run, name: str, text: str) -> None:
        if run.clear_on_next:
            self._clear(run)
        if not text or run.truncated:
            return
        if run.used + len(text) > OUTPUT_BUDGET:
            self._cut(run)
            return
        run.used += len(text)
        last = run.outputs[-1] if run.outputs else None
        if last and last["output_type"] == "stream" and last["name"] == name:
            last["text"] += text
        else:
            run.outputs.append({"output_type": "stream", "name": name, "text": text})
            run.display_ids.append(None)
        self._pending.setdefault((run.id, name), []).append(text)
        if self._flush is None:
            self._flush = asyncio.get_running_loop().call_later(FLUSH_SECONDS, self._flush_now)

    def _flush_now(self) -> None:
        if self._flush is not None:
            self._flush.cancel()
            self._flush = None
        pending, self._pending = self._pending, OrderedDict()
        for (request_id, name), pieces in pending.items():
            run = self.runs.get(request_id)
            if run is not None:
                self._broadcast({"type": "stream", "requestId": run.id, "cellId": run.cell_id, "name": name,
                                 "text": "".join(pieces)})

    def _output(self, run: Run, output: dict[str, Any], display_id: str | None) -> None:
        if run.clear_on_next:
            self._clear(run)
        if run.truncated:
            return
        weight = _weight(output)
        if run.used + weight > OUTPUT_BUDGET and output["output_type"] != "error":
            self._cut(run)
            return
        run.used += weight
        if output["output_type"] == "execute_result" and output.get("execution_count") is None:
            output["execution_count"] = run.execution_count
        run.outputs.append(output)
        run.display_ids.append(display_id)
        self._event(run, {"type": "output", "output": output, "displayId": display_id})

    def _cut(self, run: Run) -> None:
        run.truncated = True
        note = {"output_type": "stream", "name": "stderr",
                "text": f"\n[Output stopped here: this cell printed more than {OUTPUT_BUDGET // (1024 * 1024)} MB.]\n"}
        run.outputs.append(note)
        run.display_ids.append(None)
        self._event(run, {"type": "output", "output": note, "displayId": None})

    def _update_display(self, display_id: str, output: dict[str, Any]) -> None:
        for run in self.runs.values():
            for i, shown in enumerate(run.display_ids):
                if shown == display_id:
                    kept = run.outputs[i]
                    kept["data"], kept["metadata"] = output["data"], output["metadata"]
        self._flush_now()
        self._broadcast({"type": "update", "displayId": display_id, "output": output})

    def _finish(self, run: Run, status: str) -> None:
        self._flush_now()
        run.status = status if status in ("ok", "error", "aborted") else "error"
        run.ended_at = _now()
        self._event(run, {"type": "done", "status": run.status, "executionCount": run.execution_count})

    def _abort_all(self, why: str, *, status: str = "aborted") -> None:
        for run in self.runs.values():
            if not run.finished:
                if run.status == "running":
                    note = {"output_type": "stream", "name": "stderr", "text": f"\n[{why}]\n"}
                    run.outputs.append(note)
                    run.display_ids.append(None)
                    self._event(run, {"type": "output", "output": note, "displayId": None})
                self._finish(run, status)

    def _event(self, run: Run, event: dict[str, Any]) -> None:
        self._flush_now()
        self._broadcast({**event, "requestId": run.id, "cellId": run.cell_id})

    # ── state and watchers ────────────────────────────────────────
    def touch(self) -> None:
        self.last_activity = _now()

    def _set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            self._broadcast({"type": "state", "kernel": self.json()})

    def _end(self, status: str, note: str) -> None:
        self.status, self.note = status, note
        self._broadcast({"type": "state", "kernel": self.json()})

    def _broadcast(self, event: dict[str, Any]) -> None:
        for watcher in list(self.watchers):
            watcher.offer(event)

    def watch(self) -> Watcher:
        watcher = Watcher()
        self.watchers.add(watcher)
        self.touch()
        return watcher

    def unwatch(self, watcher: Watcher) -> None:
        self.watchers.discard(watcher)
        self.touch()

    def json(self, *, runs: bool = False) -> dict[str, Any]:
        out = {"id": self.id, "path": str(self.path), "notebook": self.path.name, **self.choice.json(),
               "status": self.status, "note": self.note, "executionCount": self.execution_count,
               "restarts": self.restarts, "watching": len(self.watchers), "startedAt": _stamp(self.started_at),
               "lastActiveAt": _stamp(self.last_activity)}
        if runs:
            out["runs"] = [r.json() for r in self.runs.values()]
        return out


class Kernels:
    """The kernels this API holds, per person. Held on `app.state` and shut down with the app."""

    def __init__(self) -> None:
        self._all: dict[str, Kernel] = {}
        self._reaper: asyncio.Task[None] | None = None
        self._idle_minutes = 0

    def live(self) -> list[Kernel]:
        return [k for k in self._all.values() if k.status not in ("dead", "closed")]

    def mine(self, owner: str) -> list[Kernel]:
        return sorted((k for k in self._all.values() if k.owner == owner), key=lambda k: k.started_at)

    def get(self, kernel_id: str, owner: str) -> Kernel:
        found = self._all.get(kernel_id)
        if found is None or found.owner != owner:
            raise NotFound(f"kernel {kernel_id}")
        return found

    def of(self, owner: str, path: Path) -> Kernel | None:
        return next((k for k in self.mine(owner) if k.path == path and k.status not in ("dead", "closed")), None)

    async def start(self, config: Settings, owner: str, path: Path, choice: Choice) -> tuple[Kernel, bool]:
        """The person's kernel for this notebook: the one already running, or a new one within the limits.
        Answers whether it is new."""
        running = self.of(owner, path)
        if running is not None:
            return running, False
        for gone in [k for k in self.mine(owner) if k.path == path]:
            self._all.pop(gone.id, None)
            # Dropped here, nothing can reach it again — not the reaper, not the shutdown — so it is
            # closed now: a kernel that died still holds its channels, its open log file and its folder
            # with the connection file's session key until `close` lets them go.
            await gone.close()
        if config.kernels_max == 0:
            raise Refused("Notebook kernels are switched off on this server (NEUROCODE_KERNELS_MAX is 0).", status=409)
        if len(self.live()) >= config.kernels_max:
            raise Refused(f"This server runs at most {config.kernels_max} notebook kernels at once. Close a notebook "
                          "you are not using, or shut its kernel down.", status=429)
        if len([k for k in self.live() if k.owner == owner]) >= config.kernels_per_person:
            raise Refused(f"You have {config.kernels_per_person} notebook kernels running. Shut one down first.",
                          status=429)
        kernel = Kernel(owner=owner, path=path, choice=choice, start_seconds=config.kernel_start_seconds)
        self._all[kernel.id] = kernel
        self._idle_minutes = config.kernel_idle_minutes
        try:
            await kernel.start()
        except BaseException:
            self._all.pop(kernel.id, None)
            await kernel.close()
            raise
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap())
        return kernel, True

    async def close(self, kernel_id: str, owner: str) -> None:
        kernel = self.get(kernel_id, owner)
        self._all.pop(kernel.id, None)
        await kernel.close()

    async def close_all(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reaper
            self._reaper = None
        for kernel in list(self._all.values()):
            with contextlib.suppress(Exception):
                await kernel.close()
        self._all.clear()

    async def reap_once(self) -> None:
        """Notice kernels that died; shut down those nobody has had open for the idle time; forget the dead."""
        now = _now()
        for kernel in list(self._all.values()):
            await kernel.check()
            quiet = (now - kernel.last_activity).total_seconds()
            if kernel.status in ("dead", "closed"):
                if not kernel.watchers and quiet > 60 * 15:
                    self._all.pop(kernel.id, None)
                    await kernel.close()
            elif (self._idle_minutes and kernel.status == "idle" and not kernel.watchers
                  and quiet > self._idle_minutes * 60):
                kernel.note = f"Shut down after {self._idle_minutes} minutes with nobody watching."
                self._all.pop(kernel.id, None)
                await kernel.close()

    async def _reap(self) -> None:
        while self._all:
            await asyncio.sleep(REAP_SECONDS)
            try:
                await self.reap_once()
            except Exception:  # the reaper must outlive one kernel's odd failure
                log.exception("the notebook kernel reaper failed once")


async def start_kernel(session: AsyncSession, kernels: Kernels, config: Settings, who: Person, file: Path, *,
                       kernel_name: str | None, ip: str = "") -> tuple[Kernel, bool]:
    """Start (or find) the person's kernel for a notebook, choosing it as the module says. Audited: who,
    which notebook, which interpreter — it runs code on this machine."""
    who.must(MACHINE, "run a notebook")
    opened = await asyncio.to_thread(read, file)
    found = await asyncio.to_thread(options, file, opened["language"], opened["kernelName"])
    choice: Choice | None = found["choice"]
    if kernel_name:
        choice = next((c for c in found["available"] if c.name == kernel_name), None)
        if choice is None:
            # The page picked from what opening the notebook found, which is interpreters as they are on
            # disk; asked here, one can turn out to have no ipykernel. Then say what to install, rather
            # than that a kernel the person was just offered does not exist.
            if kernel_name in ("project", "machine"):
                raise Refused(missing(opened["language"], file.parent), status=409)
            raise Refused(f"There is no kernel called {kernel_name} on this machine.", status=404)
    if choice is None:
        raise Refused(found["missing"], status=409)
    kernel, new = await kernels.start(config, who.id, file, choice)
    if new:
        await AuditRepository(session).record(
            action="notebook.kernel.start", user_id=who.id, target=str(file),
            detail={"kernel": kernel.id, "name": choice.name, "source": choice.source,
                    "interpreter": choice.interpreter, "language": choice.language}, ip=ip)
    return kernel, new
