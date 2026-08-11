"""Signed state for the GitHub App install flow (#192).

``github.com/apps/<slug>/installations/new`` round-trips a ``state`` query
param through the user's browser and GitHub's redirect, unmodified. The
callback must know which project asked without trusting the value as given —
same shape as ``app.core.tokens`` session tokens (HS256, ``settings.jwt_secret``,
a ``type`` claim), so this can never be confused with a session token or a
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


def mint_install_state(project_id: uuid.UUID, *, ttl_s: int = _TTL_S) -> str:
    now = int(time.time())
    payload = {
        "pid": str(project_id),
        "type": _TYPE,
        "iat": now,
        "exp": now + ttl_s,
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALG)


def verify_install_state(state: str) -> uuid.UUID | None:
    """The project_id a valid, unexpired state was minted for, else None."""
    try:
        decoded = jwt.decode(state, _SECRET, algorithms=[_ALG])
    except jwt.PyJWTError:
        return None
    if decoded.get("type") != _TYPE or not decoded.get("pid"):
        return None
    try:
        return uuid.UUID(str(decoded["pid"]))
    except ValueError:
        return None


# Same shape, distinct `type`, for the OTHER GitHub App flow: a logged-in
# human linking their own GitHub identity (#192 "连接 GitHub 账号",
# user-to-server auth) rather than a project connecting a repo. The distinct
# `type` claim keeps the two — and the unrelated session token in
# app.core.tokens — from ever validating as each other despite sharing
# `jwt_secret`.
_ACCOUNT_LINK_TYPE = "github_account_link"


class AccountLinkClaims(NamedTuple):
    user_id: int
    # Which project's settings page to bounce back to (the button lives on
    # ProjectSettingsView, not a standalone account page) — None falls back
    # to the app root.
    return_project_id: uuid.UUID | None


def mint_account_link_state(
    user_id: int,
    *,
    return_project_id: uuid.UUID | None = None,
    ttl_s: int = _TTL_S,
) -> str:
    now = int(time.time())
    payload = {
        "uid": user_id,
        "rpid": str(return_project_id) if return_project_id else None,
        "type": _ACCOUNT_LINK_TYPE,
        "iat": now,
        "exp": now + ttl_s,
    }
    return jwt.encode(payload, _SECRET, algorithm=_ALG)


def verify_account_link_state(state: str) -> AccountLinkClaims | None:
    """The claims a valid, unexpired account-link state was minted with."""
    try:
        decoded = jwt.decode(state, _SECRET, algorithms=[_ALG])
    except jwt.PyJWTError:
        return None
    if decoded.get("type") != _ACCOUNT_LINK_TYPE or decoded.get("uid") is None:
        return None
    try:
        user_id = int(decoded["uid"])
    except (TypeError, ValueError):
        return None
    rpid = decoded.get("rpid")
    try:
        return_project_id = uuid.UUID(str(rpid)) if rpid else None
    except ValueError:
        return_project_id = None
    return AccountLinkClaims(user_id=user_id, return_project_id=return_project_id)
