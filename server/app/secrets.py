"""Secrets the API holds for the workspace: model API keys, and a token for a forge.

They live in one JSON file next to the database, readable only by the account that runs the API
(mode 0600). They are never written to the database or a log line, and never sent in a response:
routes report whether a secret is set and its last four characters, nothing more.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path


class Secrets:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, str] = {}
        if path.is_file():
            try:
                self._data = {k: v for k, v in json.loads(path.read_text()).items() if isinstance(v, str)}
            except (ValueError, AttributeError):
                self._data = {}

    def get(self, key: str) -> str | None:
        return self._data.get(key) or None

    def set(self, key: str, value: str | None) -> None:
        """Store a secret, or remove it when `value` is empty. The file is replaced atomically."""
        with self._lock:
            if value:
                self._data[key] = value
            else:
                self._data.pop(key, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as fh:
                json.dump(self._data, fh)
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)

    @staticmethod
    def mask(value: str | None) -> str | None:
        return f"••••{value[-4:]}" if value else None


#: Where a forge token lives in this file, and the environment variable a person may already have set
#: for the same forge — the pair a model key has, for the same reason: somebody who already exports
#: GITHUB_TOKEN for their own scripts should not have to type it in again here.
#:
#: A token is the second choice and stays the second choice. `gh` and `glab` are asked first, because
#: they are already signed in and storing nothing is always safer than storing something.
FORGE_SECRETS: dict[str, tuple[str, str]] = {
    "github.com": ("github_token", "GITHUB_TOKEN"),
    "gitlab.com": ("gitlab_token", "GITLAB_TOKEN"),
}


def forge_token(host: str, secrets: Secrets, environ: dict[str, str] | None = None) -> str | None:
    """The token stored for a forge, or the one its environment variable holds. None for a host this
    product has no API for — Bitbucket among them, whose compare link is all there is."""
    where = FORGE_SECRETS.get(host)
    if where is None:
        return None
    env = os.environ if environ is None else environ
    return secrets.get(where[0]) or env.get(where[1]) or None


def forge_token_source(host: str, secrets: Secrets, environ: dict[str, str] | None = None) -> str | None:
    """"workspace" when it is in the keys file, "environment" when it is only in the environment, None
    when there is no token at all. The token itself never leaves this module unmasked."""
    where = FORGE_SECRETS.get(host)
    if where is None:
        return None
    env = os.environ if environ is None else environ
    if secrets.get(where[0]):
        return "workspace"
    return "environment" if env.get(where[1]) else None
