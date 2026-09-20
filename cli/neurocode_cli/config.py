"""Where `nc` keeps what it needs between runs: which server, which web address, the default project, and
the token.

The token goes to the operating system's keychain (macOS Keychain, Secret Service, Windows Credential
Locker) through `keyring`. Where there is no keychain — a server over SSH, a container — it falls back to
a file only its owner can read (0600), and says so. The environment wins over both, so a script or CI
job can run `nc` without ever writing a secret to disk: NC_URL, NC_TOKEN, NC_WEB, NC_PROJECT.
"""
from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: The keychain entry: this service, one account per server address.
KEYRING_SERVICE = "neurocode-cli"


def home() -> Path:
    """`NC_CONFIG_DIR`, else `$XDG_CONFIG_HOME/neurocode`, else `~/.config/neurocode`."""
    named = os.environ.get("NC_CONFIG_DIR")
    if named:
        return Path(named).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME")
    return (Path(base).expanduser() if base else Path.home() / ".config") / "neurocode"


def _config_file() -> Path:
    return home() / "config.json"


def _credentials_file() -> Path:
    return home() / "credentials.json"


def _read(path: Path) -> dict[str, Any]:
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def _write(path: Path, data: dict[str, Any], *, private: bool = False) -> None:
    """Written whole, then moved into place, so a crash never leaves half a file. A private file is
    created 0600 before a byte of it is written."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(tmp, flags, 0o600 if private else 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        json.dump(data, out, indent=2)
    if private:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    os.replace(tmp, path)


@dataclass(frozen=True)
class Profile:
    """What `nc` knows about where it is pointed. `token_store` says where the token came from:
    env | keyring | file, or None when there is none."""

    server: str | None
    web: str | None
    project: str | None
    token: str | None
    token_store: str | None


# ── the keychain, and the file it falls back to ──────────────────
def _keyring() -> Any | None:
    """The keyring module when a real backend is there; None when there is none (or it is the one that
    fails every call), so the caller falls back to the file."""
    try:
        import keyring
        from keyring.backends import fail
    except Exception:                                # noqa: BLE001 — no keyring means the file, never a crash
        return None
    try:
        backend = keyring.get_keyring()
    except Exception:                                # noqa: BLE001
        return None
    if isinstance(backend, fail.Keyring) or getattr(backend, "priority", 1) <= 0:
        return None
    return keyring


def _token_from_store(server: str) -> tuple[str | None, str | None]:
    ring = _keyring()
    if ring is not None:
        try:
            found = ring.get_password(KEYRING_SERVICE, server)
        except Exception:                            # noqa: BLE001 — a locked or broken keychain reads as empty
            found = None
        if found:
            return found, "keyring"
    found = _read(_credentials_file()).get(server)
    return (found, "file") if isinstance(found, str) and found else (None, None)


def store_token(server: str, token: str) -> str:
    """Keep the token for this server. Answers where it went: keyring or file."""
    ring = _keyring()
    if ring is not None:
        try:
            ring.set_password(KEYRING_SERVICE, server, token)
            _forget_file(server)                     # never two copies, one of them stale
            return "keyring"
        except Exception:                            # noqa: BLE001 — a keychain that refuses falls back to the file
            pass
    kept = _read(_credentials_file())
    kept[server] = token
    _write(_credentials_file(), kept, private=True)
    return "file"


def _forget_file(server: str) -> None:
    kept = _read(_credentials_file())
    if server in kept:
        del kept[server]
        _write(_credentials_file(), kept, private=True)


def forget_token(server: str) -> None:
    ring = _keyring()
    if ring is not None:
        try:
            ring.delete_password(KEYRING_SERVICE, server)
        except Exception:                            # noqa: BLE001 — nothing stored is already forgotten
            pass
    _forget_file(server)


# ── the profile ──────────────────────────────────────────────────
def load() -> Profile:
    saved = _read(_config_file())
    server = os.environ.get("NC_URL") or saved.get("server")
    server = server.rstrip("/") if isinstance(server, str) and server else None
    web = os.environ.get("NC_WEB") or saved.get("web") or None
    project = os.environ.get("NC_PROJECT") or saved.get("project") or None
    token, where = (os.environ.get("NC_TOKEN") or None), "env"
    if token is None:
        token, where = _token_from_store(server) if server else (None, None)
    return Profile(server=server, web=web.rstrip("/") if isinstance(web, str) else None,
                   project=project if isinstance(project, str) else None, token=token,
                   token_store=where if token else None)


# ── the session `--continue` continues ───────────────────────────
def _session_key(server: str | None, project: str | None) -> str:
    """One remembered session per server and project: continuing in one project must never walk into
    another project's conversation, and two servers are two different workspaces."""
    return f"{server or ''}|{project or ''}"


def remember_session(server: str | None, project: str | None, ref: str) -> None:
    """Keep the session this machine was last in. It is a convenience, not a record: the server holds
    the sessions themselves, and `--continue` falls back to asking it for the newest."""
    saved = _read(_config_file())
    kept = saved.get("lastSession")
    kept = dict(kept) if isinstance(kept, dict) else {}
    kept[_session_key(server, project)] = ref
    save(lastSession=kept)


def last_session(server: str | None, project: str | None) -> str | None:
    kept = _read(_config_file()).get("lastSession")
    found = kept.get(_session_key(server, project)) if isinstance(kept, dict) else None
    return found if isinstance(found, str) and found else None


def save(**fields: Any) -> None:
    """Change these fields of the saved profile; None removes one."""
    saved = _read(_config_file())
    for key, value in fields.items():
        if value is None:
            saved.pop(key, None)
        else:
            saved[key] = value
    _write(_config_file(), saved)
