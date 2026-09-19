"""The Blueprint wizard: from a new idea to a designed, scaffolded system.

A person answers a few questions about what they are building, starts from a template (or from nothing),
edits the architecture layer by layer, may ask a model to review it, finalizes it, and scaffolds it into
a new folder. Four things hold throughout:

* **The catalogue is product data, not sample data.** `data/tech.json` (the technologies a layer can be
  built with) and `data/blueprints/*.json` (the template bank) ship with the program and are read once,
  like `catalogue.json`. Every template is checked against `data/blueprints/_schema.json` when it is
  loaded, and a template that names a technology the catalogue does not hold stops the server from
  starting — a broken template would otherwise be offered to everyone, on every installation.
* **Nothing a model says is applied by itself.** A review comes back as a list of changes, each with the
  path it touches, the value there now, the value proposed and why; a person accepts or rejects each one,
  and only the accepted ones are applied — against the revision the person was looking at.
* **Every figure is the blueprint's own.** A template's fit is counted from the answers a person gave,
  condition by condition, and the screen shows which ones matched; the document and the diagram are the
  architecture written out, not a model's retelling of it.
* **Nothing lands unsigned.** Scaffolding makes an empty repository (one empty commit, so a run has
  something to branch from), onboards it as a project, and compiles a plan whose requirement is the
  scaffold recipe. The files themselves are written by agents in worktrees and reach the folder only
  through the review and a person's signature, like every other change.

YAML is read and written by a small, safe subset parser here rather than PyYAML, which is not a
dependency: block mappings and sequences, plain and quoted scalars, literal blocks and simple flow
collections — everything an export writes. Anchors, aliases and tags are refused in words.
"""
from __future__ import annotations

import asyncio
import copy
import json
import re
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import onboarding
from ..agent.git import AUTHOR, git
from ..ai import blueprint as reviewer
from ..ai.gateway import Gateway
from ..data.base import utcnow
from ..models import Blueprint, BlueprintTemplate, MemoryFact, Plan, Project, ProjectSource, Task, User
from ..repositories import ActivityRepository, NotFound, ProjectRepository
from ..repositories.base import bounded
from ..repositories.knowledge import MemoryRepository
from . import machine
from .errors import NO_MODEL, Refused, needs_a_model
from .identity import Person
from .knowledge import MemoryService, NewFact
from .onboarding import SourceService, SourceSpec
from .plans import PlanService

DATA = Path(__file__).resolve().parent.parent / "data"
TECH_PATH = DATA / "tech.json"
BANK = DATA / "blueprints"
SCHEMA_PATH = BANK / "_schema.json"

#: What a list of blueprints or templates hands back at most, whatever a caller asks for.
MAX_PAGE = 200
#: An imported document, as text. A real blueprint is a few kilobytes; this is only a ceiling.
MAX_IMPORT = 512 * 1024
#: The scaffold recipe handed to the compiler. It is a requirement like any other, so it is kept to a
#: size a model reads whole.
MAX_RECIPE = 9_000
#: Changes one review may propose. A review that rewrites everything is a new blueprint, not a review.
MAX_CHANGES = 12
#: The export's own name and version, as sessions have theirs.
FORMAT = "neurocode.blueprint"
TEMPLATE_FORMAT = "neurocode.template"
VERSION = 1
#: Keys kept in a blueprint's `spec` column beside the architecture: what came of it, never part of it.
EXTRAS = ("review", "final", "scaffolded")
#: Keys a person's own template keeps beside its architecture, when it was imported with them.
TEMPLATE_EXTRAS = ("summaryLine", "fitsWhen", "avoidWhen")
#: A folder chosen for a scaffold counts as empty when it holds nothing but these.
IGNORABLE = {".DS_Store", "Thumbs.db", "desktop.ini"}
#: A custom template's id starts with this, so it can never collide with a catalogue file's name.
MINE = "mine-"


# ── the questions ────────────────────────────────────────────────
def _o(value: str, label: str) -> dict[str, str]:
    return {"id": value, "label": label}


#: The questionnaire. Every question may be skipped; a skipped one counts neither for a template nor
#: against it. Served to the screen with the catalogue, so the questions are written once, here.
QUESTIONS: list[dict[str, Any]] = [
    {"id": "idea", "kind": "text", "question": "What are you building?",
     "hint": "A sentence or two, in your own words. It goes into the architecture document."},
    {"id": "productType", "kind": "one", "question": "What kind of product is it?", "options": [
        _o("web-app", "A web app people sign in to"), _o("saas", "A multi-tenant SaaS product"),
        _o("internal-tool", "An internal tool for a team"), _o("marketplace", "A marketplace"),
        _o("content-site", "A content or marketing site"), _o("api", "An API other systems call"),
        _o("mobile-app", "A mobile app"), _o("realtime", "A realtime app (chat, collaboration, live data)"),
        _o("event-driven", "A system of services reacting to events"),
        _o("data-platform", "A data pipeline or warehouse"), _o("ml-service", "A machine-learning service"),
        _o("llm-app", "An app built on a language model"), _o("cli-library", "A command-line tool or library"),
        _o("desktop-app", "A desktop app")]},
    {"id": "audience", "kind": "one", "question": "Who uses it?", "options": [
        _o("consumers", "The public"), _o("businesses", "Businesses that buy it"),
        _o("internal", "People inside one organisation"), _o("developers", "Developers")]},
    {"id": "scale", "kind": "one", "question": "How many people use it in its first year?", "options": [
        _o("small", "Up to a few thousand"), _o("medium", "Tens of thousands"),
        _o("large", "Hundreds of thousands or more")]},
    {"id": "traffic", "kind": "one", "question": "What does its load look like?", "options": [
        _o("steady", "Steady through the day"), _o("spiky", "Spiky: quiet, then sudden peaks"),
        _o("batch", "Batches: work arrives on a schedule")]},
    {"id": "data", "kind": "many", "question": "What data does it hold?", "options": [
        _o("relational", "Records with relationships"), _o("documents", "Documents and text"),
        _o("files", "Files and media"), _o("events", "A stream of events"),
        _o("analytics", "History for reporting and analysis"), _o("vectors", "Embeddings for semantic search"),
        _o("time-series", "Measurements over time")]},
    {"id": "compliance", "kind": "many", "question": "Which rules does it have to meet?", "options": [
        _o("none", "None in particular"), _o("gdpr", "GDPR (personal data of people in the EU)"),
        _o("hipaa", "HIPAA (health data in the US)"), _o("pci", "PCI DSS (card payments)"),
        _o("soc2", "SOC 2 (customers will ask for it)")]},
    {"id": "needs", "kind": "many", "question": "What does it need to do?", "options": [
        _o("realtime", "Update screens live"), _o("offline", "Work offline"), _o("mobile", "Run as a phone app"),
        _o("seo", "Rank in search engines"), _o("multi-tenant", "Serve many customer organisations"),
        _o("background-jobs", "Run background jobs"), _o("search", "Search its content"),
        _o("ai", "Use AI models")]},
    {"id": "team", "kind": "many", "question": "Which languages does the team write?", "options": [
        _o("python", "Python"), _o("typescript", "TypeScript / JavaScript"), _o("go", "Go"), _o("java", "Java"),
        _o("kotlin", "Kotlin"), _o("csharp", "C#"), _o("ruby", "Ruby"), _o("php", "PHP"), _o("rust", "Rust"),
        _o("swift", "Swift"), _o("dart", "Dart"), _o("elixir", "Elixir")]},
    {"id": "teamSize", "kind": "one", "question": "How big is the team?", "options": [
        _o("solo", "Just me"), _o("small", "Two to eight people"), _o("large", "Several teams")]},
    {"id": "hosting", "kind": "one", "question": "Where should it run?", "options": [
        _o("paas", "A managed platform (Render, Fly.io, Heroku)"), _o("serverless", "Serverless functions"),
        _o("cloud", "A big cloud (AWS, Google Cloud, Azure)"), _o("kubernetes", "Kubernetes"),
        _o("vm", "A server we run ourselves"), _o("on-device", "On the user's machine or phone")]},
    {"id": "budget", "kind": "one", "question": "What is the monthly budget for running it?", "options": [
        _o("minimal", "As close to nothing as possible"), _o("moderate", "Moderate"),
        _o("flexible", "Whatever it takes")]},
    {"id": "timeline", "kind": "one", "question": "When does the first version need to ship?", "options": [
        _o("weeks", "In weeks"), _o("months", "In a few months"), _o("longer", "Later than that")]},
]
QUESTION_BY_ID = {q["id"]: q for q in QUESTIONS}

#: The layers a blueprint is drawn in, in the order the screen and the document show them, and the
#: catalogue category the picker offers first for each.
LAYERS: list[dict[str, str]] = [
    {"id": "frontend", "label": "Front end", "category": "frontend"},
    {"id": "mobile", "label": "Mobile", "category": "mobile"},
    {"id": "desktop", "label": "Desktop", "category": "frontend"},
    {"id": "backend", "label": "Back end", "category": "backend"},
    {"id": "database", "label": "Database", "category": "database"},
    {"id": "cache", "label": "Cache", "category": "cache"},
    {"id": "queue", "label": "Queue and jobs", "category": "queue"},
    {"id": "search", "label": "Search", "category": "search"},
    {"id": "auth", "label": "Authentication", "category": "auth"},
    {"id": "ai-ml", "label": "AI and ML", "category": "ai-ml"},
    {"id": "data", "label": "Data", "category": "data"},
    {"id": "infra", "label": "Infrastructure", "category": "infra"},
    {"id": "ci-cd", "label": "CI/CD", "category": "ci-cd"},
    {"id": "observability", "label": "Observability", "category": "observability"},
    {"id": "testing", "label": "Testing", "category": "testing"},
    {"id": "hosting", "label": "Hosting", "category": "hosting"},
]
LAYER_ORDER = {x["id"]: n for n, x in enumerate(LAYERS)}
LAYER_LABEL = {x["id"]: x["label"] for x in LAYERS}


# ── a small JSON Schema checker ──────────────────────────────────
#: The keywords the template schema uses. A schema that uses one this checker does not know would be
#: checked less than it says, so an unknown keyword is an error rather than a pass.
KEYWORDS = {"$schema", "$id", "$ref", "$defs", "title", "description", "type", "required", "properties",
            "additionalProperties", "items", "minItems", "maxItems", "uniqueItems", "minLength", "maxLength",
            "pattern", "enum", "propertyNames", "minProperties"}
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _where(path: str) -> str:
    return path or "the document"


def _is(value: Any, kind: str) -> bool:
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    wanted = TYPES[kind]
    return isinstance(value, wanted) and not (kind != "boolean" and isinstance(value, bool))


def problems(value: Any, schema: dict[str, Any], root: dict[str, Any] | None = None, path: str = "",
             limit: int = 20) -> list[str]:
    """Where `value` breaks `schema`, as sentences naming the path — empty when it fits. The subset of
    JSON Schema (2020-12) the template schema is written in; `$ref` points into the same document."""
    root = root if root is not None else schema
    out: list[str] = []

    def say(where: str, text: str) -> None:
        if len(out) < limit:
            out.append(f"{_where(where)}: {text}")

    def check(v: Any, s: dict[str, Any], at: str, depth: int) -> None:
        if depth > 60 or len(out) >= limit:
            return
        unknown = set(s) - KEYWORDS
        if unknown:
            raise ValueError(f"the schema uses keywords this checker does not know: {sorted(unknown)}")
        if "$ref" in s:
            ref = s["$ref"]
            if not ref.startswith("#/$defs/"):
                raise ValueError(f"only local $defs references are supported, not {ref}")
            check(v, root["$defs"][ref.removeprefix("#/$defs/")], at, depth + 1)
        if "type" in s:
            kinds = s["type"] if isinstance(s["type"], list) else [s["type"]]
            if not any(_is(v, k) for k in kinds):
                say(at, f"must be {' or '.join(kinds)}")
                return
        if "enum" in s and v not in s["enum"]:
            shown = ", ".join(map(str, s["enum"][:12]))
            say(at, f"must be one of {shown}{'…' if len(s['enum']) > 12 else ''}")
        if isinstance(v, str):
            if "minLength" in s and len(v) < s["minLength"]:
                say(at, "is empty" if s["minLength"] == 1 else f"needs at least {s['minLength']} characters")
            if "maxLength" in s and len(v) > s["maxLength"]:
                say(at, f"is longer than {s['maxLength']} characters")
            if "pattern" in s and not re.search(s["pattern"], v):
                say(at, f"{v[:60]!r} is not in the expected form")
        if isinstance(v, list):
            if "minItems" in s and len(v) < s["minItems"]:
                say(at, f"needs at least {s['minItems']} item{'s' if s['minItems'] != 1 else ''}")
            if "maxItems" in s and len(v) > s["maxItems"]:
                say(at, f"holds more than {s['maxItems']} items")
            if s.get("uniqueItems"):
                seen = [json.dumps(x, sort_keys=True) for x in v]
                if len(seen) != len(set(seen)):
                    say(at, "names the same item twice")
            if "items" in s:
                for n, item in enumerate(v):
                    check(item, s["items"], f"{at}[{n}]", depth + 1)
        if isinstance(v, dict):
            for key in s.get("required", []):
                if key not in v:
                    say(at, f"is missing {key}")
            if "minProperties" in s and len(v) < s["minProperties"]:
                say(at, f"needs at least {s['minProperties']} entries")
            props = s.get("properties", {})
            for key, item in v.items():
                where = f"{at}.{key}" if at else key
                if "propertyNames" in s:
                    check(key, s["propertyNames"], where, depth + 1)
                if key in props:
                    check(item, props[key], where, depth + 1)
                elif s.get("additionalProperties") is False:
                    say(where, "is not a field this shape has")
                elif isinstance(s.get("additionalProperties"), dict):
                    check(item, s["additionalProperties"], where, depth + 1)

    check(value, schema, path, 0)
    return out


# ── the catalogue, read once ─────────────────────────────────────
@dataclass(frozen=True, slots=True)
class Catalogue:
    tech: tuple[dict[str, Any], ...]
    tech_by_id: dict[str, dict[str, Any]]
    templates: dict[str, dict[str, Any]]
    schema: dict[str, Any]


def technologies_named(tpl: dict[str, Any]) -> list[str]:
    """Every technology a template or an architecture names: layer choices and alternatives, services,
    data stores, the CI tool, infrastructure and observability tools."""
    named: list[str] = []
    for layer in (tpl.get("layers") or {}).values():
        if layer.get("choice"):
            named.append(layer["choice"])
        named += layer.get("alternatives") or []
    named += [s["tech"] for s in tpl.get("services") or []]
    named += [d["tech"] for d in tpl.get("dataStores") or []]
    if (tpl.get("ci") or {}).get("tool"):
        named.append(tpl["ci"]["tool"])
    named += (tpl.get("infra") or {}).get("tools") or []
    named += (tpl.get("observability") or {}).get("tools") or []
    return named


def _read_catalogue() -> Catalogue:
    raw = json.loads(TECH_PATH.read_text())
    tech = tuple(raw["tech"])
    by_id = {t["id"]: t for t in tech}
    if len(by_id) != len(tech):
        raise ValueError("tech.json names a technology twice")
    schema = json.loads(SCHEMA_PATH.read_text())
    templates: dict[str, dict[str, Any]] = {}
    for path in sorted(BANK.glob("*.json")):
        if path.name.startswith("_"):
            continue
        tpl = json.loads(path.read_text())
        # A template that does not fit the schema, or names a technology nobody can find, would be
        # offered to every workspace on every installation. Refusing to start is the kinder failure.
        wrong = problems(tpl, schema)
        if wrong:
            raise ValueError(f"blueprints/{path.name}: " + "; ".join(wrong))
        if tpl["id"] != path.stem:
            raise ValueError(f"blueprints/{path.name}: its id is {tpl['id']}, not {path.stem}")
        missing = sorted({t for t in technologies_named(tpl) if t not in by_id})
        if missing:
            raise ValueError(f"blueprints/{path.name}: names technologies tech.json does not hold: {missing}")
        templates[tpl["id"]] = tpl
    return Catalogue(tech, by_id, templates, schema)


CATALOGUE = _read_catalogue()
SPEC_SCHEMA: dict[str, Any] = {"$ref": "#/$defs/spec"}


def spec_problems(arch: Any) -> list[str]:
    return problems(arch, SPEC_SCHEMA, CATALOGUE.schema)


def tech_name(tech_id: str | None) -> str:
    """A technology's name, or the text a person typed when it is not in the catalogue."""
    if not tech_id:
        return "—"
    found = CATALOGUE.tech_by_id.get(tech_id)
    return found["name"] if found else tech_id


ARCH_KEYS = ("summary", "layers", "services", "dataStores", "environments", "ci", "infra", "observability",
             "security", "folderLayout", "conventions", "adrs", "scaffold")


def blank() -> dict[str, Any]:
    """An architecture with nothing chosen yet — where a blank start begins."""
    return {"summary": "", "layers": {}, "services": [], "dataStores": [], "environments": [],
            "ci": {"tool": "", "stages": []}, "infra": {"kind": "none", "tools": [], "notes": []},
            "observability": {"tools": [], "notes": []}, "security": {"auth": "", "secrets": "", "notes": []},
            "folderLayout": "", "conventions": [], "adrs": [], "scaffold": {"repos": []}}


def architecture_of(tpl: dict[str, Any]) -> dict[str, Any]:
    """The architecture part of a template, copied, so editing a blueprint never edits the template."""
    return copy.deepcopy({key: tpl[key] for key in ARCH_KEYS if key in tpl} | {"summary": tpl.get("summary", "")})


def split(stored: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    """A blueprint's `spec` column as (architecture, what came of it)."""
    stored = dict(stored or {})
    extras = {key: stored.pop(key) for key in EXTRAS if key in stored}
    return {**blank(), **stored}, extras


# ── fit: how well a template matches the answers ─────────────────
def _values(answers: dict[str, Any], key: str) -> list[str]:
    given = answers.get(key)
    if given is None or given == "" or given == []:
        return []
    return [str(x) for x in given] if isinstance(given, list) else [str(given)]


def fit(tpl: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    """Which of a template's conditions the answers meet. Transparent on purpose: the score is the number
    of `fitsWhen` conditions met minus the `avoidWhen` ones, and both lists are handed back with the
    answer that met them, so the screen can say exactly why a template is ranked where it is."""
    def met(conditions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        hits, unanswered = [], 0
        for c in conditions:
            given = _values(answers, c["answer"])
            if not given:
                unanswered += 1
                continue
            matched = [v for v in given if v in c["is"]]
            if matched:
                hits.append({"says": c["says"], "answer": c["answer"], "values": matched})
        return hits, unanswered

    fits, open_fits = met(tpl.get("fitsWhen") or [])
    against, open_against = met(tpl.get("avoidWhen") or [])
    conditions = len(tpl.get("fitsWhen") or []) + len(tpl.get("avoidWhen") or [])
    return {"score": len(fits) - len(against), "fits": fits, "against": against,
            "considered": conditions - open_fits - open_against, "conditions": conditions}


def template_summary(tpl: dict[str, Any], *, source: str = "catalogue", answers: dict[str, Any] | None = None,
                     extra: dict[str, Any] | None = None) -> dict[str, Any]:
    layers = tpl.get("layers") or {}
    out = {"id": tpl["id"], "name": tpl["name"], "summary": tpl.get("summary", ""), "source": source,
           "layers": {k: layers[k].get("choice") for k in sorted(layers, key=lambda k: LAYER_ORDER.get(k, 99))},
           "services": len(tpl.get("services") or []),
           "repos": len(((tpl.get("scaffold") or {}).get("repos")) or []),
           "fitsWhen": tpl.get("fitsWhen") or [], "avoidWhen": tpl.get("avoidWhen") or []}
    if answers is not None:
        out["fit"] = fit(tpl, answers)
    return out | (extra or {})


def rank(answers: dict[str, Any]) -> list[dict[str, Any]]:
    """The catalogue's templates, best fit first; ties keep more matches first, then the name."""
    ranked = [template_summary(t, answers=answers) for t in CATALOGUE.templates.values()]
    ranked.sort(key=lambda t: (-t["fit"]["score"], -len(t["fit"]["fits"]), t["name"]))
    return ranked


def check_answers(answers: Any) -> dict[str, Any]:
    """The answers kept: known questions only, each answer one of its options (or text), empty ones
    dropped — a skipped question is simply not there."""
    if not isinstance(answers, dict):
        raise Refused("The answers are a set of question → answer.", status=422)
    kept: dict[str, Any] = {}
    for key, value in answers.items():
        q = QUESTION_BY_ID.get(key)
        if q is None:
            raise Refused(f"There is no question called {key}.", status=422)
        if value is None or value == "" or value == []:
            continue
        if q["kind"] == "text":
            if not isinstance(value, str) or len(value) > 2000:
                raise Refused(f"The answer to “{q['question']}” is text of at most 2,000 characters.", status=422)
            kept[key] = value.strip()
            continue
        options = {o["id"] for o in q["options"]}
        if q["kind"] == "one":
            if value not in options:
                raise Refused(f"{value!r} is not an answer to “{q['question']}”.", status=422)
            kept[key] = value
        else:
            if not isinstance(value, list) or any(v not in options for v in value):
                raise Refused(f"The answers to “{q['question']}” must come from its options.", status=422)
            kept[key] = list(dict.fromkeys(value))
    return kept


def answer_label(key: str, value: Any) -> str:
    q = QUESTION_BY_ID.get(key)
    if q is None or q["kind"] == "text":
        return str(value)
    labels = {o["id"]: o["label"] for o in q["options"]}
    return ", ".join(labels.get(v, v) for v in (value if isinstance(value, list) else [value]))


# ── checks beyond the schema ─────────────────────────────────────
def checks(arch: dict[str, Any]) -> list[dict[str, str]]:
    """What is wrong with an architecture that its shape allows: two services with one name, a service
    talking to something that is not there, a scaffold file named twice. Shown while editing, and they
    stop a blueprint from being finalized or scaffolded."""
    out: list[dict[str, str]] = []
    services = [s["name"] for s in arch.get("services") or []]
    stores = [d["name"] for d in arch.get("dataStores") or []]
    for name in sorted({n for n in services + stores if (services + stores).count(n) > 1}):
        out.append({"path": "services", "problem": f"{name} is the name of more than one service or data store."})
    known = set(services) | set(stores)
    for n, s in enumerate(arch.get("services") or []):
        for target in s.get("talksTo") or []:
            if target not in known:
                out.append({"path": f"services[{n}].talksTo",
                            "problem": f"{s['name']} talks to {target}, which is neither a service nor a data store."})
    labels = [r["label"] for r in (arch.get("scaffold") or {}).get("repos") or []]
    for label in sorted({x for x in labels if labels.count(x) > 1}):
        out.append({"path": "scaffold.repos", "problem": f"Two repositories are labelled {label}."})
    for n, repo in enumerate((arch.get("scaffold") or {}).get("repos") or []):
        paths = [f["path"] for f in repo.get("files") or []]
        for p in sorted({x for x in paths if paths.count(x) > 1}):
            out.append({"path": f"scaffold.repos[{n}].files", "problem": f"{repo['label']} names {p} twice."})
    return out


# ── paths inside an architecture ─────────────────────────────────
PATH = re.compile(r"^[A-Za-z][A-Za-z0-9-]*(\[[^\[\]]{1,60}\])?(\.[A-Za-z][A-Za-z0-9-]*(\[[^\[\]]{1,60}\])?)*$")
STEP = re.compile(r"([A-Za-z][A-Za-z0-9-]*)(?:\[([^\[\]]+)\])?")
MISSING = object()


def _steps(path: str) -> list[str | int]:
    """`services[api].talksTo` → ['services', 'api?', 'talksTo']: keys, and list selectors kept as text
    to be resolved against the list they select from."""
    if not PATH.match(path) or path.split(".")[0].split("[")[0] not in ARCH_KEYS:
        raise Refused(f"{path} is not a place in a blueprint.", status=422)
    out: list[str | int] = []
    for part in path.split("."):
        m = STEP.fullmatch(part)
        assert m is not None
        out.append(m.group(1))
        if m.group(2) is not None:
            out.append(f"[{m.group(2)}]")
    return out


def _index(items: list[Any], selector: str, *, adding: bool) -> int | None:
    """A list selector: `[3]`, `[+]` (after the last item, only when adding), or `[name]` — the item
    whose name, label or title it is. None when nothing matches."""
    inner = selector[1:-1]
    if inner == "+":
        return len(items) if adding else None
    if inner.isdigit():
        n = int(inner)
        return n if n < len(items) or (adding and n == len(items)) else None
    for n, item in enumerate(items):
        if isinstance(item, dict) and inner in (item.get("name"), item.get("label"), item.get("title")):
            return n
    return None


def get_at(arch: dict[str, Any], path: str) -> Any:
    """The value at a path, or MISSING."""
    here: Any = arch
    for step in _steps(path):
        if isinstance(step, str) and step.startswith("["):
            if not isinstance(here, list):
                return MISSING
            n = _index(here, step, adding=False)
            if n is None:
                return MISSING
            here = here[n]
        else:
            if not isinstance(here, dict) or step not in here:
                return MISSING
            here = here[step]
    return copy.deepcopy(here)


def set_at(arch: dict[str, Any], path: str, value: Any) -> None:
    """Put a value at a path — `None` removes it. A missing layer is made; anything else must exist."""
    steps = _steps(path)
    here: Any = arch
    for step, following in zip(steps, steps[1:]):
        if isinstance(step, str) and step.startswith("["):
            n = _index(here, step, adding=False) if isinstance(here, list) else None
            if n is None:
                raise Refused(f"{path} names an item that is not there.", status=409)
            here = here[n]
        else:
            if not isinstance(here, dict):
                raise Refused(f"{path} does not lead anywhere in this blueprint.", status=409)
            if step not in here:
                if not (here is arch.get("layers") and isinstance(following, str) and not following.startswith("[")):
                    raise Refused(f"{path} names something the blueprint does not have.", status=409)
                here[step] = {"choice": None, "alternatives": [], "why": ""}
            here = here[step]
    last = steps[-1]
    if isinstance(last, str) and last.startswith("["):
        if not isinstance(here, list):
            raise Refused(f"{path} does not name a list.", status=409)
        n = _index(here, last, adding=value is not None)
        if n is None:
            raise Refused(f"{path} names an item that is not there.", status=409)
        if value is None:
            here.pop(n)
        elif n == len(here):
            here.append(value)
        else:
            here[n] = value
        return
    if not isinstance(here, dict):
        raise Refused(f"{path} does not lead anywhere in this blueprint.", status=409)
    if value is None and here is arch.get("layers"):
        here.pop(last, None)
    elif value is None and last in ("choice",):
        here[last] = None
    elif value is None:
        raise Refused(f"{path} cannot be removed, only changed.", status=422)
    else:
        here[last] = value


def same(a: Any, b: Any) -> bool:
    return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ── what a blueprint is written out as ───────────────────────────
GROUPS = [("clients", "Clients", {"web", "mobile", "desktop", "static", "cli"}),
          ("edge", "Edge", {"gateway"}),
          ("services", "Services", {"api", "service", "realtime", "function", "library"}),
          ("workers", "Workers and pipelines", {"worker", "job", "pipeline"})]


def _node(prefix: str, name: str) -> str:
    return prefix + re.sub(r"[^A-Za-z0-9_]", "_", name)


def _label(text: str) -> str:
    return text.replace('"', "#quot;")


def diagram(arch: dict[str, Any]) -> str:
    """The architecture as a Mermaid flowchart: services grouped by what they are, data stores apart, an
    arrow for every "talks to". A blueprint with no services yet is drawn as its layers."""
    lines = ["flowchart LR"]
    services = arch.get("services") or []
    stores = arch.get("dataStores") or []
    if not services and not stores:
        layers = arch.get("layers") or {}
        chosen = [(k, v) for k, v in sorted(layers.items(), key=lambda kv: LAYER_ORDER.get(kv[0], 99))
                  if v.get("choice")]
        if not chosen:
            return "flowchart LR\n  empty[\"Nothing chosen yet\"]"
        lines.append('  subgraph layers ["Layers"]')
        lines += [f'    {_node("l_", k)}["{_label(LAYER_LABEL.get(k, k))} · {_label(tech_name(v["choice"]))}"]'
                  for k, v in chosen]
        lines.append("  end")
        return "\n".join(lines)
    names = {s["name"]: _node("s_", s["name"]) for s in services}
    names.update({d["name"]: _node("d_", d["name"]) for d in stores})
    placed: set[str] = set()
    for key, title, kinds in GROUPS:
        members = [s for s in services if s["kind"] in kinds]
        if not members:
            continue
        lines.append(f'  subgraph {key} ["{title}"]')
        for s in members:
            lines.append(f'    {names[s["name"]]}["{_label(s["name"])} · {_label(tech_name(s["tech"]))}"]')
            placed.add(s["name"])
        lines.append("  end")
    for s in services:
        if s["name"] not in placed:
            lines.append(f'  {names[s["name"]]}["{_label(s["name"])} · {_label(tech_name(s["tech"]))}"]')
    if stores:
        lines.append('  subgraph data ["Data"]')
        lines += [f'    {names[d["name"]]}[("{_label(d["name"])} · {_label(tech_name(d["tech"]))}")]' for d in stores]
        lines.append("  end")
    for s in services:
        for target in s.get("talksTo") or []:
            if target in names:
                lines.append(f"  {names[s['name']]} --> {names[target]}")
    return "\n".join(lines)


def _md_cell(text: str) -> str:
    return (text or "").replace("|", "\\|").replace("\n", " ")


def document(bp: Blueprint, arch: dict[str, Any], *, by: str, template_name: str | None) -> str:
    """The architecture document: everything the blueprint holds, in Markdown, in the order a reader
    needs it. Written from the blueprint, so it says nothing the blueprint does not."""
    answers = bp.answers or {}
    out = [f"# {bp.name}", "",
           f"Architecture of blueprint `{bp.id}`, revision {bp.revision}, finalized {utcnow():%Y-%m-%d} by {by}."
           + (f" Started from the template “{template_name}”." if template_name else " Started from a blank page."), ""]
    if answers.get("idea") or arch.get("summary"):
        out += ["## What it is", ""]
        if answers.get("idea"):
            out += [answers["idea"], ""]
        if arch.get("summary"):
            out += [arch["summary"], ""]
    asked = [(q, answers[q["id"]]) for q in QUESTIONS if q["id"] != "idea" and q["id"] in answers]
    if asked:
        out += ["## What it was designed for", ""]
        out += [f"- **{q['question']}** {answer_label(q['id'], v)}" for q, v in asked]
        out.append("")
    layers = arch.get("layers") or {}
    if layers:
        out += ["## Layers", "", "| Layer | Choice | Alternatives | Why |", "| --- | --- | --- | --- |"]
        for key in sorted(layers, key=lambda k: LAYER_ORDER.get(k, 99)):
            v = layers[key]
            out.append(f"| {LAYER_LABEL.get(key, key)} | {_md_cell(tech_name(v.get('choice')))} | "
                       f"{_md_cell(', '.join(tech_name(a) for a in v.get('alternatives') or []) or '—')} | "
                       f"{_md_cell(v.get('why') or '—')} |")
        out.append("")
    if arch.get("services"):
        out += ["## Services", ""]
        for s in arch["services"]:
            out.append(f"### {s['name']} — {s['kind']}, {tech_name(s['tech'])}")
            out += [f"- {r}" for r in s.get("responsibilities") or []]
            if s.get("talksTo"):
                out.append(f"- Talks to: {', '.join(s['talksTo'])}")
            out.append("")
    if arch.get("dataStores"):
        out += ["## Data stores", "", "| Name | Technology | Purpose |", "| --- | --- | --- |"]
        out += [f"| {d['name']} | {_md_cell(tech_name(d['tech']))} | {_md_cell(d['purpose'])} |" for d in arch["dataStores"]]
        out.append("")
    out += ["## Diagram", "", "```mermaid", diagram(arch), "```", ""]
    if arch.get("environments"):
        out += ["## Environments", ""]
        out += [f"- **{e['name']}** — {e['purpose']} ({e['hosting']})" for e in arch["environments"]]
        out.append("")
    ci = arch.get("ci") or {}
    if ci.get("tool") or ci.get("stages"):
        out += ["## CI/CD", "", f"Runs on {tech_name(ci.get('tool'))}.", ""]
        out += [f"{n}. **{s['name']}** — {s['does']}" for n, s in enumerate(ci.get("stages") or [], 1)]
        out.append("")
    infra = arch.get("infra") or {}
    if infra.get("kind") not in (None, "none") or infra.get("tools") or infra.get("notes"):
        out += ["## Infrastructure", "", f"Kind: {infra.get('kind', 'none')}."
                + (f" Tools: {', '.join(tech_name(t) for t in infra.get('tools') or [])}." if infra.get("tools") else ""), ""]
        out += [f"- {n}" for n in infra.get("notes") or []]
        out.append("")
    obs = arch.get("observability") or {}
    if obs.get("tools") or obs.get("notes"):
        out += ["## Observability", ""]
        if obs.get("tools"):
            out += [f"Tools: {', '.join(tech_name(t) for t in obs['tools'])}.", ""]
        out += [f"- {n}" for n in obs.get("notes") or []]
        out.append("")
    sec = arch.get("security") or {}
    if sec.get("auth") or sec.get("secrets") or sec.get("notes"):
        out += ["## Security", ""]
        if sec.get("auth"):
            out.append(f"- **Authentication:** {sec['auth']}")
        if sec.get("secrets"):
            out.append(f"- **Secrets:** {sec['secrets']}")
        out += [f"- {n}" for n in sec.get("notes") or []]
        out.append("")
    if arch.get("folderLayout"):
        out += ["## Folder layout", "", "```", arch["folderLayout"], "```", ""]
    if arch.get("conventions"):
        out += ["## Conventions", ""] + [f"- {c}" for c in arch["conventions"]] + [""]
    adrs = decisions(arch)
    if adrs:
        out += ["## Decisions", ""]
        for n, a in enumerate(adrs, 1):
            out += [f"### ADR {n}: {a['title']}", "", f"**Decision:** {a['decision']}", "", f"**Why:** {a['why']}", ""]
            if a["alternatives"]:
                out += [f"**Considered:** {', '.join(a['alternatives'])}", ""]
    repos = (arch.get("scaffold") or {}).get("repos") or []
    if repos:
        out += ["## Scaffold", ""]
        for r in repos:
            out += [f"### {r['label']}", "", r.get("layout") or "", ""]
            out += [f"- `{f['path']}` — {f['template']}" for f in r.get("files") or []]
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def decisions(arch: dict[str, Any]) -> list[dict[str, Any]]:
    """The architecture's decisions: one for every layer with a choice and a reason, then the blueprint's
    own ADRs. What `finalize` writes into memory, and the document's Decisions section."""
    out: list[dict[str, Any]] = []
    layers = arch.get("layers") or {}
    for key in sorted(layers, key=lambda k: LAYER_ORDER.get(k, 99)):
        v = layers[key]
        if not v.get("choice"):
            continue
        out.append({"title": f"{LAYER_LABEL.get(key, key)}: {tech_name(v['choice'])}",
                    "decision": f"Use {tech_name(v['choice'])} for the {LAYER_LABEL.get(key, key).lower()} layer.",
                    "why": v.get("why") or "Chosen in the blueprint; no reason was written down.",
                    "alternatives": [tech_name(a) for a in v.get("alternatives") or []]})
    out += [{"title": a["title"], "decision": a["decision"], "why": a["why"],
             "alternatives": list(a.get("alternatives") or [])} for a in arch.get("adrs") or []]
    return out


def recipe(bp: Blueprint, arch: dict[str, Any], repos: list[dict[str, Any]], primary: str) -> str:
    """The scaffold as a requirement for the compiler. Paths are the project's: the first repository's
    plain, every other one's under its label — the way a multi-source project names its files."""
    lines = [f"Scaffold the {bp.name} system designed in blueprint {bp.id} (revision {bp.revision}). "
             "Create the files listed below, and nothing else unless the project cannot build or start without "
             "it. Keep each file small: a working skeleton, not a finished feature.", ""]
    if arch.get("summary"):
        lines += [f"What it is: {arch['summary']}", ""]
    chosen = [f"{LAYER_LABEL.get(k, k)}: {tech_name(v['choice'])}"
              for k, v in sorted((arch.get("layers") or {}).items(), key=lambda kv: LAYER_ORDER.get(kv[0], 99))
              if v.get("choice")]
    if chosen:
        lines += ["Stack: " + "; ".join(chosen) + ".", ""]
    for repo in repos:
        lead = "" if repo["label"] == primary else f"{repo['label']}/"
        lines.append(f"Repository {repo['label']} ({repo.get('layout') or 'layout as listed'}):")
        lines += [f"- {lead}{f['path']}: {f['template']}" for f in repo["files"]]
        lines.append(f"- {lead}AGENTS.md: instructions for AI coding agents working here — what the repository is, "
                     "its layout, how to install, run and test it, and the conventions below.")
        lines.append("")
    if arch.get("conventions"):
        lines += ["Conventions to follow and to write into each AGENTS.md:"] + [f"- {c}" for c in arch["conventions"]] + [""]
    text = "\n".join(lines).rstrip()
    return text if len(text) <= MAX_RECIPE else text[:MAX_RECIPE - 60].rstrip() + "\n[… the rest of the recipe did not fit]"


# ── YAML: a small, safe subset ───────────────────────────────────
RESERVED = {"true", "false", "null", "yes", "no", "on", "off", "~", "y", "n"}
PLAIN = re.compile(r"^[A-Za-z_./][A-Za-z0-9 _./()@,+-]*$")
NUMBER = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?$")


def _scalar_out(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    text = str(value)
    if (PLAIN.match(text) and text.lower() not in RESERVED and not text.endswith(" ")
            and ": " not in text and " #" not in text):
        return text
    return json.dumps(text, ensure_ascii=False)


def to_yaml(value: Any, indent: int = 0) -> str:
    """Block-style YAML for what `from_yaml` reads back: mappings, sequences, scalars, literal blocks."""
    pad = " " * indent
    if isinstance(value, dict):
        if not value:
            return pad + "{}"
        lines = []
        for key, item in value.items():
            k = _scalar_out(str(key))
            if isinstance(item, str) and "\n" in item and not item.startswith((" ", "\t")) and not item.endswith("\n\n") \
                    and not any(line != line.rstrip() for line in item.split("\n")):
                marker = "|" if item.endswith("\n") else "|-"
                body = item[:-1] if item.endswith("\n") else item
                lines.append(f"{pad}{k}: {marker}")
                lines += [(f"{pad}  {line}" if line else "") for line in body.split("\n")]
            elif isinstance(item, (dict, list)) and item:
                lines.append(f"{pad}{k}:")
                lines.append(to_yaml(item, indent + 2))
            elif isinstance(item, dict):
                lines.append(f"{pad}{k}: {{}}")
            elif isinstance(item, list):
                lines.append(f"{pad}{k}: []")
            else:
                lines.append(f"{pad}{k}: {_scalar_out(item)}")
        return "\n".join(lines)
    if isinstance(value, list):
        if not value:
            return pad + "[]"
        lines = []
        for item in value:
            if isinstance(item, (dict, list)) and item:
                inner = to_yaml(item, indent + 2)
                lines.append(pad + "- " + inner[indent + 2:])
            elif isinstance(item, str) and "\n" in item:
                lines.append(pad + "- " + json.dumps(item, ensure_ascii=False))
            elif isinstance(item, dict):
                lines.append(pad + "- {}")
            elif isinstance(item, list):
                lines.append(pad + "- []")
            else:
                lines.append(pad + "- " + _scalar_out(item))
        return "\n".join(lines)
    return pad + _scalar_out(value)


class YamlError(ValueError):
    pass


@dataclass(slots=True)
class _Line:
    n: int
    indent: int
    text: str


def _strip_comment(text: str) -> str:
    """A comment is ` #` outside quotes; a # inside a quoted scalar is text."""
    quote = None
    for i, ch in enumerate(text):
        if quote:
            if ch == "\\" and quote == '"':
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
    return text.rstrip()


def _unquote(text: str, n: int) -> str:
    if text.startswith('"'):
        try:
            value = json.loads(text)
        except json.JSONDecodeError as e:
            raise YamlError(f"line {n}: a double-quoted text that does not close properly") from e
        if not isinstance(value, str):
            raise YamlError(f"line {n}: expected text")
        return value
    if not text.endswith("'") or len(text) < 2:
        raise YamlError(f"line {n}: a single-quoted text that does not close")
    return text[1:-1].replace("''", "'")


def _split_flow(body: str, n: int) -> list[str]:
    parts, depth, quote, start = [], 0, None, 0
    for i, ch in enumerate(body):
        if quote:
            if ch == quote and (quote == "'" or body[i - 1] != "\\"):
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(body[start:i].strip())
            start = i + 1
    if quote or depth:
        raise YamlError(f"line {n}: a flow collection that does not close")
    last = body[start:].strip()
    if last:
        parts.append(last)
    return parts


def _key_split(text: str) -> tuple[str, str] | None:
    """`key: value` → (key, value); None when the text is not a mapping entry."""
    if text.startswith(('"', "'")):
        quote = text[0]
        i = 1
        while i < len(text):
            if text[i] == "\\" and quote == '"':
                i += 2
                continue
            if text[i] == quote:
                if quote == "'" and i + 1 < len(text) and text[i + 1] == "'":
                    i += 2
                    continue
                break
            i += 1
        rest = text[i + 1:]
        if rest.startswith(":") and (len(rest) == 1 or rest[1] == " "):
            return text[:i + 1], rest[1:].strip()
        return None
    m = re.match(r"^([^:#\[\]{},]+?):(?:\s+(.*))?$", text)
    if not m:
        return None
    return m.group(1).strip(), (m.group(2) or "").strip()


def _value(text: str, n: int, depth: int = 0) -> Any:
    if depth > 40:
        raise YamlError(f"line {n}: nested too deeply")
    if not text:
        return None
    if text[0] in "&*!":
        raise YamlError(f"line {n}: anchors, aliases and tags are not supported here — export the file as JSON "
                        "and import that instead")
    if text[0] in "|>":
        raise YamlError(f"line {n}: a block scalar where a single value was expected")
    if text.startswith("["):
        if not text.endswith("]"):
            raise YamlError(f"line {n}: a list that does not close on its line")
        return [_value(p, n, depth + 1) for p in _split_flow(text[1:-1], n)]
    if text.startswith("{"):
        if not text.endswith("}"):
            raise YamlError(f"line {n}: a mapping that does not close on its line")
        out: dict[str, Any] = {}
        for part in _split_flow(text[1:-1], n):
            kv = _key_split(part)
            if kv is None:
                raise YamlError(f"line {n}: {part!r} is not key: value")
            out[_key(kv[0], n)] = _value(kv[1], n, depth + 1)
        return out
    if text[0] in "\"'":
        return _unquote(text, n)
    low = text.lower()
    if low in ("null", "~"):
        return None
    if low in ("true", "yes", "on"):
        return True
    if low in ("false", "no", "off"):
        return False
    if NUMBER.match(text):
        return float(text) if any(c in text for c in ".eE") else int(text)
    return text


def _key(text: str, n: int) -> str:
    return _unquote(text, n) if text[:1] in "\"'" else text


def from_yaml(text: str) -> Any:
    """Read the YAML subset `to_yaml` writes (and what people usually write by hand in the same style)."""
    raw = text.replace("\r\n", "\n").replace("\t", "    ").split("\n")
    lines: list[_Line] = []
    for n, line in enumerate(raw, 1):
        body = line.rstrip()
        if body.strip() in ("---", "..."):
            continue
        lines.append(_Line(n, len(body) - len(body.lstrip(" ")), body.lstrip(" ")))

    def meaningful(i: int) -> int:
        while i < len(lines) and (not lines[i].text or lines[i].text.startswith("#")):
            i += 1
        return i

    def block_scalar(i: int, parent: int, marker: str) -> tuple[str, int]:
        body: list[str] = []
        indent: int | None = None
        while i < len(lines):
            line = lines[i]
            if line.text and line.indent <= parent:
                break
            if line.text and indent is None:
                indent = line.indent
            body.append(" " * max(0, line.indent - (indent or 0)) + line.text if line.text else "")
            i += 1
        while body and not body[-1]:
            body.pop()
        joined = "\n".join(body)
        if marker.startswith(">"):
            joined = re.sub(r"(?<!\n)\n(?!\n)", " ", joined)
        return (joined if marker.endswith("-") else joined + "\n"), i

    def parse(i: int, indent: int, depth: int) -> tuple[Any, int]:
        if depth > 40:
            raise YamlError("nested too deeply")
        i = meaningful(i)
        if i >= len(lines):
            return None, i
        line = lines[i]
        if line.text == "-" or line.text.startswith("- "):
            return sequence(i, line.indent, depth)
        if _key_split(_strip_comment(line.text)) is not None:
            return mapping(i, line.indent, depth)
        return _value(_strip_comment(line.text), line.n, depth), i + 1

    def child(i: int, owner: int, depth: int) -> tuple[Any, int]:
        j = meaningful(i)
        if j < len(lines) and lines[j].indent > owner:
            return parse(j, lines[j].indent, depth + 1)
        if j < len(lines) and lines[j].indent == owner and (lines[j].text == "-" or lines[j].text.startswith("- ")):
            return sequence(j, owner, depth + 1)
        return None, i

    def mapping(i: int, indent: int, depth: int) -> tuple[dict[str, Any], int]:
        out: dict[str, Any] = {}
        while True:
            i = meaningful(i)
            if i >= len(lines) or lines[i].indent < indent:
                return out, i
            line = lines[i]
            if line.indent > indent:
                raise YamlError(f"line {line.n}: indented more than the entries above it")
            if line.text == "-" or line.text.startswith("- "):
                return out, i
            kv = _key_split(_strip_comment(line.text))
            if kv is None:
                raise YamlError(f"line {line.n}: expected key: value")
            key = _key(kv[0], line.n)
            if key in out:
                raise YamlError(f"line {line.n}: {key} appears twice")
            rest = kv[1]
            if rest[:1] in ("|", ">") and re.fullmatch(r"[|>][-+]?", rest):
                out[key], i = block_scalar(i + 1, indent, rest)
            elif rest:
                out[key], i = _value(rest, line.n, depth), i + 1
            else:
                out[key], i = child(i + 1, indent, depth)
        # unreachable

    def sequence(i: int, indent: int, depth: int) -> tuple[list[Any], int]:
        out: list[Any] = []
        while True:
            i = meaningful(i)
            if i >= len(lines) or lines[i].indent != indent or not (lines[i].text == "-" or lines[i].text.startswith("- ")):
                if i < len(lines) and lines[i].indent > indent:
                    raise YamlError(f"line {lines[i].n}: indented more than the list above it")
                return out, i
            line = lines[i]
            rest = line.text[1:].lstrip(" ")
            if not rest:
                value, i = child(i + 1, indent, depth)
                out.append(value)
                continue
            col = indent + (len(line.text) - len(rest))
            if rest.startswith("- ") or rest == "-" or (_key_split(_strip_comment(rest)) is not None
                                                         and not rest.startswith(("[", "{"))):
                # "- key: value" opens a mapping whose first entry sits on the dash's line.
                lines[i] = _Line(line.n, col, rest)
                value, i = parse(i, col, depth + 1)
                out.append(value)
            else:
                out.append(_value(_strip_comment(rest), line.n, depth))
                i += 1

    value, end = parse(0, 0, 0)
    end = meaningful(end)
    if end < len(lines):
        raise YamlError(f"line {lines[end].n}: unexpected text after the document")
    return value


def read_document(text: str) -> Any:
    """JSON when it looks like JSON, else the YAML subset — with the reason in words when neither reads."""
    body = text.strip()
    if not body:
        raise Refused("The file is empty.", status=422)
    if len(text.encode()) > MAX_IMPORT:
        raise Refused(f"The file is larger than {MAX_IMPORT // 1024} KB; a blueprint is a few kilobytes.", status=413)
    if body[0] in "{[":
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise Refused(f"That is not valid JSON: {e.msg} at line {e.lineno}.", status=422) from e
    try:
        return from_yaml(body)
    except YamlError as e:
        raise Refused(f"That YAML could not be read: {e}.", status=422) from e


# ── scaffolding on disk ──────────────────────────────────────────
def _listing(folder: Path) -> list[str]:
    return [p.name for p in folder.iterdir() if p.name not in IGNORABLE]


def prepare(roots: list[Path], message: str) -> None:
    """Each root as an empty git repository with one empty commit, so a run has something to branch from.

    The commit holds no file: everything the blueprint describes is written later by agents in worktrees
    and lands only with a person's signature. Blocking."""
    for root in roots:
        root.mkdir(parents=True, exist_ok=True)
        made = git(["init", "-b", "main"], root)
        if made.returncode != 0:
            raise Refused(f"git init failed in {root}: {made.stderr.strip()[:200]}", status=500)
        first = git([*AUTHOR, "commit", "--allow-empty", "-m", message], root)
        if first.returncode != 0:
            raise Refused(f"The first commit failed in {root}: {first.stderr.strip()[:200]}", status=500)


def undo(folder: Path, roots: list[Path]) -> None:
    """Take back what `prepare` made in a folder that was empty before it. Blocking."""
    for root in roots:
        target = root / ".git" if root == folder else root
        shutil.rmtree(target, ignore_errors=True)


# ── the service ──────────────────────────────────────────────────
@dataclass(slots=True)
class Scaffolded:
    project: Project
    sources: list[ProjectSource]
    plan: Plan
    task: Task
    primary: Path


class BlueprintService:
    def __init__(self, session: AsyncSession, gateway: Gateway | None = None) -> None:
        self.session = session
        self.gateway = gateway
        self.activity = ActivityRepository(session)
        self.projects = ProjectRepository(session)

    # ── reading ──────────────────────────────────────────────────
    async def get(self, blueprint_id: str) -> Blueprint:
        found = await self.session.get(Blueprint, blueprint_id)
        if found is None:
            raise NotFound(f"blueprint {blueprint_id}")
        return found

    async def names(self, user_ids: list[str | None]) -> dict[str, str]:
        wanted = [u for u in set(user_ids) if u]
        if not wanted:
            return {}
        rows = await self.session.execute(select(User.id, User.name).where(User.id.in_(wanted)))
        return {uid: name for uid, name in rows.all()}

    async def page(self, *, limit: int | None, offset: int) -> tuple[list[Blueprint], int]:
        size = min(bounded(limit), MAX_PAGE)
        total = (await self.session.execute(select(func.count()).select_from(Blueprint))).scalar_one()
        rows = await self.session.execute(select(Blueprint).order_by(Blueprint.updated_at.desc(), Blueprint.id)
                                          .limit(size).offset(max(0, offset)))
        return list(rows.scalars()), int(total)

    async def mine(self, *, limit: int | None = None, offset: int = 0) -> tuple[list[BlueprintTemplate], int]:
        size = min(bounded(limit), MAX_PAGE)
        total = (await self.session.execute(select(func.count()).select_from(BlueprintTemplate))).scalar_one()
        rows = await self.session.execute(select(BlueprintTemplate).order_by(BlueprintTemplate.updated_at.desc())
                                          .limit(size).offset(max(0, offset)))
        return list(rows.scalars()), int(total)

    async def template(self, template_id: str) -> tuple[dict[str, Any], str, BlueprintTemplate | None]:
        """A template as one shape — `{id, name, summary, fitsWhen, avoidWhen, …architecture}` — whether it
        ships in the catalogue or a person saved it, and which of the two it is."""
        if template_id in CATALOGUE.templates:
            return copy.deepcopy(CATALOGUE.templates[template_id]), "catalogue", None
        row = await self.session.get(BlueprintTemplate, template_id)
        if row is None:
            raise NotFound(f"template {template_id}")
        stored = dict(row.spec or {})
        extras = {k: stored.pop(k) for k in TEMPLATE_EXTRAS if k in stored}
        arch = {**blank(), **stored}
        return ({"id": row.id, "name": row.name, "summary": arch.get("summary") or row.description,
                 "fitsWhen": extras.get("fitsWhen", []), "avoidWhen": extras.get("avoidWhen", []),
                 **{k: v for k, v in arch.items() if k != "summary"}}, "mine", row)

    # ── writing ──────────────────────────────────────────────────
    async def create(self, name: str, template_id: str | None, answers: dict[str, Any], *,
                     who: Person) -> Blueprint:
        name = name.strip()
        if not name:
            raise Refused("Give the blueprint a name.", status=422)
        kept = check_answers(answers)
        if template_id:
            tpl, _, _ = await self.template(template_id)
            arch = architecture_of(tpl)
        else:
            arch = blank()
        bp = Blueprint(id=f"bp-{secrets.token_hex(5)}", name=name[:160], template=template_id or "",
                       answers=kept, spec=arch, status="draft", revision=1, created_by=who.id)
        self.session.add(bp)
        await self.session.flush()
        await self.activity.record(actor=who.name, actor_kind="human", action="Blueprint started",
                                   detail=f"{bp.name} · " + (f"from {tpl['name']}" if template_id else "blank"),
                                   level="ok")
        return bp

    def _editable(self, bp: Blueprint) -> None:
        if bp.status == "scaffolded":
            raise Refused(f"{bp.name} was scaffolded into a project, so it stays as it was built. Save it as a "
                          "template and start a new blueprint from it to design the next version.")

    def _revision(self, bp: Blueprint, expected: int | None) -> None:
        if expected is not None and expected != bp.revision:
            raise Refused(f"{bp.name} changed since you opened it: it is at revision {bp.revision}, and you were "
                          f"editing revision {expected}. Reload it and make your change again.")

    async def _bump(self, bp: Blueprint, arch: dict[str, Any], extras: dict[str, Any]) -> None:
        """A new revision. A finalized blueprint that is edited is a draft again: its document describes the
        revision it was written from, and says so, until it is finalized again."""
        bp.spec = {**arch, **extras}
        bp.revision += 1
        if bp.status == "final":
            bp.status = "draft"
        await self.session.flush()

    async def update(self, blueprint_id: str, *, who: Person, expect: int, name: str | None = None,
                     answers: dict[str, Any] | None = None, spec: dict[str, Any] | None = None) -> Blueprint:
        bp = await self.get(blueprint_id)
        self._revision(bp, expect)
        arch, extras = split(bp.spec)
        said: list[str] = []
        if name is not None and name.strip() != bp.name:
            if not name.strip():
                raise Refused("Give the blueprint a name.", status=422)
            bp.name = name.strip()[:160]
            said.append("renamed")
        if answers is not None or spec is not None:
            self._editable(bp)
        if answers is not None:
            kept = check_answers(answers)
            if not same(kept, bp.answers or {}):
                bp.answers = kept
                said.append("answers")
        if spec is not None:
            if not isinstance(spec, dict):
                raise Refused("The architecture is an object.", status=422)
            wanted = {**blank(), **{k: v for k, v in spec.items() if k not in EXTRAS}}
            wrong = spec_problems(wanted)
            if wrong:
                raise Refused("The architecture does not fit its shape: " + "; ".join(wrong[:5]), status=422)
            if not same(wanted, arch):
                arch = wanted
                said.append("architecture")
        if not said:
            return bp
        await self._bump(bp, arch, extras)
        await self.activity.record(actor=who.name, actor_kind="human", action="Blueprint edited",
                                   detail=f"{bp.name} · {', '.join(said)} · revision {bp.revision}", level="info",
                                   project_id=bp.project_id)
        return bp

    async def remove(self, blueprint_id: str, *, who: Person) -> None:
        bp = await self.get(blueprint_id)
        if bp.status == "scaffolded":
            raise Refused(f"{bp.name} was scaffolded into a project and is its record of how it was designed, so it "
                          "is kept.")
        await self.session.delete(bp)
        await self.session.flush()
        await self.activity.record(actor=who.name, actor_kind="human", action="Blueprint deleted", detail=bp.name,
                                   level="warn")

    # ── a model's review ─────────────────────────────────────────
    async def suggest(self, blueprint_id: str, *, who: Person, expect: int | None = None) -> Blueprint:
        """Ask a model to critique the architecture against the answers. What it proposes is kept on the
        blueprint as a review — never applied. Each change's `from` is the value really there now, whatever
        the model quoted; a change that would break the blueprint's shape is left out and counted."""
        if self.gateway is None:
            raise Refused("No model gateway is available here.", status=500)
        bp = await self.get(blueprint_id)
        self._revision(bp, expect)
        self._editable(bp)
        arch, extras = split(bp.spec)
        catalogue = [(t["id"], t["name"], t["category"]) for t in CATALOGUE.tech]
        gw = self.gateway
        result = await asyncio.to_thread(needs_a_model, lambda: reviewer.review(
            gw, bp.name, bp.answers or {}, arch, catalogue, actor=who.id))
        proposed: list[dict[str, Any]] = []
        dropped: list[str] = []
        trial = copy.deepcopy(arch)
        for n, change in enumerate(result.data.changes[:MAX_CHANGES]):
            try:
                now = get_at(arch, change.path)
                if now is not MISSING and same(now, change.to):
                    dropped.append(f"{change.path}: already so")
                    continue
                attempt = copy.deepcopy(trial)
                set_at(attempt, change.path, change.to)
            except Refused as refused:
                dropped.append(f"{change.path}: {refused}")
                continue
            wrong = spec_problems(attempt)
            if wrong:
                dropped.append(f"{change.path}: {wrong[0]}")
                continue
            proposed.append({"id": f"c{n + 1}", "path": change.path, "from": None if now is MISSING else now,
                             "to": change.to, "why": change.why.strip()[:600]})
        extras["review"] = {"summary": result.data.summary.strip()[:1500], "changes": proposed, "dropped": dropped,
                            "revision": bp.revision, "model": result.provider.model, "provider": result.provider.id,
                            "ms": result.ms, "at": utcnow().isoformat(timespec="seconds"), "by": who.name,
                            "decided": {}}
        bp.spec = {**arch, **extras}
        await self.session.flush()
        await self.activity.record(actor=who.name, actor_kind="human", action="Blueprint reviewed",
                                   detail=f"{bp.name} · {len(proposed)} change{'s' if len(proposed) != 1 else ''} "
                                          f"proposed by {result.provider.model}"
                                          + (f" · {len(dropped)} left out" if dropped else ""),
                                   level="info", project_id=bp.project_id)
        return bp

    async def apply(self, blueprint_id: str, accepted: list[dict[str, Any]], rejected: list[str], *,
                    who: Person, expect: int) -> Blueprint:
        """Apply the changes a person accepted, all or none, against the revision they were reading. A change
        whose `from` no longer matches the blueprint is refused: it was judged against something else."""
        bp = await self.get(blueprint_id)
        self._revision(bp, expect)
        self._editable(bp)
        if not accepted and not rejected:
            raise Refused("Accept or reject at least one change.", status=422)
        arch, extras = split(bp.spec)
        review = extras.get("review") or {}
        for change in accepted:
            path = str(change.get("path") or "")
            now = get_at(arch, path)
            if "from" in change and not same(None if now is MISSING else now, change["from"]):
                raise Refused(f"{path} is no longer what the review saw. Ask for a review again.")
            set_at(arch, path, change.get("to"))
        wrong = spec_problems(arch)
        if wrong:
            raise Refused("Those changes together do not fit the blueprint's shape: " + "; ".join(wrong[:3]),
                          status=422)
        decided = dict(review.get("decided") or {})
        decided.update({str(c["id"]): "accepted" for c in accepted if c.get("id")})
        decided.update({str(x): "rejected" for x in rejected})
        if review:
            extras["review"] = {**review, "decided": decided}
        if accepted:
            await self._bump(bp, arch, extras)
        else:
            bp.spec = {**arch, **extras}
            await self.session.flush()
        await self.activity.record(actor=who.name, actor_kind="human", action="Review changes decided",
                                   detail=f"{bp.name} · {len(accepted)} accepted, {len(rejected)} rejected"
                                          + (f" · revision {bp.revision}" if accepted else ""),
                                   level="ok", project_id=bp.project_id)
        return bp

    # ── finalizing ───────────────────────────────────────────────
    async def finalize(self, blueprint_id: str, *, who: Person, expect: int | None = None) -> Blueprint:
        """The document, the diagram, and every decision written into memory (decisions, with the blueprint
        as evidence). Finalizing again replaces the decisions that changed: the old ones are archived —
        never deleted — and the ones still true are kept as they were."""
        bp = await self.get(blueprint_id)
        self._revision(bp, expect)
        self._editable(bp)
        arch, extras = split(bp.spec)
        if not any(v.get("choice") for v in (arch.get("layers") or {}).values()):
            raise Refused("Choose a technology for at least one layer before finalizing.", status=422)
        blocking = checks(arch)
        if blocking:
            raise Refused("Fix these first: " + " ".join(c["problem"] for c in blocking[:4]), status=422)
        tpl_name = None
        if bp.template:
            try:
                tpl_name = (await self.template(bp.template))[0]["name"]
            except NotFound:
                tpl_name = bp.template
        wanted = decisions(arch)
        evidence = f"Blueprint {bp.id} · {bp.name} · revision {bp.revision}"
        facts = MemoryRepository(self.session)
        before = list(((extras.get("final") or {}).get("facts")) or [])
        kept: list[str] = []
        still: set[tuple[str, str]] = set()
        for ref in before:
            fact = await facts.by_ref(ref)
            if fact is None or fact.archived:
                continue
            body = _adr_body(next((a for a in wanted if f"ADR: {a['title']}" == fact.title), None))
            if body is not None and body == fact.body:
                kept.append(fact.ref)
                still.add((fact.title, fact.body))
            else:
                await MemoryService(self.session).archive(fact.ref, who.name)
        fresh = [NewFact(title=f"ADR: {a['title']}", body=_adr_body(a) or "", category="decisions",
                         confidence="MEDIUM",
                         reason=f"Decided in the blueprint {bp.name} and finalized by {who.name}; "
                                "a design decision, not yet proven by code.",
                         evidence=[evidence])
                 for a in wanted if (f"ADR: {a['title']}", _adr_body(a)) not in still]
        written: list[MemoryFact] = []
        if fresh:
            written = await MemoryService(self.session).add(fresh, project_id=bp.project_id, by=who.name,
                                                            source=f"Blueprint {bp.name}")
        extras["final"] = {"document": document(bp, arch, by=who.name, template_name=tpl_name),
                           "diagram": diagram(arch), "facts": kept + [f.ref for f in written],
                           "revision": bp.revision, "at": utcnow().isoformat(timespec="seconds"), "by": who.name}
        bp.spec = {**arch, **extras}
        bp.status = "final"
        await self.session.flush()
        await self.activity.record(actor=who.name, actor_kind="human", action="Blueprint finalized",
                                   detail=f"{bp.name} · revision {bp.revision} · {len(wanted)} decisions in memory"
                                          + (f" ({len(written)} new)" if kept else ""),
                                   level="ok", project_id=bp.project_id)
        return bp

    # ── export and import ────────────────────────────────────────
    async def export(self, blueprint_id: str, fmt: str) -> dict[str, Any]:
        bp = await self.get(blueprint_id)
        arch, _ = split(bp.spec)
        doc = {"format": FORMAT, "version": VERSION, "name": bp.name, "template": bp.template or None,
               "answers": bp.answers or {}, "spec": arch}
        return _file(bp.name, doc, fmt)

    async def export_template(self, template_id: str, fmt: str) -> dict[str, Any]:
        tpl, _, _ = await self.template(template_id)
        return _file(tpl["name"], {"format": TEMPLATE_FORMAT, "version": VERSION, **tpl}, fmt)

    async def import_(self, text: str, *, who: Person, as_kind: str | None = None) -> tuple[str, Any]:
        """A blueprint or a template from a file: `neurocode.blueprint` becomes a draft blueprint;
        `neurocode.template` — or a template file shaped like the catalogue's — becomes one of your
        templates. Everything is checked against the same schema the catalogue is."""
        doc = read_document(text)
        if not isinstance(doc, dict):
            raise Refused("The file holds no blueprint: expected an object at the top.", status=422)
        kind = doc.get("format")
        if kind == FORMAT or (kind is None and "spec" in doc and "layers" not in doc):
            if as_kind == "template":
                raise Refused("That file is a blueprint. Import it as a blueprint, then save it as a template.",
                              status=422)
            if doc.get("version", VERSION) != VERSION:
                raise Refused(f"This server reads blueprint files of version {VERSION}; that one is version "
                              f"{doc.get('version')!r}.", status=422)
            arch = {**blank(), **(doc.get("spec") or {})} if isinstance(doc.get("spec"), dict) else None
            if arch is None:
                raise Refused("The file has no architecture (spec).", status=422)
            wrong = spec_problems(arch)
            if wrong:
                raise Refused("The architecture in that file does not fit: " + "; ".join(wrong[:5]), status=422)
            name = str(doc.get("name") or "Imported blueprint").strip()[:160]
            template = doc.get("template") if isinstance(doc.get("template"), str) else None
            bp = Blueprint(id=f"bp-{secrets.token_hex(5)}", name=name,
                           template=template if template in CATALOGUE.templates else "",
                           answers=check_answers(doc.get("answers") or {}), spec=arch, status="draft",
                           revision=1, created_by=who.id)
            self.session.add(bp)
            await self.session.flush()
            await self.activity.record(actor=who.name, actor_kind="human", action="Blueprint imported",
                                       detail=bp.name, level="ok")
            return "blueprint", bp
        if kind == TEMPLATE_FORMAT or (kind is None and "layers" in doc and "fitsWhen" in doc):
            if as_kind == "blueprint":
                raise Refused("That file is a template. Import it as a template, then start a blueprint from it.",
                              status=422)
            body = {k: v for k, v in doc.items() if k not in ("format", "version")}
            # A template of your own may have no conditions (one saved from a blueprint has none), so the
            # parts are checked on their own rather than against the catalogue's stricter whole.
            wrong = problems(body.get("name"), {"type": "string", "minLength": 1, "maxLength": 160}, path="name")
            for key in ("fitsWhen", "avoidWhen"):
                wrong += problems(body.get(key, []), {"type": "array", "maxItems": 12,
                                                      "items": {"$ref": "#/$defs/condition"}},
                                  CATALOGUE.schema, path=key)
            wrong += spec_problems(architecture_of({**blank(), **body}))
            if wrong:
                raise Refused("That template does not fit the template shape: " + "; ".join(wrong[:5]), status=422)
            row = await self._keep_template(str(body["name"]), str(body.get("summary") or ""), architecture_of(body),
                                            who=who, fits=body.get("fitsWhen") or [], avoid=body.get("avoidWhen") or [],
                                            how="imported")
            return "template", row
        raise Refused("That file is neither a NeuroCode blueprint nor a template (its format is "
                      f"{kind!r}).", status=422)

    # ── a person's own templates ─────────────────────────────────
    async def _keep_template(self, name: str, description: str, arch: dict[str, Any], *, who: Person,
                             fits: list[Any] | None = None, avoid: list[Any] | None = None,
                             how: str = "saved") -> BlueprintTemplate:
        name = name.strip()
        if not name:
            raise Refused("Give the template a name.", status=422)
        slug = onboarding.slug(name)[:40]
        row = BlueprintTemplate(id=f"{MINE}{slug}-{secrets.token_hex(2)}", name=name[:160],
                                description=description.strip()[:2000],
                                spec={**arch, **({"fitsWhen": fits} if fits else {}),
                                      **({"avoidWhen": avoid} if avoid else {})},
                                created_by=who.id)
        self.session.add(row)
        await self.session.flush()
        await self.activity.record(actor=who.name, actor_kind="human", action=f"Template {how}",
                                   detail=row.name, level="ok")
        return row

    async def save_template(self, blueprint_id: str, name: str, description: str, *, who: Person) -> BlueprintTemplate:
        bp = await self.get(blueprint_id)
        arch, _ = split(bp.spec)
        return await self._keep_template(name or bp.name, description or arch.get("summary") or "", arch, who=who)

    async def remove_template(self, template_id: str, *, who: Person) -> None:
        if template_id in CATALOGUE.templates:
            raise Refused("Templates in the catalogue ship with NeuroCode and cannot be deleted.")
        row = await self.session.get(BlueprintTemplate, template_id)
        if row is None:
            raise NotFound(f"template {template_id}")
        if row.created_by != who.id and not who.can("workspace:admin"):
            raise Refused("Only the person who saved a template, or an administrator, can delete it.", status=403)
        await self.session.delete(row)
        await self.session.flush()
        await self.activity.record(actor=who.name, actor_kind="human", action="Template deleted", detail=row.name,
                                   level="warn")

    # ── scaffolding ──────────────────────────────────────────────
    async def scaffold(self, blueprint_id: str, folder: str, labels: list[str] | None, *, who: Person) -> Scaffolded:
        """A finalized blueprint becomes a project: an empty repository per scaffold repo in the folder a
        person picked (which must be empty), the project and its further sources onboarded from them, and a
        plan compiled whose requirement is the scaffold recipe. Refused before anything is written when no
        model can compile it; if anything fails after the folder was touched, the folder is put back the way
        it was."""
        bp = await self.get(blueprint_id)
        if bp.status == "scaffolded":
            raise Refused(f"{bp.name} was already scaffolded into {bp.project_id or 'a project'}.")
        arch, extras = split(bp.spec)
        final = extras.get("final")
        if bp.status != "final" or not final or final.get("revision") != bp.revision:
            raise Refused(f"Finalize {bp.name} first: the scaffold is built from the finalized architecture.")
        repos = (arch.get("scaffold") or {}).get("repos") or []
        if not repos:
            raise Refused(f"{bp.name} names no repository to scaffold. Add one in the Scaffold section.", status=422)
        if labels:
            unknown = [x for x in labels if x not in {r["label"] for r in repos}]
            if unknown:
                raise Refused(f"{bp.name} has no repository labelled {', '.join(unknown)}.", status=422)
            repos = [r for r in repos if r["label"] in labels]
        real = await asyncio.to_thread(machine.inside, folder)
        if not await asyncio.to_thread(real.is_dir):
            raise Refused(f"{real} is not a folder.", status=404)
        if await asyncio.to_thread(_listing, real):
            raise Refused(f"{real} is not empty. Pick an empty folder — the picker can make a new one.")

        if self.gateway is None:
            raise Refused("No model gateway is available here.", status=500)
        gw = self.gateway
        # The plan needs a model. Asked before the folder is touched, so "no model" leaves it as it was
        # rather than made and taken back; a provider that fails later is undone below.
        if not await asyncio.to_thread(lambda: gw.chain()):
            raise Refused(NO_MODEL, status=409)

        single = len(repos) == 1
        roots = [real] if single else [real / r["label"] for r in repos]
        primary_label = repos[0]["label"]
        await asyncio.to_thread(prepare, roots, f"Start {bp.name} from blueprint {bp.id}")
        try:
            async with self.session.begin_nested():
                made = await self._scaffold_rows(bp, arch, extras, repos, roots, primary_label, real, who=who)
        except BaseException:
            await asyncio.to_thread(undo, real, roots)
            raise
        return made

    async def _scaffold_rows(self, bp: Blueprint, arch: dict[str, Any], extras: dict[str, Any],
                             repos: list[dict[str, Any]], roots: list[Path], primary_label: str, folder: Path,
                             *, who: Person) -> Scaffolded:
        base = onboarding.slug(bp.name)
        pid, n = base, 1
        while await self.projects.get(pid) is not None:
            n += 1
            pid = f"{base}-{n}"
        # Written here rather than through the onboarding wizard's service: the project is named for the
        # blueprint, not for whichever folder happens to hold its first repository.
        project = await self.projects.add(Project(
            id=pid, name=bp.name[:120], codename=pid.upper()[:80], kind="greenfield", status="onboarding",
            description=(arch.get("summary") or f"Scaffolded from the blueprint {bp.name}.")[:2000],
            repo=str(roots[0]), source_kind="local", source_repo=str(roots[0]), source_branch="",
            last_active_at=utcnow()))
        await self.session.flush()
        await self.activity.record(actor=who.name, actor_kind="human", action="Onboarding started",
                                   detail=f"{project.name} · {roots[0]} · from blueprint {bp.name}", project_id=pid)
        sources: list[ProjectSource] = []
        for repo, root in zip(repos[1:], roots[1:]):
            sources.append(await SourceService(self.session).add(
                pid, SourceSpec(label=repo["label"], kind="local", repo=str(root)), who.name))
        if self.gateway is None:
            raise Refused("No model gateway is available here.", status=500)
        plans = PlanService(self.session, self.gateway)
        plan, task = await plans.compile(pid, recipe(bp, arch, repos, primary_label), by=who.name, by_id=who.id)
        bp.project_id = pid
        bp.status = "scaffolded"
        extras["scaffolded"] = {"projectId": pid, "planRef": plan.ref, "taskRef": task.ref, "folder": str(folder),
                                "repos": [{"label": r["label"], "path": str(root)} for r, root in zip(repos, roots)],
                                "at": utcnow().isoformat(timespec="seconds"), "by": who.name}
        bp.spec = {**arch, **extras}
        # The decisions were written before there was a project; they belong to it now.
        refs = list(((extras.get("final") or {}).get("facts")) or [])
        if refs:
            for fact in await MemoryRepository(self.session).by_refs(refs):
                fact.project_id = pid
        await self.session.flush()
        await self.activity.record(actor=who.name, actor_kind="human", action="Blueprint scaffolded",
                                   detail=f"{bp.name} → {project.name} · {len(repos)} "
                                          f"repositor{'ies' if len(repos) != 1 else 'y'} · {plan.ref} waits for dispatch",
                                   level="ok", project_id=pid, task_ref=task.ref)
        return Scaffolded(project=project, sources=sources, plan=plan, task=task, primary=roots[0])


def _adr_body(adr: dict[str, Any] | None) -> str | None:
    if adr is None:
        return None
    body = f"{adr['decision']} Why: {adr['why']}"
    if adr.get("alternatives"):
        body += f" Considered: {', '.join(adr['alternatives'])}."
    return body


def _file(name: str, doc: dict[str, Any], fmt: str) -> dict[str, Any]:
    stem = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "blueprint"
    if fmt == "json":
        return {"filename": f"{stem}.json", "mime": "application/json",
                "text": json.dumps(doc, ensure_ascii=False, indent=2) + "\n"}
    if fmt == "yaml":
        return {"filename": f"{stem}.yaml", "mime": "application/yaml", "text": to_yaml(doc) + "\n"}
    raise Refused("Export as json or yaml.", status=422)


__all__ = ["CATALOGUE", "LAYERS", "QUESTIONS", "BlueprintService", "Scaffolded", "blank", "checks", "decisions",
           "diagram", "document", "fit", "from_yaml", "problems", "rank", "read_document", "recipe",
           "spec_problems", "split", "template_summary", "to_yaml"]
