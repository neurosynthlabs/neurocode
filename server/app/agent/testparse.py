"""What a test runner said, read from its own output.

The runtime runs a project's own test command and used to keep only its exit code and its last few
lines. This reads the whole stream, line by line as it arrives, and picks out the two things a runner
always prints in a shape of its own: the totals, and each failing test with where it failed.

Five shapes are known, each taken from the real output of the runner it names: pytest (-q and the
default), jest, vitest, `go test` and `dotnet test` (VSTest's console logger). Reading is heuristic by
nature — a Makefile can wrap anything, and formats drift between versions — so it fails closed: a total
is only reported when the runner's own summary line was seen, and nothing is reported at all when no
shape matched. "We could not read it" must never look like "nothing failed".

Pure: text in, a report out. No file is opened and nothing is run here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: How much of one run's failures is kept. The totals still count every failure past the cap.
#: Test results a single parse keeps track of, so a runaway output cannot grow without bound.
MAX_TRACKED = 20_000
MAX_FAILURES = 200
EXCERPT_LINES, EXCERPT_CHARS, MESSAGE_CHARS = 40, 8_000, 500

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


@dataclass(slots=True)
class Failure:
    """One failing test. `name` is what the runner calls it, unique within a project."""

    name: str
    file: str = ""
    line: int | None = None
    message: str = ""
    excerpt: str = ""


@dataclass(slots=True)
class Report:
    """The runner that was recognised, its totals when it printed them, and what failed."""

    runner: str = ""
    passed: int | None = None
    failed: int | None = None
    skipped: int | None = None
    total: int | None = None
    failures: list[Failure] = field(default_factory=list)


class _Excerpt:
    """The lines that follow a failure's header, bounded in lines and in characters."""

    __slots__ = ("lines", "size")

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.size = 0

    def add(self, text: str) -> None:
        if len(self.lines) < EXCERPT_LINES and self.size + len(text) <= EXCERPT_CHARS:
            self.lines.append(text)
            self.size += len(text) + 1

    def text(self) -> str:
        return "\n".join(self.lines).strip("\n")


def _relative(path: str, roots: tuple[str, ...]) -> str:
    """A path as the project knows it. Runners print absolute paths when they feel like it."""
    for root in roots:
        stem = root.rstrip("/") + "/"
        if root and path.startswith(stem):
            return path[len(stem):]
    return path


class _Parser:
    runner = ""

    def __init__(self, roots: tuple[str, ...]) -> None:
        self.roots = roots
        #: The line at which this runner was first recognised, so the first one seen leads.
        self.first: int | None = None

    def seen(self, at: int) -> None:
        if self.first is None:
            self.first = at

    def feed(self, at: int, text: str) -> None:
        raise NotImplementedError

    def report(self) -> Report | None:
        raise NotImplementedError


# ── pytest ───────────────────────────────────────────────────────
PY_BLOCK = re.compile(r"^=+ (FAILURES|ERRORS) =+$")
PY_RULE = re.compile(r"^=+( .* )?=+$")
PY_SECTION = re.compile(r"^_{3,} (.+?) _{3,}$")
PY_SUMMARY = re.compile(r"^(?:=+ )?((?:\d+ [a-z]+(?:, )?)+) in [\d.]+s(?: \([^)]*\))?(?: =+)?$")
PY_NOTHING = re.compile(r"^(?:=+ )?no tests ran in [\d.]+s")
PY_RESULT = re.compile(r"^(FAILED|ERROR) (\S.*?\.py\S*)(?: - (.*))?$")
PY_WHERE = re.compile(r"^(\S+?\.py):(\d+)(?::|$)")
PY_FIXTURE_WHERE = re.compile(r"^file (\S+?\.py), line (\d+)$")
PY_WORDS = {"passed", "failed", "skipped", "error", "errors", "xfailed", "xpassed", "deselected",
            "warning", "warnings", "rerun", "subtests"}
PY_TITLE_PREFIX = re.compile(r"^ERROR (?:at (?:setup|teardown) of|collecting) ")


class _Pytest(_Parser):
    runner = "pytest"

    def __init__(self, roots: tuple[str, ...]) -> None:
        super().__init__(roots)
        self.block = False
        self.current: str | None = None
        # title → (excerpt, locations, first E-line)
        self.sections: dict[str, tuple[_Excerpt, list[tuple[str, int]], list[str]]] = {}
        self.results: dict[str, str] = {}
        self.counts: dict[str, int] | None = None

    def feed(self, at: int, text: str) -> None:
        summary = PY_SUMMARY.match(text)
        if summary:
            pairs = re.findall(r"(\d+) ([a-z]+)", summary.group(1))
            if pairs and all(word in PY_WORDS for _, word in pairs):
                self.counts = {}
                for n, word in pairs:
                    self.counts[word] = self.counts.get(word, 0) + int(n)
                self.seen(at)
                self.block, self.current = False, None
                return
        if PY_NOTHING.match(text):
            self.counts = {}
            self.seen(at)
            return
        if PY_BLOCK.match(text):
            self.block, self.current = True, None
            self.seen(at)
            return
        if PY_RULE.match(text):
            self.block, self.current = False, None
            return
        result = PY_RESULT.match(text)
        if result and len(self.results) < MAX_FAILURES:
            self.results.setdefault(result.group(2), (result.group(3) or "").strip())
            self.seen(at)
            return
        section = PY_SECTION.match(text) if self.block else None
        if section:
            self.current = PY_TITLE_PREFIX.sub("", section.group(1)).strip()
            if len(self.sections) < MAX_FAILURES:
                self.sections.setdefault(self.current, (_Excerpt(), [], []))
            return
        if self.current is None or self.current not in self.sections:
            return
        excerpt, where, errors = self.sections[self.current]
        excerpt.add(text)
        stripped = text.strip()
        found = PY_WHERE.match(stripped) or PY_FIXTURE_WHERE.match(stripped)
        if found:
            where.append((_relative(found.group(1), self.roots), int(found.group(2))))
        if stripped.startswith("E ") and not errors:
            errors.append(stripped[1:].strip())

    def _failure(self, name: str, file: str, title: str, message: str) -> Failure:
        excerpt, where, errors = self.sections.get(title, (_Excerpt(), [], []))
        # The test's own frame is the useful line; a deeper frame in a library is not.
        mine = [line for path, line in where if path == file]
        line = mine[-1] if mine else (where[-1][1] if where else None)
        if not file and where:
            file = where[-1][0]
        return Failure(name=name, file=file, line=line,
                       message=(message or (errors[0] if errors else ""))[:MESSAGE_CHARS],
                       excerpt=excerpt.text())

    def report(self) -> Report | None:
        if self.first is None:
            return None
        failures: list[Failure] = []
        for nodeid, message in self.results.items():
            file, _, rest = nodeid.partition("::")
            file = _relative(file, self.roots)
            # A section is titled `Class.test[param]` for the node `file::Class::test[param]`, and a
            # collection error is titled by its file.
            title = rest.replace("::", ".") if rest else file
            failures.append(self._failure(f"{file}::{rest}" if rest else file, file, title, message))
        if not failures:
            for title in self.sections:
                failure = self._failure(title, "", title, "")
                if failure.file:
                    failure.name = f"{failure.file}::{title.replace('.', '::')}"
                failures.append(failure)
        out = Report(runner=self.runner, failures=failures)
        if self.counts is not None:
            c = self.counts
            out.passed = c.get("passed", 0) + c.get("xpassed", 0) + c.get("xfailed", 0)
            out.failed = c.get("failed", 0) + c.get("error", 0) + c.get("errors", 0)
            out.skipped = c.get("skipped", 0)
            out.total = out.passed + out.failed + out.skipped
        return out


# ── jest ─────────────────────────────────────────────────────────
JEST_FILE = re.compile(r"^(PASS|FAIL) (\S+)(?: \([\d.]+ ?m?s\))?$")
JEST_CASE = re.compile(r"^  ● (.+)$")
JEST_TESTS = re.compile(r"^Tests:\s+(.*?)(\d+) total$")
JEST_AT = re.compile(r"at (?:.*? \()?([^\s()]+?):(\d+):\d+\)?$")
JEST_END = re.compile(r"^(Test Suites|Tests|Snapshots|Time):")


class _Jest(_Parser):
    runner = "jest"

    def __init__(self, roots: tuple[str, ...]) -> None:
        super().__init__(roots)
        self.file = ""
        self.current: Failure | None = None
        self.excerpt = _Excerpt()
        self.failures: dict[tuple[str, str], Failure] = {}
        self.counts: tuple[int, int, int, int] | None = None

    def _close(self) -> None:
        if self.current is not None:
            self.current.excerpt = self.excerpt.text()
            first = next((ln.strip() for ln in self.excerpt.lines if ln.strip()), "")
            self.current.message = first[:MESSAGE_CHARS]
        self.current, self.excerpt = None, _Excerpt()

    def feed(self, at: int, text: str) -> None:
        tests = JEST_TESTS.match(text)
        if tests:
            self._close()
            parts = dict((word, int(n)) for n, word in re.findall(r"(\d+) ([a-z]+)", tests.group(1)))
            skipped = parts.get("skipped", 0) + parts.get("todo", 0) + parts.get("pending", 0)
            self.counts = (parts.get("passed", 0), parts.get("failed", 0), skipped, int(tests.group(2)))
            self.seen(at)
            return
        found = JEST_FILE.match(text)
        if found:
            self._close()
            self.file = _relative(found.group(2), self.roots)
            return
        case = JEST_CASE.match(text)
        if case and self.file:
            self._close()
            title = case.group(1).strip()
            if title == "Console":                 # a block of console output, not a failure
                return
            key = (self.file, f"{self.file} › {title}")
            if key not in self.failures and len(self.failures) < MAX_FAILURES:
                self.failures[key] = Failure(name=key[1], file=self.file)
                self.current = self.failures[key]
            self.seen(at)
            return
        if JEST_END.match(text):
            self._close()
            return
        if self.current is None:
            return
        self.excerpt.add(text)
        where = JEST_AT.search(text.strip())
        if where and self.current.line is None and "node_modules" not in where.group(1):
            path = _relative(where.group(1), self.roots)
            if path == self.current.file:
                self.current.line = int(where.group(2))

    def report(self) -> Report | None:
        self._close()
        if self.first is None:
            return None
        out = Report(runner=self.runner, failures=list(self.failures.values()))
        if self.counts is not None:
            out.passed, out.failed, out.skipped, out.total = self.counts
        return out


# ── vitest ───────────────────────────────────────────────────────
VITEST_FAIL = re.compile(r"^ FAIL  (\S.*?)\s*$")
VITEST_TESTS = re.compile(r"^\s+Tests\s+(.+?)\s+\((\d+)\)$")
VITEST_AT = re.compile(r"^❯ (\S+?):(\d+):\d+$")
VITEST_RULE = re.compile(r"^⎯{3,}")


class _Vitest(_Jest):
    runner = "vitest"

    def feed(self, at: int, text: str) -> None:
        tests = VITEST_TESTS.match(text)
        if tests:
            self._close()
            parts = dict((word, int(n)) for n, word in re.findall(r"(\d+) ([a-z]+)", tests.group(1)))
            skipped = parts.get("skipped", 0) + parts.get("todo", 0)
            self.counts = (parts.get("passed", 0), parts.get("failed", 0), skipped, int(tests.group(2)))
            self.seen(at)
            return
        found = VITEST_FAIL.match(text)
        if found:
            self._close()
            # `file > suite > test` for a test; `file [ file ]` when the whole file failed to load.
            name = found.group(1)
            file = _relative(re.split(r" > | \[", name, maxsplit=1)[0].strip(), self.roots)
            key = (file, name)
            if key not in self.failures and len(self.failures) < MAX_FAILURES:
                self.failures[key] = Failure(name=name, file=file)
                self.current = self.failures[key]
            self.seen(at)
            return
        if VITEST_RULE.match(text):
            self._close()
            return
        if self.current is None:
            return
        self.excerpt.add(text)
        where = VITEST_AT.match(text.strip())
        if where and self.current.line is None and _relative(where.group(1), self.roots) == self.current.file:
            self.current.line = int(where.group(2))


# ── go test ──────────────────────────────────────────────────────
GO_RESULT = re.compile(r"^(\s*)--- (FAIL|PASS|SKIP): (\S+) \([\d.]+s\)$")
GO_RUN = re.compile(r"^=== (?:RUN|CONT|NAME)\s+(\S+)$")
GO_WHERE = re.compile(r"^\s+(\S+\.go):(\d+): ?(.*)$")
# What follows the package is a duration, `(cached)` or a bracketed reason — which is also what keeps
# jest's `FAIL src/a.test.js (5.1 s)` from being read as a Go package.
GO_PACKAGE = re.compile(r"^(ok|FAIL)\s+(\S+)\s+([\d.]+s|\(cached\)|\[[^\]]*\])(?:\s|$)")
GO_BUILD = re.compile(r"^# (\S+)$")
GO_COMPILER = re.compile(r"^(\S+\.go):(\d+):")


class _Go(_Parser):
    runner = "go"

    def __init__(self, roots: tuple[str, ...], module: str) -> None:
        super().__init__(roots)
        self.module = module
        self.verbose = False
        #: The package being read. go names a package only on the line that ends its block, so what is
        #: collected here is keyed by test name alone — safe, because names are unique within a package.
        self.status: dict[str, str] = {}
        self.details: dict[str, tuple[_Excerpt, list[tuple[str, int, str]]]] = {}
        self.current: str | None = None
        #: Packages already ended, keyed by (package, test). Keyed by name alone across packages, the
        #: TestNew that failed in one package was overwritten by the TestNew that passed in the next, and a
        #: real failure vanished from the counts.
        self.closed: dict[tuple[str, str], tuple[str, tuple[_Excerpt, list[tuple[str, int, str]]] | None]] = {}
        self.build: tuple[str, _Excerpt] | None = None
        self.broken: list[Failure] = []

    def feed(self, at: int, text: str) -> None:
        result = GO_RESULT.match(text)
        if result:
            state, name = result.group(2), result.group(3)
            self.seen(at)
            self.verbose = self.verbose or state != "FAIL"
            self.status[name] = state
            if state == "FAIL":
                self.current = name
                if len(self.details) < MAX_FAILURES * 2:
                    self.details.setdefault(name, (_Excerpt(), []))
            else:
                self.details.pop(name, None)            # passed: its output is no longer worth keeping
                self.current = None
            return
        run = GO_RUN.match(text)
        if run:
            self.seen(at)
            self.verbose, self.current = True, run.group(1)
            if len(self.details) < MAX_FAILURES * 2:
                self.details.setdefault(self.current, (_Excerpt(), []))
            return
        package = GO_PACKAGE.match(text)
        if package:
            self.seen(at)
            pkg, rest = package.group(2), package.group(3)
            self._close(pkg)
            if package.group(1) == "FAIL" and rest.startswith("[") and rest.endswith("failed]"):
                excerpt = self.build[1] if self.build and self.build[0] == pkg else _Excerpt()
                first = next((ln.strip() for ln in excerpt.lines if ln.strip()), rest)
                # The compiler names the file it stopped at, relative to where `go test` ran.
                where = GO_COMPILER.match(first)
                if len(self.broken) < MAX_FAILURES:
                    self.broken.append(Failure(
                        name=f"{pkg} {rest}", file=where.group(1).removeprefix("./") if where else "",
                        line=int(where.group(2)) if where else None, message=first[:MESSAGE_CHARS],
                        excerpt=excerpt.text()))
            self.build = None
            return
        build = GO_BUILD.match(text)
        if build:
            self.build = (build.group(1), _Excerpt())
            return
        if self.build is not None:
            self.build[1].add(text)
            return
        # A test's own output is indented; `FAIL` and `coverage:` at the margin belong to the package.
        if self.current is None or self.current not in self.details or not text[:1].isspace():
            return
        excerpt, where = self.details[self.current]
        excerpt.add(text)
        found = GO_WHERE.match(text)
        if found and not where:
            where.append((found.group(1), int(found.group(2)), found.group(3).strip()))

    def _close(self, pkg: str) -> None:
        """The package's block has ended: file its tests under its name and start the next one clean."""
        if len(self.closed) < MAX_TRACKED:
            for name, state in self.status.items():
                self.closed[(pkg, name)] = (state, self.details.get(name))
        self.status, self.details, self.current = {}, {}, None

    def _dir(self, pkg: str) -> str:
        """Where a package sits in the repository, when the module it belongs to is known."""
        if self.module and pkg == self.module:
            return ""
        if self.module and pkg.startswith(self.module + "/"):
            return pkg[len(self.module) + 1:]
        return ""

    def report(self) -> Report | None:
        if self.first is None:
            return None
        # Output that stopped before its package line still counts, under no package name.
        if self.status:
            self._close("")
        tests = self.closed
        # A parent test fails because a subtest did; the subtest is the failure worth naming — within the
        # same package, since a parent in one package has nothing to do with a namesake in another.
        leaves = [(pkg, n) for pkg, n in tests
                  if not any(p == pkg and o.startswith(n + "/") for p, o in tests)]
        failures: list[Failure] = []
        for pkg, name in leaves:
            state, detail = tests[(pkg, name)]
            if state != "FAIL" or len(failures) >= MAX_FAILURES:
                continue
            excerpt, where = detail or (_Excerpt(), [])
            # A failing subtest's own output may have been printed under its parent.
            parent = tests.get((pkg, name.split("/")[0]))
            if not where and parent and parent[1]:
                where = parent[1][1]
            folder = self._dir(pkg)
            file, line, message = where[0] if where else ("", None, "")
            failures.append(Failure(
                name=f"{pkg}.{name}" if pkg else name,
                file=f"{folder}/{file}" if folder and file else file, line=line,
                message=message[:MESSAGE_CHARS], excerpt=excerpt.text()))
        failures = [*failures, *self.broken][:MAX_FAILURES]
        out = Report(runner=self.runner, failures=failures)
        # Without -v, go prints failures only: there is no count of what passed, so none is claimed.
        if self.verbose:
            states = [tests[leaf][0] for leaf in leaves]
            out.passed, out.skipped = states.count("PASS"), states.count("SKIP")
            out.failed = states.count("FAIL") + len(self.broken)
            out.total = out.passed + out.failed + out.skipped
        return out


# ── dotnet test ──────────────────────────────────────────────────
DOTNET_CASE = re.compile(r"^\s+(Passed|Failed|Skipped) (\S.*?) \[[^\]]*\]$")
DOTNET_SUMMARY = re.compile(r"^(?:Failed|Passed)!\s+-\s+Failed:\s+(\d+),\s+Passed:\s+(\d+),\s+"
                            r"Skipped:\s+(\d+),\s+Total:\s+(\d+)")
DOTNET_WHERE = re.compile(r" in (.+):line (\d+)$")


class _Dotnet(_Parser):
    runner = "dotnet"

    def __init__(self, roots: tuple[str, ...]) -> None:
        super().__init__(roots)
        self.failures: dict[str, Failure] = {}
        self.current: Failure | None = None
        self.excerpt = _Excerpt()
        self.message_next = False
        self.counts: list[int] | None = None

    def _close(self) -> None:
        if self.current is not None:
            self.current.excerpt = self.excerpt.text()
        self.current, self.excerpt, self.message_next = None, _Excerpt(), False

    def feed(self, at: int, text: str) -> None:
        summary = DOTNET_SUMMARY.match(text)
        if summary:
            self._close()
            failed, passed, skipped, total = (int(g) for g in summary.groups())
            # One summary per test project: a solution with several prints several.
            base = self.counts or [0, 0, 0, 0]
            self.counts = [base[0] + passed, base[1] + failed, base[2] + skipped, base[3] + total]
            self.seen(at)
            return
        case = DOTNET_CASE.match(text)
        if case:
            self._close()
            if case.group(1) == "Failed":
                name = case.group(2)
                if name not in self.failures and len(self.failures) < MAX_FAILURES:
                    self.failures[name] = Failure(name=name)
                    self.current = self.failures[name]
                self.seen(at)
            return
        if self.current is None:
            return
        stripped = text.strip()
        self.excerpt.add(text)
        if stripped == "Error Message:":
            self.message_next = True
        elif self.message_next and stripped:
            self.current.message, self.message_next = stripped[:MESSAGE_CHARS], False
        where = DOTNET_WHERE.search(stripped)
        if where and self.current.line is None:
            self.current.file = _relative(where.group(1), self.roots)
            self.current.line = int(where.group(2))

    def report(self) -> Report | None:
        self._close()
        if self.first is None:
            return None
        out = Report(runner=self.runner, failures=list(self.failures.values()))
        if self.counts is not None:
            out.passed, out.failed, out.skipped, out.total = self.counts
        return out


class Reader:
    """Fed one line at a time, as the command prints it; asked once, at the end, what it saw.

    `roots` are the absolute spellings of the project's directory, so a path a runner prints in full
    comes back relative. `module` is the Go module path from go.mod, when there is one, so a Go test's
    file can be placed in its package's directory.
    """

    def __init__(self, roots: tuple[str, ...] = (), module: str = "") -> None:
        self.at = 0
        self.parsers: list[_Parser] = [_Pytest(roots), _Jest(roots), _Vitest(roots), _Go(roots, module),
                                       _Dotnet(roots)]

    def feed(self, line: str) -> None:
        text = ANSI.sub("", line).rstrip("\r\n")
        for parser in self.parsers:
            parser.feed(self.at, text)
        self.at += 1

    def result(self) -> Report:
        found = sorted(((p.first, report) for p in self.parsers
                        if p.first is not None and (report := p.report()) is not None),
                       key=lambda pair: pair[0] or 0)
        if not found:
            return Report()
        if len(found) == 1:
            return found[0][1]
        # A Makefile that runs two runners: both are read, the totals add up only when both printed
        # theirs, and the runner named is the one that spoke first.
        reports = [report for _, report in found]
        out = Report(runner=reports[0].runner)
        seen: set[tuple[str, str]] = set()
        for report in reports:
            for failure in report.failures:
                key = (failure.file, failure.name)
                if key not in seen and len(out.failures) < MAX_FAILURES:
                    seen.add(key)
                    out.failures.append(failure)
        for attr in ("passed", "failed", "skipped", "total"):
            values = [getattr(r, attr) for r in reports]
            setattr(out, attr, None if any(v is None for v in values) else sum(v for v in values if v is not None))
        return out


def parse(text: str, roots: tuple[str, ...] = (), module: str = "") -> Report:
    """A whole output at once — the same reading the stream gets, for text already in hand."""
    reader = Reader(roots, module)
    for line in text.splitlines():
        reader.feed(line)
    return reader.result()


def all_expected(counted: int | None, recorded: int, expected: int) -> bool:
    """Whether every failure of a test step is one a person said to expect.

    Only when the runner's total was read, every failure it counted was recorded, and each recorded one
    has an expectation. A failing exit code with nothing named — a crash, a build error, output nobody
    could read — is never "expected", because nobody has looked at it.
    """
    return counted is not None and recorded > 0 and recorded >= counted and expected == recorded
