"""A session's tools, declared natively, and a turn read the way a real model writes it.

Found on the first run against a real model (GPT-OSS-120B on Groq): told about tools only in the prompt, it
called one natively anyway, the provider refused the answer, and every question in every session ended in
"the answer holds no JSON object". Given its tools natively it calls them — and answers in plain words.
"""
from __future__ import annotations

import pytest

from app.services import chat
from app.services.chat import Turn, read_turn, tool_specs


def test_a_plain_answer_is_the_answer_and_json_still_works():
    assert read_turn("The token is kept in ~/.config/nc/token, by store_token.") == Turn(
        answer="The token is kept in ~/.config/nc/token, by store_token.")
    assert read_turn('{"tool": "read_file", "arguments": {"path": "a.py"}, "why": "look"}').tool == "read_file"
    assert read_turn('```json\n{"answer": "done"}\n```').answer == "done"
    # An answer that quotes a JSON object is still words, not a turn with nothing in it.
    quoted = 'The config reads like {"server": "https://x"} and nothing else.'
    assert read_turn(quoted).answer == quoted
    with pytest.raises(ValueError):
        read_turn("   ")


def test_the_tools_declared_are_the_tools_the_prompt_names():
    specs = {s["function"]["name"]: s["function"] for s in tool_specs()}
    assert set(specs) == {t.name for t in chat.CATALOGUE if t.name != "load_skill"}
    assert specs["read_file"]["parameters"]["properties"] == {"path": {"type": "string"},
                                                              "start": {"type": "integer"},
                                                              "lines": {"type": "integer"}}
    assert all(s["parameters"]["type"] == "object" for s in specs.values())


def test_a_plain_answer_streams_as_it_is_written_and_a_json_one_as_before():
    words = chat._Answer()
    assert words.feed("The token ") == "The token " and words.feed("is kept.") == "is kept."
    as_json = chat._Answer()
    assert as_json.feed('{"answer": "Hel') == "Hel" and as_json.feed('lo"}') == "lo"
    tool = chat._Answer()
    assert tool.feed('{"tool": "read_file"') == ""
