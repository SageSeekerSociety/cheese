"""Tokens signed for one purpose only.

Each purpose-bound token the platform mints is signed with a key derived from
`settings.jwt_secret` and the purpose's domain string (``b"cheese:<purpose>:v1"``),
so a token minted for one purpose fails the signature check of every other,
even with the same audience and claims. The domains, not separate secrets, keep
the purposes apart: there is no per-module signing secret.

Only the key derivation and the HS256 sign/verify around it live here. What a
token claims, which audience it names and what a caller checks beyond the
signature stay with each caller.
"""

import hashlib
import hmac
from collections.abc import Iterable

import jwt

from app.core.config import settings


def scoped_key(domain: bytes) -> bytes:
    """The signing key for the purpose named by ``domain``."""
    return hmac.digest(settings.jwt_secret.encode(), domain, hashlib.sha256)


def sign(claims: dict, *, domain: bytes) -> str:
    return jwt.encode(claims, scoped_key(domain), algorithm="HS256")


def verify(token: str, *, domain: bytes, audience: str, require: Iterable[str]) -> dict:
    """The claims of ``token`` once its signature, audience, expiry and the
    ``require``d claims check out; raises ``jwt.PyJWTError`` otherwise."""
    return jwt.decode(
        token,
        scoped_key(domain),
        algorithms=["HS256"],
        audience=audience,
        options={"require": list(require)},
    )
