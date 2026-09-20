"""Turning what someone typed into a full-text query — the one rule every search in the app shares.

There are two things a person can mean, and the difference decides whether anything is found at all.
Typing into a search box, they mean every word: "shop tax" is a narrowing. Asking a question, they do
not: "how does the app hand work to a background job?" carries a dozen words the answer never contains,
and requiring all of them — which is what `websearch_to_tsquery` does — matched nothing, for every real
question, in every place a question was searched: Ask memory, session grounding, research, evals. So a
question is searched for ANY of its meaningful words, and `ts_rank` still puts the pieces that carry the
most of them first.
"""
from __future__ import annotations

import re
from typing import Literal

from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement

Mode = Literal["all", "any"]

#: Words too common to narrow anything, dropped before an "any" query so they cannot match everything.
NOISE = frozenset("a an and are as at be by can could do does for from has have how in is it its of on or "
                  "should that the their them then there these this to was what when where which who why "
                  "will with would you your".split())


def terms(q: str) -> list[str]:
    """The meaningful words of a question: what the "any" query is built from, in order, deduplicated.

    Said once here because two things need it and must agree. The query is one of them. The other is
    the relevance floor: `ts_rank` over an `a | b | c` query is the *mean* of each word's own rank —
    measured on this Postgres, `step` alone ranks 0.08275, `run` alone 0.07599, and `step | run`
    0.07937, which is their mean to seven places — so a rank only means something beside the number of
    words it was averaged over.
    """
    return list(dict.fromkeys(w for w in re.findall(r"[a-z0-9_]+", q.lower())
                              if len(w) > 2 and w not in NOISE))


def tsquery(q: str, mode: Mode = "all") -> ColumnElement:
    """The query. "all" keeps the search-box syntax — quotes, `-word`, `or` — that people type on purpose."""
    text = q.strip()
    if mode == "all":
        return func.websearch_to_tsquery("english", text)
    words = terms(text)
    if not words:
        return func.websearch_to_tsquery("english", text)
    # Each word goes through to_tsquery as a lexeme of its own, joined with `|`. Only [a-z0-9_] survives
    # the pattern above, so nothing a person typed can become tsquery syntax.
    return func.to_tsquery("english", " | ".join(words))
