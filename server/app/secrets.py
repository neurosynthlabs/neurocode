"""Secrets the API holds for the workspace. Today that means model API keys.

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
