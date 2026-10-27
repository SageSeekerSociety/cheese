"""Every credential a caller presents to the API, and the one place that says
which kind it is.

Each is a JWT (RFC 7519) whose ``typ`` header names its kind (RFC 8725 §3.11):
``access+jwt`` for a person signed in, ``agent+jwt`` for a session's agent, and
so on down :class:`Kind`. Each kind is signed with its own key, derived from
the secret of its family and the kind's name, so a token of one kind fails the
signature check of every other even if its header were rewritten.

Two families, two secrets: what a person holds (``settings.jwt_secret``) and
what the platform hands its agents (``settings.sandbox_signing_secret``, which
the metering proxy holds too, to read an agent's token without asking).

What a kind's claims mean stays with the module that mints it
(``app.common.auth`` for a person's, ``app.core.sandbox_auth`` for an agent's).
"""

from __future__ import annotations

import base64
import enum
import hashlib
import hmac
import secrets
import time
from collections.abc import Mapping

import jwt

from app.core.config import settings


class Kind(enum.StrEnum):
    # A person's.
    ACCESS = "access"
    TWO_FA_PENDING = "2fa-pending"
    SUDO = "sudo"
    NOTICES = "notices"
    APP_SIGN_IN = "app-sign-in"
    # The platform's agents'.
    AGENT = "agent"
    PROJECT_AGENT = "project-agent"
    PERSONAL = "personal"
    PERSONAL_EXECUTION = "personal-execution"
    DELEGATED = "delegated"
    PASS = "pass"
    #: Pasted into another system (a CI pipeline, an Actions secret) to post
    #: into one room: no expiry, retired by the version it names
    #: (`app.core.webhook_auth`).
    WEBHOOK = "webhook"


_PERSONS = frozenset(
    {Kind.ACCESS, Kind.TWO_FA_PENDING, Kind.SUDO, Kind.NOTICES, Kind.APP_SIGN_IN}
)
#: The kinds that name no expiry: each lasts as long as something the route
#: accepting it checks (a sign-in session, a webhook's version).
_UNEXPIRING = frozenset({Kind.NOTICES, Kind.WEBHOOK})
_ALGORITHM = "HS256"


def _typ(kind: Kind) -> str:
    return f"{kind}+jwt"


def _key(kind: Kind) -> bytes:
    secret = (
        settings.jwt_secret if kind in _PERSONS else settings.sandbox_signing_secret
    )
    return hmac.digest(
        secret.encode(), f"cheese:credential:{kind}".encode(), hashlib.sha256
    )


def mint(kind: Kind, claims: dict, *, ttl_s: int | None = None) -> str:
    """A token of ``kind`` carrying ``claims``, issued now and expiring in
    ``ttl_s`` seconds (unless ``claims`` already names its ``exp``)."""
    now = int(time.time())
    payload = {"iat": now, **claims}
    if ttl_s is not None:
        payload["exp"] = now + ttl_s
    if "exp" not in payload and kind not in _UNEXPIRING:
        raise ValueError(f"a {kind} credential expires")  # i18n-exempt: platform bug
    return jwt.encode(
        payload, _key(kind), algorithm=_ALGORITHM, headers={"typ": _typ(kind)}
    )


def kind_of(token: str) -> Kind | None:
    """The kind ``token`` says it is, unverified: for choosing which check to
    run, never for trusting it. None for anything that is not one of these
    credentials (a device's token, the platform's own secret)."""
    if not token or token.count(".") != 2:
        return None
    try:
        typ = jwt.get_unverified_header(token).get("typ")
    except jwt.PyJWTError:  # type: ignore[attr-defined]
        return None
    if not isinstance(typ, str) or not typ.endswith("+jwt"):
        return None
    try:
        return Kind(typ.removesuffix("+jwt"))
    except ValueError:
        return None


def read(token: str, kind: Kind) -> dict | None:
    """The claims of ``token`` if it is a valid, unexpired credential of
    ``kind``, else None — never an exception: this runs on whatever a caller
    sent."""
    if kind_of(token) is not kind:
        return None
    try:
        claims = jwt.decode(
            token,
            _key(kind),
            algorithms=[_ALGORITHM],
            options={"require": [] if kind in _UNEXPIRING else ["exp"]},
        )
    except jwt.PyJWTError:  # type: ignore[attr-defined]
        return None
    return claims if isinstance(claims, dict) else None


def is_platform_secret(token: str) -> bool:
    """The platform's own secret presented as-is: what its services (the
    metering proxy, a storage sweep) call each other with."""
    secret = settings.sandbox_signing_secret
    return bool(token) and secrets.compare_digest(token, secret)


def presented(headers: Mapping[str, str]) -> str:
    """The credential a request carries, from wherever its client can put it.

    ``Authorization: Bearer`` is where every client of ours puts it. Clients
    we do not write put it elsewhere, and are read where they do: git answers
    a credential prompt with a password (``Authorization: Basic``), the
    GitHub CLI says ``Authorization: token``, and Claude Code sends its model
    key as ``x-api-key``. Which kind it is stays :func:`kind_of`'s to say.
    """
    scheme, _, value = (headers.get("authorization") or "").strip().partition(" ")
    if scheme.lower() in ("bearer", "token"):
        return value.strip()
    if scheme.lower() == "basic":
        try:
            decoded = base64.b64decode(value.strip(), validate=True).decode()
        except (ValueError, UnicodeDecodeError):
            return ""
        return decoded.partition(":")[2].strip()
    return (headers.get("x-api-key") or "").strip()
