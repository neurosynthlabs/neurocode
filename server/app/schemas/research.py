"""Research in the shape the Research screen reads.

Two things are worked out here rather than stored, because both are only true at the moment they are
read. **Cited coverage** — the share of angles whose finding cites at least one piece it was handed —
stands where a model's self-rated confidence used to: a self-score is not a measurement, a citation
is. And **what was not covered** is assembled from what actually happened: an angle retrieval found
nothing for, an angle that answered without citing, a kind of source this project has not indexed,
and the sources nothing in this app can search at all.

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
UNSEARCHABLE = "Web and GitHub were not searched: nothing in this app fetches either."


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
    searched = kinds_words(list(report.kinds or []))
    for angle in angles:
        if angle.status == "failed":
            out.append(f"Not answered: {angle.question}" + (f" ({angle.error})" if angle.error else ""))
        elif angle.status == "done" and angle.hits == 0:
            out.append(f"Nothing in {searched} bore on: {angle.question}")
        elif angle.status == "done" and not angle.citations:
            out.append(f"Answered without a citation: {angle.question}")
    out.append(UNSEARCHABLE)
    return list(dict.fromkeys(out))


def report_json(report: ResearchReport) -> dict[str, Any]:
    """The whole report. Its angles and their citations must already be loaded (they are selectin)."""
    angles = list(report.angles)
    cited = sum(1 for a in angles if a.citations)
    citations: dict[tuple[str, str], dict[str, Any]] = {}
    for citation in sorted((c for a in angles for c in a.citations), key=lambda c: c.n):
        citations.setdefault((citation.kind, citation.ref), {
            "label": citation.title or citation.ref, "url": citation.ref, "via": VIA[citation.kind],
            "kind": citation.kind, "path": citation.path, "line": citation.line,
            "excerpt": citation.excerpt})
    by_kind: dict[str, int] = {}
    for kind, _ref in citations:
        by_kind[SOURCE[kind]] = by_kind.get(SOURCE[kind], 0) + 1
    return {
        "id": report.id, "ref": report.ref, "question": report.question, "status": _status(report),
        "createdAt": when(report.created_at), "startedAt": when(report.started_at),
        "finishedAt": when(report.finished_at), "durationS": _duration(report), "agents": len(angles),
        "confidence": coverage(len(angles), cited, report.status),
        "projectId": report.project_id, "projectHint": report.project_id,
        "kinds": list(report.kinds or []), "requestedBy": report.requested_by,
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
