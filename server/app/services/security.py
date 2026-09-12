"""The cryptography, on its own.

Four small functions with no database and no web framework anywhere near them, so they can be read in
one sitting and tested by calling them. Everything about *policy* — how many attempts, how long a
session lasts, who may do what — lives in the service; this file only knows how to hash a password
and how to make a token nobody can guess.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

#: scrypt at parameters that cost about a tenth of a second here — expensive to attack, quick to sign in.
SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}
TOKEN_BYTES = 32


def hash_password(password: str) -> str:
    """`scrypt$n$r$p$salt$digest` — the parameters travel with the hash, so they can be raised later
    without locking anyone out of an account hashed under the old ones."""
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **SCRYPT)
    return "$".join(["scrypt", str(SCRYPT["n"]), str(SCRYPT["r"]), str(SCRYPT["p"]),
                     base64.b64encode(salt).decode(), base64.b64encode(digest).decode()])


def verify_password(password: str, stored: str) -> bool:
    """Compared in constant time. A malformed stored hash is a failed check, never an exception."""
    try:
        algo, n, r, p, salt, digest = stored.split("$")
        if algo != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r),
                                p=int(p), dklen=len(expected))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def new_token() -> str:
    """The session token itself. It is shown once, to the browser, and never stored."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def token_hash(token: str) -> str:
    """What the database keeps. Someone who reads every row still cannot sign in as anybody."""
    return hashlib.sha256(token.encode()).hexdigest()
