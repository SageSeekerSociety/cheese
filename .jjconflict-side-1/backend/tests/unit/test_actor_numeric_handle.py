"""A token actor whose handle degraded into the int User PK must be repaired.

Main-minted tokens put the int User PK in ``sub``; when the ``handle`` claim is
absent the actor's handle becomes that number (e.g. ``"470"``). Every
authorization key in the platform is the handle STRING — topic rosters,
project membership, project owner — so a numeric handle matches nothing and the
caller silently loses every permission they hold. These tests pin the repair.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api import auth as auth_mod
from app.core.tokens import mint_session_token

pytestmark = pytest.mark.anyio


def _resolver(monkeypatch, *, token: str, user):
    """An ActorResolver whose only live dependency is the user lookup."""
    monkeypatch.setattr(
        auth_mod,
        "IdentityService",
        lambda _session: SimpleNamespace(is_agent=AsyncMock(return_value=False)),
    )
    repo = SimpleNamespace(get_by_id=AsyncMock(return_value=user))
    monkeypatch.setattr(auth_mod, "UserRepository", lambda _session: repo)
    resolver = auth_mod.ActorResolver(
        session=MagicMock(), bearer=token, cheese_token=""
    )
    return resolver, repo


async def test_numeric_handle_is_resolved_to_the_real_username(monkeypatch):
    """sub=470 with no usable handle claim → the actor acts as the real user."""
    token = mint_session_token(handle="470", user_id=470)
    resolver, repo = _resolver(
        monkeypatch, token=token, user=SimpleNamespace(username="wangchangxin")
    )

    actor = await resolver.resolve(fallback_handle=None)

    assert actor.handle == "wangchangxin"
    assert actor.user_id == 470
    assert actor.via == "token"
    repo.get_by_id.assert_awaited_once_with(470)


async def test_unresolvable_user_keeps_the_numeric_handle(monkeypatch):
    """No such user → stay denied. Repair must never invent an identity."""
    token = mint_session_token(handle="999999", user_id=999999)
    resolver, _ = _resolver(monkeypatch, token=token, user=None)

    actor = await resolver.resolve(fallback_handle=None)

    assert actor.handle == "999999"


async def test_a_normal_handle_is_left_alone(monkeypatch):
    """A token that carries a real handle never triggers the lookup."""
    token = mint_session_token(handle="wangchangxin", user_id=470)
    resolver, repo = _resolver(
        monkeypatch, token=token, user=SimpleNamespace(username="someone-else")
    )

    actor = await resolver.resolve(fallback_handle=None)

    assert actor.handle == "wangchangxin"
    repo.get_by_id.assert_not_awaited()


async def test_handle_fallback_actor_is_untouched(monkeypatch):
    """The Phase-0 fallback (no token) is not a token actor — never repaired."""
    resolver, repo = _resolver(
        monkeypatch, token=None, user=SimpleNamespace(username="wangchangxin")
    )

    actor = await resolver.resolve(fallback_handle="470")

    assert actor.handle == "470"
    assert actor.via == "handle"
    repo.get_by_id.assert_not_awaited()
