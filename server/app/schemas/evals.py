"""Eval suites, cases, runs and lessons, in the shape the Evals screen reads — and the catalogue of checks.

The catalogue lives here because it is a shape before it is a rule: a check is a small JSON object whose
fields depend on its kind, and a case can only be stored when every one of its checks is a shape the
runner knows how to score. Which checks make sense for which target is a decision, and that is the
service's.

Three things on the screen are derived rather than stored, and each is derived from rows. A suite's
status compares its last finished score with its own threshold. A case's movement is its score in the
run on screen against the finished run before it. And the Judge column says who actually decided: a
person who overrode the verdict, a model that judged it, or the rules the case states — never a model
name on a verdict a model did not give.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..models import EvalCase, EvalResult, EvalRun, EvalSuite, MemoryFact
from .work import when

CheckKind = Literal["exact", "contains", "not_contains", "regex", "json_equals", "json_contains", "cites",
                    "retrieves", "max_ms", "free_lane", "not_offline", "judge"]

TARGET_LABEL = {"compile": "Requirement compiler", "ask": "Ask memory", "retrieval": "Retrieval",
                "review": "Reviewer prompt", "prompt": "Lane prompt"}
#: A path into a JSON answer: `$`, then `.name`, `[0]` or `[*]` as many times as needed.
PATH = re.compile(r"^\$(?:\.[A-Za-z_][\w-]*|\[(?:\d+|\*)\])*$")
#: What a regex check may be, so one pattern cannot take the runner down with it.
MAX_PATTERN = 300
#: How much of an answer the table shows in its Got column.
GOT = 200


class Check(BaseModel):
    """One stated, deterministic test of an answer — or a judge, which is a labelled model call."""

    kind: CheckKind
    value: str | int | float | bool | None = None
    path: str | None = Field(default=None, max_length=200)
    ref: str | None = Field(default=None, max_length=120)
    k: int | None = Field(default=None, ge=1, le=50)
    rubric: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _complete(self) -> Check:
        text_value = isinstance(self.value, str) and bool(self.value.strip())
        if self.kind in ("exact", "contains", "not_contains") and not text_value:
            raise ValueError(f"a {self.kind} check needs the text to look for")
        if self.kind == "regex":
            if not text_value or len(str(self.value)) > MAX_PATTERN:
                raise ValueError(f"a regex check needs a pattern of at most {MAX_PATTERN} characters")
            try:
                re.compile(str(self.value))
            except re.error as bad:
                raise ValueError(f"the pattern does not compile: {bad}") from bad
        if self.kind in ("json_equals", "json_contains"):
            if not self.path or not PATH.match(self.path):
                raise ValueError(f"a {self.kind} check needs a path like $.risk or $.steps[*].agent")
            if self.value is None:
                raise ValueError(f"a {self.kind} check needs the value to compare with")
        if self.kind in ("cites", "retrieves") and not (self.ref or "").strip():
            raise ValueError(f"a {self.kind} check needs the ref it expects, such as MEM-142")
        if self.kind == "retrieves" and self.k is None:
            raise ValueError("a retrieves check needs k, how far down the results the ref may be")
        if self.kind == "max_ms" and (isinstance(self.value, bool) or not isinstance(self.value, int)
                                      or self.value < 1):
            raise ValueError("a max_ms check needs a whole number of milliseconds")
        if self.kind == "judge" and not (self.rubric or "").strip():
            raise ValueError("a judge check needs the rubric the judge reads")
        return self

    def stored(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


def expected(checks: list[dict[str, Any]]) -> str:
    """The checks, in words, for the Expected column."""
    def one(c: dict[str, Any]) -> str:
        kind, value = c.get("kind"), c.get("value")
        match kind:
            case "exact": return f"exactly “{value}”"
            case "contains": return f"contains “{value}”"
            case "not_contains": return f"never “{value}”"
            case "regex": return f"matches /{value}/"
            case "json_equals": return f"{c.get('path')} = {value}"
            case "json_contains": return f"{c.get('path')} has {value}"
            case "cites": return f"cites {c.get('ref')}"
            case "retrieves": return f"{c.get('ref')} in top {c.get('k')}"
            case "max_ms": return f"under {value} ms"
            case "free_lane": return "answered on a free lane"
            case "not_offline": return "a model answered, not the rules"
            case "judge":
                rubric = str(c.get("rubric") or "")
                return f"judge: {rubric[:80]}{'…' if len(rubric) > 80 else ''}"
        return str(kind)
    return "; ".join(one(c) for c in checks)


def target_label(suite: EvalSuite) -> str:
    return TARGET_LABEL[suite.target_kind] + (f" · {suite.lane}" if suite.lane else "")


def suite_status(suite: EvalSuite, score: int | None, running: bool) -> str:
    if running:
        return "running"
    if score is None:
        return "never"
    if score >= suite.threshold:
        return "pass"
    return "warn" if score >= suite.threshold - 10 else "fail"


def suite_json(suite: EvalSuite, *, cases: int, history: list[dict[str, Any]],
               running_ref: str | None) -> dict[str, Any]:
    """A suite as the list shows it. `history` is its finished runs, newest first."""
    last = history[0] if history else None
    score = last["score"] if last else None
    before = history[1]["score"] if len(history) > 1 else None
    return {
        "id": suite.id, "name": suite.name, "target": target_label(suite), "targetKind": suite.target_kind,
        "kind": suite.kind, "cases": cases, "passed": last["passed"] if last else 0,
        "score": score, "delta": score - before if score is not None and before is not None else None,
        "lastRun": when(last["finished_at"]) if last else None, "lastRunRef": last["ref"] if last else None,
        "status": suite_status(suite, score, running_ref is not None), "runningRef": running_ref,
        "threshold": suite.threshold, "lane": suite.lane, "projectId": suite.project_id,
        "allowOffline": suite.allow_offline,
    }


def run_json(run: EvalRun) -> dict[str, Any]:
    return {"ref": run.ref, "status": run.status, "lane": run.lane, "score": run.score,
            "passed": run.passed, "failed": run.failed, "partial": run.partial, "errored": run.errored,
            "note": run.note, "startedAt": when(run.created_at), "finishedAt": when(run.finished_at)}


def judge_label(case: EvalCase, result: EvalResult | None) -> str:
    if result is not None and result.override_status:
        return f"Human override — {result.override_note}" if result.override_note else "Human override"
    if result is not None and result.judge_model:
        return f"LLM judge · {result.judge_model}"
    kinds = list(dict.fromkeys(str(c.get("kind")) for c in case.checks if c.get("kind") != "judge"))
    return f"rule · {', '.join(kinds)}" if kinds else "judge"


def _got(result: EvalResult) -> str:
    """What the case produced, as short as it can be said: the error, the check that failed, or the answer."""
    if result.error:
        return result.error[:GOT]
    failed = next((c for c in result.checks if c.get("ok") is False and c.get("observed")), None)
    if failed is not None:
        return str(failed["observed"])[:GOT]
    return result.output[:GOT]


def case_json(case: EvalCase, suite_name: str, result: EvalResult | None, *, moved: float | None,
              total_weight: int) -> dict[str, Any]:
    """A case, and — when the run on screen reached it — what it produced there.

    `moved` is this case's score minus its score in the finished run before, or None when there is no
    run to compare with; it becomes points of the suite's score, weighted like the score itself.
    """
    base: dict[str, Any] = {
        "id": case.id, "suite": suite_name, "n": case.n, "name": case.name, "input": case.input,
        "checks": case.checks, "weight": case.weight, "expected": expected(case.checks),
        "sourcePlanId": case.source_plan_id, "judge": judge_label(case, result),
        "scoreDelta": round(100 * case.weight * moved / total_weight) if moved and total_weight else 0,
    }
    if result is None:
        return {**base, "resultId": None, "status": None, "machineStatus": None, "got": "", "score": None,
                "lane": "", "model": "", "ms": None, "offline": False, "error": "", "checkResults": [],
                "judgeReason": "", "overridden": False, "overrideNote": "", "output": ""}
    return {**base, "resultId": result.id, "status": result.override_status or result.status,
            "machineStatus": result.status, "got": _got(result), "score": float(result.score),
            "lane": result.lane, "model": result.model, "ms": result.ms, "offline": result.offline,
            "error": result.error, "checkResults": result.checks, "judgeReason": result.judge_reason,
            "overridden": result.override_status is not None, "overrideNote": result.override_note,
            "output": result.output}


def lesson_source(suite_name: str, run_ref: str) -> str:
    return f"eval:{suite_name}:{run_ref}"


def lesson_json(fact: MemoryFact) -> dict[str, Any]:
    """A lesson says which suite and run it was learned from — read back out of its source."""
    suite, _, run_ref = fact.source.removeprefix("eval:").rpartition(":")
    return {"ref": fact.ref, "at": when(fact.created_at), "from": suite or run_ref, "runRef": run_ref,
            "text": fact.body, "category": fact.category}
