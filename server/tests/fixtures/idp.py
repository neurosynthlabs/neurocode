"""A local OpenID Connect provider, made here, reached by nobody.

No test in this tree may touch a real identity provider: it would need the internet, somebody else's
uptime and a client secret in a repository. So this file is one — a discovery document, a JWKS and a
token endpoint — served straight into `SsoService`'s fetch port, with an RSA key pair written out as
three numbers so a token can really be signed and really be verified.

The key is a throwaway 2048-bit key generated once for these tests and committed on purpose. It signs
nothing but test tokens, and a test that generated its own would spend seconds per run doing it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any

ISSUER = "https://idp.test"
CLIENT_ID = "neurocode-test-client"

#: The throwaway key, base64url as a JWK spells it.
N = ("lo-qf6MrNDe7XOvxpgCE4w9-d4HekU2zru2FPLS6CFXsM2TzSz-J7t02HkX8wvnQNaBrRuLwvq9ioUU8O67xp1UZrv8Ts1fdSql9"
     "eEQjBKnbLPJtmrYh1rWQpSWdvogv3QElgYBmgqnR6zfk2mlKwOn9X3hqWMivZZpkMW9iqN-b-y-F7qPzdZlPBNK1oCVD3MjaL-r9"
     "TrJhbO3AhCMWoCzT7q7QHpFJgEvqaYLrrYU0xEccQmgWXaWOe3O9V1K4izkx_-VEbAoKun2LHtPd7hM4AZWl-PQ3oGQfH9VU-E-2"
     "GynFbUH9CYenMZkkqE4si4vtLPQA0aCwBXY4W1DLjw")
E = "AQAB"
D = ("OFYwk4rwDx1tf8MPjDB0iOvSxA_1woQDIIjiyojLdXQFKmPzP2xaheUDK4e_oQtN08sfaQpgz9EbhEG-XIzFAnpu3lK2wew2MwpB"
     "lx9TxRpzlxltVq8g8VDF-22cXV9jHXyg1pi_gtPdZvjmqq0sWgwUEUWi8W7CZq_DHtZRYeOa6rHz-jJ-DlmmNgxcBbqOjPHR6nTJ"
     "ZkY1C9hcIvZlBPlRS4lKDpGBEPiiOFE867nJP3DDlGoRBqHADplAf8fTkV3g_XT0C0ppekAFD2bE516TxfZZQ73gqOhLItnbGfU5"
     "vwrV-c71PN4KxkIL79z7fCPKlvlCmqbnz7zE7EZqkQ")
KID = "test-key-1"

#: A second key, published by nobody: a token signed with it is a forgery, which is what proves the
#: verifier is doing anything at all. (The same modulus with a different private exponent would not
#: verify either, but a key the provider never published is the honest shape of the attack.)
OTHER_N = N
OTHER_D = D


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _int(text: str) -> int:
    return int.from_bytes(base64.urlsafe_b64decode(text + "=" * (-len(text) % 4)), "big")


#: The DER prefix PKCS#1 v1.5 wraps a SHA-256 digest in.
SHA256_DER = bytes.fromhex("3031300d060960864801650304020105000420")


def sign(claims: dict[str, Any], *, alg: str = "RS256", kid: str | None = KID,
         n: str = N, d: str = D) -> str:
    """An id token, really signed. `alg`, `kid` and the key are arguments so a test can forge one."""
    header: dict[str, Any] = {"alg": alg, "typ": "JWT"}
    if kid is not None:
        header["kid"] = kid
    head = b64url(json.dumps(header, separators=(",", ":")).encode())
    body = b64url(json.dumps(claims, separators=(",", ":")).encode())
    if alg == "none":
        return f"{head}.{body}."
    modulus, exponent = _int(n), _int(d)
    size = (modulus.bit_length() + 7) // 8
    digest = SHA256_DER + hashlib.sha256(f"{head}.{body}".encode()).digest()
    block = b"\x00\x01" + b"\xff" * (size - len(digest) - 3) + b"\x00" + digest
    raw = pow(int.from_bytes(block, "big"), exponent, modulus).to_bytes(size, "big")
    return f"{head}.{body}.{b64url(raw)}"


def claims(**over: Any) -> dict[str, Any]:
    """A token this workspace should accept, before a test spoils one field of it."""
    now = int(time.time())
    return {"iss": ISSUER, "aud": CLIENT_ID, "sub": "idp-subject-1", "email": "dev@example.com",
            "name": "Dev Person", "iat": now, "exp": now + 300, **over}


class FakeIdp:
    """The three documents a sign-in reads, and a record of every address that was asked for.

    That record is the test for one of the product's own rules: nothing but the id token decides who
    someone is, so the userinfo endpoint must never be fetched — and the only way to know it was not
    is to have written down what was.
    """

    def __init__(self, *, issuer: str = ISSUER, token: str | None = None,
                 token_answer: dict[str, Any] | None = None, keys: list[dict[str, Any]] | None = None) -> None:
        self.issuer = issuer
        self.token = token
        self.token_answer = token_answer
        self.keys = keys
        self.asked: list[str] = []
        self.posted: list[dict[str, str]] = []

    def jwks(self) -> list[dict[str, Any]]:
        return self.keys if self.keys is not None else [{"kty": "RSA", "kid": KID, "alg": "RS256",
                                                         "use": "sig", "n": N, "e": E}]

    def fetch(self, url: str, *, data: bytes | None = None, headers: Any = None,
              timeout: float = 10.0) -> dict[str, Any]:
        from urllib.parse import parse_qsl

        self.asked.append(url)
        if url.endswith("/.well-known/openid-configuration"):
            return {"issuer": self.issuer,
                    "authorization_endpoint": f"{self.issuer}/authorize",
                    "token_endpoint": f"{self.issuer}/token",
                    "userinfo_endpoint": f"{self.issuer}/userinfo",
                    "jwks_uri": f"{self.issuer}/jwks"}
        if url.endswith("/jwks"):
            return {"keys": self.jwks()}
        if url.endswith("/token"):
            self.posted.append(dict(parse_qsl((data or b"").decode())))
            if self.token_answer is not None:
                return self.token_answer
            return {"access_token": "at", "token_type": "Bearer", "id_token": self.token or ""}
        raise AssertionError(f"the sign-in reached {url}, which it has no business reading")
