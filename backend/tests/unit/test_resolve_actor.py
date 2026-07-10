"""The actor seam (pure, adapter-injected): token > cheese > handle fallback."""

import uuid

import pytest

from app.domain.identity.actor import (
    Actor,
    TokenIdentity,
    resolve_actor,
)

pytestmark = pytest.mark.anyio

_AGENTS = {"cheese"}


def _verify_ok(handle: str, uid: uuid.UUID | None = None):
    def verify(_token: str) -> TokenIdentity | None:
        return TokenIdentity(handle=handle, user_id=uid)

    return verify


def _verify_none(_token: str) -> TokenIdentity | None:
    return None


async def _is_agent(handle: str) -> bool:
    return handle in _AGENTS


async def _cheese_true() -> bool:
    return True


async def _cheese_false() -> bool:
    return False


async def _resolve(**kw) -> Actor | None:
    base = dict(
        bearer_token=None,
        verify_token=_verify_none,
        cheese_valid=_cheese_false,
        is_agent=_is_agent,
        cheese_handle="cheese",
        fallback_handle=None,
    )
    base.update(kw)
    return await resolve_actor(**base)


async def test_token_wins_over_everything():
    uid = uuid.uuid4()
    actor = await _resolve(
        bearer_token="t",
        verify_token=_verify_ok("alice", uid),
        cheese_valid=_cheese_true,  # even a valid cheese token loses to the human token
        fallback_handle="mallory",
    )
    assert actor is not None
    assert actor.handle == "alice"
    assert actor.user_id == uid
    assert actor.via == "token"
    assert actor.authenticated is True
    assert actor.is_agent is False


async def test_invalid_token_falls_through_to_cheese():
    actor = await _resolve(
        bearer_token="bad", verify_token=_verify_none, cheese_valid=_cheese_true
    )
    assert actor is not None
    assert actor.handle == "cheese"
    assert actor.is_agent is True
    assert actor.via == "cheese"
    assert actor.authenticated is True


async def test_handle_fallback_when_no_credentials():
    actor = await _resolve(fallback_handle="bob")
    assert actor is not None
    assert actor.handle == "bob"
    assert actor.via == "handle"
    assert actor.authenticated is False  # fallback is NOT authenticated
    assert actor.is_agent is False


async def test_agent_derivation_on_fallback_handle():
    # Even the deprecated fallback derives is-agent from the binding adapter.
    actor = await _resolve(fallback_handle="cheese")
    assert actor is not None
    assert actor.is_agent is True


async def test_no_signal_resolves_none():
    assert await _resolve(fallback_handle=None) is None
    assert await _resolve(fallback_handle="   ") is None
