"""Signed state for the GitHub App install flow (#192).

``github.com/apps/<slug>/installations/new`` round-trips a ``state`` query
param through the user's browser and GitHub's redirect, unmodified. The
callback must know which project asked without trusting the value as given —
same shape as access tokens (HS256, ``settings.jwt_secret``,
a ``type`` claim), so this can never be confused with an access token or a
sandbox scoped token even though all three ultimately share the one platform
secret (project convention: no per-module signing secret).
"""

import time
import uuid
from typing import NamedTuple

import jwt

from app.core.config import settings

_SECRET = settings.jwt_secret
_ALG = "HS256"
_TYPE = "github_install"
# Generous but bounded: a human clicking through GitHub's install UI, not a
# long-lived credential.
_TTL_S = 600


INSTALL_TTL_S = _TTL_S


class InstallClaims(NamedTuple):
    project_id: uuid.UUID
    user_id: int
    handle: str
    jti: str
    # Started in the desktop app: the callback hands the result back to it
    # (app/api/app_return.py).
    in_app: bool = False


def mint_install_state(
    project_id: uuid.UUID,
    *,
    user_id: int,
    handle: str,
    in_app: bool = False,
    ttl_s: int = _TTL_S,
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "pid": str(project_id),
            "uid": user_id,
            "handle": handle,
            "jti": uuid.uuid4().hex,
            "app": in_app,
            "type": _TYPE,
            "iat": now,
            "exp": now + ttl_s,
        },
        _SECRET,
        algorithm=_ALG,
    )


def verify_install_state(state: str) -> InstallClaims | None:
    """Verify the initiating identity and one-use state; legacy states expire closed."""
    try:
        decoded = jwt.decode(state, _SECRET, algorithms=[_ALG])
        if decoded.get("type") != _TYPE:
            return None
        handle, jti = decoded.get("handle"), decoded.get("jti")
        if (
            not isinstance(handle, str)
            or not handle
            or not isinstance(jti, str)
            or not jti
        ):
            return None
        return InstallClaims(
            uuid.UUID(decoded["pid"]),
            int(decoded["uid"]),
            handle,
            jti,
            decoded.get("app") is True,
        )
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        return None
