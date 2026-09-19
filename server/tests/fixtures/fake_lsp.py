"""A tiny language server for the tests: it speaks the Language Server Protocol over stdio, as pyright or
gopls do, and knows just enough Python to answer from the files it is given.

- hover: the word under the position, the version of the document it holds (so a test can see the
  editor's unsaved text arrived), the root it was started in, and what the client answered to its own
  `workspace/configuration` request.
- definition: `def <word>` or `class <word>` in any .py file under the root, as a Location — or, for
  the word `elsewhere`, a file outside every root, and for `linked`, a LocationLink.
- documentSymbol: classes with their methods as children, and top-level functions.

Standard library only; it never touches the network.
"""
from __future__ import annotations

import json
import os
import re
import sys
from urllib.parse import quote, unquote, urlparse

docs: dict[str, tuple[int, str]] = {}
state: dict[str, object] = {"root": "", "configured": "unanswered"}


def read() -> dict | None:
    headers: dict[str, str] = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        text = line.decode().strip()
        if not text:
            break
        key, _, value = text.partition(":")
        headers[key.lower()] = value.strip()
    body = sys.stdin.buffer.read(int(headers.get("content-length", "0")))
    return json.loads(body)


def send(message: dict) -> None:
    body = json.dumps({"jsonrpc": "2.0", **message}).encode()
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    sys.stdout.buffer.flush()


def path_of(uri: str) -> str:
    return unquote(urlparse(uri).path)


def word_at(text: str, line: int, character: int) -> str:
    rows = text.split("\n")
    row = rows[line] if line < len(rows) else ""
    for m in re.finditer(r"\w+", row):
        if m.start() <= character <= m.end():
            return m.group()
    return ""


def text_of(uri: str) -> tuple[int, str]:
    if uri in docs:
        return docs[uri]
    with open(path_of(uri), encoding="utf-8") as f:
        return 0, f.read()


def find_definition(word: str) -> dict | None:
    pattern = re.compile(rf"^\s*(?:def|class)\s+({re.escape(word)})\b", re.M)
    for here, dirs, names in os.walk(str(state["root"])):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            full = os.path.join(here, name)
            uri = "file://" + quote(full)
            _, text = text_of(uri)
            m = pattern.search(text)
            if m:
                line = text.count("\n", 0, m.start(1))
                col = m.start(1) - (text.rfind("\n", 0, m.start(1)) + 1)
                return {"uri": uri, "range": {"start": {"line": line, "character": col},
                                              "end": {"line": line, "character": col + len(word)}}}
    return None


def symbols(text: str) -> list[dict]:
    out: list[dict] = []
    for i, row in enumerate(text.split("\n")):
        m = re.match(r"^(\s*)(def|class)\s+(\w+)", row)
        if not m:
            continue
        rng = {"start": {"line": i, "character": len(m.group(1))}, "end": {"line": i, "character": len(row)}}
        sel = {"start": {"line": i, "character": m.start(3)}, "end": {"line": i, "character": m.end(3)}}
        item = {"name": m.group(3), "kind": 5 if m.group(2) == "class" else (6 if m.group(1) else 12),
                "range": rng, "selectionRange": sel, "children": []}
        if m.group(1) and out and out[-1]["kind"] == 5:
            out[-1]["children"].append(item)
        else:
            out.append(item)
    return out


def main() -> None:
    while True:
        message = read()
        if message is None:
            return
        method = message.get("method")
        if method is None:
            if message.get("id") == "cfg":
                state["configured"] = json.dumps(message.get("result"))
            continue
        params = message.get("params") or {}
        if method == "initialize":
            state["root"] = path_of(params["rootUri"])
            send({"id": message["id"], "result": {"capabilities": {
                "hoverProvider": True, "definitionProvider": True, "documentSymbolProvider": True,
                "textDocumentSync": 1}, "serverInfo": {"name": "fake-lsp"}}})
        elif method == "initialized":
            send({"id": "cfg", "method": "workspace/configuration", "params": {"items": [{"section": "python"}]}})
            send({"method": "window/logMessage", "params": {"type": 3, "message": "fake-lsp is up"}})
        elif method == "textDocument/didOpen":
            doc = params["textDocument"]
            docs[doc["uri"]] = (doc["version"], doc["text"])
            # As real servers do once they have read a file: its first diagnostics report.
            send({"method": "textDocument/publishDiagnostics", "params": {"uri": doc["uri"], "diagnostics": []}})
        elif method == "textDocument/didChange":
            doc = params["textDocument"]
            docs[doc["uri"]] = (doc["version"], params["contentChanges"][-1]["text"])
        elif method == "textDocument/hover":
            uri = params["textDocument"]["uri"]
            version, text = text_of(uri)
            pos = params["position"]
            word = word_at(text, pos["line"], pos["character"])
            result = None if not word else {"contents": {"kind": "markdown", "value": (
                f"**{word}** · version {version} · root {os.path.basename(str(state['root']))} · "
                f"configured {state['configured']}")}}
            send({"id": message["id"], "result": result})
        elif method == "textDocument/definition":
            uri = params["textDocument"]["uri"]
            _, text = text_of(uri)
            pos = params["position"]
            word = word_at(text, pos["line"], pos["character"])
            if word == "elsewhere":
                result = [{"uri": "file:///definitely/not/inside/lib.py",
                           "range": {"start": {"line": 9, "character": 4}, "end": {"line": 9, "character": 13}}}]
            elif word == "linked":
                target = find_definition("helper")
                result = [{"targetUri": target["uri"], "targetRange": target["range"],
                           "targetSelectionRange": target["range"]}] if target else []
            else:
                found = find_definition(word) if word else None
                result = [found] if found else []
            send({"id": message["id"], "result": result})
        elif method == "textDocument/documentSymbol":
            _, text = text_of(params["textDocument"]["uri"])
            send({"id": message["id"], "result": symbols(text)})
        elif method == "shutdown":
            send({"id": message["id"], "result": None})
        elif method == "exit":
            return
        elif "id" in message:
            send({"id": message["id"], "error": {"code": -32601, "message": f"{method} is not handled"}})


if __name__ == "__main__":
    main()
