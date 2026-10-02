"""The actor seam (pure, adapter-injected): token > cheese > nobody."""

import uuid

import pytest

from app.domain.identity.actor import (
    Actor,
    TokenIdentity,
    resolve_actor,
)

pytestmark = pytest.mark.anyio


def _verify_ok(handle: str, uid: uuid.UUID | None = None):
    def verify(_token: str) -> TokenIdentity | None:
        return TokenIdentity(handle=handle, user_id=uid)

    return verify


def _verify_none(_token: str) -> TokenIdentity | None:
    return None


async def _cheese_true() -> bool:
    return True


async def _cheese_false() -> bool:
    return False


async def _resolve(**kw) -> Actor | None:
    base = dict(
        bearer_token=None,
        verify_token=_verify_none,
        cheese_valid=_cheese_false,
        cheese_handle="cheese",
    )
    base.update(kw)
    return await resolve_actor(**base)


async def test_token_wins_over_everything():
    uid = uuid.uuid4()
    actor = await _resolve(
        bearer_token="t",
        verify_token=_verify_ok("alice", uid),
        cheese_valid=_cheese_true,  # even a valid cheese token loses to the human token
    )
    assert actor is not None
    assert actor.handle == "alice"
    assert actor.user_id == uid
    assert actor.via == "token"
    assert actor.authenticated is True


async def test_invalid_token_falls_through_to_cheese():
    actor = await _resolve(
        bearer_token="bad", verify_token=_verify_none, cheese_valid=_cheese_true
    )
    assert actor is not None
    assert actor.handle == "cheese"
    assert actor.via == "cheese"
    assert actor.authenticated is True


async def test_no_credential_resolves_none():
    assert await _resolve() is None
    assert await _resolve(bearer_token="bad", verify_token=_verify_none) is None
