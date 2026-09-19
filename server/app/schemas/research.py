"""Research in the shape the Research screen reads.

Two things are worked out here rather than stored, because both are only true at the moment they are
read. **Cited coverage** — the share of angles whose finding cites at least one piece it was handed —
stands where a model's self-rated confidence used to: a self-score is not a measurement, a citation
is. And **what was not covered** is assembled from what actually happened: an angle retrieval found
nothing for, an angle that answered without citing, a kind of source this project has not indexed,
and the sources nothing in this app can search at all.

A web page a research read is cited like any document — its kind is `doc`, because `chunk_kind` has no
`web` (adding one is a migration) — and is told apart by its ref, which is the page's own http(s) URL; a
piece of the repository never has one. The screen is given it as `kind: web`, `via: web`. Whether the
web was searched at all is read from what the research wrote: a web citation, or a gap it noted about
the web, each of which starts with `WEB`.

Citations are listed once per piece. Two angles may cite the same chunk, and the table keeps a row for
each — that is what ties a finding to its evidence — but a reader wants the list of sources, not the
list of times each was used.
"""
from __future__ import annotations

from typing import Any

from ..models import ResearchAngle, ResearchReport
from ..repositories.research import ReportRow
from .work import when

#: run_status as the screen names it. A report is never parked on a person, so `waiting` never occurs.
STATUS = {"done": "complete"}
#: How a chunk kind reads as a source, and what the citation says it came through.
SOURCE = {"code": "code", "doc": "docs", "memory": "memory"}
VIA = {"code": "internal", "doc": "docs", "memory": "memory"}
KIND_WORDS = {"code": "code", "doc": "documentation", "memory": "memory"}
#: How every note the research writes about the web begins, so a report can tell it searched the web.
WEB = "The web"
UNSEARCHABLE = "The web was not searched for this research, and nothing in this app reads GitHub."
UNSEARCHABLE_WITH_WEB = "GitHub was not searched: nothing in this app reads it."


def is_web(kind: str, ref: str) -> bool:
    """A web page's citation: a document whose ref is its URL."""
    return kind == "doc" and ref.startswith(("http://", "https://"))


def searched_web(report: ResearchReport) -> bool:
    return (any(is_web(c.kind, c.ref) for a in report.angles for c in a.citations)
            or any(g.startswith(WEB) for g in report.gaps or []))


def _status(report: ResearchReport) -> str:
    return STATUS.get(report.status, report.status)


def _duration(report: ResearchReport) -> int:
    """Seconds from start to finish; 0 until it has finished, rather than a number still moving."""
    if report.finished_at is None:
        return 0
    return max(0, round((report.finished_at - (report.started_at or report.created_at)).total_seconds()))


def coverage(angles: int, cited: int, status: str) -> int:
    return round(100 * cited / angles) if status == "done" and angles else 0


def kinds_words(kinds: list[str]) -> str:
    words = [KIND_WORDS.get(k, k) for k in kinds]
    return words[0] if len(words) == 1 else f"{', '.join(words[:-1])} or {words[-1]}"


def report_row_json(row: ReportRow) -> dict[str, Any]:
    report = row.report
    return {"id": report.id, "ref": report.ref, "question": report.question, "status": _status(report),
            "createdAt": when(report.created_at), "durationS": _duration(report), "agents": row.angles,
            "confidence": coverage(row.angles, row.cited, report.status), "projectId": report.project_id,
            "requestedBy": report.requested_by}


def _gaps(report: ResearchReport, angles: list[ResearchAngle]) -> list[str]:
    out = list(report.gaps or [])
    web = searched_web(report)
    searched = kinds_words(list(report.kinds or []) + (["the web"] if web else []))
    for angle in angles:
        if angle.status == "failed":
            out.append(f"Not answered: {angle.question}" + (f" ({angle.error})" if angle.error else ""))
        elif angle.status == "done" and angle.hits == 0:
            out.append(f"Nothing in {searched} bore on: {angle.question}")
        elif angle.status == "done" and not angle.citations:
            out.append(f"Answered without a citation: {angle.question}")
    out.append(UNSEARCHABLE_WITH_WEB if web else UNSEARCHABLE)
    return list(dict.fromkeys(out))


def report_json(report: ResearchReport) -> dict[str, Any]:
    """The whole report. Its angles and their citations must already be loaded (they are selectin)."""
    angles = list(report.angles)
    cited = sum(1 for a in angles if a.citations)
    citations: dict[tuple[str, str], dict[str, Any]] = {}
    for citation in sorted((c for a in angles for c in a.citations), key=lambda c: c.n):
        web = is_web(citation.kind, citation.ref)
        citations.setdefault((citation.kind, citation.ref), {
            "label": citation.title or citation.ref, "url": citation.ref,
            "via": "web" if web else VIA[citation.kind], "kind": "web" if web else citation.kind,
            "path": citation.path, "line": citation.line, "excerpt": citation.excerpt})
    by_kind: dict[str, int] = {}
    for kind, ref in citations:
        source = "web" if is_web(kind, ref) else SOURCE[kind]
        by_kind[source] = by_kind.get(source, 0) + 1
    return {
        "id": report.id, "ref": report.ref, "question": report.question, "status": _status(report),
        "createdAt": when(report.created_at), "startedAt": when(report.started_at),
        "finishedAt": when(report.finished_at), "durationS": _duration(report), "agents": len(angles),
        "confidence": coverage(len(angles), cited, report.status),
        "projectId": report.project_id, "projectHint": report.project_id,
        "kinds": list(report.kinds or []), "web": searched_web(report), "requestedBy": report.requested_by,
        "sources": [{"kind": kind, "count": n} for kind, n in by_kind.items()],
        "summary": report.summary, "recommendation": report.recommendation,
        "architecture": report.architecture, "risks": list(report.risks or []),
        "alternatives": list(report.alternatives or []),
        "findings": [a.finding for a in angles if a.finding],
        "citations": list(citations.values()),
        "sweep": [{"angle": a.question, "hits": a.hits} for a in angles],
        "angles": [{"n": a.n, "question": a.question, "status": a.status, "hits": a.hits,
                    "lexical": a.hits_lexical, "semantic": a.hits_semantic, "finding": a.finding,
                    "lane": a.lane, "model": a.model, "ms": a.ms, "error": a.error,
                    "cited": len(a.citations)} for a in angles],
        "gaps": _gaps(report, angles) if report.status == "done" else [],
        "provider": report.lane or "",
        "model": report.model or "", "fallback": report.fallback, "note": report.note,
    }
