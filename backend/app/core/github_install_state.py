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


INSTALL_TTL_S = _TTL_S


class InstallClaims(NamedTuple):
    project_id: uuid.UUID
    user_id: int
    handle: str
    jti: str


def mint_install_state(
    project_id: uuid.UUID, *, user_id: int, handle: str, ttl_s: int = _TTL_S
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "pid": str(project_id),
            "uid": user_id,
            "handle": handle,
            "jti": uuid.uuid4().hex,
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
            uuid.UUID(decoded["pid"]), int(decoded["uid"]), handle, jti
        )
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        return None


# Same shape, distinct `type`, for the OTHER GitHub App flow: a logged-in
# human linking their own GitHub identity (#192 "连接 GitHub 账号",
# user-to-server auth) rather than a project connecting a repo. The distinct
# `type` claim keeps the two — and the unrelated session token in
# app.core.tokens — from ever validating as each other despite sharing
# `jwt_secret`.
_ACCOUNT_LINK_TYPE = "github_account_link"
# Exported so the reservation that makes a state single-use expires with the
# state itself, rather than being kept in step by hand in two files.
ACCOUNT_LINK_TTL_S = _TTL_S


class AccountLinkClaims(NamedTuple):
    user_id: int
    # Which project's settings page to bounce back to (the button lives on
    # ProjectSettingsView, not a standalone account page) — None falls back
    # to the app root.
    return_project_id: uuid.UUID | None
    # This state's one-shot identity. The signature says the platform minted
    # it; only this says nobody has spent it yet — see core.single_use_state
    # and #222.
    jti: str


class MintedAccountLinkState(NamedTuple):
    state: str
    jti: str


def mint_account_link_state(
    user_id: int,
    *,
    return_project_id: uuid.UUID | None = None,
    ttl_s: int = _TTL_S,
) -> MintedAccountLinkState:
    """A state plus the ``jti`` the caller must reserve before handing it out.

    Returned rather than reserved here so this module stays pure JWT with no
    I/O — and so the route cannot accidentally hand out a state whose reserve
    failed, which would be a link that never works.
    """
    now = int(time.time())
    jti = uuid.uuid4().hex
    payload = {
        "uid": user_id,
        "rpid": str(return_project_id) if return_project_id else None,
        "jti": jti,
        "type": _ACCOUNT_LINK_TYPE,
        "iat": now,
        "exp": now + ttl_s,
    }
    return MintedAccountLinkState(
        state=jwt.encode(payload, _SECRET, algorithm=_ALG), jti=jti
    )


def verify_account_link_state(state: str) -> AccountLinkClaims | None:
    """The claims a valid, unexpired account-link state was minted with.

    A state with no ``jti`` cannot be spent exactly once, so it is not a valid
    state — that includes any minted by the previous build. They are gone
    within the 600s TTL, and 「重新点一次」 is what the invalid_state copy
    already tells the user to do.
    """
    try:
        decoded = jwt.decode(state, _SECRET, algorithms=[_ALG])
    except jwt.PyJWTError:
        return None
    if decoded.get("type") != _ACCOUNT_LINK_TYPE or decoded.get("uid") is None:
        return None
    jti = decoded.get("jti")
    if not isinstance(jti, str) or not jti:
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
    return AccountLinkClaims(
        user_id=user_id, return_project_id=return_project_id, jti=jti
    )
