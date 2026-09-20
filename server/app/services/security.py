"""The cryptography, on its own.

Small functions with no database and no web framework anywhere near them, so they can be read in
one sitting and tested by calling them. Everything about *policy* — how many attempts, how long a
session lasts, who may do what, which identity provider is trusted — lives in the service; this file
only knows how to hash a password, how to make a token nobody can guess, and how to tell whether an
id token was really signed by the key the provider published.

**Why the JWT verifier is written out here.** Verifying an RS256 signature is one modular
exponentiation with a *public* key and one fixed-length comparison. Both are public operations —
there is no secret in the machine to leak by timing — so the arithmetic Python already has is enough,
and the alternative was adding a cryptography library to a product whose whole promise is that it
runs on your laptop with nothing downloaded. What that trade needs in return is strictness, and the
strictness is here: the padding is rebuilt and compared whole rather than parsed, the digest
algorithm is taken from a fixed table and never from the token, and an algorithm this does not
implement is refused by name instead of being waved through.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from typing import Any

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


# ── base64url, the way every OpenID Connect document spells it ───
def b64url_decode(text: str) -> bytes:
    """Padding is optional in a JWT and absent in every real one, so it is put back before decoding."""
    padded = text + "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(padded.encode())


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


# ── the state and nonce that travel with a sign-in ───────────────
def sign_state(secret: str, payload: dict[str, Any]) -> str:
    """`<payload>.<mac>` — what goes into the browser's one-time cookie while it is at the provider.

    The payload is not secret (it is a state string and a nonce this server just made); what matters
    is that it comes back unchanged, so it is carried with a MAC rather than stored in a table. A
    table would be the other honest answer; a cookie is one round trip fewer and one row fewer to
    expire, and the MAC is what makes the two equivalent.
    """
    body = b64url_encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    return f"{body}.{_mac(secret, body)}"


def read_state(secret: str, signed: str) -> dict[str, Any] | None:
    """The payload back, or nothing at all — a bad MAC, a mangled cookie and a missing one are one
    answer, because none of them is a sign-in and telling them apart tells an attacker something."""
    try:
        body, mac = signed.split(".", 1)
    except ValueError:
        return None
    if not hmac.compare_digest(mac, _mac(secret, body)):
        return None
    try:
        found = json.loads(b64url_decode(body))
    except (ValueError, TypeError):
        return None
    return found if isinstance(found, dict) else None


def _mac(secret: str, body: str) -> str:
    return hmac.new(secret.encode(), body.encode(), hashlib.sha256).hexdigest()


# ── verifying an id token ────────────────────────────────────────
class BadToken(Exception):
    """An id token that will not be trusted, and the sentence saying why."""


#: The signature algorithms this verifies, and the digest each one names. RS256 is what Google, Okta,
#: Entra and every discovery document worth the name offer; an EC or an Ed25519 key is refused by name
#: rather than accepted unverified, which is the whole of the `alg: none` family of attacks.
RSA_ALGS = {"RS256": "sha256", "RS384": "sha384", "RS512": "sha512"}
#: The DER of the DigestInfo that wraps each digest in PKCS#1 v1.5, without the digest itself.
DIGEST_INFO = {
    "sha256": bytes.fromhex("3031300d060960864801650304020105000420"),
    "sha384": bytes.fromhex("3041300d060960864801650304020205000430"),
    "sha512": bytes.fromhex("3051300d060960864801650304020305000440"),
}


def jwt_header(token: str) -> dict[str, Any]:
    """The header alone, unverified — only ever used to find *which* published key to verify with."""
    try:
        header = json.loads(b64url_decode(token.split(".")[0]))
    except (ValueError, TypeError, IndexError) as broken:
        raise BadToken("That is not a token this can read.") from broken
    if not isinstance(header, dict):
        raise BadToken("That token's header is not an object.")
    return header


def verify_jwt(token: str, keys: list[dict[str, Any]]) -> dict[str, Any]:
    """The claims of `token`, once one of the provider's published keys has signed it.

    `keys` is the `keys` array of the provider's JWKS, exactly as it published it. The claims are
    returned unjudged: *who* the token says someone is, whether it has expired and whether it was
    meant for this workspace are the identity service's questions, not this file's.
    """
    try:
        head_b64, body_b64, sig_b64 = token.split(".")
    except ValueError as broken:
        raise BadToken("That token is not in three parts, so it is not a JWT.") from broken
    header = jwt_header(token)
    alg = str(header.get("alg", ""))
    if alg not in RSA_ALGS:
        raise BadToken(f"This identity provider signed with {alg or 'no algorithm'}; NeuroCode verifies "
                       f"{', '.join(sorted(RSA_ALGS))}.")
    signed = f"{head_b64}.{body_b64}".encode()
    signature = b64url_decode(sig_b64)
    kid = header.get("kid")
    # A `kid` names the key; without one every RSA key the provider published is tried, which is what
    # a provider mid-rotation actually needs.
    candidates = [k for k in keys if k.get("kty") == "RSA" and (kid is None or k.get("kid") == kid)] \
        or [k for k in keys if k.get("kty") == "RSA"]
    if not candidates:
        raise BadToken("This identity provider published no RSA key that could have signed that token.")
    if not any(_rsa_verify(signed, signature, key, RSA_ALGS[alg]) for key in candidates):
        raise BadToken("That token's signature is not this identity provider's.")
    try:
        claims = json.loads(b64url_decode(body_b64))
    except (ValueError, TypeError) as broken:
        raise BadToken("That token's claims are not readable.") from broken
    if not isinstance(claims, dict):
        raise BadToken("That token's claims are not an object.")
    return claims


def _rsa_verify(signed: bytes, signature: bytes, key: dict[str, Any], digest: str) -> bool:
    """PKCS#1 v1.5, rebuilt and compared whole.

    The usual mistake is to *parse* what the exponentiation produced — walk past the 0x00 0x01, skip
    the padding, read the DigestInfo — which is what lets a forged signature with a short padding
    through (Bleichenbacher's e=3 attack). Building the one byte string a valid signature must
    produce and comparing it entire cannot make that mistake.
    """
    try:
        n = int.from_bytes(b64url_decode(str(key["n"])), "big")
        e = int.from_bytes(b64url_decode(str(key["e"])), "big")
    except (KeyError, ValueError, TypeError):
        return False
    size = (n.bit_length() + 7) // 8
    if n <= 0 or e <= 0 or len(signature) != size:
        return False
    produced = pow(int.from_bytes(signature, "big"), e, n).to_bytes(size, "big")
    body = DIGEST_INFO[digest] + hashlib.new(digest, signed).digest()
    if size < len(body) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (size - len(body) - 3) + b"\x00" + body
    return hmac.compare_digest(produced, expected)
