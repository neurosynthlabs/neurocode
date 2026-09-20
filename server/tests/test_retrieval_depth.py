"""Retrieval that measures itself: the relevance floor, the diversity rule, the trace, and the eval
checks that say whether any of it got better.

The numbers the floor stands on were measured against this Postgres, not guessed, and the tests here
pin the behaviour those measurements bought:

* `ts_rank` over an `a | b | c` query is the **mean** of each word's own rank, and one occurrence of
  one word scores exactly 0.0607927. So a piece that shares a single word once with the question
  carries no evidence about it, and is not handed to a model as an answer to it.
* Eight results were happily eight slices of one file, because nothing anywhere said otherwise.
* Nothing recorded what was retrieved for any answer, so "were they the right eight" could not be
  asked of anything that had already happened.

No model is called anywhere in this file: the gateway is a stand-in with no embedding lane, which is
also the case the floor matters most in.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.data.engine import Database
from app.models import EMBED_DIM, Chat, Chunk, Project
from app.repositories import ChatRepository
from app.repositories.retrieval import PER_PATH, ChunkRepository, diversify
from app.schemas.evals import Check, expected
from app.services.errors import Refused
from app.services.evals import _refuse_checks, check
from app.services.evals import Answer as EvalAnswer
from app.services.retrieval import (
    DISTANCE_CEILING,
    RetrievalService,
    embedded_text,
    near_enough,
    trace,
)

PID = "retrieval-depth"
#: What Postgres scores one occurrence of one word — the number the floor is drawn just above.
SINGLE_HIT = 0.0607927106320858


class NoLanes:
    """Retrieval asks for an embedding lane; there is none, so it searches by words and says so."""

    def embed_lane(self) -> None:
        return None


# ── the floor, as a rule about one piece ─────────────────────────

def _piece(**over: Any) -> dict[str, Any]:
    base = {"ref": "app/x.py#f:1", "kind": "code", "path": "app/x.py", "title": "f · function",
            "line": 1, "text": "…", "score": 0.016, "how": "lexical", "tsRank": None,
            "distance": None, "rank": {"lexical": 1, "semantic": None}}
    return {**base, **over}


def test_one_mention_of_one_word_is_not_evidence_about_the_question():
    """0.0607927 is what Postgres scores a single occurrence of a single word. Over an OR query the
    rank is the mean over the question's words, so the evidence is the rank times that count."""
    # "how does the Kubernetes operator reconcile a custom resource" over a billing document: five
    # meaningful words, one of them matched once — 0.0607927106 spread over the five, measured 0.01216.
    assert near_enough(_piece(tsRank=SINGLE_HIT / 5), 5) is False
    # The same document asked "how is tax applied to an invoice": three words, measured at 0.07444.
    assert near_enough(_piece(tsRank=0.07444), 3) is True
    # One rare word, once, in a one-word question: one hit is one hit however short the question is.
    assert near_enough(_piece(tsRank=SINGLE_HIT), 1) is False
    # Two words once each, or one word twice: measured at 0.0865 and above, and that is evidence.
    assert near_enough(_piece(tsRank=0.08655), 1) is True


def test_meaning_and_words_agreeing_is_never_floored():
    assert near_enough(_piece(how="both", tsRank=0.0, distance=0.99), 9) is True


def test_the_semantic_half_is_held_to_its_own_ceiling():
    assert near_enough(_piece(how="semantic", distance=DISTANCE_CEILING - 0.01), 4) is True
    assert near_enough(_piece(how="semantic", distance=DISTANCE_CEILING + 0.01), 4) is False


def test_a_piece_with_no_numbers_at_all_is_kept():
    """A missing figure is not evidence against a piece, and inventing one to refuse with is worse."""
    assert near_enough(_piece(tsRank=None), 6) is True


# ── diversity, as a rule about a list ────────────────────────────

def _rows(n: int, path: str, kind: str = "code") -> list[dict[str, Any]]:
    return [{"ref": f"{path}#m{i}:{i}", "kind": kind, "path": path, "score": 1.0 - i / 100} for i in range(n)]


def test_eight_pieces_are_not_eight_slices_of_one_file():
    rows = [*_rows(12, "app/services/runs.py"), {"ref": "app/x.py#f:1", "kind": "code", "path": "app/x.py"}]
    kept, dropped = diversify(rows, limit=4, per_path=PER_PATH, memory_share=3)
    assert [x["path"] for x in kept] == ["app/services/runs.py", "app/services/runs.py", "app/x.py"]
    assert dropped == 10                     # the ten slices of that file that were walked past


def test_a_caller_that_wants_the_old_behaviour_can_still_have_it():
    rows = _rows(12, "app/services/runs.py")
    kept, dropped = diversify(rows, limit=4, per_path=99, memory_share=3)
    assert len(kept) == 4 and dropped == 0


def test_a_files_head_gives_way_to_a_symbol_of_the_same_file():
    rows = [{"ref": "app/x.py#f:1", "kind": "code", "path": "app/x.py"},
            {"ref": "app/x.py#file", "kind": "code", "path": "app/x.py"},
            {"ref": "app/y.py#g:1", "kind": "code", "path": "app/y.py"}]
    kept, dropped = diversify(rows, limit=3, per_path=PER_PATH, memory_share=3)
    assert [x["ref"] for x in kept] == ["app/x.py#f:1", "app/y.py#g:1"] and dropped == 1


def test_remembered_facts_take_a_share_of_the_slots_and_not_all_of_them():
    rows = [*_rows(6, "business_rules", kind="memory"), {"ref": "app/x.py#f:1", "kind": "code", "path": "app/x.py"}]
    kept, dropped = diversify(rows, limit=6, per_path=PER_PATH, memory_share=3)
    assert [x["kind"] for x in kept] == ["memory", "memory", "code"] and dropped == 4


# ── what is embedded, and what a trace holds ─────────────────────

def test_a_document_is_embedded_with_its_path_and_heading():
    text = embedded_text("docs/ARCHITECTURE.md · Access control", "Permissions are resource:action strings.")
    assert text.startswith("docs/ARCHITECTURE.md · Access control\n")


def test_a_code_piece_does_not_repeat_a_header_it_already_carries():
    body = "app/x.py:12 · function f\ndef f(): ..."
    assert embedded_text("app/x.py:12 · function f", body) == body


def test_a_trace_says_what_answered_and_stops_where_the_search_stops():
    pieces = [_piece(ref=f"app/x.py#f{i}:{i}", tsRank=0.2, score=0.03) for i in range(60)]
    record = trace("where is tax handled", pieces, lane="gemini", ms=7, floored=3, k=8)
    assert (record["q"], record["k"], record["lane"], record["ms"], record["floored"]) == (
        "where is tax handled", 8, "gemini", 7, 3)
    assert len(record["hits"]) == 50
    assert record["hits"][0] == {"ref": "app/x.py#f0:0", "kind": "code", "path": "app/x.py",
                                 "how": "lexical", "score": 0.03, "tsRank": 0.2, "distance": None,
                                 "lexicalRank": 1, "semanticRank": None}


# ── the floor against a real index ───────────────────────────────

INVOICES = [
    ("code", "app/billing/invoices.py#issue_invoice:40", "app/billing/invoices.py", "issue_invoice · function",
     "app/billing/invoices.py:40 · function issue_invoice\n"
     "def issue_invoice(order, tax_rate):\n    # an invoice is issued once the order is paid, and the "
     "tax is applied to each line before the total is stored"),
    ("doc", "docs/billing.md#0", "docs/billing.md", "docs/billing.md · Invoices and tax",
     "Invoices and tax\nInvoices are issued from a paid order. Tax is applied per line, at the rate of "
     "the customer's region. A credit note reverses an invoice; the original invoice is never edited."),
]


@pytest_asyncio.fixture
async def billing(session: AsyncSession) -> AsyncSession:
    """A project that knows about invoices and tax, and about nothing else at all."""
    session.add(Project(id=PID, name="Billing"))
    await session.flush()
    session.add_all([Chunk(project_id=PID, kind=kind, ref=ref, path=path, title=title, body=body, line=1)
                     for kind, ref, path, title, body in INVOICES])
    await session.flush()
    return session


async def test_an_unanswerable_question_is_handed_nothing_at_all(billing: AsyncSession):
    """It used to come back with both pieces under "What this repository already holds about the
    question" — the one feature in this product that never refused."""
    service = RetrievalService(billing, NoLanes())          # type: ignore[arg-type]
    text, pieces, record = await service.grounded(PID, "how does the Kubernetes operator reconcile a "
                                                       "custom resource", limit=4)
    assert (text, pieces) == ("", [])
    assert record["floored"] >= 1 and record["hits"] == []
    assert record["q"].startswith("how does the Kubernetes") and record["lane"] is None


async def test_the_question_it_really_holds_still_comes_back(billing: AsyncSession):
    service = RetrievalService(billing, NoLanes())          # type: ignore[arg-type]
    text, pieces, record = await service.grounded(PID, "how is tax applied to an invoice", limit=4)
    assert "What this repository already holds" in text
    assert {p["ref"] for p in pieces} == {ref for _k, ref, *_ in INVOICES}
    assert record["floored"] == 0 and len(record["hits"]) == 2
    assert all(h["how"] == "lexical" and h["tsRank"] > 0 and h["lexicalRank"] >= 1 for h in record["hits"])
    assert all(h["distance"] is None and h["semanticRank"] is None for h in record["hits"])


async def test_the_search_box_shows_what_it_found_and_says_what_grounding_would_refuse(billing: AsyncSession):
    """A person narrowing on purpose sees everything that matched; the screen says which of it is
    below the floor, rather than the list quietly being two different rules in two places."""
    service = RetrievalService(billing, NoLanes())          # type: ignore[arg-type]
    found, counts = await service.search_counted(PID, "invoice tax", 8)
    assert len(found) == 2 and counts["fused"] == 2
    assert counts["dropped"] == 0 and counts["floored"] == 0
    assert counts["lexical"] >= 2 and counts["semantic"] == 0


async def test_one_file_cannot_fill_every_slot_of_a_real_search(session: AsyncSession):
    session.add(Project(id=f"{PID}-crowd", name="Crowded"))
    await session.flush()
    session.add_all([
        Chunk(project_id=f"{PID}-crowd", kind="code", ref=f"app/services/runs.py#step_{i}:{i * 10}",
              path="app/services/runs.py", line=i * 10, title=f"step_{i} · function",
              body=f"app/services/runs.py:{i * 10} · function step_{i}\n"
                   "a step of a run writes the files a model returned for that step of the run")
        for i in range(12)])
    session.add(Chunk(project_id=f"{PID}-crowd", kind="code", ref="app/services/gates.py#allow:4",
                      path="app/services/gates.py", line=4, title="allow · function",
                      body="app/services/gates.py:4 · function allow\na step of a run waits here"))
    await session.flush()

    found, dropped = await ChunkRepository(session).fused(f"{PID}-crowd", "what does a step of a run write",
                                                          limit=4)
    assert [x["path"] for x in found].count("app/services/runs.py") == PER_PATH
    assert "app/services/gates.py" in [x["path"] for x in found]
    assert dropped >= 1
    assert all(x["tsRank"] is not None and x["rank"]["lexical"] is not None for x in found)


async def test_a_piece_whose_heading_moved_is_embedded_again(billing: AsyncSession):
    """The title is part of what the model is given, so a vector made without it no longer describes
    the piece. Comparing bodies alone left that vector in place for ever."""
    service = RetrievalService(billing, NoLanes())          # type: ignore[arg-type]
    stored = (await billing.execute(
        select(Chunk).where(Chunk.project_id == PID, Chunk.ref == "docs/billing.md#0"))).scalar_one()
    stored.embedding, stored.dim, stored.model = [0.01] * EMBED_DIM, 768, "fake-embed"
    await billing.flush()

    rows = [{"kind": kind, "ref": ref, "path": path,
             "title": "docs/billing.md · Invoices, tax and credit notes" if ref == "docs/billing.md#0" else title,
             "line": 1, "body": body}
            for kind, ref, path, title, body in INVOICES]
    replaced = await service._replace(PID, rows)

    assert [c.ref for c in replaced.fresh] == ["docs/billing.md#0"]
    assert stored.embedding is None and replaced.embedded == 0


# ── the eval checks that turn all of this into a number ──────────

def _answer(*refs: str) -> EvalAnswer:
    return EvalAnswer(output="{}", data={"refs": list(refs)}, refs=list(refs), model="lexical")


def test_recall_is_graded_rather_than_a_pass_or_a_fail():
    spec = {"kind": "retrieves_all", "k": 8,
            "refs": ["app/a.py#f:1", "app/b.py#g:2", "docs/c.md#0", "MEM-14"]}
    ok, observed, score = check(spec, _answer("app/a.py#f:1", "docs/c.md#0"))
    assert ok is False and score == 0.5 and "2 of 4 in top 8" in observed and "app/b.py#g:2" in observed

    ok, _observed, score = check(spec, _answer(*spec["refs"]))
    assert ok is True and score == 1.0

    ok, observed, score = check(spec, _answer("app/z.py#q:9"))
    assert ok is False and score == 0.0 and "0 of 4" in observed


def test_recall_only_counts_what_came_back_inside_k():
    spec = {"kind": "retrieves_all", "k": 2, "refs": ["app/a.py#f:1", "app/b.py#g:2"]}
    _ok, _observed, score = check(spec, _answer("x", "y", "app/a.py#f:1", "app/b.py#g:2"))
    assert score == 0.0


def test_the_unanswerable_case_passes_only_when_nothing_was_handed_over():
    spec = {"kind": "retrieves_nothing", "k": 8}
    ok, observed, score = check(spec, _answer())
    assert ok is True and score == 1.0 and observed == "nothing was handed over"

    ok, observed, score = check(spec, _answer("docs/billing.md#0"))
    assert ok is False and score == 0.0 and "docs/billing.md#0" in observed


def test_a_retrieval_check_is_refused_on_a_suite_that_could_never_answer_it():
    refs_check = Check(kind="retrieves_all", k=8, refs=["MEM-1"])
    with pytest.raises(Refused) as refused:
        _refuse_checks("ask", [refs_check])
    assert "retrieval suite" in str(refused.value)
    _refuse_checks("retrieval", [refs_check])               # where it belongs, it is allowed

    with pytest.raises(Refused):
        _refuse_checks("compile", [Check(kind="retrieves_nothing", k=8)])


def test_a_check_that_names_no_ref_is_not_a_check():
    with pytest.raises(ValueError):
        Check(kind="retrieves_all", k=8, refs=[])
    with pytest.raises(ValueError):
        Check(kind="retrieves_all", refs=["MEM-1"])         # k is what "in the top" means
    assert expected([Check(kind="retrieves_all", k=8, refs=["MEM-1", "MEM-2"]).stored()]) == \
        "2 of 2 refs in top 8"
    assert "unanswerable" in expected([Check(kind="retrieves_nothing", k=8).stored()])


# ── the session, which has to say so in words ────────────────────

@pytest_asyncio.fixture
async def live(schema: str) -> AsyncIterator[Database]:
    """Committed data: `think` opens sessions of its own, which cannot see a rolled-back transaction."""
    db = Database(url=schema)
    async with db.session() as s:
        s.add(Project(id=f"{PID}-live", name="Billing Live"))
        await s.flush()
        for kind, ref, path, title, body in INVOICES:
            s.add(Chunk(project_id=f"{PID}-live", kind=kind, ref=ref, path=path, title=title, body=body, line=1))
    yield db
    async with db.session() as s:
        await s.execute(delete(Chat).where(Chat.project_id == f"{PID}-live"))
        await s.execute(delete(Chunk).where(Chunk.project_id == f"{PID}-live"))
        await s.execute(delete(Project).where(Project.id == f"{PID}-live"))
    await db.close()


class ScriptedGateway:
    """One scripted answer, no lane that embeds. Nothing leaves the machine."""

    def __init__(self, answer: str) -> None:
        self.answer = answer

    def embed_lane(self) -> None:
        return None

    def ask(self, messages: list[dict[str, str]], parse: Any, **_: Any) -> Any:
        from app.ai.gateway import Provider, Result
        return Result(parse(self.answer), Provider("groq", "openai/gpt-oss-120b"), 11)


async def _session(db: Database, ref: str, question: str) -> None:
    async with db.session() as s:
        chats = ChatRepository(s)
        chat = await chats.add(Chat(id=ref.lower(), ref=ref, project_id=f"{PID}-live", title=question[:80],
                                    started_by="Rajat", status="thinking"))
        await chats.say(chat.id, role="you", body=question, by="Rajat")


async def _turns(db: Database, ref: str) -> list[Any]:
    async with db.read() as s:
        chat = await ChatRepository(s).by_ref(ref)
        return await ChatRepository(s).messages(chat.id)


async def test_a_session_is_told_in_words_that_retrieval_holds_nothing(live: Database):
    from app.services.chat import think

    await _session(live, "CHAT-9401", "how does the Kubernetes operator reconcile a custom resource")
    await think(live, ScriptedGateway('{"answer": "I will read the files."}'), "CHAT-9401", "Rajat")

    turns = await _turns(live, "CHAT-9401")
    grounding = next(m for m in turns if m.tool == "grounding")
    assert "is not close enough to it, so nothing is quoted here" in grounding.body
    assert "Read the files with the tools" in grounding.body
    assert grounding.detail == "nothing near enough — 1 below the relevance floor"
    assert grounding.arguments["hits"] == [] and grounding.arguments["floored"] >= 1


async def test_a_grounded_session_carries_the_trace_of_what_answered(live: Database):
    from app.services.chat import think

    await _session(live, "CHAT-9402", "how is tax applied to an invoice")
    await think(live, ScriptedGateway('{"answer": "Per line, at the customer\'s rate."}'), "CHAT-9402", "Rajat")

    turns = await _turns(live, "CHAT-9402")
    grounding = next(m for m in turns if m.tool == "grounding")
    assert grounding.body.startswith("What this repository already holds about the question")
    assert grounding.detail == "2 pieces from the index"
    hits = grounding.arguments["hits"]
    assert {h["ref"] for h in hits} == {ref for _k, ref, *_ in INVOICES}
    assert all(h["how"] == "lexical" and h["lexicalRank"] >= 1 for h in hits)
    assert grounding.arguments["q"] == "how is tax applied to an invoice"


# ── a question that names nothing of its own ─────────────────────

class _Turn:
    """A turn as `_grounding_question` reads one: a role, a body, and a tool when it was one."""

    def __init__(self, role: str, body: str, tool: str | None = None,
                 arguments: dict[str, Any] | None = None) -> None:
        self.role, self.body, self.tool, self.arguments = role, body, tool, arguments


def test_a_short_question_is_searched_with_the_names_the_conversation_just_used():
    """"Make that faster" has no word the index could match, and since the floor now refuses what such
    a question retrieves, it would come back with nothing at all."""
    from app.services.chat import _grounding_question

    asked, carried = _grounding_question([
        _Turn("you", "where does the hybrid search fuse its two halves?"),
        _Turn("assistant", "In `app/services/retrieval.py` — RetrievalService.search calls the repository."),
        _Turn("you", "make that faster"),
    ])
    assert "RetrievalService" in asked and "app/services/retrieval.py" in asked
    assert asked.startswith("make that faster")
    assert carried[:2] == ["RetrievalService", "app/services/retrieval.py"]


def test_a_question_that_stands_on_its_own_is_left_exactly_as_it_was():
    from app.services.chat import _grounding_question

    asked, carried = _grounding_question([
        _Turn("assistant", "`app/services/retrieval.py` holds it."),
        _Turn("you", "how is tax applied to an invoice line?"),
    ])
    assert (asked, carried) == ("how is tax applied to an invoice line?", [])


def test_a_command_is_still_searched_by_what_it_was_given():
    from app.services.chat import _grounding_question

    asked, carried = _grounding_question([
        _Turn("you", "/plan invoice tax rounding"),
        _Turn("tool", "Plan this.", tool="command", arguments={"name": "/plan", "args": "invoice tax rounding"}),
    ])
    assert (asked, carried) == ("invoice tax rounding", [])


# ── the eval target measures what a session is really handed ─────

async def test_a_retrieval_case_is_answered_the_way_a_session_is(live: Database):
    """The target used to call the bare search, so a case could score pieces the relevance floor would
    never have let a model see. It grounds now, which is what a session gets."""
    from app.services.evals import RunPlan, _answer

    plan = RunPlan(run_id="er-1", ref="EVR-1", suite_id="es-1", suite="Retrieval · Billing",
                   target="retrieval", project={"id": f"{PID}-live", "name": "Billing Live"},
                   lane=None, system_prompt="", allow_offline=False, actor=None, cases=[])
    gateway = ScriptedGateway("")

    answered = await _answer(live, gateway, plan, {  # type: ignore[arg-type]
        "id": "ec-1", "name": "tax on an invoice", "input": "how is tax applied to an invoice",
        "checks": [{"kind": "retrieves_all", "k": 8, "refs": [ref for _k, ref, *_ in INVOICES]}], "weight": 1})
    assert set(answered.refs) == {ref for _k, ref, *_ in INVOICES}
    assert answered.model == "lexical" and answered.data["floored"] == 0
    assert check({"kind": "retrieves_all", "k": 8, "refs": [ref for _k, ref, *_ in INVOICES]},
                 answered)[2] == 1.0

    nothing = await _answer(live, gateway, plan, {  # type: ignore[arg-type]
        "id": "ec-2", "name": "an operator this repository has never heard of",
        "input": "how does the Kubernetes operator reconcile a custom resource",
        "checks": [{"kind": "retrieves_nothing", "k": 8}], "weight": 1})
    assert nothing.refs == [] and nothing.data["floored"] >= 1
    assert check({"kind": "retrieves_nothing", "k": 8}, nothing) == (True, "nothing was handed over", 1.0)


async def test_the_half_that_finds_by_meaning_brings_its_distance_out_with_it(session: AsyncSession):
    """No lane here makes embeddings, so the vector is handed in by hand — what is under test is that
    the statement returns the numbers it computes instead of dropping them at its own edge."""
    session.add(Project(id=f"{PID}-vectors", name="Vectors"))
    await session.flush()
    near, far = [0.0] * EMBED_DIM, [0.0] * EMBED_DIM
    near[0], far[1] = 1.0, 1.0
    session.add_all([
        Chunk(project_id=f"{PID}-vectors", kind="doc", ref="docs/near.md#0", path="docs/near.md",
              title="docs/near.md · Tax", body="tax on an invoice line", line=1, embedding=near,
              dim=EMBED_DIM, model="fake-embed"),
        Chunk(project_id=f"{PID}-vectors", kind="doc", ref="docs/far.md#0", path="docs/far.md",
              title="docs/far.md · Something else", body="a tram timetable", line=1, embedding=far,
              dim=EMBED_DIM, model="fake-embed"),
    ])
    await session.flush()

    found, _dropped = await ChunkRepository(session).fused(f"{PID}-vectors", "tax on an invoice",
                                                           vector=near, limit=4)
    by_ref = {x["ref"]: x for x in found}
    assert by_ref["docs/near.md#0"]["how"] == "both"
    assert by_ref["docs/near.md#0"]["distance"] == 0.0 and by_ref["docs/near.md#0"]["rank"]["semantic"] == 1
    assert by_ref["docs/far.md#0"]["how"] == "semantic"
    assert by_ref["docs/far.md#0"]["distance"] == 1.0 and by_ref["docs/far.md#0"]["tsRank"] is None
    # Orthogonal is a cosine distance of 1: far outside the ceiling, so it is never grounded on.
    assert near_enough(by_ref["docs/near.md#0"], 3) is True
    assert near_enough(by_ref["docs/far.md#0"], 3) is False
